# #397~#398 구조 최종 점검

기준: #407 통합 브랜치 `4ae7761`. #399~#401, main 병합, 운영 DB 실행은 제외한다.
앞선 [필드 점검](db-schema-audit.md)과 [조작 코드 정리](db-operations-audit.md)에 이어
schema 검사·이전의 책임과 변경 지점을 최종 점검했다.

## 판단과 최종 소유권

기존 검사기는 임의 DB를 변환하는 범용 migration framework가 아니다. 고정된
store/settings/admin 영역과 baseline-v1, 알려진 구형 형태만 지원한다. 미래 버전,
알 수 없는 컬럼·제약·재구성 의존성을 거부하는 책임은 필요하다.

줄일 수 있었던 부분은 같은 준비 작업이 여러 파일과 런타임 정책에 흩어져 있던 점,
구형 컬럼 이름과 인덱스 복구·설정 변환의 중복된 실행 경로였다.

| 변경 전 | 변경 후 |
| --- | --- |
| `store_ddl.py`, `admin_schema.py`, `queue_schema.py`, `settings_schema.py`에 DDL 분산 | `db_ddl.py`: 목표 DDL과 세 고정 영역의 구성만 정의한다. 연결·실행·commit 없음. |
| `db_schema.py`, `store_schema.py`, `settings_schema.py`, `chatlog_modes.py`에 준비·구형 변환 분산 | `db_schema.py`: 검사, 알려진 이전, 적용 순서와 단일 transaction을 소유한다. `chatlog_modes.py`는 현행 정책만 맡는다. |
| 검사기의 optional 컬럼 목록과 별도 ALTER 목록 | 알려진 `LEGACY_COLUMNS` 한 목록을 검사와 ALTER가 함께 사용한다. |
| 초기화 때 인덱스 생성, 테이블 재구성 때 다시 복구 | 컬럼 추가·재구성을 마친 후 기존 인덱스 목록을 한 번 적용한다. |
| 구형 chatlog의 read → plan dict → 별도 SQL apply | 같은 이전 함수에서 기존 값을 읽고 변환·저장·완료 표식을 처리한다. 중간 plan 상태와 파일 간 호출을 제거한다. |
| 이미 읽은 컬럼/CHECK/unique를 `_matches`에서 다시 조회 | 같은 검사 단계에서는 읽은 값을 재사용한다. ALTER 후 재구성 판단은 별도로 다시 검사한다. |

DDL 변경은 한 파일, 구형 호환·적용 순서 변경은 한 파일에서 확인한다. 기존 SQLite
DDL을 새로운 schema DSL, 등록식 migrator, Repository/Service 또는 DTO로 다시 표현하지 않는다.
DDL에서 테이블 이름을 정규식으로 추출하던 부분은 해당 DDL을 실행한 SQLite의
실제 테이블 목록으로 대체했다. 이력은 이미 검증한 영역의 집합만 보관하고,
pending도 집합으로 중복을 없앤 뒤 기존 순서의 tuple로 반환한다.

## 복잡하지만 유지한 부분

- **목표 DDL을 임시 SQLite에서 실행해 얻은 메타데이터와의 비교**: 컬럼 순서가 다른
  ALTER 결과, 기본값, PK/unique·정렬/콜레이션을 수작업 명세로 모두 중복하지 않는다.
  SQL 문자열 전체 일치로 바꾸면 의미가 같은 기존 DB 표현도 거부할 수 있다.
- **CHECK의 제한된 비교와 미지원 의미 거부**: SQLite의 PRAGMA만으로 CHECK 본문을
  얻을 수 없다. 문자열 리터럴의 대소문자를 보존하고, 지원하지 않는 표현은 통과시키지 않는다.
  임의 SQL parser로 확장하지 않는다.
- **세 영역과 schema 이력 검사**: admin-only import와 store/settings 준비를 구분한다.
  모르는 버전이나 기록된 baseline과 실제 구조가 다른 DB를 최신으로 stamp하지 않는다.
- **준비 전·잠금 후·적용 후 검사**: 각각 미지원 입력 거부, 백업 이후 경합 거부,
  실제 결과와 이력 검증이다. 중복 조회를 줄인다는 이유로 단계 사이 검사를 생략하지 않는다.
- **테이블 copy/rebuild·sqlite_sequence 복원과 rollback**: CHECK를 설치하면서 ID,
  삭제된 최고 ID, revision·편집 이력·출처 JSON·조정 제안을 보존한다. 임의 trigger/index를
  재구성 중 잃을 수 있으면 실행 전 거부한다.
- **#398의 별도 원자성 계약**: 기억 추출의 커서/해시 guard, 공개 요약의 snapshot/guard,
  완료 대화 공동 저장, 큐의 claim/action/finish는 범위가 다르다. 범용 transaction executor로 합치지 않는다.

## 별도 안전성 보완: 재구성의 외부 의존성

