import argparse
import ast
import asyncio
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import GEMINI_THINKING_LEVELS, SUPPORTED_MODEL_PROVIDERS, Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

DEFAULT_CASES = Path("evals/character_lore_cases.jsonl")
DEFAULT_RESULTS_DIR = Path("data/evals")
EVAL_USER_ID = 910001
SPECIAL_EVAL_USER_ID = 910002
EVAL_GUILD_ID = 920001
EVAL_CHANNEL_ID = 930001
VALIDATORS = frozenset({"python_fenced_code", "python_syntax"})
_PYTHON_FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def response_validation_errors(response: str, validators: list[str]) -> list[str]:
    """Run deterministic checks on the final response without executing generated code."""

    blocks = _PYTHON_FENCE.findall(response)
    errors = []
    if "python_fenced_code" in validators and not blocks:
        errors.append("python 코드 블록이 없습니다.")
    if "python_syntax" in validators:
        if not blocks:
            if "python_fenced_code" not in validators:
                errors.append("구문을 검사할 python 코드 블록이 없습니다.")
        else:
            for index, code in enumerate(blocks, 1):
                try:
                    ast.parse(code)
                except SyntaxError as exc:
                    location = f"{exc.lineno}:{exc.offset}" if exc.lineno else "알 수 없음"
                    errors.append(f"python 코드 블록 {index} 구문 오류({location}): {exc.msg}")
    return errors


def read_cases(path: Path) -> list[dict]:
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        if not isinstance(case.get("id"), str) or not case["id"].strip():
            raise ValueError(f"{path}:{number}: id가 필요합니다.")
        if not isinstance(case.get("expected"), str) or not case["expected"].strip():
            raise ValueError(f"{path}:{number}: expected가 필요합니다.")
        if "turns" in case:
            turns = case["turns"]
            if not isinstance(turns, list) or not turns:
                raise ValueError(f"{path}:{number}: turns는 비어 있지 않은 배열이어야 합니다.")
            for turn in turns:
                if isinstance(turn, str) and turn.strip():
                    continue
                if (isinstance(turn, dict) and case.get("mode") == "server"
                        and isinstance(turn.get("input"), str) and turn["input"].strip()
                        and type(turn.get("user_id")) is int and turn["user_id"] > 0
                        and isinstance(turn.get("speaker"), str) and turn["speaker"].strip()):
                    continue
                raise ValueError(
                    f"{path}:{number}: turn은 문자열 또는 server 모드의 "
                    "{input, user_id(양의 정수), speaker} 객체여야 합니다."
                )
        elif not isinstance(case.get("input"), str) or not case["input"].strip():
            raise ValueError(f"{path}:{number}: input 또는 turns가 필요합니다.")
        mode = case.get("mode", "dm")
        if mode not in {"dm", "special_dm", "server"}:
            raise ValueError(f"{path}:{number}: mode는 dm, special_dm, server 중 하나여야 합니다.")
        channel_context = case.get("channel_context", [])
        if (not isinstance(channel_context, list)
                or any(not isinstance(row, dict)
                       or not isinstance(row.get("content"), str)
                       or not row["content"].strip()
                       for row in channel_context)):
            raise ValueError(f"{path}:{number}: channel_context는 content가 있는 객체 배열이어야 합니다.")
        validators = case.get("validators", [])
        if (not isinstance(validators, list)
                or any(not isinstance(value, str) or value not in VALIDATORS
                       for value in validators)):
            supported = ", ".join(sorted(VALIDATORS))
            raise ValueError(f"{path}:{number}: validators는 다음 값의 배열이어야 합니다: {supported}")
        cases.append(case)
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("eval case id가 중복됩니다.")
    return cases


def _provider_key(provider: str) -> str:
    variable = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }[provider]
    value = os.getenv(variable, "").strip()
    if not value:
        raise ValueError(f"{variable}가 필요합니다.")
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name}는 true 또는 false여야 합니다.")
    return normalized == "true"


