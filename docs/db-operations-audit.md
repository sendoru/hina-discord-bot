# #395 중간 점검: DB 조작 코드

기준은 `integration/db395`의 `857c9f8`이다. [필드 점검](db-schema-audit.md)에
이어, core의 Store/Repository/Registry·관리 큐, Dashboard 조회, 준비·백업·flush·reset·
epoch/JSON 이전 CLI와 그 호출부·테스트를 조사했다. 정적 참조뿐 아니라 `getattr`과
문자열을 통한 호출, CLI entrypoint, 테스트의 상태 준비와 실제 실행 경로를 구분했다.
운영 DB나 저장 정책을 변경하는 작업은 아니다.

## 제거한 코드

| 위치 | 확인한 사용 상태와 처리 |
| --- | --- |
| Dashboard `table_names`, `memory_counts_by_user`, `recent_reconciliation_proposals`, `shared_summaries` | 애플리케이션·테스트의 메서드 호출이 없다. 현행 화면이 쓰는 schema/status/필터 조회를 유지하고 폐기된 단순 목록·집계 API를 제거했다. SQL의 같은 테이블 이름을 메서드 호출로 세지 않았다. |
| Dashboard `search_turns`, `_turn_filters`, `count_turns`의 필터 인자 | 검색은 성공·실패 대화를 합치는 `search_conversations/count_conversations`로 이미 이동했다. 옛 검색은 테스트만 사용했다. 필터·날짜·사용자 이름 검증을 실제 화면 경로로 옮겼다. `count_turns()`는 telemetry coverage의 **성공 대화 총수**로 계속 사용되므로 단순화해 유지했다. |
| Dashboard `recent_memory_items` | 테스트만 사용했다. 같은 데이터 준비 후 현행 `search_memory_items`로 검증한다. 기존 목록의 기본 lifecycle 조건과 현행 검색 조건을 같은 것으로 간주해 런타임 호출을 바꾼 것은 아니다. |
| Dashboard `_has_column` | `_table_columns`와 중복됐다. 한 번의 `PRAGMA table_info`로 필요한 컬럼을 확인한다. 없는 테이블은 SQLite가 빈 결과를 반환하므로 별도의 존재 확인 연결도 제거했다. |
| Store `_decode_memory_item` | Repository 구현으로 옮겨진 뒤 남은 미사용 private wrapper였다. 실제 decoder는 Repository에 유지했다. |
| Store `set_memory_mode` | 현재 관리 경로는 `set_memory_mode_override`를 사용한다. 테스트만 쓰던 채널 별칭을 제거하고 테스트도 실제 override API를 사용한다. |
| Store/ConversationRepository `add_shared_call`, `save_shared_summary` | #398 이후 현재 봇은 각각 공동 저장과 snapshot/guarded commit을 사용한다. 두 standalone writer의 남은 호출은 테스트 상태 준비뿐이었다. 별도 커밋을 만드는 wrapper를 제거했다. 과거 상태·서로 다른 ID·경합을 재현하는 테스트는 기존 `write_shared_call/write_shared_summary`를 **명시적인 테스트 트랜잭션**에서 호출해 데이터를 준비한다. 런타임 fallback은 만들지 않았다. |

이 정리는 Store facade의 현행 호출·트랜잭션 조정 역할을 유지하면서, 위의 사용하지
않는 메서드 표면을 줄인다. `add`는 자동 hydration 등 실제 독립 저장 경로가 있어
유지한다. 이름이 비슷하다는 이유로 `record_completed_turn`으로 일괄 치환하지 않는다.

## 줄인 중복 작업과 보완한 실패 처리

- Dashboard 읽기 연결과 관리 큐 enqueue는 기존 `open_readonly/open_existing`을
  사용한다. 별도 connection factory나 flags를 추가하지 않았다. 각 호출자의 연결
  수명·read-only/query_only·5초 timeout·큐의 독립 커밋은 유지한다.
- Dashboard의 turn/name 컬럼 선택을 `_turn_select`에 모았다. 구형 DB 조회는 여전히
  쓰기·이전 없이 동작하고, schema 결과를 장기간 캐시하지 않는다.
- 공개 요약의 pending 조회는 기존 `shared_calls_after` SQL을 재사용한다. read snapshot을
  새로 열거나 커밋하는 동작은 추가하지 않았다.
- flush의 반복 조건은 미처리 행의 **존재만** 필요하다. `limit=1`로 전체 tail을
  반복해서 가져오지 않는다. 실제 추출 batch 크기·순서·커서 진행 조건은 그대로다.
- 유지보수 연결 설정에서 이미 공통 opener가 설정한 row factory를 다시 설정하던
  코드를 제거했다. PRAGMA/스키마 확인 실패 시 획득한 연결이 누수되던 부분은 모든
  실패 경로에서 닫도록 보완했다. 유지보수 전용 1초 timeout과 secure_delete는 유지한다.

## 조사 후 유지한 부분

| 영역 | 합치거나 제거하지 않은 이유 |
| --- | --- |
| Store 메서드의 Repository 위임 | 실제 봇·추출·Discord 관리 호출이 사용하는 facade다. caller-owned 연결과 추출 write context가 필요하다. 범용 CRUD/Service로 바꾸지 않는다. |
| 추출 batch, 공개 요약 guard, 완료 대화 공동 저장 | 각기 다른 입력·충돌·원자성 계약이다. 공통 transaction decorator로 숨기지 않는다. 저장 실패·커서 경합·purge 후 stale 저장·두 연결 가시성 검증을 유지한다. |
| forget/channel/realm/all purge | 범위 조건, 반환 삭제 건수, edit history 처리 순서와 보존 대상이 다르다. 현재 삭제 계약을 바꾸는 일괄 삭제 helper를 만들지 않는다. |
| 관리 큐 claim/action/finish | 서로 다른 커밋이다. 중단 후 action을 재실행하면 안 되므로 일반 작업 실행기나 단일 트랜잭션으로 합치지 않는다. |
| Registry SQL과 nested AdminDatabase.transaction | JSON import가 레지스트리 쓰기를 하나로 묶는다. 검증·ID/내용 비교·budget 확인은 실제 관리/LLM 경로에서 사용한다. 별도 Repository를 추가하지 않는다. |
| 유지보수 pending/plan 쿼리 | reset 종류별 삭제 대상과 허용하는 구형 스키마가 다르다. Store getter는 커서를 생성할 수 있어 read-only 조사에 대신 쓸 수 없다. |
| 백업·schema 준비·epoch 이전 | 연결 수명·백업 전후 검사·잠금 획득 후 재검증은 삭제나 DDL의 복구 계약이다. 검사 횟수만 보고 중복으로 제거하지 않는다. |

## 검증 기준

옛 API를 위한 대체 런타임 shim이나 테스트 통과용 분기를 추가하지 않는다.
기존 테스트의 검색·이름·커서·ID·보존/삭제·경합 assertion은 유지하고 사용 경로만 바꾼다.
추가한 유지보수 실패 주입은 writer/read-only opener 이후 PRAGMA 및 schema 확인이
실패했을 때 연결이 닫히는지 검증한다. 기존 Dashboard read-only 검증도 연결 종료를 확인한다.
전체 Python 3.11~3.13 CI와 Ruff·버전 검사를 통합 전후 확인한다.

실제 schema와 보존량은 변경하지 않는다. `history_turns`의 원본 삭제, 논리 reset,
검색 기능은 여전히 #399~#401의 별도 작업이다.
