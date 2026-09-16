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

`MODEL_ROUTING_MODE=adaptive`로 설정하면 fast/smart tier를 선택합니다. 채팅은 기본적으로 기존
`chat-v3` 점수식을 사용하고 선택적으로 별도 의미 분류기를 결합할 수 있습니다. 장기 기억은
분류기를 호출하지 않고 독립적인 `memory-v1` 점수식을 계속 사용합니다.

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

# 선택 사항: off | shadow | active
ROUTING_CLASSIFIER_MODE=off
```

### 채팅 라우팅

채팅에서는 분석·설계·구현·증명처럼 **실제로 작업을 요청하는 표현**과 긴 답변 요청을 강한 의미적
신호로 사용합니다. `코드`, `알고리즘`, `비교` 같은 단어가 단순히 등장하거나 "분석하지 말고"처럼
명시적으로 부정된 표현만으로는 승격하지 않습니다. 그 밖에 입력·답장 원문·주변 문맥 길이,
이미지·reference·요구사항 개수 등을 조합합니다. 한 개의 약한 신호만으로 smart를 선택하지 않고,
여러 신호가 겹치거나 강한 신호가 있을 때 승격합니다.

사용자 입력 길이는 500자 같은 hard dead zone을 두지 않습니다. 짧은 입력에는 아주 작은 점수부터
부여하고, 중간 길이에서 완만하게 증가한 뒤 긴 입력에서는 추가 글자당 영향이 줄어드는 soft curve를
사용합니다. 입력 길이만으로는 약 2500자까지 fast를 유지하며 4000자에서 최대 `2.0`에 도달합니다.
답장 원문과 주변 문맥 등 다른 길이 신호는 서로 의미가 다르므로 같은 곡선을 일괄 적용하지 않습니다.

`왜?` 같은 짧은 후속 질문에서는 검색용 topic anchor와 현재 사용자가 직전에 직접 한 요청을 분리합니다.
직전 사용자 요청이 분석·증명 같은 복잡한 작업이었다면 `complex_followup`으로 smart tier를 유지하지만,
제3자의 복잡한 문장을 인용하거나 reply한 경우에는 `complex_reference`의 약한 보조 점수만 줍니다.
히나의 직전 답변에 reply한 경우에도 같은 사용자의 원래 요청을 별도로 찾아 후속 작업의 난이도를
유지합니다. 다른 사용자의 메시지에 명시적으로 reply하면 이전 요청의 복잡도는 새 주제로 넘기지
않습니다.

시각 입력은 단순 개수 대신 출처와 종류를 함께 봅니다. 현재 메시지와 명시적 reply 이미지는 강한
입력이고, 최근 채널에서 수동적으로 수집한 이미지는 최대 `0.3`의 약한 문맥입니다. 첨부 이미지는
`1.0`, 스티커는 `0.5`, 커스텀 이모지는 `0.25` 단위로 환산한 뒤 각각 포화 곡선을 적용합니다.
따라서 현재 첨부 이미지 1장은 `0.5`, 4장은 약 `0.909`이지만 최근 이미지 1장은 `0.1`입니다.
이미지 파일명, message ID, 작성자와 실제 이미지 내용은 라우팅 telemetry에 기록하지 않습니다.

`MODEL_ROUTING_SMART_THRESHOLD`는 채팅 score가 smart tier로 넘어가는 기준이며 기본값은 `2.0`,
허용 범위는 `0.1`~`10.0`입니다.

### 의미 분류기를 결합한 채팅 라우팅

길이·이미지·검색·문맥량은 계산식으로 측정할 수 있지만, 짧은 요청의 추론 난이도를 키워드 규칙만으로
일반화하기는 어렵습니다. `ROUTING_CLASSIFIER_MODE`로 별도의 작은 모델을 선택적으로 결합합니다.

- `off`: 분류기를 호출하지 않고 기존 `chat-v3` 결과를 그대로 사용합니다.
- `shadow`: 기존 결과로 실제 답변 모델을 선택하면서 분류 결과와 제안 tier를 백그라운드에서
  `usage.jsonl`에 기록합니다. classifier 비용은 발생하지만 답변 경로는 기다리지 않습니다.
- `active`: 의미 분류 결과와 객관적 부하를 결합한 `chat-hybrid-v3` 결과를 실제 답변에 사용합니다.

객관적 부하는 `request_load`, `context_load`, `retrieval_load`, `visual_load`로 나눕니다. 한 축이 이미
smart threshold에 도달했거나 분석·설계·증명 요청처럼 오탐 가능성이 낮은 기존 신호가 있으면 추론
난이도 분류 결과와 관계없이 smart를 사용합니다. 다만 deterministic 웹 검색 결정이 열려 있으면
같은 classifier 호출을 웹 검색 필요성 판단에 사용할 수 있습니다. 나머지는 다음 기준으로 결합합니다.

- 의미 난이도 `high`: smart
- `medium`이고 객관적 축 하나 이상이 `medium`: smart
- `low`이고 객관적 축 두 개 이상이 `medium`: smart
- 그 외: fast

분류기는 같은 응답에서 웹 검색 필요성도 `none`, `auto`, `required`로 판단합니다. deterministic
검색 결정이 이미 잠겨 있으면 이 결과는 관찰 정보로만 남고, 열려 있으면 실제 검색 모드를 보완합니다.
검색 모드가 달라지면 `required_web_search` 등 객관적 부하를 다시 계산한 뒤 최종 tier를 정합니다.

추론 결과가 `uncertain`이거나 timeout, provider 오류, 불완전 응답, 잘못된 JSON이 발생하면 해당
요청은 기존 `chat-v3` tier로 fallback합니다. 웹 결과가 없거나 `web_uncertain`이면 deterministic 검색
결정으로 fallback합니다. classifier 오류 때문에 사용자 답변 자체가 실패하지는 않습니다.

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

분류기 client는 같은 provider/key를 사용해도 일반 답변 client와 분리되며 vision, 웹 도구, retry를
사용하지 않습니다. 별도 API key는 quota·비용·폐기 범위를 분리할 때만 필요합니다. 하나의 classifier
호출에서 추론 난이도와 웹 검색 필요성을 함께 분류합니다. 별도 provider를
지정하면 현재 사용자 요청이 그 provider에도 전달되므로 운영자가 명시적으로 선택해야 합니다.

분류기에는 현재 사용자 요청과, 실제 현재 사용자가 소유한 후속 요청 문맥만 제한된 길이로 전달합니다.
채널 전체 문맥, 제3자의 reply 원문, memory/note/lore, 캐릭터 프롬프트, 이미지와 파일명·사용자·메시지
식별자는 보내지 않습니다. 사용자 텍스트는 지시가 아닌 분류 대상 데이터로 감싸며, 결과는 허용된
`level`, `codes`, `uncertain`, `web_need`, `web_codes`, `web_uncertain`만 있는 JSON이 아니면 거부합니다.
이전 세 필드만 반환하는 legacy 응답도 호환상 허용하지만 웹 판단은 불확실한 것으로 처리합니다.

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

`pending_input_volume`은 실제 요약 payload에 들어가는 필드만 셉니다. DM 개인 기억은 사용자 발화와
히나 답변을 모두 포함하지만, 서버 개인 기억과 공개 shared memory는 사용자 발화만 포함하므로 히나
답변 길이가 해당 라우팅 점수를 올리지 않습니다.

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
`model_route_margin`, `model_route_policy`, `model_route_components`, `model_route_reasons`,
`requested_max_output_tokens`에 남습니다. `model_route_margin`은 score에서 threshold를 뺀 값이고,
`model_route_components`는 콘텐츠 없이 각 신호가 더하거나 뺀 숫자만 기록합니다. 정책 버전은 현재
채팅 `chat-v3`, hybrid 채팅 `chat-hybrid-v3`, 기억 `memory-v1`입니다. Gemini에서는 선택된 thinking
level도 함께 기록합니다. hybrid에서는 `model_route_objective_axes`,
`model_route_objective_bands`, `semantic_route_status`, `semantic_route_level`,
`semantic_route_codes`, `model_route_decision_source`, `model_route_baseline_tier`와 웹 검색 결정 관련
`search_route_*`, `semantic_web_*` 필드도 기록합니다.
분류기 호출은 `operation=model_route_classify`, shadow 비교 결과는
`operation=model_route_shadow` 행으로 남으며 요청 원문이나 자유 형식 설명은 기록하지 않습니다.
`operation=summarize`와 `operation=summarize_shared` 행에서도 같은 telemetry를 확인할 수 있습니다.
fixed 모드의 정책 값은 각각 `chat-fixed-v1`, `memory-fixed-v1`이며 컴포넌트는 비어 있습니다.

## 장기 기억 요약 크기

개인 대화 기억과 공개 shared memory는 같은 모델 호출 경로를 사용하지만 저장 목적과 크기는 분리합니다.
요약 정책, adaptive 라우팅, provider 요청 조립은 공통 `MemorySummaryMixin`에 모아 실제 runtime과
`InformationPipeline` 직접 사용 경로가 동일한 구현을 사용합니다.

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