def eval_settings(args) -> Settings:
    load_dotenv(Path.cwd() / ".env.local", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    provider = (args.provider or os.getenv("LLM_PROVIDER", "openai")).strip().lower()
    if provider not in SUPPORTED_MODEL_PROVIDERS:
        raise ValueError("--provider는 openai, gemini, openrouter 중 하나여야 합니다.")
    api_key = _provider_key(provider)
    model = (args.model or os.getenv("LLM_MODEL", "").strip()
             or os.getenv("OPENAI_MODEL", "").strip()
             or ("gpt-4.1-mini" if provider == "openai" else ""))
    if not model:
        raise ValueError("--model 또는 LLM_MODEL이 필요합니다.")
    community = os.getenv("COMMUNITY_LORE", "true").lower()
    if community not in {"true", "false"}:
        raise ValueError("COMMUNITY_LORE는 true 또는 false여야 합니다.")
    gemini_thinking_level = (
        getattr(args, "gemini_thinking_level", None)
        or os.getenv("GEMINI_THINKING_LEVEL", "low")
    ).strip().lower()
    if gemini_thinking_level not in GEMINI_THINKING_LEVELS:
        allowed = ", ".join(sorted(GEMINI_THINKING_LEVELS))
        raise ValueError(f"GEMINI_THINKING_LEVEL은 {allowed} 중 하나여야 합니다.")
    chat_web_search = _env_bool("CHAT_WEB_SEARCH", True)
    base = Settings(
        api_key=api_key,
        discord_token="eval-only",
        provider=provider,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        model=model,
        db_path=os.getenv("DATABASE_PATH", "data/hina.sqlite3"),
        prompt_path=os.getenv("CHARACTER_PROMPT_PATH", ""),
        output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "1000")),
        history_turns=int(os.getenv("HISTORY_TURNS", "12")),
        history_max_chars=int(os.getenv("HISTORY_MAX_CHARS", "12000")),
        usage_log_path=args.usage_log,
        special_dm_user_id=SPECIAL_EVAL_USER_ID,
        lore_path=os.getenv("LORE_PATH", ""),
        lore_max_items=int(os.getenv("LORE_MAX_ITEMS", "6")),
        lore_max_chars=int(os.getenv("LORE_MAX_CHARS", "3200")),
        community_lore=community == "true",
        gemini_thinking_level=gemini_thinking_level,
        chat_web_search=chat_web_search,
    )
    return replace(base, output_tokens=max(128, min(base.output_tokens, 4096)))


def scope_for(mode: str) -> Scope:
    if mode == "server":
        return Scope(EVAL_GUILD_ID, EVAL_CHANNEL_ID, EVAL_USER_ID)
    user_id = SPECIAL_EVAL_USER_ID if mode == "special_dm" else EVAL_USER_ID
    return Scope(None, EVAL_CHANNEL_ID, user_id)


def case_turns(case: dict) -> list[str]:
    if "turns" in case:
        return [turn if isinstance(turn, str) else turn["input"] for turn in case["turns"]]
    return [case["input"]]


def case_speakers(case: dict) -> list[dict]:
    default = {"user_id": scope_for(case.get("mode", "dm")).user_id,
               "speaker": case.get("speaker", "테스트 사용자")}
    return [dict(default) if isinstance(turn, str) else {
        "user_id": turn["user_id"], "speaker": turn["speaker"],
    } for turn in case.get("turns", [case.get("input", "")])]


async def run_case(llm: LLM, case: dict) -> dict:
    mode = case.get("mode", "dm")
    scope = scope_for(mode)
    store = Store(":memory:", history_turns=llm.settings.history_turns)
    responses = []
    error = ""
    channel_context = list(case.get("channel_context", []))
    speakers = case_speakers(case)
    try:
        for index, turn in enumerate(case_turns(case), 1):
            speaker = speakers[index - 1]
            scope = replace(scope, user_id=speaker["user_id"])
            reply = await llm.answer(
                store, scope, speaker["speaker"], turn,
                public_context=[], channel_context=list(channel_context),
                emoji_catalog=[], use_memory=True,
            )
            responses.append(reply)
            store.add(scope, index, turn, reply)
            if mode == "server":
                # Keep actual generated replies visible when the next turn changes speaker.
                channel_context.extend([
                    {"message_id": str(index), "user_id": str(scope.user_id),
                     "author_user_id": str(scope.user_id), "name": speaker["speaker"],
                     "role": "user", "content": turn, "direct_trigger": True},
                    {"message_id": f"eval-reply-{index}", "user_id": "",
                     "author_user_id": None, "reply_target_user_id": str(scope.user_id),
                     "name": "히나", "role": "assistant", "content": reply},
                ])
                channel_context = channel_context[-12:]
    except Exception as exc:  # noqa: BLE001 - keep the remaining eval batch running
        error = f"{type(exc).__name__}: {exc}"
    finally:
        store.close()
    validation_errors = (
        response_validation_errors(responses[-1], case.get("validators", []))
        if responses and not error
        else []
    )
    return {
        "id": case["id"],
        "mode": mode,
        "input": case.get("input", ""),
        "turns": case_turns(case),
        "channel_context": case.get("channel_context", []),
        "speakers": speakers,
        "expected": case["expected"],
        "responses": responses,
        "error": error,
        "validators": case.get("validators", []),
        "validation_errors": validation_errors,
        "provider": llm.settings.provider,
        "model": llm.settings.model,
        "thinking_level": (
            llm.settings.gemini_thinking_level
            if llm.settings.provider == "gemini"
            else ""
        ),
    }


