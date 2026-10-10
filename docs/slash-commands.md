# Discord slash command interface

현재 서버·채널·사용자를 대상으로 하는 즉시 조작은 Discord native slash command로 제공하고,
전체 조회·검색·편집은 Dashboard를 사용합니다.
`히나야 /메모`, `히나야 /기억`, `히나야 /이모지 ...` 같은 prefix+slash 메시지 명령은
production entrypoint에서 더 이상 해석하지 않습니다. 일반 대화 호출은 기존처럼 `히나야`, 멘션,
답장 핑을 사용합니다.

비전 기능은 별도 slash command가 아니라 일반 대화 호출의 입력 확장입니다. 현재 호출 메시지에 포함된
지원 이미지 첨부·커스텀 이모지·래스터 스티커를 일반 질문과 함께 해석할 수 있습니다. 과거 이미지나
답장 대상의 이미지는 자동으로 가져오지 않습니다. 자세한 범위는 [`vision-input.md`](vision-input.md)를
참고하세요.

## 자동 장기 기억

`/memory`는 대화에서 자동으로 쌓이는 기록·요약만 관리합니다. 사용자가 직접 저장하는 메모는 아래
`/note` 그룹으로 분리되어 있습니다.

| 명령 | 기능 |
| --- | --- |
| `/memory show` | 현재 채널에서 자동으로 요약된 내 장기 기억 확인 |
| `/memory clear confirm:true` | 해당 서버 또는 DM에서 자동으로 쌓인 내 대화 기록·요약 삭제 |

`/memory clear`는 직접 저장한 `/note` 메모와 최근 채널 대화 문맥을 삭제하지 않습니다.

## 수동 메모

`/note`는 사용자가 명시적으로 저장하는 지속 메모를 관리합니다. 자동 장기 기억의 읽기·쓰기 모드와
독립적으로 유지됩니다.

| 명령 | 기능 |
| --- | --- |
| `/note show scope:me` | 같은 서버 또는 DM에서 사용할 내 메모 확인 |
| `/note set text:<내용> scope:me` | 내 메모 저장·교체 |
| `/note clear scope:me` | 내 메모 삭제 |
| `/note show scope:server` | 현재 서버 공통 메모 확인 |
| `/note set text:<내용> scope:server` | 서버 공통 메모 저장·교체. Discord `Manage Server` 권한 필요 |
| `/note clear scope:server` | 서버 공통 메모 삭제. Discord `Manage Server` 권한 필요 |

`scope`를 생략하면 `me`가 기본입니다. `server` 범위는 DM에서 사용할 수 없습니다. 자동 장기 기억을
`off` 또는 `write_only`로 설정해 자동 기억 읽기가 꺼진 경우에도 명시적으로 저장한 메모는 응답에
계속 참고됩니다. 다만 사용자가 질문 범위를 현재 채널로 명시한 경우에는 다른 채널·서버 범위의
참고 정보와 마찬가지로 제외됩니다.

## 봇 관리자 장기 기억 설정

다음 명령은 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자만 실행할 수 있습니다.

| 명령 | 기능 |
| --- | --- |
| `/memory mode` | 전역/서버/채널 자동 장기 기억 읽기·쓰기 모드 설정. 선택적 `channel`로 같은 서버의 다른 텍스트 채널/스레드 지정 |
| `/memory status` | 현재 채널의 전역 → 서버 → 채널 상속 체인과 최종 적용값 확인 |
| `/memory purge` | 현재 채널·같은 서버의 다른 채널 또는 현재 서버 전체의 자동 사용자 기억 삭제. **전역 삭제는 Discord에서 제공하지 않음** |

설정·삭제 명령의 `channel`은 생략하면 실행 중인 채널을 사용하며, 명시하면 같은 서버에서
조회 권한이 있는 텍스트 채널/스레드만 선택할 수 있습니다. `target:server/global`과
`channel`을 동시에 지정할 수는 없습니다. DM에서는 다른 채널 지정이 허용되지 않습니다.

`/memory mode` 사용 예시:

```text
/memory mode value:off
/memory mode value:off target:channel channel:#general
/memory mode value:read_only target:server
/memory mode value:off target:global
/memory mode value:inherit target:server
```

`target`은 `channel`(기본, 현재 채널), `server`(현재 서버), `global`(전역) 중에서
선택합니다. `value`는 `normal/read_only/write_only/off/inherit`이며,
`inherit`는 채널·서버에서만 가능하고 전역에서는 사용할 수 없습니다.
장기 기억 모드는 `channel → server → global → 기본(normal)` 순서로
우선 적용됩니다. 전역 값을 변경해도 서버·채널에 설정된 override는 유지됩니다.

