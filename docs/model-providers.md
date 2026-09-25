# 모델 provider 설정

봇의 일반 답변과 장기 기억 요약은 같은 `LLM_PROVIDER`와 모델 pool을 사용합니다.
Discord, SQLite 기억, lore 검색, 캐릭터 프롬프트 조립은 provider와 독립적으로 유지하고 실제 모델
호출과 provider별 웹 검색·이미지 입력 형식만 adapter에서 변환합니다.

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

`MODEL_ROUTING_MODE=adaptive`에서는 일반 답변과 장기 기억 요약이 fast/smart 모델 pool을 공유하지만,
두 작업의 난이도는 서로 다른 방식으로 계산합니다.

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
MODEL_ROUTING_MODE=adaptive
MODEL_ROUTING_SMART_THRESHOLD=2.0
MEMORY_ROUTING_SMART_THRESHOLD=2.0
LLM_FAST_MODEL=gemini-3.5-flash-lite
LLM_SMART_MODEL=gemini-3.6-flash
FAST_MAX_OUTPUT_TOKENS=4096
SMART_MAX_OUTPUT_TOKENS=8192
MEMORY_MAX_OUTPUT_TOKENS=4096
GEMINI_FAST_THINKING_LEVEL=minimal
GEMINI_SMART_THINKING_LEVEL=medium

