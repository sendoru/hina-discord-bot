# Changelog

Notable user-facing, operator-facing, compatibility, and migration changes are tracked here.

## Unreleased

### Added

- tiered 개인 기억 선택(shadow 포함)에 핵심 프로필(Tp)과 화제 이월을 추가했습니다. 중요도 3인
  ambient 생활 기억(ongoing·recurring, 지금 유효)은 어휘 일치 없이도 `user_memory.profile`로 항상
  들어가며, 이해에만 쓰고 먼저 꺼내지 않도록 정책 문구로 제한합니다. 서버에서는 같은 공간 출처이고
  `audience=server`인 것만 넣습니다. 현재 화자가 이 채널·DM에서 30분 안에 보낸 직전 발화(최대 2개)의
  화제는 T2로 이어지며(contextual은 바로 앞 발화만, sensitive는 제외), cooldown의 직접 언급 예외도
  이 발화에 적용됩니다. `recall_memory` 도구 설명과 정책 문구에 사진·짧은 반응 사례를 추가했습니다.
- `PERSONAL_MEMORY_READ_MODE=shadow`가 답변 뒤 shadow 커버리지를 기록합니다. legacy 답변이 실제로
  쓴 기억(keywords 등장) 중 tiered 선택에 없던 것을 세어 `memory.shadow_coverage` 이벤트(개수와
  사진 턴·직전 본인 발화 표시만)와 trace의 Memory selection(놓친 항목 ID 링크)에 남기고,
  `hina-memory-report`가 `telemetry.memory_shadow_coverage`로 집계합니다. 기억이나 annotation에는
  아무것도 쓰지 않습니다.
- `hina-memory-report`에 `database.memory_annotations`(annotation 현황, usage_mode·importance·
  time_scope 분포, 핵심 프로필 후보 수)를 추가하고 리포트 버전을 3으로 올렸습니다.
- 대화 모델이 필요할 때만 현재 Discord 채널의 사용자 명단을 로컬 function tool로 조회할 수
  있습니다. 도구는 현재 채널을 볼 수 있는 사람의 server nickname, global name, username, user ID만
  반환하며, 봇은 이를 위해 Server Members Intent를 항상 요청합니다.
- Configurable guild channels that treat every human message as a direct bot turn, while keeping bot-authored messages behind explicit triggers.
- Formal release-version and compatibility lifecycle policy.
- CI validation for package version format and release-tag consistency.

### Changed

- `RAG_MODE`의 시작 기본값을 `v1`에서 `full`로 바꿨습니다(#463). 운영에서 쓰는 모드와 맞춘 것이며, DB
  override가 있으면 그 값을 그대로 씁니다. `v1`은 정적 lore가 커질 때의 비상 경로로 남습니다.
- 사용자 이름 해석은 별도 preflight resolver 대신 일반 답변의 `get_current_channel_members`
  도구 호출로 처리합니다. 사용자 명단은 매 요청에 선제적으로 포함하지 않고 모델이 실제로 요청한
  턴에만 provider로 전달됩니다.
- Clarified the distinct roles of `app_version`, `build_revision`, and `runtime_id`.

### Fixed

- `OUTPUT_REPAIR_MODE=active`에서 교정 요청이 시간 초과·실패해 원문이 그대로 나갈 때도, 일반
  대화에 붙은 Discord snowflake형 숫자 마커(`[1565771137684869141]` 등, 17~32자리)를 결정적 규칙으로 지웁니다
  (#454). 짧은 숫자, 코드, URL, ID를 설명하는 문장은 보존하며 `off`/`shadow`의 동작은 그대로입니다.

### Removed

- 예전 DB 변환 도구를 제거했습니다(#463): `hina-upgrade-legacy-db`(비버전 DB 변환), `hina-migrate-runtime`(예전
  JSON instruction/knowledge 이전, `INSTRUCTION_PATH`/`RUNTIME_LORE_PATH`/`CONTEXT_PATH`), `hina-migrate-reset-epochs`
  (epoch 테이블 단독 정리). 일반 준비는 예전 형식 DB를 계속 거부하며, 오류 메시지가 #463 이전 버전의 변환기를
  안내합니다. epoch 테이블 정리는 `hina-reset-conversation`이 계속 트랜잭션 안에서 수행합니다.
- Retrieval v2를 제거했습니다(#463): `RAG_MODE=shadow`/`v2`, hybrid·semantic·ambient retrieval, exact
  relationship grounder, Gemini 임베딩 어댑터, calibration 도구, `RETRIEVAL_V2_*` 설정과 `RETRIEVAL_V2_MODE`
  별칭, `retrieval.v2` 텔레메트리, 응답 요청의 빈 `character_insights`/`reaction_guides` 필드. 운영 `.env`에
  `RAG_MODE=shadow`/`v2`가 있으면 시작을 거부하므로 지우거나 `full`/`v1`/`off`로 바꿔야 합니다. DB에 남은 예전
  런타임 값은 읽을 때 무시됩니다. 웹 검색 전 로컬 근거 판단은 그대로입니다.
- 실효성이 낮았던 exact/fuzzy/semantic identity resolver, historical alias fallback, `resolved_identities`
  context와 전용 Identity dashboard를 제거했습니다.

- Obsolete Discord-client implicit LLM construction and standalone module entrypoints; `hina-bot`
  now has a single composition root in `discord.runtime_entry`.

- Unreleased internal compatibility wrappers for web-search routing and an ignored structured-memory
  policy argument. Runtime deployment compatibility and persistent-data migration paths are unchanged.
- The internal `DashboardService` compatibility facade; dashboard routes now receive domain-specific
  read services directly.
- Shadowed legacy `answer`, `summarize`, and `summarize_shared` implementations from the base LLM;
  current request assembly and memory-summary layers remain the active implementations.
- The base LLM's OpenAI-only implicit client fallback; provider-aware composition now owns client creation.

## 0.1.0

- Existing package-version baseline from before formal release tracking.
- Earlier project history is intentionally not reconstructed release-by-release.
