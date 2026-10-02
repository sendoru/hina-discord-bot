# Dashboard P1·P2 구현 및 검증 기록

2026-10-02 (Asia/Seoul). `feat/state-scope-presentation`의 `bc081c6`에서
`/tmp/hina-dashboard-ui-review` worktree와 `feat/dashboard-ui-review` stacked branch를 만들었습니다.
읽기 전용 repository, 런타임 의미, 보존 정책을 유지하고 P3 CSS 재정리는 제외했습니다.

## 목적별 커밋

| 커밋 | 변경과 검증 |
|---|---|
| `511cdbb` | 개요의 관측 값·부분 합계·미관측과 집계 출처 표시. 실제 0·누락·usage-only 회귀 검증. |
| `bf8b29c` | 메모리 실패 카드의 실제 조건 연결, 실패 횟수·trace 수 구분. 문자열 검색과 결과가 다른 사례 검증. |
| `6bc3436` | 공통 날짜·confidence·범위 순서 검증. 오류 입력 유지와 잘못된 SQL 조회 차단 검증. |
| `a8a63b3` | 정규화된 적용 조건과 복합 범위 해제, 추가 목록의 칩. 개별 ID·Scope 해제 검증. |
| `69d2f23` | 목록 조건·페이지 복귀 보존, 정확한 내부 경로 허용. 외부 URL·다른 경로·제어 문자 회귀 검증. |
| `1712b2b` | HTML 404·422 오류와 복귀 안내. JSON 클라이언트·healthz 유지 검증. |
| `30b3940` | 필터 label·현재 메뉴·본문 바로가기·표 포커스. 렌더링된 모든 입력의 label 연결 검증. |
| `a5987fa` | Identity 전체 해시 disclosure. 전체 값 보존과 원문 참조 비표시 검증. |
| `d46e857` | 보조 텍스트 크기·대비와 한국어 안내, 미관측·Unknown 구분. 전체 대시보드 회귀 검증. |
| `3b4bed0` | 시간대·초·소수 초를 보존하는 날짜 표시와 필드 오류 배치. 정확한 시각 roundtrip 검증. |
| `b8b8fd2` | Trace 구간 탐색, 모바일 운영 목록, 분석 표 가로 스크롤. 합성 데이터 Playwright 검증 스크립트 추가. |
| `e3a847c` | 보존·재시도·관계 접근·epoch·최근 관측 안내 통일. 표시 문구 회귀 검증. |
| `00a93d2` | 밀리초까지 native 날짜 컨트롤 유지, 더 정밀한 값의 텍스트 보존. Chromium 실제 입력값 검증. |

각 기능 커밋에 관련 문서와 테스트를 포함했으며, 마지막 문서 커밋에 이 실행 기록을 추가했습니다.

## 실행 결과

| 검사 | 결과 |
|---|---|
| 기준 브랜치 `pytest tests/dashboard -q` | 93 passed |
| 최종 `pytest tests/dashboard -q` | 115 passed |
| 최종 `ruff check .` | 통과 |
| `git diff --check` | 통과 |
| Playwright + 시스템 Chromium 151.0.7922.173 | 1440·768·390px에서 54개 화면 통과 |

FastAPI TestClient에서 Starlette/httpx 사용 중단 예정 경고 1개가 발생하며 테스트 실패는 없습니다.
Playwright 브라우저 다운로드는 환경 정책에 차단되어 설치된 시스템 Chromium을 사용했습니다.

검증 스크립트는 다음을 확인합니다.

- 긴 한국어 본문과 긴 trace·해시 식별자, 빈 결과·부분 관측·실제 0, HTML 오류 화면.
- 페이지 전체 가로 넘침 없음, 모든 입력의 label 연결, 모바일 카드·메타데이터 펼치기.
- 오류 입력 유지, 시간대·초·소수 초 표시, Scope와 종속 조건의 동시 해제, 목록 페이지 복귀.
- 키보드 본문 이동, Scope 라디오 전환과 입력 활성화, 전체 해시 펼치기, 넓은 표 가로 스크롤.
- 모바일 메뉴와 상세 구간 링크, 브라우저 JavaScript 오류 없음.

최종 합성 데이터·스크린샷·결과 JSON은 `/tmp/hina-dashboard-browser-dn5m786u`에 있습니다.
브라우저 재현 명령은 [dashboard.md](dashboard.md)에 기록했습니다. 운영 데이터는 사용하지 않았습니다.

## 검증 한계

실제 모바일 기기와 스크린리더는 검증하지 않았습니다. 터치 검증은 Chromium 에뮬레이션이며,
스크린리더를 위한 label·ARIA·키보드 동작은 자동화와 렌더링 결과로 확인했습니다.
브라우저 픽스처와 스크린샷은 커밋하지 않습니다.
