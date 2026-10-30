# Conversation maintenance — 작은 오프라인 초기화 계약

> **Status: approved direction, not yet implemented.** #355
>
> 이전 논리적 cutover/범용 유지보수 상태 머신 설계는 범위에서 제외한다.
> 이 문서는 `integration/conversation-maintenance-v2`에서 후속 구현의 기준이 된다.
> `main` 및 운영 데이터는 변경하지 않는다. 폐기된 실험 PR #356은 구현 아이디어와
> 테스트만 참고하고 **그대로 병합하지 않는다**.

## 1. 목표

기존 대화의 분위기나 히나의 과거 반응이 새 응답에 영향을 줄 때
**원본 대화 기록과 공유 요약을 오프라인에서 정리**한다.
사용자들이 쌓아 온 구조화 장기기억과 관계, 수동 설정, 운영 설정, Trace는 유지한다.

이 작업은 **가끔 운영자가 봇을 직접 중단하고 실행하는 유지보수 명령**이다.
무중단 실행·실시간 Dashboard 삭제·범용 job scheduler·복잡한 epoch별 SQL 필터는 필요 없다.

## 2. 데이터 보존과 삭제 범위

| 데이터 | 동작 | 이유 |
| --- | --- | --- |
| `memory_items`, `memory_item_edit_history`, `memory_item_annotations`, `memory_reconciliation_proposals` | **보존** | 장기기억·관계·revision·provenance 유지 |
| `memory_extraction_cursors`, `sqlite_sequence` | **보존** | 신규 turn ID 단조 증가, 중복 추출 방지 |
| `memory_modes`, `chat_log_modes`, `notes`, `runtime_config`, `instructions`, `runtime_knowledge`, 메타데이터, admin/migration state | **보존** | 사용자·운영 설정 유지 |
| `turns`, `shared_calls`, `failed_turns` | **삭제** | 개인/공개 원본 대화와 실패한 이전 턴 제거 |
| `shared_summaries` | **삭제** | 오래된 요약의 분위기가 새 응답에 재유입되지 않도록 |
| usage/event/Trace 로그 | **보존** | 리셋 전후 비교 및 진단 |
| RAM recent context | **봇 재시작으로 비움** | 옛 채널 문맥 제거 |

`shared_summaries`에는 구조화 기억으로 옮겨지지 않은 사실이 있을 수 있다.
모든 내용을 장기기억으로 일괄 승격하지 않는다. **리셋 전 전체 백업에 원본과
요약이 모두 남는다.** 리셋 후에는 공유 요약의 정보가 운영 기억에서 빠질 수
있음을 dry-run과 문서에 분명히 알린다.

`Store.user_name`처럼 원본 대화·요약에 의존하는 부가 데이터가 있는지도
테스트한다. **보존 계약을 지키려면 초기화 후 사용자 이름/관계 표시가
부자연스럽게 사라지지 않도록 필요한 최소한의 조치를 포함한다.**

## 3. 표준 운영 절차

1. **봇을 완전히 종료**하고 Discord 이벤트, 기억 추출 백그라운드 작업,
   관리자 명령 처리, DB를 쓰는 대시보드 프로세스 등을 중지한다.
   오프라인 상태를 운영자가 확인해야 한다. `--offline-confirmed`는
   확인 선언일 뿐 자동 동시성 보호 장치가 **아니다**.
2. CLI dry-run으로 삭제 대상 row 수, 보존할 기억 수, 아직 추출되지 않은
   **구조화 기억 턴과 scope**를 확인한다.
3. WAL 내용을 포함하는 SQLite **전체 백업 A**를 만든다.
   전용 디렉터리/파일 권한, non-overwrite, `PRAGMA integrity_check`,
   복원 가능성 확인을 실패 조건으로 다룬다.
4. 기본값은 **미처리 구조화 기억 자동 동기화**다. 기존 extraction 정책과
   사용자별 memory mode를 유지하면서 남은 1턴까지 처리한다.
   결과가 정상 `{"items":[]}`라면 생성할 장기기억이 없다는 성공이다.
   실패한 scope가 있으면 **기록을 삭제하지 않고 중단**한다.
   scope별 완료 여부와 미처리 수는 cursor 및 기존 transaction journal로 재확인한다.
5. 동기화로 새 기억이 생성됐으므로 **전체 백업 B를 다시 만들고 검증**한다.
   초기화 직전의 장기기억이 포함된 복구 기준점은 B이다. 백업 A는
   flush 이전 대화 원본 보관용으로 유지한다.
6. SQLite **하나의 트랜잭션**에서 `turns`, `shared_calls`,
   `failed_turns`, `shared_summaries`를 삭제하고 마지막 reset 시각과
   Discord snowflake cutoff만 기록한다. schema, ID, cursor, 기타 데이터는
   건드리지 않는다. 오류 시 DELETE 전체를 롤백한다.
7. 보존 row와 `sqlite_sequence`, 새 기억 항목 및 로그 유지 여부를 검사하고
   봇을 재시작한다. 첫 새 대화와 기억 추출·공유 요약의 정상 동작을 확인한다.

초기 버전은 `--flush-pending`과 즉시 파괴적 reset을 **원클릭으로 묶지 않는다**.
`inspect`, `flush`, `reset`을 분리하거나 같은 CLI의 명확한 별도 하위 명령으로 둔다.
각 단계 후 운영자가 결과를 확인할 수 있어야 한다. 기존
`hina-reset-analysis-data`는 수정하지 않는다.

