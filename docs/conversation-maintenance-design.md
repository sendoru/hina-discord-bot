# Conversation maintenance v2 — 운영 데이터 보존 및 문맥 전환 계약

> **Status: Proposed / design only** — #355의 새 계약. 이 문서는 운영 명령을 활성화하지 않으며,
> 기존 #356의 파괴적 CLI를 승인하지 않는다. 관련 변경은
> `integration/conversation-maintenance-v2`에서 통합하고, umbrella 완료 전에는 main에 병합하지 않는다.

## 1. 목적과 비목적

운영 중 히나가 오래된 대화·일시적 갈등·자신의 과거 답변 패턴에 끌려가는 문제를 줄이되,
사용자들이 쌓아 온 **구조화 장기기억과 관계**, 운영 설정, 추적 가능성을 보존한다.

**초기 안전 기본값은 데이터를 지우는 reset이 아니라 논리적 conversation cutover**다.
기존 SQLite 원본은 남기고 특정 epoch 이전 기록을 **모델 응답 입력과 새로운 공유 요약 입력에서**
제외한다. 적용 후 실제 응답/기억 동작이 올바른지 확인한 다음,
**물리 삭제(garbage collection)는 별도 기능·승인·PR**로 설계한다.

- **보장하지 않는 것:** 저장된 `memory_items`·`notes`·수동 지침에 이미 기록된 오해를
  자동 수정하거나, 사용자가 직접 다시 인용한 옛 대화까지 숨기는 것.
- 대화 품질 향상을 보장하지 않는다. cutover는 **입력 분리**이고 프롬프트/모델 품질은 따로 평가한다.
- 기존 `hina-reset-analysis-data`(로그 포함 분석 baseline 삭제)는 의미를 변경하지 않는다.

## 2. 데이터 분류와 보존 계약

| 분류 | 대표 자료 | 논리적 cutover 후 처리 | 물리 삭제 |
| --- | --- | --- | --- |
| 현재 대화 원본 | `turns`, `shared_calls`, `failed_turns` | 기존 epoch 기록은 read filter로 제외, 원본 보존 | 별도 후속 |
| 공개 공유 요약 | `shared_summaries` | 기존 요약은 archive 상태로 취급, 응답과 **이후 요약의 old_memory**에서 제외 | 별도 후속 |
| 구조화 장기기억 | `memory_items`, edit history, reconciliation proposals | **계속 사용**, ID/revision/status/disclosure/evidence 보존 | 범위 외 |
| 기억 진행 상태 | `memory_extraction_cursors`, summary through-id, `sqlite_sequence` | 단조 증가·재시도 안전성 보존. 구 epoch 미처리분은 정책에 따라 완료/보류 | 범위 외 |
| 관리 및 수동 자료 | `notes`, `memory_modes`, `chat_log_modes`, `instructions`, `runtime_config`, `runtime_knowledge`, emoji, admin/migration, guild/channel metadata | 변경 없음 | 범위 외 |
| 관측성 | usage/event/Trace, `observability_epochs` | 보존. 새 conversation epoch와 trace를 연계 | 범위 외 |
| 최근 Discord 맥락 | 메모리의 RecentMessages, history backfill | 기존 epoch 자동 유입 제외, 새 대화 정상 축적 | DB 삭제와 별개 |

기존 `shared_summaries`는 지속적인 사용자 정보를 **포함할 수 있으나**
`memory_items`와 동등한 구조화 장기기억이 아니다.
원문/요약을 무조건 `memory_items`로 승격하지 않고, 보관·검토할 수 있게 한다.

## 3. 불변 조건

1. cutover는 기존 `memory_items` 행의 모든 필드, ID, 이력, 관계 근거·정정 참조와
   관리자 설정을 변경하지 않는다. 단, 사전에 별도로 **승인된 기억 flush**는 새 항목을 만들 수 있다.
2. cutover 이전 대화/공유 요약은 **자동 응답 맥락뿐 아니라 이후 요약·검색의 입력에서도** 배제된다.
   새 epoch의 새 원본 대화와 요약은 정상 누적된다.
3. 초기 버전은 **전역 cutover 하나**만 지원한다. 채널별 epoch 혼합은 향후 별도 설계한다.
4. 경계는 시간 문자열 하나가 아니라 **해당 SQLite에서 관찰한 `MAX(turns.id)`,
   `MAX(shared_calls.id)`와 Discord snowflake watermark**로 정의한다.
   새로운 원본 행은 반드시 이보다 큰 ID만 갖는다. `sqlite_sequence`를 초기화하지 않는다.
5. 위 경계와 활성 epoch는 **단일 DB 트랜잭션으로** 커밋된다. crash 후에는 이전 epoch 또는
   새 epoch 중 하나만 보여야 한다. 반복 실행은 별도 epoch를 만들 때만 유효하고, 이전 상태를 덮지 않는다.
6. `shared_summaries.through_id`가 구 epoch에 머무는 경우, 새 epoch 응답에서는 요약을 숨기고
   새 epoch 요약 생성 시 `previous_memory=""` 및 **새 epoch `shared_calls`만** 사용해야 한다.
   기존 행을 단순히 덮어쓰면서 과거 내용을 이어 붙이면 안 된다.
