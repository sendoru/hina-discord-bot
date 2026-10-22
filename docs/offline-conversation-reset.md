# Offline conversation reset

이 기능은 **운영자가 봇과 DB writer를 모두 중단한 상태**에서만 사용한다.
`hina-reset-analysis-data` (분석 로그 정리)와 목적이 다르며 기존 명령은 수정하지 않는다.

## 0. 현재 원칙

- **보존:** 구조화 장기기억 `memory_items`, revision/edit history, 관계·정정 제안,
  extraction cursor와 batch journal, `sqlite_sequence`, 메모리/채팅 설정,
  notes, 런타임 지침과 지식, 관리 메타데이터, Trace/usage/event JSONL,
  그리고 디버깅용 `conversation_debug_archive`.
- **초기화:** 운영용 `turns`, `shared_calls`, `failed_turns`, `shared_summaries`.
  `turns`와 `failed_turns`는 삭제 전 디버그 아카이브에 복사하며,
  대시보드의 대화/Trace 조회에서는 예전 기록을 계속 볼 수 있다.
  공유 요약에만 남아 있던 정보는 운영 컨텍스트에서 사라진다. 전체 DB 백업에 원본을 보관한다.
- **Discord backfill:** reset 시각 이전의 메시지는 봇이 재시작된 뒤에도 최근 채널 문맥으로
  자동 재수집하지 않는다. 사용자가 **직접** 과거 메시지를 인용/답장하는 경우는 새 입력으로 간주한다.
- **임시/실험 DB에서 먼저 검증하고** 사용자 데이터를 포함한 운영 DB에는 명시적 승인 전 적용하지 않는다.

스키마 업그레이드가 필요하면 먼저 [명시적 DB 준비 절차](db-initialization.md)를
실행한다. flush/reset의 조회·계획은 DB를 초기화하지 않고, flush 실행도 봇 시작
스냅샷을 덮어쓰지 않는다.

## 1. 정상 실행 순서

1. 현재 DB 위치와 올바른 설정/토큰을 확인한다. 봇과 대시보드를 중단하고,
   별도 관리자 명령·백그라운드 DB writer가 실행되지 않는지 확인한다.
   `--offline-confirmed`는 사람의 확인이지 자동 프로세스 중지 기능이 아니다.
2. 백업을 남기고 **미처리 구조화 기억**을 1턴짜리 잔여분까지 LLM으로 추출한다.

   ```sh
   hina-flush-memory --dry-run
   hina-flush-memory --yes --offline-confirmed
   ```

   실행 전에 `data/backups`에 전용권한(0700)의 **전체 SQLite 백업 A**를 만들고
   `integrity_check`, 주요 테이블 count를 검증한다.
   기억 mode가 `off`/`read_only`면 해당 scope는 처리하지 않고 오류를 출력한다.
   모든 scope가 정상 완료하지 못하면 초기화를 시도하지 않는다.

3. 초기화 계획을 확인한다.

   ```sh
   hina-reset-conversation --dry-run
   ```

   `Structured memory pending: 0 turns / 0 scopes`가 되어야 한다.
   공유 요약이 남아 있어도 새 요약 생성 없이 전체 백업에 보존하고 초기화한다.

4. **백업 B** 생성 및 초기화:

   ```sh
   hina-reset-conversation --yes --offline-confirmed
   ```

   초기화 명령도 자체적으로 전체 SQLite 백업 B를 새 파일에 만들고 검사한 뒤,
   하나의 트랜잭션에서 성공/실패 대화를 디버깅 전용 아카이브로 복사하고
   네 운영 conversation 테이블을 정리한다. 아카이브는 봇의 최근 대화,
   공개 문맥, 장기기억 추출 입력으로 사용하지 않는다.
   연속해서 초기화해도 기존 아카이브 기록은 유지된다.
   구형 `conversation_reset_epochs`의 사용하지 않는
   `discarded_structured_turns`/`discarded_shared_turns` 컬럼은
   초기화 트랜잭션 안에서 자동으로 제거된다. 기존 epoch의
   `id`/`reset_at`/`cutoff_message_id`와 존재하는 `backup_sha256`
   값은 보존한다. 오래된 행에 백업 해시가 없었던 경우에는 빈 문자열로
   유지하고 새 epoch에는 검증된 백업의 SHA-256을 기록한다.
   알 수 없는 추가 컬럼이 있으면 삭제 전에 오류로 중단하고 롤백한다.
   백업 실패 시에는 원본을 변경하지 않는다. 미처리 기억이 있으면 기본적으로
   초기화를 거부하며, 운영자가 `--discard-pending`을 명시한 경우에만 허용한다.

5. `backup=` 및 `sha256=` 로그를 안전하게 기록하고, 임시 SQLite 복제본에
   백업 파일을 열어 주요 장기기억과 설정이 복구 가능한지 확인한다.
   그다음 봇을 재시작해서 DM/서버 첫 메시지, 기억 추출, 공유 요약 재생성을 검증한다.