## 4. 반드시 지켜야 할 안전 조건

- **전체 백업 없이 대화 삭제 금지.** 리셋 단계 직전 백업 B를 직접 검증한다.
  복구 리허설은 임시 DB에서 수행하고 라이브 DB에 복원하지 않는다.
- **미처리 구조화 기억이 있으면 reset 기본 거부.** 기억 모드
  `off`/`read_only`에 해당하는 범위는 강제로 쓰지 말고 보고한다.
  미처리 기록 포기 옵션은 초기 버전에 포함하지 않는다.
- #364에서 구현한 **원자적 기억 저장·cursor 갱신**을 재사용한다.
  별도 범용 재시도 큐/작업 상태 머신은 만들지 않는다. 기존 cursor와
  `memory_extraction_batches`가 재시도의 기준이다.
- SQLite 초기화는 DB writer가 모두 중단된 **오프라인**에서만 허용한다.
  관리자 확인·실행 전 DB 쓰기 상태 검사는 필요하지만,
  범용 프로세스 간 fencing 프로토콜은 새로 만들지 않는다.
- `sqlite_sequence`를 초기화하지 않는다. 기억 항목의 source IDs,
  관계·수정 이력·disclosure, 메모리 모드 등 기존 데이터를 보존한다.
- reset marker는 **Discord 최근 메시지 재수집 차단**용으로만 쓰며,
  실제 운영 엔트리인 `web_bot.HinaClient.hydrate_recent_history`에 적용한다.
  `bot.py`의 상위 구현만 수정해서는 충분하지 않다.
- Discord 이전 메시지를 사용자가 새 요청에서 **명시적으로 인용/답장**한 경우는
  새로운 사용자 입력으로 허용할 수 있지만, 자동 채널 backfill과 구분한다.
- 이전 `memory_items`/수동 `notes`의 오해는 이 기능이 고치지 않는다.
  개별 메모리 편집 기능으로 검토한다.

## 5. 최소 구현 범위

### A. 미처리 기억 동기화 (후속 #357)
- 기존 `extract_structured_memory(min_turns=1)`을 scope마다 반복하고
  cursor 진전·실패·처리 건수를 확인한다.
- API 호출 실패 시 다음 scope는 진행할 수 있어도 **최종 reset은 차단**한다.
- 정상 memory mode를 무시하지 않는다. 오프라인 CLI와 관련 테스트.
- 별도 공유 요약 강제 flush는 **필요하지 않다**.
  요약은 백업으로 보관하고 reset에서 삭제되기 때문이다.

### B. 오프라인 SQLite 백업 + 초기화 (후속 #358)
- 읽기 전용 사전 점검, 두 시점 백업과 무결성/복원 검증, 원자적 삭제,
  보존 데이터 불변 조건 검사, reset marker, 재시작 안내.
- 해당 DB가 활성 봇에서 사용 중일 수 있으면 자동 실행하지 않는다.
- `web_bot.py` **실제** Discord backfill 경로를 차단하고 재시작 회귀 테스트.

### C. 간단한 대시보드 표시 (후순위 #360)
- 초기화 대상·미처리 기억 수·최근 성공 여부 정도를 **읽기 전용**으로 표시.
- 웹에서 오프라인 파괴적 reset을 실행하는 버튼은 만들지 않는다.
- A+B가 실제로 안정화된 뒤 진행한다. 첫 배포를 차단하지 않는다.

## 6. 테스트 / 출시 조건

- 백업 A/B가 실제 SQLite로 열리고 복원된 임시 DB에서도 기억
  ID, revision, status, relationships, notes, runtime settings가 유지됨.
- 정상 기억 추출(1턴·여러 배치·빈 결과), 실패한 추출, 쓰기 금지 모드,
  cursor 불변/진전, 중복 재시도가 안전함.
- 원자적 DELETE 예외 시 원본 데이터가 남고, 성공 시 해당 네 테이블만 비워짐.
- 실제 `web_bot.HinaClient`가 재시작 후 **리셋 이전 Discord 메시지를 backfill하지 않음**.
  새 DM/서버 질문, 새 shared summary와 memory 추출은 정상 동작.
- `Store.user_name` 등 지워지는 테이블에서 부가 데이터가 소실되는
  경로를 점검; 남아야 할 사용자 관계와 운영 설정을 검증.
- usage/event/Trace 삭제 없음. 개인정보가 담긴 백업 공개 URL 없음.
- CI 통과 뒤에도 운영 DB 실행은 **운영자의 명시적 승인과 별도 복구 점검** 후에만 가능.

## 7. 브랜치 전략

현재 완료된 #363 설계와 #364의 원자성 개선은
`integration/conversation-maintenance-v2`에 유지한다.

이 문서를 기준으로 A/B를 작고 검증 가능한 별도 PR로 구현하고
**통합 브랜치에만** 병합한다. 기존 #356 코드를 필요한 만큼만 재사용한다.
모든 테스트와 복제 DB 복원 리허설이 끝난 후 운영자 승인 하에 main 병합을
검토한다. #359의 논리적 cutover, #361의 범용 release framework,
#362의 물리 GC는 **현재 범위 밖**이다.
