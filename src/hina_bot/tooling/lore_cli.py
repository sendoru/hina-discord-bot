import argparse
import os
from collections import Counter
from copy import deepcopy
from pathlib import Path

from hina_bot.core.lore import (
    CONFIDENCE_LEVELS,
    FACT_TYPES,
    REFERENCE_ONLY_FACT_TYPES,
    LoreValidationError,
    fact_type,
    read_jsonl,
    validate_record,
    write_jsonl,
)

from . import lore_pipeline
from .lore_web import verify_candidate

_PRIMARY_SOURCE_TYPES = {"official_game", "official_site", "official_video", "official_profile"}
_SECONDARY_SOURCE_TYPES = {"official_secondary", "game_data_mirror", "official_data_mirror"}
_RUNTIME_OMIT = {
    "source_id",
    "evidence",
    "uncertainty",
    "kr_release_evidence",
    "verification",
}


def _default_bulk_confidence(row: dict) -> str:
    evidence_type = fact_type(row)
    if row["lane"] == "community_meme" or evidence_type in {"inference", "unknown"}:
        return "crosschecked"
    source_type = str(row.get("source", {}).get("type", ""))
    if source_type in _PRIMARY_SOURCE_TYPES:
        return "verified"
    if source_type in _SECONDARY_SOURCE_TYPES:
        return "official_secondary"
    return "crosschecked"


def _matches_scope(row: dict, args) -> bool:
    source = row.get("source", {})
    if args.source_type is not None and source.get("type") != args.source_type:
        return False
    if args.id_prefix is not None and not row.get("id", "").startswith(args.id_prefix):
        return False
    if args.title is not None and source.get("title") != args.title:
        return False
    return args.fact_type is None or fact_type(row) in args.fact_type


def _has_web_conflict(row: dict) -> bool:
    verification = row.get("verification")
    return isinstance(verification, dict) and verification.get("status") == "conflict"


def _prepare_acceptance(
    row: dict, *, confidence: str, confirm_kr_release: bool
) -> tuple[dict, dict]:
    if row["status"] != "candidate":
        raise LoreValidationError(f"{row['id']}: candidate 상태만 일괄 승인할 수 있습니다.")
    if row["lane"] == "canon" and fact_type(row) in REFERENCE_ONLY_FACT_TYPES:
        raise LoreValidationError(f"{row['id']}: reference-only canon 항목은 승인할 수 없습니다.")
    if row["lane"] == "canon" and not confirm_kr_release:
        raise LoreValidationError(
            "canon 일괄 승인은 --confirm-kr-release로 한국 서버 출시를 확인해야 합니다."
        )

    accepted = deepcopy(row)
    accepted["status"] = "accepted"
    accepted["confidence"] = confidence
    if accepted["lane"] == "canon":
        accepted["kr_release"] = "confirmed"
    validate_record(accepted)

    runtime = {key: value for key, value in accepted.items() if key not in _RUNTIME_OMIT}
    validate_record(runtime, accepted=True)
    return accepted, runtime


def _print_bulk_plan(*, scoped: list[dict], prepared: list[tuple[dict, dict]],
                     suppressed: list[dict], conflicts: list[dict], duplicates: list[dict],
                     dry_run: bool) -> None:
    counts = Counter((fact_type(accepted), accepted["confidence"]) for accepted, _ in prepared)
    print(f"scope matched: {len(scoped)}")
    print(f"approve: {len(prepared)}")
    for (evidence_type, confidence), count in sorted(counts.items()):
        print(f"  {evidence_type}: {count} ({confidence})")
    print(f"excluded suppressed/non-candidate: {len(suppressed)}")
    print(f"excluded web conflicts: {len(conflicts)}")
    print(f"excluded runtime duplicates: {len(duplicates)}")
    if dry_run:
        print("실제 변경 없음 (--dry-run)")


