# 모델 provider 설정

봇의 일반 답변과 장기 기억 요약 모델을 `openai`, `gemini`, `openrouter` 중에서 선택할 수 있습니다.
Discord, SQLite 기억, lore 검색, 캐릭터 프롬프트 조립은 provider와 독립적으로 유지하고 실제 모델
호출과 provider별 웹 검색·이미지 입력 형식만 어댑터에서 변환합니다.

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

## 질문 복잡도에 따른 모델 라우팅

`MODEL_ROUTING_MODE=adaptive`로 설정하면 별도의 분류용 모델 호출 없이 기존 정보 routing 결과와
질문의 명시적 특성을 조합해 답변 모델을 선택합니다. 분석·설계·코드·증명·긴 답변 요청처럼 의미적으로
강한 신호는 바로 smart 쪽으로 기울고, 입력·답장 원문·주변 문맥의 길이, 이미지·reference·요구사항
개수처럼 연속적인 신호는 크기에 따라 점진적으로 가중됩니다. 웹 검색 1회나 이미지 1장 같은 약한 신호
하나만으로는 보통 fast를 유지하고, 여러 약한 신호나 충분히 큰 문맥이 겹치면 smart로 승격합니다.
대상 사용자의 basic history는 작은 보조 신호이고, deep history는 강한 신호지만 실제 조회량과 다른
요인까지 함께 반영해 최종 tier를 정합니다. 직접 답장한 원문은 일반 주변 문맥보다 강하게 보되,
인용문 속 `분석` 같은 단어를 사용자의 명령으로 오인하지 않도록 별도로 취급합니다.

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
MODEL_ROUTING_MODE=adaptive
LLM_FAST_MODEL=gemini-3.5-flash-lite
LLM_SMART_MODEL=gemini-3.8-flash

FAST_MAX_OUTPUT_TOKENS=4096
SMART_MAX_OUTPUT_TOKENS=8192
GEMINI_FAST_THINKING_LEVEL=minimal
GEMINI_SMART_THINKING_LEVEL=medium
```

`FAST_MAX_OUTPUT_TOKENS`와 `SMART_MAX_OUTPUT_TOKENS`는 provider 공통 전체 생성 예산입니다. reasoning
또는 thought token을 사용하는 모델에서는 숨은 추론 토큰도 이 예산에 포함될 수 있습니다. 따라서
이 값은 "사용자에게 보이는 답변 길이"를 직접 뜻하지 않습니다. Discord에 실제로 내보내는 텍스트는
별도의 출력 정책과 메시지 길이 제한을 따릅니다.

`fixed`가 호환 기본값이며 `LLM_MODEL`, `MAX_OUTPUT_TOKENS`와 provider별 reasoning 설정을 사용합니다.
adaptive에서도 비어 있는 fast/smart 모델명은 `LLM_MODEL`로 대체되므로, 모델은 같게 두고 예산만
분리하는 운영도 가능합니다. 기억 요약은 이 라우터를 거치지 않고 기존 `MEMORY_MODEL` 하나를
사용합니다.

선택 결과는 `usage.jsonl`의 `model_tier`, `model_route_score`, `model_route_reasons`,
`requested_max_output_tokens`에 남습니다. Gemini에서는 선택된 thinking level도 함께 기록합니다.
사유에는 사용자 메시지 원문이 기록되지 않습니다. 모델 이름과 thinking level의 실제 지원 범위는
provider별로 다르므로 운영 모델 조합을 바꿀 때 smoke test가 필요합니다.

### Gemini

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
GEMINI_API_KEY=...

# fixed 모드의 전체 생성 예산
MAX_OUTPUT_TOKENS=4096
GEMINI_THINKING_LEVEL=low

MEMORY_PROVIDER=
MEMORY_MODEL=
```

Gemini는 Interactions API를 직접 사용합니다. `CHAT_WEB_SEARCH=true`일 때 검색이 필요한 답변은
기존 내부 `web_search` 요청을 Google Search 도구로 변환합니다.

