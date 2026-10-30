# 장기기억 v2 선택 fixture

`selection_cases.json`은 #449 §7·§8.5의 계층형 선택이 **무엇을 넣고 무엇을 빼야 하는지**를
데이터로 고정한 사례 모음입니다(#450).

- **지금(P0)**: 선택 로직이 아직 없으므로 `tests/ai/test_memory_v2_fixtures.py`가 형식과
  교차 참조만 검증합니다.
- **P2**: 선택 엔진이 들어오면 같은 파일을 읽어 실제로 실행하는 테스트를 추가합니다. 결정적
  테스트이므로 LLM 호출 없이 CI에서 돌아야 합니다.

## 형식

최상위 `defaults`의 값을 각 사례·항목에 얕게 병합합니다. 사례에는 기본값과 다른 값만 적습니다.

- **식별자는 모두 기호입니다.** 사용자 `u1`, 서버 `g1`, 채널 `c1`을 쓰고, 실제 Discord ID는
  쓰지 않습니다(테스트가 15자리 이상 숫자를 거부).
- **공간 표기**: `dm` 또는 `<guild>:<channel>:<public|private>` (예: `g1:c1:public`).
  `scope`는 현재 대화 공간, 항목의 `origin`은 형성된 공간입니다.
- **`memories`**: 개인 기억. `annotation`은 #449 §4.1 필드와 같은 이름을 씁니다.
  - `annotated: false`: annotation 행이 없는 레거시 항목입니다.
  - `revision`이 `annotation.item_revision`보다 크면 낡은 annotation입니다(원칙 6).
  - `cluster`, `superseded_by`는 같은 사례 안의 `ref`를 가리킵니다.
  - `proactive_count`는 상한 2입니다. 선제 안부가 실제로 쓰였으면 2로 올려 소진 처리합니다.
- **`server_memories`**: 커뮤니티 기억(§4.3). `space`는 `guild` 또는 `channel:<channel>`입니다.
  쓰기 시점 불변식(§8.4: `directive`는 admin, `ambient`는 admin·corroborated·observed)을
  fixture도 지킵니다.
- **`context`**: 선택기에 주어지는 턴 정보입니다.
  - `hours_since_last_turn_any_space`: 모든 공간을 통틀어 마지막 턴 이후 시간(T1 세션 판정)
  - `mentions`, `reply_to_user`, `thread_participants`: 멤버 공개 프로필 관련성 판정(§8.5)
  - `classifier_codes`: 대화 성격 힌트(예: `debugging`)
  - `authorized_reference_gated`: 이번 턴 T4가 승인한 항목
  - `member_opt_out`, `departed_members`: 공유 옵트아웃, 서버를 떠난 멤버
  - `reader_denied_channels`: 현재 화자가 지금 볼 수 없는 채널(원 채널 열람 권한 실시간 확인)
- **`settings`**: `MEMORY_SESSION_FOLLOWUP`(`off|dm|all`), `external_context_policy`
  (`full|bot_interactions_only`). 기본값은 코드 기본값과 같은 `dm`, `bot_interactions_only`입니다.
- **`server_note`**: 관리자 서버 메모 본문(선택). 서버 기억 S0 계층 판정에 씁니다.

## 기대값(`expect`)

| 키 | 의미 |
|---|---|
| `include` / `exclude` | 응답 컨텍스트에 들어가야 하는 / 들어가면 안 되는 항목 |
| `exactly_one_of` | 묶음 중 정확히 하나만 들어가야 함(중복 억제) |
| `placement` | 렌더링 위치: `directives`, `quiet_boundaries`, `notes`, `members`, `server_directives`, `server_notes` |
| `tier` | 선택 계층: `T0`~`T2`, `S0`~`S2` |
| `render_status` | 렌더링되는 유효 상태(`current`, `unverified`, `upcoming`, `just_passed`, `past` 등) |
| `recall` | `recall_memory(query, scope)` 도구 결과에 대한 include/exclude/render_status |
| `relationship_profile_axes` | 수치 관계 프로필에 나타나야 하는 축 |
| `server_note` | 사례의 `server_note`가 응답 컨텍스트에 들어가는지(`included`/`excluded`) |

`include`에 없다고 해서 금지라는 뜻은 아닙니다. 금지는 `exclude`로만 표현합니다.

## 외부 전송 정책 (결정 17)

- **기본 정책 사례:** 서버 기억과 멤버 공개 프로필 사례는 기본 정책인 `bot_interactions_only`에서
  실행합니다.
  - #449 결정 17에 따라 이 정책에서도 히나 호출에서 나온 서버 범위 장기 기억과 `server_note`를
    허용합니다.
  - `full`에서도 결과가 같아야 하며, `member_profile_same_under_full_policy`가 이를 확인합니다.
- **원 채널 열람 권한:** 정책과 무관하게, 현재 화자가 원 채널을 지금 볼 수 없으면 해당 서버 범위
  기억은 들어가지 않습니다(`reader_denied_channels`).
