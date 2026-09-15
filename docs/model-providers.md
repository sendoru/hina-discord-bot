# 모델 provider 설정

봇의 일반 답변과 장기 기억 요약은 같은 `LLM_PROVIDER`와 모델 pool을 사용합니다.
Discord, SQLite 기억, lore 검색, 캐릭터 프롬프트 조립은 provider와 독립적으로 유지하고 실제 모델
호출과 provider별 웹 검색·이미지 입력 형식만 어댑터에서 변환합니다.

## 기본 설정

`.env.local`에서 `LLM_PROVIDER`, `LLM_MODEL`과 선택한 provider의 키를 설정합니다.
장기 기억용 별도 provider/model 설정은 두지 않습니다. fixed 모드에서는 답변과 기억 모두
`LLM_MODEL`, adaptive 모드에서는 둘 다 `LLM_FAST_MODEL` / `LLM_SMART_MODEL` 후보군을 공유합니다.
기억 요약의 생성 예산만 `MEMORY_MAX_OUTPUT_TOKENS`로 별도 설정하며 기본값은 `4096`입니다.

### OpenAI

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...

MEMORY_MAX_OUTPUT_TOKENS=4096
```

기존 설치의 `OPENAI_MODEL`은 호환 alias로 계속 읽지만 새 설정에서는 `LLM_MODEL`을 권장합니다.
예전에 사용하던 `MEMORY_PROVIDER`, `MEMORY_MODEL`은 더 이상 읽지 않습니다.

## adaptive 모델 라우팅

`MODEL_ROUTING_MODE=adaptive`로 설정하면 별도의 분류용 모델 호출 없이 fast/smart tier를 선택합니다.
채팅과 장기 기억은 같은 모델 후보군을 공유하지만 **서로 다른 점수식**을 사용합니다.

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
MODEL_ROUTING_MODE=adaptive
MODEL_ROUTING_SMART_THRESHOLD=2.0
MEMORY_ROUTING_SMART_THRESHOLD=2.0
LLM_FAST_MODEL=gemini-3.5-flash-lite
LLM_SMART_MODEL=gemini-3.8-flash

FAST_MAX_OUTPUT_TOKENS=4096
SMART_MAX_OUTPUT_TOKENS=8192
MEMORY_MAX_OUTPUT_TOKENS=4096
GEMINI_FAST_THINKING_LEVEL=minimal
GEMINI_SMART_THINKING_LEVEL=medium
```

### 채팅 라우팅

채팅에서는 분석·설계·코드·증명·긴 답변 요청 같은 의미적 신호와 입력·답장 원문·주변 문맥 길이,
이미지·reference·요구사항 개수 등을 조합합니다. 한 개의 약한 신호만으로 smart를 선택하지 않고,
여러 신호가 겹치거나 강한 신호가 있을 때 승격합니다.

`MODEL_ROUTING_SMART_THRESHOLD`는 채팅 score가 smart tier로 넘어가는 기준이며 기본값은 `2.0`,
허용 범위는 `0.1`~`10.0`입니다.

### 장기 기억 라우팅

장기 기억은 질문 난이도가 아니라 **rolling summary를 안전하게 다시 압축하는 난이도**를 계산합니다.
현재 구현은 다음 신호를 사용합니다.

- `memory_capacity_pressure`: 기존 summary가 목표 길이에 가까울수록 증가합니다.
- `pending_input_volume`: 이번에 병합할 pending 대화 본문이 많을수록 증가합니다.
- `memory_update_signal`: "앞으로는", "이제부터", "정정", 설정/선호 변경·취소처럼 기존 기억을
  수정할 가능성이 높은 명시적 표현을 보조 신호로 사용합니다.