`/memory purge`는 **채널 및 서버 대상만** 지원합니다. 실행 위치가 기본 채널이고,
같은 서버의 다른 텍스트 채널·스레드를 `channel`에서 선택할 수 있습니다.

```text
/memory purge target:channel confirm:true
/memory purge target:channel channel:#general confirm:true
/memory purge target:server confirm:true
```

`target:server`는 **현재 서버의 모든 채널·모든 사용자**의 자동 기억을 삭제합니다.
삭제 범위를 반드시 확인하고 실행해 주세요. `confirm:true`를 지정하지 않으면
삭제하지 않고 대상을 안내합니다. **`target:global`은 Discord에서 제공하지 않습니다.**
모든 서버·DM의 자동 기억을 삭제하는 전역 purge는 사고 방지를 위해 Dashboard의
별도 관리 기능에서만 수행할 수 있습니다.

현재 위치의 상속 경로는 `/memory status`로 확인할 수 있습니다. 여러 서버·채널의 직접 설정과
전체 상속 결과는 Dashboard `/state`에서 확인합니다.

`/memory purge`는 자동 대화 기록·요약·공유 요약만 범위에 맞게 삭제합니다. 개인/서버 수동 메모와
memory/chatlog 설정 자체는 유지합니다.

## 런타임 설정

`/config` 그룹은 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자 전용입니다.
서버 내의 빠른 채널 설정과 긴급 privacy 변경만 Discord에서 제공하며,
다른 런타임 설정의 목록 조회·편집·override 초기화는 Dashboard `/admin/runtime`에서 합니다.

| 명령 | 기능 |
| --- | --- |
| `/config privacy value:<정책>` | 외부 LLM으로 보낼 대화 문맥의 최종 프라이버시 경계 설정 |
| `/config always-reply enable [channel]` | 현재/선택 채널의 자동 응답 켜기 |
| `/config always-reply disable [channel]` | 현재/선택 채널의 자동 응답 끄기 |
| `/config always-reply status [channel]` | 해당 채널의 자동 응답 상태와 설정 출처 확인 |

```text
/config always-reply enable
/config always-reply status
/config always-reply disable channel:#general
```

`channel`을 생략하면 현재 채널, 선택하면 **같은 서버에서 조회 가능한**
텍스트 채널이나 스레드를 대상으로 합니다. DM이나 다른 서버 채널은 지정할 수 없습니다.
이 명령은 `ALWAYS_REPLY_CHANNEL_IDS`의 **effective 목록에서 해당 채널 하나만
추가·삭제**하며 다른 등록 채널은 보존합니다. 이미 원하는 상태라면 새 DB override를
만들거나 recent buffer를 비우지 않습니다.

변경 시에는 SQLite override에 즉시 저장되고, direct-trigger 판정이 바뀌므로
기존 recent buffer와 hydration 상태를 초기화합니다. 처음 상태가 startup `.env`에서
온 경우에도 하나를 삭제하면 나머지 채널을 포함한 새 목록이 DB override가 되며
재시작 후에도 유지됩니다. 채널 등록은 최대 100개입니다.

Dashboard에서는 같은 `ALWAYS_REPLY_CHANNEL_IDS` 목록과 기타 모든 runtime 설정을
계속 편집할 수 있습니다. 두 인터페이스 모두 동일한 런타임 검증·저장 경로를 사용합니다.
설정값의 우선순위는 **SQLite override → 시작 시 `.env.local`/`.env` → 코드 기본값**이고,
Dashboard에서 DB override를 reset하면 시작 시 기본값으로 돌아갑니다.
`EXTERNAL_CONTEXT_POLICY`는 Discord의 `/config privacy`에서도 즉시 변경 가능합니다.

`ALWAYS_REPLY_CHANNEL_IDS` 지정 채널에서는 사람의 일반 메시지도 직접 대화 턴으로 취급하지만,
다른 봇은 호출어 또는 멘션/답장 핑이 있을 때만 응답합니다.

### 외부 모델 전송 경계

`EXTERNAL_CONTEXT_POLICY`는 `/chatlog mode`와 별개인 **최종 외부 전송 정책**입니다. chatlog mode는
로컬 recent buffer에 어떤 채널 대화를 모아 사용할지 정하고, 이 설정은 수집·라우팅된 데이터 중
무엇이 실제 LLM provider 요청에 직렬화될 수 있는지를 마지막 단계에서 다시 제한합니다.

- `bot_interactions_only` (기본): 현재 채널에서 히나가 직접 참여한 대화만 외부 대화 문맥으로
  허용합니다.
- `full`: 라우팅에서 허용한 주변 문맥까지 외부 모델에 제공할 수 있습니다. 신뢰하는 provider에서
  필요한 경우에만 명시적으로 선택합니다.