# 선택 사항: off | shadow | active
ROUTING_CLASSIFIER_MODE=off
```

### 채팅 `chat-v4`

채팅 baseline은 의미가 비슷한 작은 규칙을 여러 개 더하지 않고 다음 네 가지 **물리적 load**만 계산합니다.

- `request_load`: 현재 사용자 입력 길이. 기존 soft curve를 유지해 짧은 입력에는 작은 점수만 주고,
  약 4000자에서 최대 `2.0`에 도달합니다.
- `context_load`: 실제 외부 모델 요청에 허용되는 memory/history/channel/reply-chain 등 동적 대화 문맥의
  문자량. 1000자까지는 0, 8000자에서 최대 `2.0`입니다. `replied_message`, `target_user_history`,
  `reply_origin_request` 같은 provenance 종류별 점수는 두지 않습니다.
- `evidence_load`: 실제 lore/reference 본문량을 최대 `1.0`으로 계산하고, 최종 검색 모드가
  `required`이면 `0.5`를 더합니다. route 이름 자체에는 점수를 주지 않습니다.
- `visual_load`: 실제 모델에 전달되는 시각 입력 개수를 4개까지 선형으로 계산해 최대 `1.0`으로
  제한합니다. attachment/sticker/emoji 또는 recent/reply provenance별 별도 가중치는 없습니다.

이 네 값을 합한 objective score에 semantic score를 더해 `MODEL_ROUTING_SMART_THRESHOLD`와 비교합니다.
기본 threshold는 `2.0`, 허용 범위는 `0.1`~`10.0`입니다.

분석·설계·구현·디버깅·증명처럼 오탐 가능성이 낮은 명시적 복잡 작업과, 같은 사용자의 그런 요청에
대한 짧은 follow-up은 cheap local semantic hint `high=2.0`으로 처리합니다. 인용된 제3자 원문이
복잡하다는 이유만으로 별도 점수를 주지는 않습니다.

긴 답변 요청은 모델 지능과 생성 길이를 분리합니다. `단계별로 자세히 설명해 줘`처럼 추론 자체는
단순하지만 긴 출력이 필요한 요청은 fast tier를 유지할 수 있고, 이 경우 생성 예산만
`SMART_MAX_OUTPUT_TOKENS`까지 확장합니다. 별도 `LONG_MAX_OUTPUT_TOKENS` 설정은 두지 않습니다.

### semantic classifier를 결합한 `chat-hybrid-v4`

`ROUTING_CLASSIFIER_MODE`로 작은 classifier를 선택적으로 결합할 수 있습니다. classifier는 한 번의
요청에서 **추론 난이도**와 **웹 검색 필요성**을 독립적으로 반환합니다.

- `off`: classifier를 호출하지 않고 `chat-v4` + deterministic web 정책을 사용합니다.
- `shadow`: 실제 답변은 baseline을 유지하고 classifier가 제안한 model/web 결과만 백그라운드 telemetry에
  기록합니다. 비용은 발생하지만 답변 경로는 기다리지 않습니다.
- `active`: classifier 결과를 실제 model/web 선택에 사용합니다. 정책명은 `chat-hybrid-v4`입니다.

추론 난이도는 별도 axis/band 조합표 없이 직접 점수로 변환합니다.

- `low`: `0.0`
- `medium`: `1.0`
- `high`: `2.0`

최종 점수는 `request_load + context_load + evidence_load + visual_load + semantic_score`입니다.
물리적 load와 local high-confidence hint만으로 이미 smart threshold에 도달했다면 reasoning 판정은
생략할 수 있습니다. 다만 web decision이 잠기지 않았다면 같은 combined classifier 호출은 웹 판정을
위해 계속 실행할 수 있습니다.

웹 검색은 먼저 deterministic baseline과 `locked` 여부를 만듭니다. 검색 비활성화, 로컬 전용 route,
명시적 출처 요청, 확실한 live web route, 위치가 필요한데 기본 위치가 없는 경우, 충분한 로컬 근거가
있는 경우처럼 고신뢰 결정은 classifier가 바꾸지 못합니다. 일반 STATIC 질의의 `none`과 모호한 시간
의존 질의의 `auto`는 열어 두고 `web_need=none|auto|required`로 보정할 수 있습니다.

active에서는 classifier/web 결정을 먼저 끝낸 뒤 최종 `ModelPlan`을 한 번 만듭니다. 이전처럼 web mode가
바뀐 뒤 baseline plan과 hybrid plan을 다시 만드는 단계는 없습니다.

reasoning의 `uncertain` 또는 classifier 실패는 semantic score만 local/deterministic baseline으로
fallback합니다. `web_uncertain`은 검색 모드만 deterministic baseline으로 유지합니다. classifier 오류가
사용자 답변 자체를 실패시키지는 않습니다.

```dotenv
ROUTING_CLASSIFIER_MODE=shadow
# 비우면 각각 LLM_PROVIDER와 LLM_FAST_MODEL을 상속합니다.
ROUTING_CLASSIFIER_PROVIDER=openai
ROUTING_CLASSIFIER_MODEL=gpt-4.1-mini
# 비우면 위 provider의 기존 API key를 사용합니다.
ROUTING_CLASSIFIER_API_KEY=
ROUTING_CLASSIFIER_TIMEOUT_SECONDS=4
ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS=256
```

classifier client는 일반 답변 client와 분리되며 vision, 웹 도구, retry를 사용하지 않습니다. 별도 API
key는 quota·비용·폐기 범위를 분리할 때만 필요합니다. 별도 provider를 지정하면 현재 사용자 요청뿐
아니라 아래에서 허용된 routing context도 그 provider에 전달되므로 운영자가 명시적으로 선택해야 합니다.

classifier에는 현재 사용자 요청과 기존 `prior_user_request`/reply anchor 외에 최대 **4000자, 8개 항목**의
`routing_context`를 전달합니다. 최근 채널 전체를 넘기지는 않고, 이미 context subsystem이 선택한
`reply_origin_source` → `reply_origin_request` → `replied_message` 인과 체인과 `prior_reply_source`,
그리고 현재 사용자와 히나 사이의 `speaker_thread`만 사용합니다. reply 인과 문맥이 있으면 최대 2800자를
우선 확보하고 남는 예산을 speaker thread에 사용합니다. 각 항목에는 `kind`, `role`, `ownership`을 함께
보내 제3자 인용문을 현재 사용자의 지시로 오인하지 않도록 합니다.

이 context도 `EXTERNAL_CONTEXT_POLICY`를 그대로 따릅니다. `bot_interactions_only`에서는 임의의 제3자
메시지 원문이 classifier에 전달되지 않습니다. `full`에서는 사용자가 명시적으로 reply한 제3자 원문이
`ownership=external`인 인용 문맥으로 포함될 수 있으며, classifier provider를 별도로 설정했다면 해당
provider에도 전달됩니다. physical load score, ambient 채널 대화 전체, target-user history, memory/note/lore,
캐릭터 프롬프트, 이미지와 식별자는 classifier에 보내지 않습니다. 현재 schema는 `level`, `codes`,
`uncertain`, `web_need`, `web_codes`, `web_uncertain` 여섯 필드를 모두 요구하며 예전 reasoning-only
3-field 응답은 더 이상 허용하지 않습니다.

### 장기 기억 `memory-v2`

장기 기억 routing은 semantic classifier나 한국어 의미 regex를 사용하지 않고 **압축 부하**만 봅니다.

- `capacity_load`: 기존 summary가 목표 길이의 45%를 넘으면 증가하기 시작해 목표 길이에서 `2.0`에
  도달합니다. 개인 기억 목표는 1800자, shared memory 목표는 1200자입니다.
- `pending_load`: 실제 summarizer에 전달할 pending list를 compact JSON으로 직렬화한 문자 수를 기준으로
  800자까지는 0, 5000자에서 `2.0`에 도달합니다.

최종 memory score는 두 값의 단순 합입니다. 따라서 기존 summary 하나가 거의 가득 찼거나 pending
payload 하나가 매우 큰 경우에도 smart가 될 수 있고, 두 부하가 중간 수준으로 겹쳐도 threshold를
넘을 수 있습니다. 별도 `memory_update_signal`, `compaction_pressure`, `extra_pending_turns`,
`shared_scope_discount`는 사용하지 않습니다.

DM 개인 기억은 실제 payload에 사용자 발화와 히나 답변이 모두 들어가므로 둘 다 pending load에
반영됩니다. 서버 개인 기억과 shared memory는 히나 답변을 payload에 넣지 않으므로 그 길이가 routing
score에도 들어가지 않습니다. shared 차이는 더 작은 target size와 실제 payload 구조 자체로 표현합니다.

`MEMORY_ROUTING_SMART_THRESHOLD` 기본값은 `2.0`, 허용 범위는 `0.1`~`10.0`입니다. 채팅 score와 기억
score의 의미가 다르므로 threshold는 계속 분리합니다.

```text
/config set MODEL_ROUTING_SMART_THRESHOLD 1.8
/config set MEMORY_ROUTING_SMART_THRESHOLD 1.9
/config reset MEMORY_ROUTING_SMART_THRESHOLD
```

`MODEL_ROUTING_MODE=fixed`가 호환 기본값입니다. 이 경우 답변과 기억 모두 `LLM_MODEL`을 사용하고,
기억 요약에는 `MEMORY_MAX_OUTPUT_TOKENS`가 적용됩니다.

## 생성 예산과 telemetry

`FAST_MAX_OUTPUT_TOKENS`와 `SMART_MAX_OUTPUT_TOKENS`는 adaptive 일반 답변의 provider 공통 전체 생성
예산입니다. `MEMORY_MAX_OUTPUT_TOKENS`는 fast/smart 기억 요약 모두에 공통으로 적용됩니다. reasoning
또는 thought token을 사용하는 provider에서는 숨은 추론 토큰도 이 예산에 포함될 수 있으므로 이 값이
사용자에게 보이는 텍스트 길이를 직접 뜻하지는 않습니다.

선택 결과는 `usage.jsonl`의 `model_tier`, `model_route_score`, `model_route_threshold`,
`model_route_margin`, `model_route_policy`, `model_route_components`, `model_route_reasons`,
`requested_max_output_tokens`에 남습니다. 채팅 component는 `request_load`, `context_load`,
`evidence_load`, `visual_load`, `semantic_score`이고 기억은 `capacity_load`, `pending_load`만 사용합니다.
정책 버전은 채팅 `chat-v4`, hybrid 채팅 `chat-hybrid-v4`, 기억 `memory-v2`입니다.

hybrid에서는 `semantic_route_status`, `semantic_route_level`, `semantic_route_codes`,
`model_route_decision_source`, `model_route_baseline_tier`와 검색 결정 metadata도 기록합니다. 이전
`model_route_objective_axes` / `model_route_objective_bands`는 더 이상 routing 결과에 생성하지 않습니다.
분류기 호출은 `operation=model_route_classify`, shadow 비교 결과는 `operation=model_route_shadow`로
남으며 요청 원문이나 자유 형식 설명은 기록하지 않습니다.

## 장기 기억 요약 크기

- 개인 `conversation_memory`: 1800자 이내를 목표로 생성하고 저장 시 2000자에서 잘라냅니다.
- 공개 `shared_summary`: 사용자·채널 scope별 공개 기억이며 1200자 이내를 목표로 하고 저장 상한은
  1500자입니다.
- 두 요약 모두 `MEMORY_MAX_OUTPUT_TOKENS`를 사용합니다. 기본값은 `4096`입니다.

문자 수 제한은 저장할 정보량을 제어하는 정책이고 token 제한은 provider 생성 상한입니다.

### Gemini

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash
GEMINI_API_KEY=...
MODEL_ROUTING_MODE=adaptive
LLM_FAST_MODEL=gemini-3.5-flash-lite
LLM_SMART_MODEL=gemini-3.6-flash
GEMINI_FAST_THINKING_LEVEL=minimal
GEMINI_SMART_THINKING_LEVEL=medium
MEMORY_MAX_OUTPUT_TOKENS=4096
```