- `compaction_pressure`: 기존 기억이 이미 많이 찬 상태에서 새 입력도 큰 경우 추가 가중치를 줍니다.
- `extra_pending_turns`: 정상 `SUMMARY_EVERY`보다 pending이 더 누적된 경우 완만하게 증가합니다.
- `shared_scope_discount`: 공개 shared memory는 직접 호출 발화만 저장하고 정보 범위가 좁으므로 같은
  입력량에서도 개인 기억보다 smart 승격을 조금 보수적으로 합니다.

`MEMORY_ROUTING_SMART_THRESHOLD`가 기억 score의 smart 기준이며 기본값은 `2.0`, 허용 범위는
`0.1`~`10.0`입니다. 채팅 score와 기억 score의 의미가 다르므로 threshold를 분리했습니다.
두 threshold는 runtime 설정이라 재시작 없이 변경할 수 있습니다.

```text
/config set MODEL_ROUTING_SMART_THRESHOLD 1.8
/config set MEMORY_ROUTING_SMART_THRESHOLD 1.9
/config reset MEMORY_ROUTING_SMART_THRESHOLD
```

`MODEL_ROUTING_MODE=fixed`가 호환 기본값입니다. 이 경우 답변과 기억 모두 `LLM_MODEL`을 사용하고,
기억 요약에는 `MEMORY_MAX_OUTPUT_TOKENS`가 적용됩니다.

## 생성 예산

`FAST_MAX_OUTPUT_TOKENS`와 `SMART_MAX_OUTPUT_TOKENS`는 일반 답변의 provider 공통 전체 생성 예산입니다.
`MEMORY_MAX_OUTPUT_TOKENS`는 fast/smart 기억 요약 모두에 공통으로 적용되는 별도 생성 예산입니다.
현재 기억의 fast/smart 차이는 **모델과 reasoning/thinking tier**에 두고, memory output budget 자체는
분리하지 않습니다.

reasoning 또는 thought token을 사용하는 provider에서는 숨은 추론 토큰도 이 예산에 포함될 수
있습니다. 따라서 이 값들은 사용자에게 보이는 텍스트 길이를 직접 뜻하지 않습니다.

선택 결과는 `usage.jsonl`의 `model_tier`, `model_route_score`, `model_route_threshold`,
`model_route_reasons`, `requested_max_output_tokens`에 남습니다. Gemini에서는 선택된 thinking level도
함께 기록합니다. `operation=summarize`와 `operation=summarize_shared` 행에서도 같은 telemetry를
확인할 수 있습니다.

## 장기 기억 요약 크기

개인 대화 기억과 공개 shared memory는 같은 모델 호출 경로를 사용하지만 저장 목적과 크기는 분리합니다.

- 개인 `conversation_memory`: 기존 기억과 새 턴을 rolling summary로 합치며 **1800자 이내**를 목표로
  생성합니다. 모델이 지시보다 길게 출력하더라도 저장 시 **2000자**에서 잘라냅니다.
- 공개 `shared_summary`: 공개 서버 채널에서 해당 사용자가 히나를 직접 호출한 발화만 요약합니다.
  다른 대화에서 공개 참고 문맥으로 재사용될 수 있으므로 **1200자 이내** 목표와 **1500자** 저장 상한을
  유지합니다. 서버 전체 단일 요약이 아니라 사용자·채널 scope별 공개 기억입니다.
- 두 요약 모두 `MEMORY_MAX_OUTPUT_TOKENS`를 사용합니다. 기본값은 `4096`, 허용 범위는
  `128`~`65536`입니다.

문자 수 제한은 저장할 정보량을 제어하는 정책이고, token 제한은 추론 때문에 요청이 중간에 잘리지
않도록 하는 provider 생성 상한입니다.

### Gemini

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
GEMINI_API_KEY=...