7. 사용자가 옛 메시지를 **명시적으로 답장·인용**할 때 그 인용은 새 입력의 일부로 볼 수 있다.
   자동 재수집과 명시적 참조는 별도 경로·telemetry로 구별한다.
8. 모든 실패는 **기존 데이터 보존**을 기본으로 한다. flush 실패, backup 실패, 경계 검증 실패
   또는 운영 프로세스 쓰기 정지 실패 시 cutover를 수행하지 않는다.

## 4. 진짜 입력 경로 목록 (완료 전 체크리스트)

`runtime_entry.py`는 `web_bot.HinaClient`를 실행한다. 따라서 `bot.py`의
`hydrate_recent_history`만 변경해도 실제 운영에는 적용되지 않는다.

- [ ] **Discord**: `web_bot.HinaClient.hydrate_recent_history` (실제 override)와
  상위 클래스 경로를 모두 검증. SQL cutoff와 별도로 Discord snowflake를 비교;
  restart, 15분 내 backfill, 경계와 같은 ID, 직접 입력된 신규 메시지 검사.
- [ ] **RAM**: `RecentMessages` 버퍼와 리셋 직후 첫 턴의 캐시 상태. 안전한 restart로
  초기화하거나 epoch-aware invalidation을 검증.
- [ ] **DM / 서버 개인 대화**: `request_assembly.py`의
  `_dm_conversation_history`, `_server_recent_conversation`은
  `store.history`의 구 epoch 행을 받지 않도록 한다.
- [ ] **공개 맥락**: `store.public_candidates`, `store.public_context`,
  `store.pending_shared`, `store.shared_summary`의 epoch 경계를 맞춘다.
  과거 요약 `through_id`와 신규 `shared_calls` 혼용 회귀 테스트 필수.
- [ ] **미처리 개인 기억**: `store.pending_memory_extraction`,
  `memory_extraction_evidence` 및 기억 후보 출처. cutover 직전 미완료 범위 처리 정책 필요.
- [ ] **답장·참고문맥**: 명시적 Discord reply/target/context fetching,
  past message provenance; 자동 주입 금지와 사용자가 요청한 인용 허용의 차이.
- [ ] **Retrieval v2**: ambient/reaction/history 입력에 구 epoch 채널 데이터가 포함되지 않는지.
- [ ] **대시보드**: 최근 대화/요약/개별 trace 조회에서 운영 데이터가 사라진 것처럼 표시하지 않음.
  **응답에서 격리됨**과 **DB에 보관됨**을 구분.

이 목록을 소진하기 전에는 `no old conversation context`라는 합격 판정을 내리지 않는다.

## 5. 기억 강제 flush는 별도 유지보수 기능

### 입력과 커밋 계약

- 대화가 많은 scope에도 **고정된 batch 범위 (scope, source turn IDs, starting cursor)**를 기록한다.
  1턴 꼬리도 다룰 수 있지만, 정상 빈 `{"items":[]}` 결과와 API 실패를 구분한다.
- 개인 구조화 기억은 기존 `SHADOW_EXTRACTION_POLICY`의 provenance, disclosure,
  evidence, 관계·중복 검증을 그대로 사용한다. 사용자/다른 봇 사실을 섞어 추출하지 않는다.
- **LLM 호출은 DB 트랜잭션 밖**, 검증된 응답 결과의 기억 항목·reconciliation·cursor·배치 완료 기록은
  **하나의 DB 트랜잭션 안**에서 커밋한다.
  트랜잭션 시작 시 batch 입력 fingerprint 및 cursor 상태를 재검증한다.
- 같은 batch 재실행은 기억 항목·관계 수정을 **중복 적용하지 않도록** 고유 batch key를 갖는다.
  부분 결과를 남겨야 한다면 명시적 `needs_recovery` 상태로 분리하고 다음 batch로 넘어가지 않는다.
- 활성 memory mode `read_only`/`off`는 우회하지 않는다. 이 scope는
  **blocked_by_policy**로 보고하며 운영자가 별도 판단하기 전 cutover를 차단한다.
- 실패 시 source turn과 cursor를 남긴다. 다른 scope 성공도 버리지 않되 실행별 상태를 보관한다.

### 공개 shared summary

- `summarize_shared`는 원래 이전 대화의 요약을 갱신하는 기능이다.
  이 작업에 성공했다고 그 내용이 `memory_items`에 이관되는 것은 아니다.
- 구 epoch 요약은 **archive**로만 남겨서 새 epoch 응답/요약에는 쓰지 않는다.
- 그러므로 전체 대화 cutover를 위한 필수 사전 단계는 **구조화 기억 추출의 미처리 상태 검증**이며,
  shared-summary는 별도 `retain/archive` 정책을 정의한다.
  내용을 어차피 숨기거나 삭제할 예정이라면 **강제 재요약을 필수로 요구하지 않는다**.
- 공유 요약에만 있었던 정보는 대시보드에서 열람·검토할 수 있지만,
  자동 구조화 기억 승격은 금지한다.