## 2. 선택: 미처리 기억을 flush 없이 포기하고 초기화

이번 대화의 분위기를 장기기억으로 추출하고 싶지 않은 경우에만 사용한다.
**기본 초기화 경로는 변경되지 않으며**, `--discard-pending`을 명시하지 않으면
미처리 구조화 기억이 있는 DB의 초기화는 계속 차단한다.

봇, 대시보드와 **모든 DB writer를 먼저 중단**한 뒤 아래처럼 확인하고 실행한다.

```sh
uv run hina-reset-conversation --dry-run --discard-pending
uv run hina-reset-conversation --yes --offline-confirmed --discard-pending
```

- Dry-run에 표시된 `Structured memory pending` 수를 반드시 확인한다.
  `--discard-pending`은 미처리 턴을 LLM에 보내지 않고, 해당 턴으로부터의
  **새로운 구조화 기억 추출 기회만 포기**하는 옵션이다.
- 초기화 명령 자체는 여전히 전체 SQLite 백업을 만들고 검증한다.
  기존 `memory_items`, 관계·기억 추출 cursor, 설정, notes, runtime 정보,
  `sqlite_sequence`는 보존한다. 기존 기억을 삭제하거나 수정하지 않는다.
- 삭제할 대화와 실패 턴은 디버그 아카이브에 보존하고,
  운영용 `turns`, `shared_calls`, `failed_turns`, `shared_summaries`를
  기존과 동일한 트랜잭션으로 삭제한다. 공유 요약에서만 알 수 있던 내용도
  런타임에서 사라진다.
- 이후 새 대화는 기존 단조 증가 ID와 cursor를 사용하여 정상 추출한다.
  이전 메시지는 자동 Discord backfill 대상으로 다시 들어오지 않는다.
- `--offline-confirmed`는 필수이며, 백업 후 DB 데이터가 변경되면
  `--discard-pending` 사용 여부와 관계없이 초기화를 거부한다.

## 3. 별도 epoch 스키마 정리 (선택)

초기화와 관계없이 구형 epoch 기록 테이블의 컬럼만 정리하려면
운영 봇·대시보드·모든 DB writer를 종료한 뒤 다음을 실행한다.

```sh
uv run hina-migrate-reset-epochs --dry-run
uv run hina-migrate-reset-epochs --yes --offline-confirmed
```

이 명령은 `conversation_reset_epochs`만 정리한다. 오래된 두
`discarded_*` 컬럼은 삭제되므로 과거 기록의 해당 값이 필요하면
자동으로 생성되는 전체 SQLite 백업(`data/backups`)을 참조한다.
마이그레이션은 기존 행·ID·cutoff와 시퀀스를 보존하며,
대화·메모리·설정 테이블은 변경하지 않는다. 이미 최신 스키마면 아무것도
수정하지 않는다. 이 명령을 따로 실행하지 않아도
`hina-reset-conversation`이 백업 이후 같은 정리를 트랜잭션 안에서 수행한다.

SQLite 3.35 이상이 필요하다 (`ALTER TABLE DROP COLUMN` 지원).

## 4. 주의 사항

- 보관된 디버그 기록에는 **개인 대화 원문**이 계속 남는다. 이 명령은 개인정보 완전 삭제용이 아니다.
  별도 `hina-reset-analysis-data`는 이 디버그 아카이브도 삭제한다.
  기존 retention 정책에 따라 초기화 *이전에* 이미 제거된 턴은 복구하지 못한다.
- 백업에는 **개인 대화 원문과 비공개 기억**이 담긴다. 공개 파일 서버, 웹 URL,
  Discord 채널이나 GitHub에 업로드하면 안 된다. 백업 파일(0600)과 디렉터리(0700)를 보호한다.
- 복구 시 봇과 DB writer를 중단하고, 원본을 보관한 뒤 SQLite 파일과
  `-wal`/`-shm` 잔재가 섞이지 않도록 주의한다.
- 파일 삭제로 인해 줄어든 유저 표시 이름은 `conversation_identity_names`에
  마지막 관측값을 보존해 보완한다. 캐릭터와 사용자 관계의 근거가 되는
  `memory_items` 자체는 변경하지 않는다.
- 한 번의 실행으로 flush+reset을 자동 연결하지 않는다. 중간 결과를 검토할 수 있도록
  **두 개의 별도 명령**으로 유지한다. flush 생략은 위의 명시적 옵션으로만 가능하다.
- 공유 요약에만 있던 정보까지 **구조화 장기기억으로 무조건 승격하지 않는다**.
  이 정보를 운영 DB에서 계속 참조할지 결정하고 싶으면 초기화 전에 백업을 검토한다.
- 기본/동적 프롬프트, 구조화 기억, 개별 수동 notes에 남은 오래된 반응 관성은
  이 초기화로 없어지지 않는다.
