# Discord slash command interface

운영 중 사용하는 관리·설정 명령은 Discord native slash command로 통일합니다.
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
| `/memory mode` | 전역/서버/채널 자동 장기 기억 읽기·쓰기 모드 설정 |
| `/memory status` | 현재 채널의 전역 → 서버 → 채널 상속 체인과 최종 적용값 확인 |
| `/memory overview` | 기본적으로 직접 override된 범위만 표시. `view:전체 상속 결과`로 전체 확인 |
| `/memory purge` | 채널/서버/전역 범위의 자동 사용자 기억 초기화 |

`/memory overview`의 기본 화면에서는 상속만 받는 서버·채널을 숨깁니다. 현재 위치의 자세한 상속
경로가 필요하면 `/memory status`, 모든 범위의 계산 결과가 필요하면 overview의
`전체 상속 결과` 보기를 사용합니다.

`/memory purge`는 자동 대화 기록·요약·공유 요약만 범위에 맞게 삭제합니다. 개인/서버 수동 메모와
memory/chatlog 설정 자체는 유지합니다.

## 런타임 설정

`/config` 그룹 전체는 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자 전용입니다. 아래 설정은 봇을
재시작하지 않고 바꿀 수 있으며 SQLite에 override로 저장됩니다.

| 명령 | 기능 |
| --- | --- |
| `/config status` | 현재 effective 값과 DB override 여부 확인 |
| `/config privacy value:<정책>` | 외부 LLM으로 보낼 대화 문맥의 최종 프라이버시 경계 설정 |
| `/config set key:<설정> value:<값>` | 일반 runtime DB override 저장 후 즉시 적용 |
| `/config reset key:<설정>` | 일반 runtime DB override 삭제 후 시작 시 값으로 복귀 |

`/config set/reset` 대상은 `CALL_PREFIXES`, `DM_ALWAYS_REPLY`, `PUBLIC_SERVER_MEMORY_IN_DM`,
`CHAT_WEB_SEARCH`, `COMMUNITY_LORE`, `MAX_OUTPUT_TOKENS`, `CHANNEL_CONTEXT_CHARS`,
`HISTORY_MAX_CHARS`, `LORE_MAX_ITEMS`, `LORE_MAX_CHARS`, `RUNTIME_DEFAULT_LOCATION`입니다.
`EXTERNAL_CONTEXT_POLICY`는 프라이버시 경계라는 의미가 드러나도록 `/config privacy`에서 별도로
관리합니다.

우선순위는 **SQLite override → 시작 시 `.env.local`/`.env` 값 → 코드 기본값**입니다. 따라서 env의
값은 여전히 배포 기본값으로 사용할 수 있고, `/config reset`은 해당 DB override만 지웁니다.
`RUNTIME_DEFAULT_LOCATION`을 명시적으로 비우려면 `/config set`의 value에 `none`을 사용합니다.
`CALL_PREFIXES`는 쉼표 구분 문자열, 불리언 값은 `on/off` 또는 `true/false`를 받습니다.

### 외부 모델 전송 경계

`EXTERNAL_CONTEXT_POLICY`는 `/chatlog mode`와 별개인 **최종 외부 전송 정책**입니다. chatlog mode는
로컬 recent buffer에 어떤 채널 대화를 모아 사용할지 정하고, 이 설정은 수집·라우팅된 데이터 중
무엇이 실제 LLM provider 요청에 직렬화될 수 있는지를 마지막 단계에서 다시 제한합니다.

- `direct_party_only` (기본): 현재 호출자와 히나 사이의 직접 대화만 외부 대화 문맥으로 허용합니다.
- `full`: 라우팅에서 허용한 주변 문맥까지 외부 모델에 제공할 수 있습니다. 신뢰하는 provider에서
  필요한 경우에만 명시적으로 선택합니다.

`direct_party_only`에서는 현재 사용자의 현재 발화, 과거에 히나를 직접 호출한 발화, 그 사용자에게
히나가 직접 보낸 답변, 현재 사용자의 수동 메모·자동 장기 기억·DM 대화·본인 소유 공개 기억은
사용할 수 있습니다. 반면 같은 사용자의 일반 채널 잡담, 다른 사용자의 직접 호출, 다른 사용자에게
보낸 히나 답변, 대상 사용자 7일 history scan, 다른 사용자의 공개 기억, 서버 공통 메모는 외부
provider 요청에서 제외됩니다. 명시적 Discord reply는 **현재 호출자가 자기 자신의 과거 메시지를
직접 선택한 경우에만** reply 원문을 허용하며, 제3자가 작성한 reply 대상 원문은 제외합니다.