MODEL_ROUTING_MODE=adaptive
LLM_FAST_MODEL=gemini-3.5-flash-lite
LLM_SMART_MODEL=gemini-3.8-flash
GEMINI_FAST_THINKING_LEVEL=minimal
GEMINI_SMART_THINKING_LEVEL=medium
MEMORY_MAX_OUTPUT_TOKENS=4096
```

Gemini는 Interactions API를 직접 사용합니다. `CHAT_WEB_SEARCH=true`일 때 검색이 필요한 일반 답변은
기존 내부 `web_search` 요청을 Google Search 도구로 변환합니다. 기억 요약에는 웹 검색 도구를 붙이지
않습니다.

Gemini 3.x의 `max_output_tokens`에는 사용자에게 보이는 답변뿐 아니라 내부 thought token도 포함됩니다.
fixed 답변에서는 `MAX_OUTPUT_TOKENS`, adaptive 답변에서는 `FAST_MAX_OUTPUT_TOKENS` /
`SMART_MAX_OUTPUT_TOKENS`, 기억 요약에서는 `MEMORY_MAX_OUTPUT_TOKENS`를 전달합니다.

- `GEMINI_THINKING_LEVEL`: fixed 모드에서 답변과 기억 요약이 사용하는 추론 강도입니다.
- `GEMINI_FAST_THINKING_LEVEL`, `GEMINI_SMART_THINKING_LEVEL`: adaptive 모드에서 채팅과 기억이
  공통으로 사용하는 fast/smart 추론 강도입니다.

Gemini에서 생성 예산이 너무 작으면 thought token이 예산을 대부분 소진해 `status=incomplete`와 빈
출력이 발생할 수 있습니다. 문제가 발생하면 `data/logs/usage.jsonl`의 `status`, `reasoning_tokens`,
`response_error_codes`, `requested_max_output_tokens`를 확인합니다.

### OpenRouter

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=anthropic/claude-sonnet-4.6
OPENROUTER_API_KEY=...
MEMORY_MAX_OUTPUT_TOKENS=4096
```

OpenRouter에서는 OpenAI-compatible Responses API를 사용하고, 일반 답변에서 검색이 필요한 경우
`web` plugin으로 변환합니다.

## 이미지 입력과 모델 capability

세 provider 모두 현재 턴 이미지 입력을 일반 답변에 전달할 수 있습니다.

- OpenAI: Responses `input_image` data URL
- Gemini: Interactions API의 inline `image` block
- OpenRouter: OpenAI-compatible Responses `input_image`

provider adapter가 이미지 형식을 지원해도 지정한 모델 자체가 vision을 지원해야 합니다.
adaptive를 사용할 경우 `LLM_FAST_MODEL`과 `LLM_SMART_MODEL` 모두 실제 채팅에서 이미지가 들어올 수
있으므로 운영 모델 조합의 vision 지원 여부를 확인해야 합니다.

비전 입력은 일반 `answer` 요청에만 붙습니다. `summarize`와 `summarize_shared`는 같은 모델 pool을
사용하지만 이미지 bytes를 전달하지 않으므로 기억 요약 자체는 vision 기능을 요구하지 않습니다.
현재 지원 범위와 저장 경계는 [`vision-input.md`](vision-input.md)를 참고하세요.

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
smoke test와 결정적 adapter/collector 테스트로 진행합니다.

## 현재 범위

provider 선택은 실제 Discord 답변, 장기 기억 요약, runtime knowledge ingest가 사용하는 LLM 호출,
`hina-eval`에 적용됩니다. 기존 `hina-lore extract`/`verify-web` 파이프라인과
`scripts/run_prompt_injection_eval.py`는 아직 OpenAI 전용 보조 도구입니다.

웹 검색은 각 provider의 기능과 과금 정책을 따릅니다. `CHAT_WEB_SEARCH=false`로 공통 검색 사용을
끌 수 있습니다. provider마다 system instruction, 긴 문맥, 검색 결과, 이미지 입력을 따르는 방식이
다르므로 모델을 바꾼 뒤에는 주요 eval과 실제 Discord smoke test를 다시 실행하는 것을 권장합니다.
