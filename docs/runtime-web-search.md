# Runtime clock and live-information routing

일반 Discord 답변은 항상 앱이 제공하는 **현재 기준 시각**을 전달하고, 현실 세계의 현재 상태에
따라 답이 달라질 수 있는 질문에만 provider의 웹 검색 기능을 선택적으로 사용합니다.
`CHAT_WEB_SEARCH=false`면 외부 검색만 끄며, 현재 날짜·시각 context는 계속 제공합니다.

## Runtime context

각 답변에는 다음 값을 system instruction으로 전달합니다.

- 현재 ISO datetime과 요일
- 현재 시간대 구분 (`새벽` / `아침` / `오전` / `오후` / `저녁` / `밤`)
- 현재 계절 (`봄` / `여름` / `가을` / `겨울`)
- 평일/주말 구분
- `RUNTIME_TIMEZONE` (기본 `Asia/Seoul`)
- `RUNTIME_LOCALE` (기본 `ko-KR`)
- 선택적 `RUNTIME_DEFAULT_LOCATION`

따라서 `지금 몇 시야?`, `오늘 무슨 요일이야?` 같은 질문은 검색 없이 런타임 clock으로
답합니다. 또한 단순 잡담에서도 사용자의 상태·일정·행동과 자연스럽게 관련될 때는 시간대·요일·계절을
반영할 수 있습니다. 예를 들어 아침에 `너무 졸려`라고 하면 현재 시점에 맞는 반응을 할 수 있지만,
관련 없는 답변마다 시간이나 계절을 반복해서 언급하지 않도록 지시합니다. 제공된 현재 시점과
모순되는 시간대 표현을 만들지 않으며, 날씨처럼 runtime context에 없는 현재 환경 정보는 추측하지
않습니다.

`RUNTIME_DEFAULT_LOCATION`은 날씨·교통·영업시간처럼 사용자가 지역을 생략한 경우의 fallback일
뿐이며 실제 사용자 위치로 취급하지 않습니다. 기본 지역도 없으면 지역을 추측하지 않고 필요한
위치를 물어보도록 지시합니다.

## Freshness routing

`freshness.py`는 질문을 네 종류로 분류합니다.

- `static`: 현재 시점과 무관. 웹 검색 tool을 넣지 않음
- `clock`: 현재 날짜·시각만 필요. 런타임 context를 사용하고 웹 검색 tool을 넣지 않음
- `auto`: 시간 의존 가능성이 있지만 외부 확인이 반드시 필요한지는 불명확. 웹 검색 tool을
  제공하되 모델이 필요할 때만 사용
- `required`: 날씨, 시장 가격, 공휴일·공개 일정, 경기 결과, 교통, 영업시간, 최신 업데이트 등
  외부의 현재 상태가 답을 결정하는 고신뢰 질문. 웹 검색을 강제

지역 의존 질문에서 `RUNTIME_DEFAULT_LOCATION`도 없으면 `required`를 `auto`로 낮춥니다. 이는
위치를 임의로 정한 검색을 강제하는 대신 모델이 먼저 지역을 물을 수 있게 하기 위함입니다.

개인 기억 질문과 봇 자신의 정체성/역할극 질문은 freshness 표현이 있어도 외부 검색으로 보내지
않습니다. 예를 들어 `지금 뭐해?`, `아까 내가 뭐라고 했지?`는 현재 현실 정보 검색 문제가
아닙니다.

## 기존 lore 검색과의 결합

Freshness routing은 기존 Blue Archive lore 정책을 대체하지 않습니다.

- 인물 간 접점·관계, 사건 참여, 당시의 인지 범위는 계속 `required`
- 단순 프로필·소속·장비 질문은 로컬 `world_fact`가 충분하면 검색하지 않음
- 로컬 `world_fact`가 부족하면 `required`
- 사용자가 출처를 직접 요구하면 `required`

검색 결과는 자동으로 lore나 memory에 저장하지 않습니다. 세계관 질문에서는 한국 공식 → 다른
공식 → 게임 데이터/스크립트 전사 → 정리형 위키 → 커뮤니티 순으로 우선하며, 로컬 카논과 검색
결과 하나가 충돌한다고 기존 카논을 바로 덮어쓰지 않습니다.

## Vision input과의 관계

비전 입력은 freshness/information routing의 새 route가 아닙니다. 현재 호출 메시지에 이미지가
있으면 기존 텍스트 질문과 함께 시각 입력이 전달되고, routing은 별도로 필요한 사실 출처를 결정합니다.

예를 들어 다음 요청은 이미지와 web을 동시에 사용할 수 있습니다.

```text
[가게 사진 첨부]
히나야 여기 지금 열었어?
```

이 경우 이미지는 장소나 간판을 해석하는 자료이고, `지금 열었어?`는 현재 영업 상태에 의존하므로
기존 live-information route가 web을 선택할 수 있습니다. 반대로 `이 이모지 무슨 표정 같아?`처럼
현재 이미지 자체만 보면 되는 요청은 웹 검색을 요구하지 않습니다.

비전의 현재 범위와 저장 경계는 [`vision-input.md`](vision-input.md)를 참고하세요.

## Provider mapping

공통 `web_search` 요청은 provider adapter가 변환합니다.

- OpenAI: Responses API `web_search`
- Gemini: Google Search
- OpenRouter: web plugin

`auto`에서는 tool만 제공하고 `tool_choice=required`를 보내지 않습니다. `required`에서만 강제
선택합니다. 검색 context는 현재 `low`를 사용합니다.

## 현재 정보의 신뢰 기준

현재 상태를 묻는 질문에서는 검색 결과의 게시·관측·발표 시점을 런타임 기준 시각과 비교하도록
지시합니다. 오래된 자료만 확인되면 이를 현재 값처럼 표현하지 않아야 합니다. 지역 정보는 사용자
또는 설정에서 주어진 지역만 사용하고, 사용자 위치를 추측하지 않습니다.

## Usage telemetry

`UsageLogger`는 freshness 정책을 결정하지 않고 호출 telemetry만 기록합니다.

`data/logs/usage.jsonl`의 각 API row에는 다음 필드가 기록됩니다.

- `web_search_used`: 해당 logical response에서 실제 검색을 사용했는지
- `web_search_calls`: 생성된 `web_search_call` output item 수

`discord-usage.jsonl`에는 한 Discord 응답에 딸린 모든 API 호출의 `web_search_calls` 합계가
기록됩니다. 사용자 메시지, 검색어, 검색 결과 URL은 usage telemetry에 저장하지 않습니다.
