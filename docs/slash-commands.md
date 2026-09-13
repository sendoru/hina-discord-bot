# Discord slash command interface

운영 중 사용하는 관리·설정 명령은 Discord native slash command로 통일합니다.
`히나야 /메모`, `히나야 /기억`, `히나야 /이모지 ...` 같은 prefix+slash 메시지 명령은
production entrypoint에서 더 이상 해석하지 않습니다. 일반 대화 호출은 기존처럼 `히나야`, 멘션,
답장 핑을 사용합니다.

비전 기능은 별도 slash command가 아니라 일반 대화 호출의 입력 확장입니다. 현재 호출 메시지에 포함된
지원 이미지 첨부·커스텀 이모지·래스터 스티커를 일반 질문과 함께 해석할 수 있습니다. 과거 이미지나
답장 대상의 이미지는 자동으로 가져오지 않습니다. 자세한 범위는 [`vision-input.md`](vision-input.md)를
참고하세요.

## 일반 사용자 기억 명령

| 명령 | 기능 |
| --- | --- |
| `/memory show` | 현재 채널의 내 장기 요약과 개인 메모 확인 |
| `/memory note text:<내용>` | 같은 서버의 내 응답에 사용할 개인 메모 교체 |
| `/memory note-clear` | 개인 메모 삭제 |
| `/memory clear confirm:true` | 해당 서버 또는 DM에서의 내 대화 기록·자동 요약·개인 메모 삭제 |
| `/memory server-show` | 현재 서버 공통 메모 확인 |
| `/memory server-note text:<내용>` | 서버 공통 메모 교체. Discord `Manage Server` 권한 필요 |
| `/memory server-clear` | 서버 공통 메모 삭제. Discord `Manage Server` 권한 필요 |

`/memory clear`는 해당 사용자의 지속 장기 기억만 삭제하며 최근 채널 대화 문맥은 유지합니다.

장기 기억 최종 모드에서 쓰기가 꺼져 있으면 `/memory note`와 `/memory server-note`는 새 데이터를
저장하지 않습니다.

## 봇 관리자 장기 기억 설정

다음 명령은 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자만 실행할 수 있습니다.

| 명령 | 기능 |
| --- | --- |
| `/memory mode` | 전역/서버/채널 장기 기억 읽기·쓰기 모드 설정 |
| `/memory status` | 현재 채널의 최종 장기 기억 설정 확인 |
| `/memory overview` | 전체 서버/채널의 장기 기억 직접 설정과 상속 결과 확인 |
| `/memory purge` | 채널/서버/전역 범위의 사용자 장기 기억 초기화 |

`/memory purge`는 사용자 대화 기록·자동 요약·개인 메모를 범위에 맞게 삭제하지만 서버 공통 메모와
memory/chatlog 설정 자체는 유지합니다.

## 최근 채널 대화 문맥

`/chatlog` 그룹 전체는 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자만 실행할 수 있습니다.

| 명령 | 기능 |
| --- | --- |
| `/chatlog mode` | 전역/서버/채널 최근 채널 문맥 읽기 설정 |
| `/chatlog capture` | 전역/서버/채널에서 어떤 메시지를 recent context에 수집할지 설정 |
| `/chatlog status` | 현재 채널의 최종 chatlog on/off와 capture 범위 확인 |
| `/chatlog overview` | 전체 서버/채널의 직접 on/off 설정과 상속 결과 확인 |
| `/chatlog clear` | 현재 채널의 메모리 내 최근 대화 문맥 비우기 |

`/chatlog capture`의 기본값은 기존 동작과 호환되는 `all`입니다. `direct`를 선택하면 같은 채널의
일반 대화는 recent context에 넣지 않고, 사용자가 `히나야`·멘션·답장 핑 등으로 히나를 직접 호출한
메시지와 히나가 실제로 보낸 답변만 보관합니다. `all`/`direct` 모두 `global → server → channel`
순서로 override되며 `inherit`으로 상위 설정을 따를 수 있습니다.

capture 정책을 바꾸면 해당 범위의 메모리 내 recent buffer를 즉시 비워 이전의 더 넓은 문맥이 TTL
동안 남지 않게 합니다. 이후 필요한 history backfill도 현재 capture 정책을 적용합니다. 또한
`@사용자 어떻게 생각해?` 같은 대상 사용자 문맥 조회는 `direct` 모드에서 그 사용자가 과거에 히나를
직접 호출했던 메시지만 대상으로 삼습니다.

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

기존 `/instruction ...`, `/knowledge ...` 명령도 그대로 Discord slash command로 유지합니다.
두 그룹은 앱 소유자 또는 `BOT_ADMIN_IDS` 사용자 전용입니다.

## 도움말

`/help`는 현재 slash command 구조와 일반 대화 호출 방법을 간단히 보여줍니다. 비전 브랜치에서는
현재 호출 메시지의 지원 이미지·커스텀 이모지·래스터 스티커를 읽을 수 있다는 점과 과거 이미지가
자동으로 제공되지 않는다는 경계도 함께 안내합니다.