Gemini는 Interactions API를 직접 사용합니다. `CHAT_WEB_SEARCH=true`일 때 검색이 필요한 일반 답변은
내부 `web_search` 요청을 Google Search 도구로 변환합니다. 기억 요약에는 웹 검색 도구를 붙이지
않습니다. Gemini 3.x의 `max_output_tokens`에는 사용자에게 보이는 답변뿐 아니라 내부 thought token도
포함됩니다.

- `GEMINI_THINKING_LEVEL`: fixed 모드 답변과 기억 요약의 추론 강도
- `GEMINI_FAST_THINKING_LEVEL`, `GEMINI_SMART_THINKING_LEVEL`: adaptive fast/smart 추론 강도. 기본값은 각각 `minimal`, `medium`입니다.

Gemini에서 생성 예산이 너무 작으면 thought token이 예산을 대부분 소진해 빈 출력이 발생할 수 있습니다.
문제가 발생하면 `usage.jsonl`의 `status`, `reasoning_tokens`, `response_error_codes`,
`requested_max_output_tokens`를 확인합니다.

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

세 provider 모두 일반 답변에 이미지 입력을 전달할 수 있습니다.

- OpenAI: Responses `input_image` data URL
- Gemini: Interactions API inline `image` block
- OpenRouter: OpenAI-compatible Responses `input_image`