def approve_all(args) -> None:
    if not any((args.source_type, args.id_prefix, args.title)):
        raise SystemExit("approve-all은 --source-type, --id-prefix, --title 중 하나 이상이 필요합니다.")

    queue = read_jsonl(lore_pipeline.QUEUE_PATH)
    runtime = read_jsonl(lore_pipeline.RUNTIME_PATH)
    scoped = [row for row in queue if _matches_scope(row, args)]
    candidates = [row for row in scoped if row.get("status") == "candidate"]
    suppressed = [row for row in scoped if row.get("status") != "candidate"]

    runtime_ids = {row["id"] for row in runtime}
    duplicates = [row for row in candidates if row["id"] in runtime_ids]
    duplicate_ids = {row["id"] for row in duplicates}
    conflicts = [row for row in candidates if _has_web_conflict(row)]
    conflict_ids = {row["id"] for row in conflicts}
    targets = [
        row for row in candidates
        if row["id"] not in duplicate_ids and row["id"] not in conflict_ids
    ]

    prepared = []
    for row in targets:
        confidence = args.confidence or _default_bulk_confidence(row)
        prepared.append(_prepare_acceptance(
            row,
            confidence=confidence,
            confirm_kr_release=args.confirm_kr_release,
        ))

    final_runtime = runtime + [clean for _, clean in prepared]
    ids = [row["id"] for row in final_runtime]
    if len(ids) != len(set(ids)):
        raise LoreValidationError("bulk approval 결과에 duplicate runtime ids가 있습니다.")
    for row in final_runtime:
        validate_record(row, accepted=True)

    _print_bulk_plan(
        scoped=scoped,
        prepared=prepared,
        suppressed=suppressed,
        conflicts=conflicts,
        duplicates=duplicates,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        return
    if not prepared:
        print("승인할 새 candidate가 없습니다.")
        return

    accepted_by_id = {accepted["id"]: accepted for accepted, _ in prepared}
    final_queue = [accepted_by_id.get(row["id"], row) for row in queue]
    for row in final_queue:
        validate_record(row)

    try:
        write_jsonl(lore_pipeline.RUNTIME_PATH, final_runtime)
        write_jsonl(lore_pipeline.QUEUE_PATH, final_queue)
    except Exception:
        write_jsonl(lore_pipeline.RUNTIME_PATH, runtime)
        write_jsonl(lore_pipeline.QUEUE_PATH, queue)
        raise
    print(f"accepted: {len(prepared)} candidate(s)")


def list_queue(_args) -> None:
    queue = read_jsonl(lore_pipeline.QUEUE_PATH)
    rows = [row for row in queue if row["status"] == "candidate"]
    suppressed = [row for row in queue if row["status"] == "suppressed"]
    accepted_conflicts = [
        row for row in queue
        if row.get("status") == "accepted" and _has_web_conflict(row)
    ]
    verification_counts = Counter()

    for row in rows:
        verification = row.get("verification")
        web_status = "not_checked"
        if isinstance(verification, dict):
            web_status = str(verification.get("status", "not_checked"))
        verification_counts[web_status] += 1

        print(
            f"{row['id']} [{row['lane']}/{row.get('fact_type', 'legacy')}/{row['knowledge']}]"
            f" [web:{web_status}]\n"
            f"  {row['summary']}\n"
            f"  evidence: {row.get('evidence', '')}\n"
            f"  uncertainty: {row.get('uncertainty', '')}"
        )
        if isinstance(verification, dict):
            print(f"  web note: {verification.get('note', '')}")
            print(
                "  web sources: "
                + ", ".join(source.get("url", "") for source in verification.get("sources", [])[:3])
            )
        if row["lane"] == "canon":
            print(f"  KR release evidence: {row.get('kr_release_evidence', '')}")
            if isinstance(verification, dict):
                print(
                    f"  web KR release: {verification.get('kr_release', 'not_found')} "
                    f"({verification.get('kr_release_note', '')})"
                )

    print(f"pending: {len(rows)} / suppressed reference-only: {len(suppressed)}")
    if rows:
        summary = ", ".join(
            f"{status}={count}" for status, count in sorted(verification_counts.items())
        )
        print(f"web verification: {summary}")
    if accepted_conflicts:
        print(f"WARNING: accepted runtime web conflicts: {len(accepted_conflicts)}")
        for row in accepted_conflicts[:20]:
            print(f"  {row['id']}: {row['verification'].get('note', '')}")


def _verify_targets(args, queue: list[dict]) -> tuple[list[dict], int]:
    if args.id is None and not any((args.source_type, args.id_prefix, args.title)):
        raise SystemExit(
            "verify-web은 id 또는 --source-type/--id-prefix/--title 중 하나가 필요합니다."
        )

    statuses = set(getattr(args, "status", None) or ["candidate"])
    rows = []
    for row in queue:
        if row.get("status") not in statuses or row.get("lane") != "canon":
            continue
        if args.id is not None:
            if row.get("id") != args.id:
                continue
        elif not _matches_scope(row, args):
            continue
        if not args.force and isinstance(row.get("verification"), dict):
            continue
        rows.append(row)

    total = len(rows)
    if args.limit > 0:
        rows = rows[:args.limit]
    return rows, total


def verify_web(args) -> None:
    from openai import OpenAI

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY가 필요합니다. 키 값은 명령행 인자로 전달하지 마세요.")
    if args.limit < 0:
        raise SystemExit("--limit은 0 이상의 정수여야 합니다.")

    queue = read_jsonl(lore_pipeline.QUEUE_PATH)
    targets, total = _verify_targets(args, queue)
    if not targets:
        raise SystemExit("웹 검증할 새 canon 항목이 없습니다.")

    print(f"web verify targets: {len(targets)} / matched unchecked: {total}")
    if args.dry_run:
        for row in targets:
            print(
                f"  {row['id']} [{row['status']}/{fact_type(row)}] {row['summary']}"
            )
        print("실제 웹 검색 없음 (--dry-run)")
        return

    client = OpenAI(timeout=90, max_retries=2)
    index_by_id = {row["id"]: index for index, row in enumerate(queue)}
    target_ids = {row["id"] for row in targets}
    for number, row in enumerate(targets, 1):
        verification = verify_candidate(client, row, model=args.model)
        queue[index_by_id[row["id"]]]["verification"] = verification
        if verification.get("kr_release") == "confirmed":
            sources = verification.get("sources", [])
            source_url = sources[0]["url"] if sources else ""
            note = verification.get("kr_release_note", "")
            queue[index_by_id[row["id"]]]["kr_release_evidence"] = (
                f"web: {note} {source_url}".strip()
            )[:300]
        validate_record(queue[index_by_id[row["id"]]])
        write_jsonl(lore_pipeline.QUEUE_PATH, queue)
        print(
            f"[{number}/{len(targets)}] {row['id']}: {verification['status']} "
            f"(sources={len(verification.get('sources', []))}, "
            f"search_calls={verification.get('search_calls', 0)})"
        )
        if row.get("status") == "accepted" and verification["status"] == "conflict":
            print(
                "  WARNING: 이미 runtime에 승인된 항목과 웹 검증 결과가 충돌합니다. "
                "자동으로 runtime을 수정하지 않습니다."
            )

    counts = Counter(
        row["verification"]["status"]
        for row in queue
        if row.get("id") in target_ids and isinstance(row.get("verification"), dict)
    )
    print("web verification complete: " + ", ".join(
        f"{status}={count}" for status, count in sorted(counts.items())
    ))


def parser() -> argparse.ArgumentParser:
    root = lore_pipeline.parser()
    subparsers = next(
        action for action in root._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    subparsers.choices["list"].set_defaults(run=list_queue)

    command = subparsers.add_parser("approve-all", help="검수 후보를 필터링해 일괄 승인합니다.")
    command.add_argument("--source-type")
    command.add_argument("--id-prefix")
    command.add_argument("--title")
    command.add_argument("--fact-type", action="append", choices=sorted(FACT_TYPES))
    command.add_argument("--confirm-kr-release", action="store_true")
    command.add_argument(
        "--confidence",
        choices=sorted(CONFIDENCE_LEVELS - {"candidate"}),
        help="지정하면 모든 승인 대상에 같은 confidence를 사용합니다.",
    )
    mode = command.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="승인 계획만 출력하고 파일은 바꾸지 않습니다.")
    mode.add_argument("--yes", action="store_true", help="사전 검사를 통과한 후보를 실제로 승인합니다.")
    command.set_defaults(run=approve_all)

    command = subparsers.add_parser("verify-web", help="canon 항목을 OpenAI 웹 검색으로 검증합니다.")
    command.add_argument("id", nargs="?")
    command.add_argument("--source-type")
    command.add_argument("--id-prefix")
    command.add_argument("--title")
    command.add_argument("--fact-type", action="append", choices=sorted(FACT_TYPES))
    command.add_argument(
        "--status",
        action="append",
        choices=["candidate", "accepted"],
        help="검증할 review 상태. 반복 지정 가능하며 기본은 candidate입니다.",
    )
    command.add_argument(
        "--limit",
        type=int,
        default=20,
        help="한 번에 실제 검색할 최대 항목 수. 0이면 제한 없음 (기본 20).",
    )
    command.add_argument("--force", action="store_true", help="이미 검증된 항목도 다시 검색합니다.")
    command.add_argument("--dry-run", action="store_true", help="대상만 출력하고 웹 검색은 하지 않습니다.")
    command.add_argument(
        "--model",
        default=os.getenv("LORE_VERIFY_MODEL", "gpt-5.4-mini"),
        help="웹 검증 모델 (기본 LORE_VERIFY_MODEL 또는 gpt-5.4-mini).",
    )
    command.set_defaults(run=verify_web)
    return root


def main() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        pass
    else:
        load_dotenv(Path.cwd() / ".env.local", override=False)
        load_dotenv(Path.cwd() / ".env", override=False)
    args = parser().parse_args()
    try:
        args.run(args)
    except (LoreValidationError, OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