`bot_interactions_only`에서는 현재 사용자의 현재 발화와 허용된 개인 기억뿐 아니라, 같은 채널에서
어떤 사용자든 히나를 직접 호출한 발화와 히나가 보낸 답변을 함께 사용할 수 있습니다. 같은 채널을
'히나에게 보낸 메시지 + 히나가 보낸 메시지'만 보이는 공유 bot conversation space로 취급하는
정책입니다. 대상 사용자 history 조회도 같은 채널에서 대상 사용자가 히나를 직접 호출한 발언만
허용합니다. 반면 일반 채널 잡담과 일반 발언 target history, 다른 사용자의 공개 기억, 서버
공통 메모는 외부 provider 요청에서 제외됩니다. 명시적 Discord reply는 **현재 호출자가 자기 자신의
과거 메시지를 직접 선택한 경우에만** reply 원문을 허용하며, 제3자가 작성한 일반 reply 대상 원문은
제외합니다.

엄격 모드의 대상 사용자 history는 직접 호출 여부를 확인할 수 있는 행만 수집·전송하고,
cross-user public memory 조회는 막습니다.
또한 최종 request assembly 직전에 같은 정책을 다시 적용하므로, 앞 단계에서 더 넓은 데이터가
실수로 남아 있더라도 provider 요청에는 포함되지 않도록 fail-closed 방식으로 동작합니다.
`/config privacy value:bot_interactions_only` 또는 `value:full`로 즉시 변경할 수 있고,
`value:startup`은 DB override를 삭제해 `.env` 또는 코드 기본값으로 돌아갑니다. 정책을 바꾸면
기존 recent buffer와 hydration 상태도 즉시 전부 비워, 더 넓은 정책에서 모은 임시 문맥이 남아
있지 않게 합니다.

## 최근 채널 대화 문맥

`/chatlog` 그룹 전체는 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자만 실행할 수 있습니다.

| 명령 | 기능 |
| --- | --- |
| `/chatlog mode value:<all|direct|off|inherit>` | 전역/서버/채널의 최근 채널 문맥 수집·사용 범위 설정. 선택적 `channel`로 같은 서버의 다른 텍스트 채널/스레드 지정 |
| `/chatlog status` | 현재 채널의 상속 체인과 최종 적용값 확인 |
| `/chatlog clear` | 현재 채널의 메모리 내 최근 대화 문맥 비우기 |

`/chatlog mode` 사용 예시:

```text
/chatlog mode value:direct
/chatlog mode value:direct target:channel channel:#general
/chatlog mode value:off target:server
/chatlog mode value:direct target:global
/chatlog mode value:inherit target:channel
```

`target`은 `channel`(기본, 현재 채널), `server`(현재 서버), `global`(전역)이고,
선택적 `channel`은 `target:channel`일 때 같은 서버의 다른 텍스트 채널/스레드만
지정할 수 있습니다. `inherit`는 채널·서버에서만 가능하며, 전역 기본값에서는
사용할 수 없습니다. 전역 값을 바꿔도 기존 서버·채널 override가 우선합니다.
DM에서는 최근 **서버 채널** 대화 문맥을 사용하지 않습니다.

`/chatlog mode`는 예전의 on/off와 capture 설정을 하나의 정책으로 합칩니다.

- `all`: 같은 채널의 일반 대화까지 recent context에 수집·사용합니다.
- `direct`: 사용자가 `히나야`·멘션·답장 핑 등으로 히나를 직접 호출한 대화 중심으로만
  recent context를 수집·사용합니다.
- `off`: 최근 채널 대화 문맥을 수집하거나 사용하지 않습니다.
- `inherit`: 서버 또는 채널에서 상위 범위의 설정을 따릅니다. 전역에서는 사용할 수 없습니다.

상속 우선순위는 `channel → server → global → 기본(all)`입니다. 예전 DB에 `mode(on/off)`와
`capture(all/direct)`가 따로 저장되어 있으면 최초 명령 그룹 초기화 때 현재 effective 동작을 보존하는
형태로 통합합니다. 여러 서버·채널의 직접 설정과 최종 적용값 목록은 Dashboard `/state`에서 확인합니다.

`all/direct/off/inherit` 중 어떤 실질적인 정책 변경이든 해당 범위의 메모리 내 recent buffer와
hydration 상태를 즉시 비웁니다. 따라서 `all → direct`에서 넓게 수집한 문맥이 남지 않고,
`direct → all`에서도 다음 호출 때 현재 정책으로 필요한 history backfill을 다시 수행할 수 있습니다.
`direct` 상태에서 backfill하면 최근 TTL 범위의 직접 호출 사용자 발화와 과거 히나 답변을 다시
채웁니다. 과거 히나 답변의 정확한 대상 사용자는 복구할 수 없지만, 히나가 참여한 같은 채널의 공유
대화로는 안전하게 사용할 수 있습니다.

