# #395 중간 점검: 스키마와 불필요한 호환 경로

점검 기준은 `integration/db395`의 `4d1da99` (#397 및 #398 통합)이다.
현재 애플리케이션, Dashboard, 유지보수 CLI, DDL과 테스트를 비교했다.
운영 DB를 열어 실제 값의 분포나 외부 SQL 사용자를 조사한 결과는 아니다.

## 결론

현행 스키마에 **CI만 통과시키려고 추가한 필드**는 확인하지 못했다.
대신 두 종류의 불필요한 의존을 확인했다.

- 폐기된 개인 `summaries`의 `name/text/exportable`은 커서 이전에서 읽지 않는데도
  #397 검사기가 필수로 요구했다. 이 필드 없이 커서만 남은 DB를 거부하거나,
  fixture에 사용하지 않는 필드를 만들 필요가 없도록 검사를 수정했다.
- #397에서 실제 준비 경로가 `prepare_connection`으로 옮겨진 뒤에도 예전 설정
  초기화/변환 API가 남아 있었다. 테스트가 그 경로를 직접 호출하면서 죽은 API를
  유지하게 된 부분을 제거하고, 같은 실패/롤백 검증을 실제 준비 경로로 옮겼다.

이와 별도로 현재 쓰기는 하지만 애플리케이션이 읽어 활용하지 않는 감사 정보가
있다. 아래에 별도 표시했다. 이를 제거하려면 감사·복구 계약을 결정하고 스키마를
변경해야 한다. 이번 구조 정리에 섞지 않았고, 용도를 만들기 위한 읽기도 추가하지 않았다.

## 레거시처럼 보이는 필드의 실제 사용

| 필드/키 | 실제 사용 근거 | 판단 |
| --- | --- | --- |
| `turns.exportable` | `conversation_repository.turn_values`가 수집 당시 공개 여부를 저장하고, `ai/memory_extraction.build_shadow_turns`가 출처별 공개 범위에 사용한다. `flush_pending_memory`도 범위 판단에 읽는다. | 이름은 과거 개인 요약에서 왔지만 현행 데이터 계약이다. 유지한다. |
| `turns.memory_context`, `context_provenance` | 기억 추출의 보조 문맥과 Dashboard의 추론/출처 추적에 사용한다. 원자적 추출의 원본 해시에도 포함된다. | 유지한다. |
| `turns.turn_id`, 실패 대화의 상태·단계·전달 여부·오류 정보 | 런타임이 저장하고 Dashboard가 telemetry와 대화를 연결하거나 실패를 표시한다. 실패 대화는 기억 추출 입력에서 제외된다. | CI 전용 필드가 아니다. |
| `memory_items.status/superseded_by/revision` | `memory_repository`의 교정·대체·철회, 낙관적 수정 충돌 검사와 Dashboard 상세 표시에 사용한다. | 삭제하면 기존 lifecycle 계약이 깨진다. |
| 기억의 출처·공개 범위·근거 JSON·이름 | 추출/검색/조정에서 사용하며 Dashboard에 원문·관계 근거·사용자 이름을 제공한다. | JSON 필드나 중복 이름이라는 이유로 삭제하지 않는다. |
| `memory_item_edit_history.admin_command_id` | Discord 관리 수정에서 전달되고 Dashboard `memory_detail.html`에서 표시된다. | 실제 관리 이력이다. |
| `emoji_registry.source_guild_id` | `discord/emoji_commands.EmojiRegistry.catalog`가 애플리케이션 이모지와 서버 이모지를 구분하고 외부 이모지 권한을 확인한다. | 기본값이 NULL이어도 의미가 있다. |
| 설정의 `updated_at/captured_at` | Dashboard 런타임 설정 화면이 override 수정 시각과 시작 snapshot 시각을 구분한다. | 테스트 전용 timestamp가 아니다. |
| `runtime_config`의 구형 `retrieval_v2_mode` 키 | 명시적 준비에서 기존 `rag_mode`가 없을 때 값을 변환하고 구형 키를 삭제한다. | 이전 입력으로만 지원한다. 새 DB에 만들지 않는다. |
| Python `Settings.retrieval_v2_mode` | 현행 검색 코드가 사용하는 설정 호환 표현이다. | 위의 폐기된 DB 키와 혼동해 제거하지 않는다. |
| `notes`의 `config:chatlog_unified_v1` 및 `config:chatlog_capture:*` | 전자는 일회 변환의 완료 표식, 후자는 현행 all/direct/off 설정의 저장 표현이다. | 표식·운영 설정을 일반 메모로 오인해 삭제하지 않는다. |

## 현행 필드 중 읽기 활용이 약한 부분

| 필드 | 현재 역할과 확인한 한계 | 이번 처리 |
| --- | --- | --- |
| `memory_extraction_batches.id/start_id/source_hash/completed_at` | `Store.atomic_memory_batch`가 커서·기억과 함께 기록한다. 현재 봇/Dashboard에 이 값을 읽는 경로는 확인되지 않았다. `UNIQUE(scope, through_id)`는 추출 중복 방지에 쓰인다. **저장된 `source_hash`를 읽어 stale 여부를 검사하는 것은 아니다.** 실제 guard는 저장 전 원본 해시를 재계산한다. | 기존 배치 감사 자료로 보존한다. 재시도/조사 기능이 이미 있다고 설명하지 않는다. |
| `admin_migrations.name/applied_at` | JSON import의 실행 표식이다. importer가 기록하고 분석 데이터 reset이 보존하지만, 이를 읽어 재실행을 막는 기능은 없다. 실제 import 중복 처리는 각 레지스트리의 ID/내용 비교가 맡는다. | 운영 import 이력으로 보존한다. schema 이력과 통합하지 않는다. |
| `schema_migrations.applied_at` | 준비 시각을 기록한다. 검사기는 `component/version/name`을 읽으며 timestamp를 버전 판정에 쓰지 않는다. | schema 감사 정보로 보존한다. |
| `conversation_reset_epochs.backup_sha256` | reset CLI가 검증한 백업의 해시를 기록한다. 자동 hydration은 `cutoff_message_id`를 읽으며 해시를 검증하지 않는다. | 복구 감사 자료로 보존한다. 자동 복구 검증 기능이 있다고 설명하지 않는다. |

이 필드들은 런타임의 핵심 판단에 쓰이지 않는다는 점에서는 정리 후보지만,
테스트 때문에만 존재한다는 증거는 없다. 이후 삭제하려면 실제 운영 데이터와
감사 필요성을 확인한 별도 변경으로 다뤄야 한다.

## 이미 폐기된 스키마

### 개인 `summaries`

현행 새 DB에는 이 테이블이 없다. 준비 과정은 `scope/realm/user_id/through_id`만
`memory_extraction_cursors`에 복사한다. 기존 구조화 커서가 있으면 그 값이 우선하며,
기존 계약대로 변환 후 `summaries`를 제거한다. 표의 세 구형 필드는 복사하지 않는다.

이번에는 필수 네 컬럼과 알려진 선택 컬럼 `name/text/exportable`의 부분집합을
허용한다. 알 수 없는 추가 컬럼 또는 커서 필수 컬럼 누락은 계속 거부한다.
이는 검사기의 입력 허용 범위를 넓히는 변경이며, 현행 테이블/필드를 변경하는 migration은 아니다.
제거되는 구형 요약 원문은 준비 전 검증된 전체 백업에 남는 기존 계약을 유지한다.
원문을 새로 보존하는 기능 변경은 아니다.

일곱 컬럼은 [개인 요약 제거 PR #289](https://github.com/sendoru/hina-discord-bot/pull/289)
직전 `core/store.py`의 DDL에도 존재했던 형태다. **이 구형 fixture를 유지**한다. 이 fixture는
업그레이드와 실제 백업 복원 검증에 필요하다. 새로 추가한 검증은 사용하지 않는
필드가 없어도 커서를 이전할 수 있고, dry-run·백업·반복 실행 계약이 유지되는지 확인한다.

### reset epoch의 `discarded_structured_turns/discarded_shared_turns`

현행 epoch 컬럼은 `id/reset_at/cutoff_message_id/backup_sha256`이다.
폐기된 두 컬럼은 새 DDL에 없고, 전용 `migrate_conversation_reset_epochs`가
백업 후 명시적으로 제거한다. 옛 fixture에 남은 것은 그 변환 경로 검증을 위해서다.
현행 테이블에 테스트용 필드를 다시 추가하지 않는다.

## 전체 테이블의 사용 경계

공통 ID·scope·시각 필드는 키, 순서, 범위 선택, 목록/상세 표시와 추적에 포함해 확인했다.
`SELECT *`만 있는 경우에는 반환값의 소비자도 함께 확인했다.

| 테이블 묶음 | 실제 소비자 |
| --- | --- |
| `turns`, `failed_turns` | 대화 기록·추출·실패 관측, Dashboard conversation/trace, flush/reset CLI |
| `shared_calls`, `shared_summaries` | 공개 참고 문맥·공개 요약 guard, 이름 복원, Dashboard 요약/커서, purge/reset |
| `memory_items`, `memory_item_edit_history`, `memory_reconciliation_proposals` | 기억 검색·수정·교정·철회, Discord 관리, Dashboard 기억/관계/조정 이력 |
| `memory_extraction_cursors`, `memory_extraction_batches` | 추출 진행·원자적 저장·중복 방지, 커서는 Dashboard 상태 조회; 배치의 읽기 한계는 위에 명시 |
| `notes`, `memory_modes`, `chat_log_modes`, `emoji_registry` | 런타임 설정/정책/이모지, Discord와 Dashboard 관리 경로 |
| `guild_metadata`, `channel_metadata`, `conversation_identity_names` | 관측한 이름·범위 표시, reset 후 이름 복원 |
| `runtime_config`, `runtime_config_startup` | 설정 override와 실제 시작 snapshot, Dashboard 설정 화면 |
| `instructions`, `runtime_knowledge` | LLM 입력·검색, 레지스트리 관리·JSON import, Dashboard 관리 |
| `admin_commands` | Dashboard enqueue, 봇 claim/dispatch/finish 및 재시작 처리, Dashboard 결과/오류 조회 |
| `schema_migrations`, `admin_migrations` | 각각 schema 검증 이력과 JSON import 감사 표식; 용도와 읽기 한계는 위에 명시 |
| `conversation_reset_epochs`, `observability_epochs` | 자동 hydration cutoff, reset 결과/백업 연결; Dashboard 분석 기준선 |

## 코드에서 줄인 것과 유지한 것

- `SettingsRepository.ensure_runtime_schema/migrate_legacy_rag_override/replace_legacy_chatlog_settings`
  및 Store를 받던 구형 chatlog 변환 함수를 제거했다. 레포지토리는 현행 설정 SQL만
  맡고, schema/구형 변환은 `prepare_connection`의 한 트랜잭션이 맡는다.
- 큐 런타임 모듈의 DDL wrapper를 제거했다. Store 준비가 `QUEUE_DDL`을 직접 사용한다.
- Store 인덱스를 한 목록으로 모았다. bootstrap, 검사, 구형 테이블 재구성이 같은
  정의를 사용하며, 검사기에 박힌 별도 SQL과 재구성의 특수 분기를 제거했다.
- 평가와 import dry-run의 임시 복제 작업을 `AdminDatabase.copy_to_memory`로 옮기고
  평가 전용 wrapper 파일을 제거했다. 실제 import는 사용하지 않을 복제본을 만들지 않는다.
- JSON import CLI를 `tooling/runtime_migration.py`로 옮겼다. `hina-migrate-runtime`
  명령은 유지하며 core가 평가/유지보수 도구를 import하던 역방향 의존을 제거했다.

읽기 전용 open, schema 검사, 백업 검증, 트랜잭션 조정은 각각 실패 책임이 달라
유지한다. 모든 SQL을 Repository로 옮기거나 파일 수를 줄이기 위해 계층을 합치지 않는다.
Store facade, 연결 소유권, 추출 원자성·커서·출처, #398 guard와 복합 저장 계약도 유지한다.
원본 보존·논리 reset·검색 기능은 #399~#401에서 별도로 다룬다.