adaptive에서는 `LLM_FAST_MODEL`과 `LLM_SMART_MODEL` 모두 실제 채팅 이미지 입력을 받을 수 있으므로 두
모델의 vision 지원 여부를 확인해야 합니다. 기억 요약에는 이미지 bytes를 전달하지 않습니다. 자세한
범위와 저장 경계는 [`vision-input.md`](vision-input.md)를 참고하세요.

## 같은 eval을 여러 provider에서 비교

```bash
uv run hina-eval --provider openai --model gpt-4.1-mini --limit 5
uv run hina-eval --provider gemini --model gemini-3.5-flash --limit 5
uv run hina-eval --provider openrouter --model anthropic/claude-sonnet-4.6 --limit 5
```

`hina-routing-eval`은 semantic classifier의 reasoning level과 최종 model tier를 비교합니다. classifier가
함께 반환하는 web 판단은 unit test에서 별도로 검증합니다. 자세한 내용은 [`../evals/README.md`](../evals/README.md)를
참고하세요.

## 현재 범위

provider 선택은 실제 Discord 답변, 장기 기억 요약, runtime knowledge ingest가 사용하는 LLM 호출,
`hina-eval`에 적용됩니다. 기존 `hina-lore extract`/`verify-web` 파이프라인과
`scripts/run_prompt_injection_eval.py`는 아직 OpenAI 전용 보조 도구입니다.

웹 검색은 각 provider의 기능과 과금 정책을 따릅니다. `CHAT_WEB_SEARCH=false`로 공통 검색 사용을 끌 수
있습니다. provider나 모델을 바꾼 뒤에는 주요 eval과 실제 Discord smoke test를 다시 실행하는 것을
권장합니다.