대상 사용자 문맥 조회 깊이는 관리 설정이 아니라 현재 요청의 의도에 따라 내부에서 결정합니다.
`@사용자 아까 뭐라고 했어?` 같은 최근 발언 확인은 1일·최대 3개·1,200자의 basic 조회를,
`@사용자 어떤 사람 같아?` 또는 명시적인 채팅 기록 분석·요약은 7일·최대 8개·2,400자의 deep 조회를
사용합니다. 단순 멘션만으로는 별도 history 조회를 하지 않습니다.

조회 가능한 발언 범위는 별도의 정책 경계를 따릅니다. `full + mode=all`은 같은 채널의 일반 발언까지,
`full + mode=direct`와 `bot_interactions_only`는 그 사용자가 히나를 직접 호출한 발언만 대상으로
삼습니다. 각 결과에는 직접 호출 provenance를 보존하며, `bot_interactions_only`의 최종 egress
단계에서도 이 값이 명시적으로 참인 행만 허용합니다. `/chatlog mode off`에서는 Discord history
조회 자체를 수행하지 않습니다.

최근 채널 대화 문맥은 장기 기억과 별개의 TTL 기반 임시 버퍼이며 장기 요약에는 포함되지 않습니다.
현재 턴 이미지 원본도 이 recent buffer에 저장하지 않습니다.

## 이모지

`/emoji` 그룹 전체는 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자 전용입니다.

| 명령 | 기능 |
| --- | --- |
| `/emoji add` | 기존 서버 이모지 `source` 또는 이미지 `image` 중 하나를 지정해 등록 |
| `/emoji import` | 현재 서버의 이모지를 이름으로 찾아 여러 개 한꺼번에 등록 |
| `/emoji list` | 등록된 모델용 별칭·미리보기·사용 상황 확인 |
| `/emoji edit` | 등록된 별칭의 사용 상황 수정 |
| `/emoji remove` | 모델 사용 목록에서 제외. 원본 이모지는 삭제하지 않음 |

`/emoji add`의 `source`에는 커스텀 이모지 markup 또는 숫자 ID를 넣을 수 있습니다. `image`는
256 KiB 이하 PNG/GIF/JPEG/WebP를 받습니다. `source`와 `image`를 동시에 지정할 수 없습니다.

`/emoji import`의 `items`에는 줄마다 아래 형식으로 최대 20개를 입력합니다.

```text
hina_sleep | 졸리거나 잠이 올 때
hina_cry | 슬프거나 울고 싶을 때
hina_angry | 화가 나거나 짜증이 났을 때
```

각 줄의 왼쪽 이름은 현재 서버의 커스텀 이모지 이름과 정확히 같아야 하며, 그 이름이 모델이 사용할
alias가 됩니다. 오른쪽 설명은 모델이 해당 이모지를 사용할 상황으로 1~100자입니다. 일부 이모지를
찾지 못하거나 이미 등록된 경우 해당 항목만 실패하고 나머지는 계속 처리합니다.

`/emoji`로 등록된 **출력용 이모지 catalog**와 사용자가 현재 메시지에 넣은 **비전 입력용 커스텀
이모지**는 역할이 다릅니다. catalog는 alias/description을 모델에 제공해 히나가 답변에서 사용할
이모지를 고르게 하고, 현재 메시지에 실제로 포함된 커스텀 이모지는 비전 입력으로 전달해 외형을
해석할 수 있습니다.

## 동적 prompt / knowledge

동적 instruction과 runtime knowledge의 목록·검색 및 CRUD는 Dashboard `/admin/prompts`에서 관리합니다.
Discord `/instruction` 그룹과 `/knowledge`의 일반 조회·편집 명령은 등록하지 않습니다.

`/knowledge ingest`는 봇 관리자 전용으로 계속 제공합니다. 긴 조사 메모를 기존 knowledge와 조정해
사실/해석 항목으로 자동 반영하는 기능이며, Dashboard로 이전하는 후속 작업은 #333에서 진행합니다.

## 도움말

`/help`는 guild install과 user install 모두에서 사용할 수 있으며 서버, DM, private channel에
노출됩니다. User Install로 앱을 추가한 뒤 히나와 공통 서버가 없어 프로필을 찾기 어려운 경우,
App Launcher에서 `/help`를 실행한 뒤 도움말 메시지의 히나 프로필에서 **메시지 보내기**를 선택해
1:1 DM을 시작할 수 있습니다.

도움말은 이 DM 진입 방법을 먼저 안내한 뒤 현재 slash command 구조와 일반 대화 호출 방법을
보여줍니다. 현재 호출 메시지의 지원 이미지·커스텀 이모지·래스터 스티커를 읽을 수 있다는 점과
과거 이미지가 자동으로 제공되지 않는다는 경계도 함께 안내합니다.
