# Operational conversation reset (offline; Stage 1)

이 명령은 **운영 DB의 대화 기록과 공유 요약을 정리**하면서 기존 사용자들의
구조화 장기기억과 관계 정보, 운영 설정을 보존하기 위한 것입니다. 분석용
`hina-reset-analysis-data`와 목적이 다릅니다.

> **현재 구현 상태:** 오프라인 기억 강제 동기화·SQLite 초기화·전체 백업·검증과
> 재시작 후 Discord 최근 채널 기록 자동 재수집 차단까지 구현한 단계입니다.
> 대시보드 유지보수 화면은 후속 단계(#355)입니다.
> 사용자가 초기화 이전 메시지에 **명시적으로 답장·인용**하는 경우에는
> 해당 참조를 새 요청의 일부로 다시 가져올 수 있습니다.

## 안전한 실행 순서

1. **반드시** 봇을 중단하고, SQLite를 기록하는 다른 프로세스·작업이 없는지
   확인합니다. CLI가 프로세스 종료를 자동으로 보장하지는 않습니다.
2. 전체 DB의 파일시스템 단위 백업도 별도로 보관하면 좋습니다.
   이 명령은 실행 전 `sqlite3.Connection.backup()`을 사용해 WAL까지 반영된
   **검증된 DB 스냅샷을 자동 생성**합니다.
3. 초기화할 row와 미처리 기억 양을 확인합니다.

   ```bash
   hina-reset-conversation --dry-run
   # 또는
   python -m hina_bot.tooling.reset_conversation_state --dry-run
   ```

4. pending 건이 없는 경우, 다음과 같이 실행합니다.

   ```bash
   hina-reset-conversation --yes --offline-confirmed
   ```

5. 미처리 턴이 있는 경우 **추출·요약을 먼저 강제 수행**하는 것을 권장합니다.
   1턴짜리 잔여 배치도 처리하며, 모델 호출이 성공하고 해당 scope의
   cursor/through_id가 실제로 진전한 경우만 완료로 취급합니다.

   ```bash
   # 강제 동기화의 대상 개수 확인 (API 호출 없음)
   hina-flush-memory --dry-run

   # 봇과 모든 DB writer가 종료된 상태에서 강제 기억 동기화
   hina-flush-memory --yes --offline-confirmed

   # 동기화 성공 여부와 미처리 건수 재확인
   hina-reset-conversation --dry-run
   ```

   별도로 실행할 수도 있고, 초기화 과정에서 한꺼번에 수행할 수도 있습니다.

   ```bash
   # 초기화 직전에 강제 기억 동기화; 실패한 scope가 있으면 reset 취소
   hina-reset-conversation --yes --offline-confirmed --flush-pending
   ```

   두 명령 모두 **LLM 호출 전에 별도 전체 SQLite 백업**을 생성합니다.
   `--flush-pending` 경로는 flush 전후 각각 백업이 생성됩니다. 처리 실패
   scope의 원본 턴은 삭제하지 않고 남깁니다. `read_only`나 `off` 모드 등
   기억 쓰기가 금지된 scope는 우회하지 않으며 전체 초기화를 중단합니다.

   처리할 내용이 없는 경우에도 `{"items":[]}`처럼 정상 빈 결과는
   추출 성공으로 간주할 수 있습니다. **모든 턴이 장기기억 항목으로 변환되는 것은 아닙니다.**
   일시적인 감정이나 단발성 질문은 기억에 저장하지 않는 게 정상입니다.

   기존 `shared_summaries`는 전체 대화 상태 초기화에서 삭제되므로,
   shared-summary flush가 성공했더라도 **그 요약이 구조화 장기기억으로
   자동 승격되는 것은 아닙니다.** 대신 flush 전/후 SQLite 백업에 보존됩니다.

   최후의 수단으로 꼬리 대화를 포기하더라도 백업을 반드시 남기도록
   `--discard-pending` 옵션은 유지합니다. 아래 옵션은 flush와 **동시에 사용할 수
   없습니다.**

   ```bash
   hina-reset-conversation --yes --offline-confirmed --discard-pending
   ```

   미처리 기록의 폐기는 사용자와 쌓아 온 정보를 잃을 수 있으므로 운영에
   권장하지 않습니다.

6. SQLite 백업 경로와 SHA-256 digest, 결과 row 수, reset ID를 기록합니다.
   재시작 전 백업 위치에 접근할 수 있는지 확인합니다.

## 데이터 계약

| 대상 | 처리 |
| --- | --- |
| `turns`, `shared_calls`, `failed_turns` | 삭제 |
| `shared_summaries` | **삭제** (과거 공용 대화 분위기의 영향까지 제거) |
| `memory_items`, `memory_item_edit_history` | **보존** (ID, status, revision 유지) |
| `memory_reconciliation_proposals`, `memory_extraction_cursors` | 보존 |
| `memory_modes`, `chat_log_modes`, `notes` | 보존 |
| `runtime_config`, `instructions`, `runtime_knowledge` | 보존 |
| `emoji_registry`, `admin_commands`, migration/metadata | 보존 |
| usage/event/discord-usage JSONL 파일과 Trace | 보존 |
| `sqlite_sequence` (AUTOINCREMENT) | 보존; 새 ID가 과거 cursor보다 커짐 |

`shared_summaries`도 일부 사용자 경험에 영향을 줄 수 있는 영속 요약입니다.
완전히 대화 상태를 초기화하려면 이것도 제거할 필요가 있지만,
그 안에만 남아 있던 정보는 현재 DB에서 더는 바로 사용할 수 없으므로
**반드시 백업을 보호**해야 합니다.

리셋마다 `conversation_reset_epochs`에 reset time, Discord cutoff snowflake,
discarded structured/shared pending turn counts가 남습니다. 이는 재시작 후
과거 Discord 메시지 유입을 막는 단계와 연결할 준비된 marker입니다.
리셋 전/후 Trace는 **삭제하지 않습니다.**

## 백업 복구

저장된 snapshot은 일반 SQLite 파일입니다. **운영 봇을 완전히 중단하고**
문제 발생 시 기존 DB를 보관한 뒤 snapshot을 되돌릴 수 있습니다.
기존 `-wal`/`-shm` 파일과 새 DB 파일이 섞이지 않게 주의하세요.
파일명을 임의로 공개 링크로 노출하면 안 됩니다. 백업에는 민감한 사용자
대화와 구조화 기억이 모두 들어 있고, 기본 백업 디렉터리는 소유자 전용
권한으로 생성됩니다.

이 명령은 DB 파일을 물리적으로 새로 생성하지 않고 선택된 테이블을
트랜잭션 내에서 삭제합니다. 수동 `DELETE`, cursor 재작성, `sqlite_sequence`
초기화는 금지합니다.

## 기존 analysis reset과의 차이

- `hina-reset-analysis-data`: `shared_summaries` 보존, telemetry 파일까지 삭제,
  observability epoch 시작.
- **`hina-reset-conversation`**: `shared_summaries` 포함 과거 대화 상태 정리,
  telemetry 보존, 매번 검증된 전체 SQLite 백업, 별도 conversation reset epoch.

대화 문맥의 일부는 `notes`, `memory_items`, 동적 지침에서도 올 수 있습니다.
이들은 현재 기능의 보존 계약에 포함되므로, 그 내용이 문제라면 리셋이 아니라
대시보드에서 개별 기억/지침을 확인하고 수정해야 합니다.