## 6. 쓰기 중지, 백업, 감사

1. 진행 전 운영 봇과 dashboard/admin writer 및 백그라운드 작업이 중단됐음을
   **프로그램으로 검증 가능한 유지보수 락/상태**와 프로세스 종료 절차로 확인한다.
   `--offline-confirmed` 플래그 하나는 동시 쓰기 방지 증거가 아니다.
2. `sqlite3.Connection.backup()` 기반 **복구 가능한 전체 스냅샷**을 생성한다.
   WAL 포함, `integrity_check`, 스키마/중요 테이블 count 및 manifest hash를 검증한다.
   백업은 소유자 전용 권한을 갖고 symlink/overwrite를 거부한다.
3. 외부 업로드·다운로드 URL·자동 복원 버튼을 만들지 않는다. 복구 절차는 오프라인
   복제 DB에서 실제로 열어 보고, 주요 기억 불변 조건이 복원되는지 리허설한다.
4. cutover 직전 확보한 writer fence가 유효한지 확인하고, 트랜잭션 안에서
   epoch, 두 DB cutoff, Discord watermark, backup manifest 식별자를 기록한다.
5. 기존 로그는 삭제하지 않고 trace에 epoch를 붙여 결과 비교를 지원한다.
   감사 로그는 counts/status/ID만 기록하고 원문·민감한 정보는 기록하지 않는다.

## 7. 상태 머신과 운영 UX

초기 버전은 **읽기 전용 preview → 별도 memory flush → logical cutover**를 기본 흐름으로 한다.
flush와 cutover를 한 CLI 플래그에 묶지 않는다.

| 상태 | 허용 동작 | 중단/실패 |
| --- | --- | --- |
| `planned` | 영향 범위·scope별 pending·백업 위치·모드 표시 | 데이터 변경 없음 |
| `quiesced` | writer 중지 확인 및 잠금 | 검증 실패면 abort |
| `backed_up` | 원본 snapshot+manifest 검증 | 실패 시 원본 유지 |
| `flushing` | scope별 작은 배치와 결과 기록 | 해당 scope만 `blocked`/`retryable`; cutover 불가 |
| `ready` | 미처리 정책 점검, 변경 범위 재검사 | 오래된 계획이면 invalidate |
| `cutover_committed` | 원자적 epoch 삽입 | rollback or committed, 중간 없음 |
| `verified` | 첫 새 응답·요약에서 구 epoch 미포함 검사 | 실패 시 즉시 pause/rollback 절차 |
| `archived` | 기존 원본은 격리 보관, 사용은 새 epoch | GC는 별도 승인 |

- `resume`은 같은 batch key를 사용하고 완료된 작업을 건너뛴다.
- `cancel`은 새 batch 중단만 의미한다. 이미 확정된 장기기억 변경을 소리 없이 되돌리지 않는다.
- `rollback`은 `cutover` 이전 상태를 다시 **활성화**하는 별도 epoch 변경으로,
  그 사이 새 대화와 충돌할 수 있으므로 자동 토글이 아니라 점검·승인 절차로 한다.
- 대시보드는 처음에는 **읽기 전용 계획·단계/오류/백업 확인**만 제공한다.
  온라인 대량 삭제 버튼은 추가하지 않는다.

## 8. 테스트 게이트 / 출시 기준

- **기억:** 신규 정상 기억/정정·충돌 제안/빈 결과/1턴 꼬리/중복 실행/중간 DB 실패/
  처리 모드 off-read_only/동시 변경/기억 항목 동일성 검증.
- **맥락:** DM/개인 서버/채널 최근/Discord backfill/공개 요약 및 신규 요약/
  Retrieval v1/v2·상대 화자 전이/명시 인용을 포함한 end-to-end 입력 검증.
- **안전:** backup 없음/불일치/쓰기 중 프로세스 존재/락 상실/유효하지 않은 스키마/
  중복 cutover/crash 시점/재시작/복제 DB rollback 테스트.
- **사용자 경험:** 이전 대화가 숨겨지더라도 `memory_items`와 관계 설정·커스텀 운영 설정은
  그대로 적용되고, 새 턴의 기록·기억 추출은 정상 동작.
- 실제 Gemini 운영 경로는 **복제 DB의 오프라인 리허설** 후에만 승인.
  CI 통과만으로 운영 데이터 삭제를 허용하지 않는다.

## 9. PR 분할 / merge gate

1. Contract/ADR (이 문서, 동작 변경 없음)
2. Flush atomicity + idempotency + resumable batch journal
3. SQLite snapshot/restore rehearsal + writer quiescence
4. Logical cutover epoch + production-wide read/write-path filters
5. Read-only dashboard plan/progress/audit
6. Optional physical GC (별도 장기 검증 단계, 기본 비활성)

각 단계는 독립 PR과 테스트를 갖고 **umbrella integration branch**에만 병합한다.
하위 작업 완료 및 복구 리허설 전까지 `main`은 변경하지 않는다.
기존 #356은 구현 시도/회귀 케이스 참고로 남기되 **현재 형태 그대로 merge하지 않는다**.