def write_results(results: list[dict], output: Path) -> tuple[Path, Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in results
    ), encoding="utf-8")
    report = output.with_suffix(".md")
    lines = [
        "# Hina character eval results",
        "",
        f"- cases: {len(results)}",
        f"- provider: `{results[0]['provider'] if results else ''}`",
        f"- model: `{results[0]['model'] if results else ''}`",
        f"- thinking level: `{results[0]['thinking_level'] if results else ''}`",
        f"- errors: {sum(bool(row['error']) for row in results)}",
        f"- validator failures: {sum(bool(row['validation_errors']) for row in results)}",
        "",
        "각 케이스의 `expected`와 실제 응답을 비교해 PASS/FAIL을 수동으로 판정하세요.",
        "실패한 사례는 `evals/character_lore_cases.jsonl`에 회귀 테스트로 남기는 것을 권장합니다.",
        "",
    ]
    for row in results:
        attempt = f", attempt {row['attempt']}" if "attempt" in row else ""
        lines += [f"## {row['id']} ({row['mode']}{attempt})", "",
                  f"**Expected:** {row['expected']}", ""]
        if row["channel_context"]:
            context_text = json.dumps(row["channel_context"], ensure_ascii=False, indent=2)
            lines += ["**Channel context**", "", "```json", context_text, "```", ""]
        if row["error"]:
            lines += [f"**ERROR:** `{row['error']}`", ""]
        if row["validation_errors"]:
            lines += ["**Validator failures**", ""]
            lines += [f"- {message}" for message in row["validation_errors"]]
            lines.append("")
        for index, (turn, response) in enumerate(zip(row["turns"], row["responses"]), 1):
            speaker = row["speakers"][index - 1]
            lines += [f"**Turn {index} input ({speaker['speaker']}, {speaker['user_id']})**",
                      "", f"> {turn}", "", "**Response**", "", response, ""]
        lines += ["---", ""]
    report.write_text("\n".join(lines), encoding="utf-8")
    return output, report


async def run(args) -> None:
    cases = read_cases(Path(args.cases))
    if args.id:
        wanted = set(args.id)
        cases = [case for case in cases if case["id"] in wanted]
        missing = wanted - {case["id"] for case in cases}
        if missing:
            raise ValueError(f"없는 eval id: {', '.join(sorted(missing))}")
    if args.limit is not None:
        cases = cases[:args.limit]
    if not cases:
        raise ValueError("실행할 eval case가 없습니다.")

    settings = eval_settings(args)
    llm = LLM(settings)
    try:
        results = []
        total = len(cases) * args.repeat
        completed = 0
        for case in cases:
            for attempt in range(1, args.repeat + 1):
                completed += 1
                print(
                    f"[{completed}/{total}] {case['id']} (attempt {attempt}/{args.repeat})",
                    flush=True,
                )
                result = await run_case(llm, case)
                result["attempt"] = attempt
                results.append(result)
    finally:
        await llm.close()

    if args.output:
        output = Path(args.output)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = DEFAULT_RESULTS_DIR / f"character-{stamp}.jsonl"
    jsonl_path, report_path = write_results(results, output)
    print(f"results: {jsonl_path}")
    print(f"report:  {report_path}")
    print(f"errors:  {sum(bool(row['error']) for row in results)}/{len(results)}")
    print(
        "validator failures:  "
        f"{sum(bool(row['validation_errors']) for row in results)}/{len(results)}"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="실제 모델로 히나 캐릭터·설정 회귀 테스트를 실행합니다.")
    root.add_argument("--cases", default=str(DEFAULT_CASES))
    root.add_argument("--id", action="append", help="특정 case id만 실행합니다. 반복 지정할 수 있습니다.")
    root.add_argument("--limit", type=int, help="앞에서부터 N개 case만 실행합니다.")
    root.add_argument("--repeat", type=int, default=1, help="각 case 반복 횟수. 기본은 1입니다.")
    root.add_argument("--provider", help="openai, gemini, openrouter. 기본은 LLM_PROVIDER입니다.")
    root.add_argument("--model", help="평가에 사용할 모델. 기본은 LLM_MODEL입니다.")
    root.add_argument(
        "--gemini-thinking-level",
        choices=sorted(GEMINI_THINKING_LEVELS),
        help="Gemini fixed eval의 thinking level. 기본은 GEMINI_THINKING_LEVEL 또는 low입니다.",
    )
    root.add_argument("--output", help="결과 JSONL 경로. 같은 이름의 .md 리포트도 생성합니다.")
    root.add_argument("--usage-log", default="data/logs/eval-usage.jsonl")
    return root


def main() -> None:
    args = parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit은 양수여야 합니다.")
    if args.repeat <= 0:
        raise SystemExit("--repeat은 양수여야 합니다.")
    try:
        asyncio.run(run(args))
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