Gemini 3.x의 `max_output_tokens`에는 사용자에게 보이는 답변뿐 아니라 내부 thought token도 포함됩니다.
이제 Gemini 전용 `GEMINI_TOTAL_OUTPUT_TOKENS`, `GEMINI_FAST_TOTAL_OUTPUT_TOKENS`,
`GEMINI_SMART_TOTAL_OUTPUT_TOKENS`는 사용하지 않습니다. fixed 모드에서는 `MAX_OUTPUT_TOKENS`,
adaptive 모드에서는 `FAST_MAX_OUTPUT_TOKENS`와 `SMART_MAX_OUTPUT_TOKENS`를 그대로 Gemini의
`max_output_tokens`로 전달합니다.

- `GEMINI_THINKING_LEVEL`: fixed 모드의 추론 강도입니다. `minimal`, `low`, `medium`, `high`를
  사용할 수 있습니다.
- `GEMINI_FAST_THINKING_LEVEL`, `GEMINI_SMART_THINKING_LEVEL`: adaptive 모드의 fast/smart 추론
  강도입니다.
- `MAX_OUTPUT_TOKENS`, `FAST_MAX_OUTPUT_TOKENS`, `SMART_MAX_OUTPUT_TOKENS`: provider 공통 전체 생성
  예산입니다. Gemini에서는 thought token도 이 한도에서 소비됩니다.

Gemini에서 생성 예산이 너무 작으면 thought token이 예산을 대부분 소진해 `status=incomplete`와 빈
출력이 발생할 수 있습니다. 그래서 Gemini adaptive 운영 예시는 fast 4096, smart 8192를 사용합니다.
문제가 다시 발생하면 `data/logs/usage.jsonl`의 마지막 `answer` 행에서 `status`,
`reasoning_tokens`, `response_error_codes`, `requested_max_output_tokens`를 확인하고, 필요하면 공통 생성
예산을 늘리거나 Gemini thinking level을 낮추는 것이 좋습니다.

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

## 이미지 입력과 모델 capability

`feature/vision-input`의 1차 구현은 세 provider 모두에 현재 턴 이미지 입력을 전달할 수 있는 어댑터를
둡니다.

- OpenAI: Responses `input_image` data URL
- Gemini: Interactions API의 inline `image` block
- OpenRouter: OpenAI-compatible Responses `input_image`

다만 **provider adapter가 이미지 입력 형식을 지원한다고 해서 지정한 모든 모델이 vision을 지원하는 것은
아닙니다.** `LLM_MODEL`에는 이미지 입력을 실제로 받을 수 있는 모델을 사용해야 하며, 모델별 지원 범위와
비용은 provider 정책을 따릅니다.

비전 입력은 일반 `answer` 요청에만 붙습니다. `MEMORY_MODEL`이 수행하는 `summarize`와
`summarize_shared`에는 현재 이미지 bytes를 전달하지 않으므로 기억 모델은 vision 지원이 필수가 아닙니다.
현재 지원 범위, 파일 제한, 저장 경계는 [`vision-input.md`](vision-input.md)를 참고하세요.

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

현재 `hina-eval`의 기존 character/lore case는 텍스트 중심입니다. 비전 기능 검증은 우선 실제 Discord
smoke test와 결정적 adapter/collector 테스트로 진행합니다. 비전 회귀 사례를 자동 eval에 추가하는 것은
1차 구현 검증 이후에 별도 범위로 다룹니다.

## 현재 범위

provider 선택은 실제 Discord 답변, 장기 기억 요약, runtime knowledge ingest가 사용하는 LLM 호출,
`hina-eval`에 적용됩니다. 기존 `hina-lore extract`/`verify-web` 파이프라인과
`scripts/run_prompt_injection_eval.py`는 아직 OpenAI 전용 보조 도구이므로 이번 provider 전환 범위에
포함하지 않았습니다.

웹 검색은 각 provider의 기능과 과금 정책을 따릅니다. `CHAT_WEB_SEARCH=false`로 공통 검색 사용을
끌 수 있습니다. provider마다 모델이 system instruction, 긴 문맥, 검색 결과, 이미지 입력을 따르는
방식이 다르므로 모델을 바꾼 뒤에는 주요 `hina-eval`과 함께 실제 Discord smoke test를 다시 실행하는
것을 권장합니다.
