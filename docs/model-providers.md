# 모델 provider 설정

봇의 일반 답변과 장기 기억 요약 모델을 `openai`, `gemini`, `openrouter` 중에서 선택할 수 있습니다.
Discord, SQLite 기억, lore 검색, 캐릭터 프롬프트 조립은 provider와 독립적으로 유지하고 실제 모델
호출과 provider별 웹 검색 형식만 어댑터에서 변환합니다.

## 기본 설정

`.env.local`에서 `LLM_PROVIDER`, `LLM_MODEL`과 선택한 provider의 키를 설정합니다.
`MEMORY_PROVIDER`, `MEMORY_MODEL`을 비워 두면 일반 답변과 같은 provider/model을 사용합니다.

### OpenAI

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...

MEMORY_PROVIDER=
MEMORY_MODEL=
```

기존 설치의 `OPENAI_MODEL`은 호환 alias로 계속 읽지만 새 설정에서는 `LLM_MODEL`을 권장합니다.

### Gemini

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
GEMINI_API_KEY=...

GEMINI_THINKING_LEVEL=low
GEMINI_TOTAL_OUTPUT_TOKENS=4096

MEMORY_PROVIDER=
MEMORY_MODEL=
```

Gemini는 Interactions API를 직접 사용합니다. `CHAT_WEB_SEARCH=true`일 때 검색이 필요한 답변은
기존 내부 `web_search` 요청을 Google Search 도구로 변환합니다.

Gemini 3.x의 `max_output_tokens`에는 사용자에게 보이는 답변뿐 아니라 내부 thought token도 포함됩니다.
따라서 공통 설정인 `MAX_OUTPUT_TOKENS=1000`을 그대로 Gemini의 총 생성 한도로 사용하면, 요청에 따라
모델이 생각에 토큰을 많이 쓰는 순간 `status=incomplete`와 빈 출력이 간헐적으로 발생할 수 있습니다.
어댑터는 이를 피하기 위해 Gemini에 별도의 총 생성 예산을 적용합니다.

- `GEMINI_THINKING_LEVEL`: `minimal`, `low`, `medium`, `high`. 짧은 Discord RP에는 `low`가 기본입니다.
- `GEMINI_TOTAL_OUTPUT_TOKENS`: thought token을 포함한 Gemini의 총 생성 상한입니다. 기본값은
  `max(4096, MAX_OUTPUT_TOKENS)`입니다.
- `MAX_OUTPUT_TOKENS`: 앱의 일반 출력 크기 기준으로 계속 사용하며, Gemini 요청에서는 위 총 생성
  예산보다 작을 경우 총 예산을 줄이지 않습니다.

문제가 다시 발생하면 `data/logs/usage.jsonl`의 마지막 `answer` 행에서 `status`,
`reasoning_tokens`, `response_error_codes`를 확인하세요. `status`가 `incomplete`이고
`reasoning_tokens`가 총 생성 예산에 가까우면 `GEMINI_TOTAL_OUTPUT_TOKENS`를 늘리거나
`GEMINI_THINKING_LEVEL=minimal`로 낮추는 것이 좋습니다.

### OpenRouter

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=anthropic/claude-sonnet-4.6
OPENROUTER_API_KEY=...

MEMORY_PROVIDER=
MEMORY_MODEL=
```

OpenRouter에서는 OpenAI-compatible Responses API를 사용하고, 검색이 필요한 경우 `web` plugin으로
변환합니다. OpenRouter가 제공하는 다른 모델 slug도 같은 방식으로 지정할 수 있습니다.

## 답변 모델과 기억 모델 분리

비용이나 성능 비교를 위해 장기 기억 요약만 다른 provider/model로 보낼 수 있습니다. 이 경우
두 provider의 API key가 모두 필요하며 `MEMORY_MODEL`도 명시해야 합니다.

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
GEMINI_API_KEY=...

MEMORY_PROVIDER=openai
MEMORY_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```

## 같은 eval을 여러 provider에서 비교

`hina-eval`은 `--provider`와 `--model`을 지원합니다. 선택한 provider의 환경 변수 키가 필요합니다.

```bash
uv run hina-eval --provider openai --model gpt-4.1-mini --limit 5
uv run hina-eval --provider gemini --model gemini-3.5-flash --limit 5
uv run hina-eval --provider openrouter --model anthropic/claude-sonnet-4.6 --limit 5
```

같은 `evals/character_lore_cases.jsonl`을 사용하므로 캐릭터 유지, 설정 정확도, 메타 발언 같은 차이를
동일한 입력으로 비교할 수 있습니다. 결과 JSONL/Markdown에는 provider와 model이 함께 기록됩니다.

## 현재 범위

provider 선택은 실제 Discord 답변, 장기 기억 요약, runtime knowledge ingest가 사용하는 LLM 호출,
`hina-eval`에 적용됩니다. 기존 `hina-lore extract`/`verify-web` 파이프라인과
`scripts/run_prompt_injection_eval.py`는 아직 OpenAI 전용 보조 도구이므로 이번 provider 전환 범위에
포함하지 않았습니다.

웹 검색은 각 provider의 기능과 과금 정책을 따릅니다. `CHAT_WEB_SEARCH=false`로 공통 검색 사용을
끌 수 있습니다. provider마다 모델이 system instruction, 긴 문맥, 검색 결과를 따르는 방식이 다르므로
모델을 바꾼 뒤에는 최소한 주요 `hina-eval` 케이스를 다시 실행하는 것을 권장합니다.
