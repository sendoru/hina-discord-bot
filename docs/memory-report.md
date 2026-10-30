# 장기기억 집계 리포트 (`hina-memory-report`)

장기기억 v2 설계(#449)의 P0(#450)에서 운영 데이터를 바탕으로 기준값을 정하기 위한 집계 도구입니다.
운영 DB나 로그를 공유하지 않고, 운영자가 로컬에서 실행한 **집계 결과(JSON)만** 공유하는 것을
전제로 합니다.

## 실행

```bash
uv run hina-memory-report > memory-report.json
# 경로 지정
uv run hina-memory-report --db data/hina.sqlite3 --usage-log data/logs/usage.jsonl
# usage 로그 없이 DB만
uv run hina-memory-report --usage-log ""
```

Docker Compose 환경에서는 이 명령이 포함된 이미지로 다시 빌드한 뒤, 컨테이너 안의 경로 설정을
그대로 씁니다. `-T`는 출력에 터미널 제어 문자가 섞이지 않게 합니다.

```bash
docker compose build
docker compose run --rm -T hina hina-memory-report > memory-report.json
```

- 기본 경로는 `DATABASE_PATH`, `USAGE_LOG_PATH` 환경 변수이고, 없으면 `data/hina.sqlite3`,
  `data/logs/usage.jsonl`입니다.
- DB는 SQLite `mode=ro` + `query_only`로 열고 아무것도 쓰지 않습니다. 봇이 실행 중이어도 읽을 수
  있지만, 장시간 쓰기와 겹치면 잠시 대기할 수 있습니다.
- usage 로그는 회전 파일(`usage.jsonl.1`~`.3`)까지 읽습니다.
- `--near-threshold`(기본 0.8)는 유사 중복을 셀 때 쓰는 문자 bigram Jaccard 기준입니다. 같은
  사실을 다른 문장으로 쓴 중복(예: "고양이 보리를 키움"과 "보리라는 이름의 고양이를 키우고 있음",
  0.22)은 이 방식으로 잡히지 않으므로, 중복 수치가 작다고 실제 중복이 적다고 볼 수는 없습니다.

## 출력에 포함하지 않는 것

기억 원문, 대화 발화, 메모 내용, 사용자 이름, Discord 사용자·서버·채널·메시지 ID는 출력하지
않습니다. 출력은 개수·비율·분위수(min/p50/p90/max/mean)·구간별 분포뿐입니다. 이 보장은
`tests/tooling/test_memory_report.py`가 확인합니다.

사용자 수가 적은 운영 환경에서는 분포 값(예: 사용자별 항목 수의 max)이 특정 사용자의 규모를
짐작하게 할 수 있습니다. 결과를 외부에 공개할 때는 이 점을 감안하세요.

## 주요 항목

| 경로 | 의미 |
|---|---|
| `database.memory_items.active` | active 항목의 kind·disclosure·출처 유형 분포, 사용자별 항목 수, 나이·길이·confidence 분포, 관리자 편집 비율 |
| `database.memory_items.duplicates_non_relationship` | 같은 사용자 안의 정확 중복(정규화 후 동일)과 유사 중복. 같은 공개 범위/다른 공개 범위로 나눔 |
| `database.memory_items.current_injection_estimate` | 현재 방식에서 DM 전량 주입 시 항목 수·문자 수, 서버 공개 공간에서 FULL인 항목 수 |
| `database.memory_items.temporal_signals` | 과거 표지·상대 날짜·절대 날짜를 포함한 항목 수(정규식 휴리스틱) |
| `database.memory_items.relationship` | 관계 기억의 evidence 축·수준 분포 |
| `database.memory_items.by_status` | active/superseded/retracted별 kind·출처 유형·길이·confidence·시간 신호. 운영자가 정리한 현재 active만으로는 보이지 않는 추출 품질을 보기 위한 항목 |
| `database.memory_items.duplicates_all_statuses_non_relationship` | 철회·대체된 항목까지 포함한 전체 이력 기준 중복 |
| `database.memory_items.supersede_provenance` | superseded 항목 중 대응하는 reconciliation 제안이 있는 것/없는 것(수동 편집 등) |
| `database.reconciliation_proposals` | 제안의 적용 결과 재분류(적용, 기준 미달, kind 불일치, 관계 보류, conflict), 관계/종류별 confidence 구간 분포, 미해결 conflict 수 |
| `database.extraction_backlog`, `database.turns` | 추출 대기 턴과 `turns` 보존 현황 |
| `database.shared`, `database.notes`, `database.memory_modes` | 공유 요약·메모 길이 분포, memory mode 설정 분포 |
| `telemetry.context_provenance` | 실제 응답에 들어간 구조화 기억 항목 수, `public_server_context` 주입 빈도, factual recall 상태. 문자 수와 비중은 같은 `turn_id`의 `context.size` 행과 합쳐 계산 |
| `telemetry.shadow_extraction` | 추출 상태별 횟수와 저장·중복·거절·제안·적용·보류 합계 |

`reconciliation_proposals`의 재분류는 저장된 항목 상태와 현재 자동 적용 규칙(기준 0.95)으로
추정한 값입니다. 제안 시점의 실제 결과를 기록한 로그는 아닙니다.