엄격 모드에서는 대상 사용자 history 수집 자체도 생략하고 cross-user public memory 조회도 막습니다.
또한 최종 request assembly 직전에 같은 정책을 다시 적용하므로, 앞 단계에서 더 넓은 데이터가
실수로 남아 있더라도 provider 요청에는 포함되지 않도록 fail-closed 방식으로 동작합니다.
`/config privacy value:direct_party_only` 또는 `value:full`로 즉시 변경할 수 있고,
`value:startup`은 DB override를 삭제해 `.env` 또는 코드 기본값으로 돌아갑니다. 정책을 바꾸면
기존 recent buffer와 hydration 상태도 즉시 전부 비워, 더 넓은 정책에서 모은 임시 문맥이 남아
있지 않게 합니다.

## 최근 채널 대화 문맥

`/chatlog` 그룹 전체는 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자만 실행할 수 있습니다.

| 명령 | 기능 |
| --- | --- |
| `/chatlog mode value:<all|direct|off|inherit>` | 전역/서버/채널의 최근 채널 문맥 수집·사용 범위 설정 |
| `/chatlog status` | 현재 채널의 상속 체인과 최종 적용값 확인 |
| `/chatlog overview` | 기본적으로 직접 override된 범위만 표시. 필요하면 전체 상속 결과 확인 |
| `/chatlog clear` | 현재 채널의 메모리 내 최근 대화 문맥 비우기 |

`/chatlog mode`는 예전의 on/off와 capture 설정을 하나의 정책으로 합칩니다.

- `all`: 같은 채널의 일반 대화까지 recent context에 수집·사용합니다.
- `direct`: 사용자가 `히나야`·멘션·답장 핑 등으로 히나를 직접 호출한 대화 중심으로만
  recent context를 수집·사용합니다.
- `off`: 최근 채널 대화 문맥을 수집하거나 사용하지 않습니다.
- `inherit`: 서버 또는 채널에서 상위 범위의 설정을 따릅니다. 전역에서는 사용할 수 없습니다.

상속 우선순위는 `channel → server → global → 기본(all)`입니다. 예전 DB에 `mode(on/off)`와
`capture(all/direct)`가 따로 저장되어 있으면 최초 명령 그룹 초기화 때 현재 effective 동작을 보존하는
형태로 통합합니다. `/chatlog overview`도 통합된 직접값과 최종 적용값만 한 열씩 보여줍니다.

`all/direct/off/inherit` 중 어떤 실질적인 정책 변경이든 해당 범위의 메모리 내 recent buffer와
hydration 상태를 즉시 비웁니다. 따라서 `all → direct`에서 넓게 수집한 문맥이 남지 않고,
`direct → all`에서도 다음 호출 때 현재 정책으로 필요한 history backfill을 다시 수행할 수 있습니다.
`direct` 상태에서 backfill하면 최근 TTL 범위의 직접 호출 사용자 발화만 다시 채우며, 과거 히나
답변은 답변 대상 사용자를 안전하게 복구할 수 없어 hydration하지 않습니다.

`@사용자 어떻게 생각해?` 같은 대상 사용자 문맥 조회는 `EXTERNAL_CONTEXT_POLICY=full`일 때 chatlog
정책을 따릅니다. `mode=direct`라면 그 사용자가 과거에 히나를 직접 호출했던 메시지만 대상으로
삼습니다. 반대로 기본 `direct_party_only`에서는 이 대상 사용자 history scan을 아예 수행하지 않습니다.

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

`/instruction`, `/knowledge` 그룹은 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자 전용입니다. 긴 본문을
Discord 메시지 폭에 맞춰 잘라 보여주지 않고 UTF-8 텍스트 파일로 첨부합니다.

| 명령 | 기능 |
| --- | --- |
| `/instruction list` | 검색·정렬된 instruction 전체를 `instructions*.txt`로 받기 |
| `/instruction show identifier:<ID>` | instruction 한 항목을 `instruction-<ID>.txt`로 받기 |
| `/instruction add/edit/enable/disable/remove` | 동적 캐릭터 보조 지침 관리 |
| `/knowledge list` | 검색·정렬된 knowledge 전체와 메타데이터를 `knowledge*.txt`로 받기 |
| `/knowledge show identifier:<ID>` | knowledge 한 항목을 `knowledge-<ID>.txt`로 받기 |
| `/knowledge ingest` | 조사 메모를 사실/해석 knowledge로 분해·조정해 반영 |
| `/knowledge enable/disable/remove` | runtime knowledge 상태·항목 관리 |

첨부 파일의 `created_at`/`updated_at`은 Discord 전용 `<t:...>` markup이 아니라 사람이 읽을 수 있는
UTC 시각으로 기록됩니다. `knowledge` 파일에는 종류, ON/OFF, awareness, timeline, subjects,
keywords와 본문 전체가 포함됩니다.

## 도움말

`/help`는 현재 slash command 구조와 일반 대화 호출 방법을 간단히 보여줍니다. 비전 브랜치에서는
현재 호출 메시지의 지원 이미지·커스텀 이모지·래스터 스티커를 읽을 수 있다는 점과 과거 이미지가
자동으로 제공되지 않는다는 경계도 함께 안내합니다.