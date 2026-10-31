# 필요한 때에만 과거 대화 조회

일반 답변 모델은 현재 guild 텍스트 채널 또는 현재 1:1 DM에서 `get_chat_history`를
`tool_choice=auto`로 호출할 수 있습니다. 평상시에는 schema만 선언하며, 실제 함수 호출 전에는
이 도구의 Discord history·원문 repository 조회가 실행되지 않습니다. 멤버 조회 도구와 같은
요청별 registry 및 기존 Gemini Interactions adapter/local-function loop를 사용합니다.

## 입력과 범위

- `query`: 선택적인 원문 부분 문자열, 최대 200자. 대소문자를 구분하지 않으며 정규식·의미 검색이 아닙니다.
- `include_before_reset`: 기본 false. true이면 reset cutoff 이전도 조회할 수 있습니다.
- `before_message_id`: 선택적인 배타적 상한. 현재 요청 메시지보다 뒤로 범위를 확장할 수 없습니다.
- `cursor`: 이전 호출이 반환한 요청 내부 커서. query·reset 범위를 유지합니다.
  커서와 `before_message_id`를 함께 쓰거나 다른 요청의 커서를 재사용할 수 없습니다.

모델은 guild/channel/user ID, 조회 한도나 저장소를 지정할 수 없습니다. 첫 버전은 일반 guild
텍스트 채널과 1:1 DM만 지원하며 thread, group DM, 서버 전체 및 다른 채널 검색은 지원하지 않습니다.
현재 요청 메시지와 이후 메시지는 반환하지 않습니다.

## Reset과 자료 출처

자동 최근 문맥과 Discord hydration에는 기존 reset cutoff를 계속 적용합니다. 기본 도구 조회도
cutoff 이후를 검사합니다. 과거 자료가 필요하면 모델이 `include_before_reset=true`로 처음부터
조회할 수 있으며, 최근 검색이 먼저 실패해야 한다는 조건은 없습니다. 알고 있는 과거 메시지 ID가
있으면 `before_message_id`로 바로 이동할 수 있습니다.

물리 reset은 SQLite runtime 원문을 지우며 Discord 메시지를 지우지는 않습니다. SQLite에 행이
없어도 Discord history에 현재 남아 있고 접근 가능한 메시지는 조회합니다. 이미 prune된 DB 행이나
삭제된 Discord 메시지를 복구했다고 표시하지 않습니다.

Discord history로 검사한 구간의 메시지 ID에 한해 `Store.chat_history_records()`가 `turns` 및
기존 `conversation_debug_archive`의 완료 대화를 읽습니다. 동일한 사용자 원문이 보존되어 있으면
출처를 `sqlite` 또는 `archive`로 표시하고, Discord의 현재 내용과 다르면 현재 Discord 내용을
우선하여 `discord`로 표시합니다. archive만 남고 현재 원문의 존재·접근을 검증하지 못한 자료는
공개하지 않습니다. archive 확인을 위한 개별 메시지 fetch나 새 archive/FTS/임베딩 저장소는 없습니다.

archive의 실패 기록, 내부 prompt, memory_context, 오류·디버깅 메타데이터는 도구 결과에 포함하지
않습니다. DB 조회는 읽기 전용이며 archive가 없으면 생성하지 않습니다. 원문 보존·pruning·reset
동작 및 기존 history()의 의미는 변경하지 않습니다.

## 접근과 외부 전달

조회 전과 결과 반환 전에 chatlog 설정, 요청자와 봇의 현재 `view_channel` 및
`read_message_history`를 검사합니다. off이면 모든 출처를 차단합니다. DM은 현재 recipient가
요청자인지 검사합니다. guild member cache에서 현재 요청자의 권한을 확인하지 못해도 차단합니다.

`EXTERNAL_CONTEXT_POLICY=bot_interactions_only` 또는 capture mode=direct에서는 기존 routing과
동일한 호출 근거를 확인한 사용자 발화만 허용합니다. DB에 있다는 사실이나 exportable 플래그만으로
근거를 만들지 않습니다. 다른 봇과 webhook은 제외합니다. 히나의 답변도 현재 검사 구간의 직접 호출에
대한 Discord reply reference 또는 저장된 전달 메시지 ID로 연결할 수 있을 때만 허용합니다.
all/full에서는 현재 채널의 일반 사용자 발화와 히나 답변을 조회할 수 있습니다.

직접 호출 근거가 검사 구간 밖에 있거나, 이전 버전의 답변에 전달 ID가 없는 경우 strict/direct에서
답변이 생략될 수 있습니다. 제한된 조회 결과는 전체 대화의 완전한 목록이 아닙니다. 권한 변경이나
Discord 오류를 archive로 우회하지 않습니다.