구조 정리 후 합성 DB에서 기존 구현의 문제를 재현했다. 다른 테이블의 FK나 외부 view가
기억 테이블을 참조하면 RENAME이 참조를 임시 이름으로 바꾼다. 이어진 DROP은 FK OFF에서
깨진 참조를 남기고, FK ON + ON DELETE CASCADE에서는 자식 행까지 삭제할 수 있었다.
자기 테이블의 index/trigger만 확인하던 기존 검사를 보완해, 들어오는 FK와 custom
view/trigger가 있는 재구성은 **DDL·백업 전에 거부**한다.

임의 SQL body를 해석하는 parser는 추가하지 않는다. 따라서 무관한 custom view/trigger도
재구성이 필요한 DB에서는 자동 지원하지 않는다. 재구성이 필요 없는 현행 DB의 관련
동작은 바꾸지 않는다. 이 입력 거부 동작 변경은 소유권 정리 PR과 별도 PR로 검토한다.
목표 DDL·운영 자료·보존 정책을 바꾸지 않고, 위험한 이전을 그대로 중단시키는 변경이다.

## 단순화 여지가 있지만 이번에 바꾸지 않은 부분

- Store/AdminDatabase의 새 DB 생성 코드에는 비슷한 경로가 남는다. 공통 factory를
  만들면 area 선택, 읽기/쓰기 설정, 기존 파일 처리와 facade 생성 규칙까지 묶게 된다.
  현재의 짧고 명시적인 두 수명 계약을 유지한다.
- 시작 실패 시 bot/LLM/provider와 DB를 정리하는 경로도 더 묶을 수 있다. 부분 생성과
  close 실패 후 DB를 닫는 보장이 필요하므로 단순한 분기 삭제로 축약하지 않았다.
- 검사 메타데이터 tuple을 class로 감싸거나 schema 이력 검사를 일반화할 수 있다.
  현재 네 항목과 한 baseline을 위해 별도 객체·미래 버전 dispatch 체계를 추가할 이점은 작다.
- 구형 개인 요약·chatlog·RAG 이전은 실제 지원 입력이다. 테스트만을 위한 API와
  달리 운영 DB 조사가 끝나지 않았으므로 없애지 않는다. SQL CHECK와 Python 입력 검증도
  서로 다른 쓰기 진입점을 보호하므로 한쪽을 제거하지 않는다.

## 변경량과 검증

`src/`의 Python 파일 물리 줄 수 기준이며 문서·테스트는 제외한다. DDL과 주석은 포함한다.

| 항목 | 전 → 후 |
| --- | --- |
| schema 관련 파일 | 6 → 2 (전체 `src/` Python 파일 157 → 153) |
| `db_schema.py` | 348 → 416줄: 다른 파일의 이전 책임 통합 및 외부 의존성 거부로 증가 |
| 전체 `src/` Python 코드 | 33,883 → 33,855줄, 합계 28줄 감소 |

구조 정리 PR의 감소분은 38줄이며, 별도 안전성 보완에서 10줄을 추가했다.

여섯 목표 DDL 상수의 SQL 문자열은 기준 코드와 정확히 같다. 새 컬럼·인덱스·version은
추가하지 않는다. 기존 구형 DB가 목표 baseline으로 이전되는 결과와 기존 요약 제거·백업
계약도 그대로이며, 보존·삭제 정책은 변경하지 않는다.

- 기존 전체 회귀에 인덱스 설치 실패 rollback, chatlog 상속/무효 capture 처리 2건,
  세 기억 테이블의 편집 이력·revision·출처·ID 최고값 및 실제 backup restore 검증을 추가했다.
  구조 정리 PR은 **1,476 tests + 3 subtests**를 검증한다. 안전성 보완은 FK ON/OFF 및
  외부/무관 view·trigger의 무쓰기 거부 5건을 추가해 **1,481 tests + 3 subtests**가 된다.
  Ruff·version check 및 CI 결과는 각 변경의
  PR과 #407 상태 검사에 기록한다.
- 별도 일회성 비교에서는 기준 코드와 수정 코드에 같은 26개 합성 DB/설정 형태와
  영역 선택 7조합을 적용했다. **182조합(승인 151, 거부 31)**의 승인/거부, pending/적용
  영역, 반복 실행, 공백/SQL 키워드를 정규화한 스키마 SQL·전체 행 결과가 같았다.
  거부된 입력은 변경되지 않았다.
  양쪽의 `CURRENT_TIMESTAMP`를 동일하게 고정해 새 감사 시각도 비교했다.
  이 보조 비교는 CI 테스트 개수에 포함되지 않는다.
  위의 외부 FK/view/trigger 사례는 기존 코드가 위험하게 허용하던 입력을 의도적으로
  거부하도록 바뀌었으므로, 동일성 비교와 별도의 안전성 회귀로 검증한다.

운영 DB의 실제 형태·데이터량·손상 여부, 운영 복제본 migration/restore, 실제 Discord/LLM
연동과 프로세스 강제 종료·전원 장애는 검증하지 않았다. fixture의 실제 파일 backup/restore와
SQL/commit 실패 주입을 운영 적용 완료로 간주하지 않는다. 이번 변경의 로컬 SQLite는 3.53.1이다.