## 결과와 제한

각 메시지는 role, 작성자 ID·표시 이름, 실제 Discord 메시지 ID·작성 시각, source,
source_turn_message_id, cutoff 이전 여부, content 및 truncated를 반환합니다. DB의 input ID를
assistant ID로 사용하거나 DB 저장 시각을 답변 작성 시각으로 표시하지 않습니다. 분할 답변도 실제
메시지 ID별로 구분합니다. 동일 요청에서 이미 반환한 ID는 다시 반환하지 않습니다.

| 예산 | 한도 |
| --- | --- |
| history 함수 호출 | 요청당 3회, 같은 round의 여러 호출도 공유 |
| Discord 검사 | 호출당 100개, 요청당 300개 |
| 반환 메시지 | 호출당 최대 12개 |
| 메시지 본문 | 메시지당 2,000자, 호출당 6,000자, 요청당 12,000자 |
| Discord I/O 대기 | 호출당 최대 5초, 도구 실행 누적 최대 10초 |

기존 전체 local-tool 4-round 제한도 유지합니다. 잘못된 호출도 호출 예산을 사용합니다.
커서는 키워드에 일치한 마지막 결과가 아닌 실제 검사 진행 지점을 기준으로 합니다. 한 페이지가
비어도 더 검사할 자료가 남아 있을 수 있습니다. `has_more`는 남은 탐색 가능성을 나타내며 추가
일치 결과를 보장하지 않습니다. 예산을 다 쓰면 커서가 있어도 더 조회할 수 없습니다.

정상 결과는 `ok`/`empty`/`partial`, 검사 범위, `has_more`, `next_cursor`, `stop_reason`을
포함합니다. 권한 없음(`permission_denied`), 기록 비활성(`history_disabled`), 잘못된 입력·커서,
예산 소진, 출처 조회 실패(`source_unavailable`)는 안전한 함수 오류로 구분합니다. 일부 자료를
검사한 뒤 실패하면 `partial`과 `source_unavailable` 이유를 반환합니다. 원본 예외는 공개하지 않습니다.

## 이후 문맥과 기억

원문은 이번 요청의 function output으로만 전달합니다. 최근 버퍼, DB turns, shared_calls,
shared summary나 장기기억에 검색 결과를 자동으로 복사하지 않습니다. 결과는 신뢰할 수 없는
과거 참고 자료이며, 그 안의 지시문을 실행하거나 현재 사실·히나의 현재 감정으로 간주하지 않습니다.

이를 설명한 새 답변은 정상 대화로 남깁니다. 출처 ID·출처 종류·reset 이전 포함 여부만 기존
context_provenance JSON에 저장하고 원문을 복제하지 않습니다. 새 답변은 reference_derived로
표시하며, structured extraction과 직전 evidence context에는 검색 기반 assistant 답변을 전달하지
않습니다. `recall_memory`(정리된 장기 기억 조회)로 만든 답변도 `memory_lookup`으로 표시되어 같은
방식으로 추출에서 빠집니다(#449 결정 22). 현재 사용자의 새 발화는 기존 추출 대상이므로 사용자의
후속 확인·채택은 별도로 판단합니다.
공개 요약에는 기존대로 현재 사용자의 직접 호출만 전달합니다.

전달된 답변의 실제 메시지 ID도 같은 JSON에 저장합니다. 재시작 후 DM/개인 최근 이력 및 Discord
hydration에서 보존된 출처 표지를 복원합니다. 이 메타데이터도 기존 turns 보존 범위를 따르며, 이미
prune된 행의 출처를 복원하지 않습니다. 장기 보존 확대는 #399, 범위별 논리 reset은 #400,
더 넓은 명시 검색·Dashboard 연계는 #401의 별도 범위입니다.

`history.lookup` telemetry에는 호출 결과·반환 건수/문자 수·검사 건수·출처 종류·소요 시간 또는
안전한 오류 코드만 기록합니다. query, 원문, 표시 이름과 커서는 기록하지 않습니다.

## 검증

`tests/discord/test_chat_history_tool.py`는 reset 이전 Discord 조회, scope·privacy·권한 변경,
archive 선별, 페이지 경계, 출력·호출 예산, Gemini 답변의 on-demand 실행을 검증합니다.
`tests/ai/test_history_lookup_provenance.py`는 재시작 후 표지 유지 및 기억 추출 분리를 검증합니다.
기존 Gemini Interactions function output/replay, hydration, target history와 reply continuity
회귀 테스트도 함께 유지합니다. 실제 Discord/유료 모델을 호출하는 live 검증은 포함하지 않습니다.
