# Vision input

이 문서는 `feature/vision-input` 브랜치의 1차 비전 입력 범위를 정리합니다. 현재 단계에서는 기능을
더 넓히기보다, 이미 구현한 범위를 실제 Discord에서 검증하고 버그를 수정하는 것을 우선합니다.

## 지원 범위

봇이 **현재 호출 메시지**에서 직접 수집한 시각 입력만 모델에 전달합니다.

| 입력 | 현재 지원 |
| --- | --- |
| 이미지 첨부 | PNG, JPEG, GIF, WebP |
| Discord 커스텀 이모지 | 정적 PNG, animated GIF |
| Discord 스티커 | 래스터 형식. Lottie 제외 |
| 이미지 없이 텍스트만 | 기존 동작 유지 |
| 호출어/멘션 + 이미지만 | 지원. 내부적으로 이미지 확인용 짧은 텍스트를 보충 |

지원 여부는 파일 확장자보다 실제 바이트 signature를 기준으로 확인합니다. 이미지가 아닌 첨부파일은
비전 입력으로 보내지 않습니다.

현재 count quota는 source별로 독립적으로 적용합니다. 기본값은 다음과 같습니다.

| source | 환경 변수 | 기본값 |
| --- | --- | ---: |
| 이미지 첨부 | `VISION_MAX_ATTACHMENTS` | 4 |
| 커스텀 이모지 | `VISION_MAX_EMOJIS` | 12 |
| 래스터 스티커 | `VISION_MAX_STICKERS` | 8 |

각 값은 0~32이고 세 값의 합계는 32 이하여야 합니다. 0으로 두면 해당 source를 비전 입력에서
비활성화합니다. 기본 설정에서는 한 호출에 최대 24개의 시각 입력이 가능하지만, 아래 byte budget도
동시에 적용됩니다.

- 개별 시각 입력 최대 5 MiB
- 한 호출의 시각 입력 합계 최대 12 MiB
- Discord CDN에서 이모지/스티커를 가져올 때 HTTP timeout 15초
- quota나 byte limit을 넘거나 읽지 못한 항목은 현재 턴의 비전 입력에서 제외

파일 byte 크기와 provider의 이미지 token 비용은 같은 값이 아닙니다. 이미지 token 계산은 선택한
provider/model과 이미지 크기·처리 방식에 따라 달라질 수 있습니다. source별 quota는 작은 Discord
이모지/스티커를 일반 첨부 이미지와 같은 count limit으로 잘라 버리지 않기 위한 운영 제한입니다.

## 의도적으로 지원하지 않는 것

1차 구현에서는 다음 기능을 넣지 않습니다.

- 과거 채널 메시지의 이미지 재조회
- 답장 대상 메시지의 이미지 자동 조회
- `아까 그 사진`처럼 이전 턴 이미지를 다시 찾는 기능
- 이미지 원본이나 base64를 SQLite 장기 기억 또는 recent chatlog에 저장
- 이미지 caption을 생성해 장기 기억에 자동 저장
- 일반 URL을 따라가 이미지로 해석
- PDF/문서 등 이미지가 아닌 파일 해석
- Lottie 스티커 렌더링
- 이미지 생성·편집
- 이미지 캐시나 커스텀 이모지별 사전 caption 캐시

GIF는 `image/gif` 그대로 provider에 전달합니다. 애니메이션의 여러 프레임을 얼마나 활용하는지는
선택한 모델/provider의 이미지 이해 동작에 따르며, 봇 자체에서 프레임 추출은 하지 않습니다.

## 데이터 흐름

비전 입력은 information routing과 별개의 입력 modality로 취급합니다.

```text
Discord message
  ├─ text
  └─ current-turn visual inputs
       ├─ attachment
       ├─ custom emoji
       └─ raster sticker
            ↓
src/hina_bot/discord/vision.py
            ↓
VisualInput[] / CURRENT_VISUAL_INPUTS
            ↓
기존 information routing + chat LLM
            ↓
src/hina_bot/ai/vision.py
            ↓
provider adapter
  ├─ OpenAI
  ├─ Gemini
  └─ OpenRouter
```

`information_routing`은 여전히 memory/clock/local lore/web/general 중 어떤 사실 출처를 사용할지를
결정합니다. 비전은 여기에 새로운 route를 추가하지 않습니다. 따라서 예를 들어 이미지 안의 가게를
보고 `지금 열었어?`라고 묻는 요청은 이미지 입력과 기존 web route를 동시에 사용할 수 있습니다.

## 현재 턴에서만 사용하는 이유

이미지 bytes는 `ContextVar`를 통해 현재 `answer()` 요청에만 전달합니다. 장기 기억 요약과 공유 기억
요약에는 넣지 않습니다. 일반 텍스트 대화 저장 규칙은 기존과 같지만, 이미지 원본·base64·Discord CDN
URL은 기억용 데이터로 저장하지 않습니다.

이 경계는 비용과 보관 범위를 예측 가능하게 만들고, 최근 채팅의 오래된 이미지를 매 요청마다 다시
provider에 보내는 일을 막기 위한 것입니다.

## Provider 동작

### OpenAI

현재 user message에 `input_text`와 `input_image` block을 함께 넣습니다. 이미지 bytes는 data URL로
전달합니다.

### Gemini

공통 `input_image` block을 Gemini Interactions API의 `image` block으로 변환합니다. inline base64와
MIME type을 전달합니다.

### OpenRouter

OpenAI-compatible Responses 요청의 `input_image` 형식을 사용합니다.

중요한 점은 **adapter가 이미지 형식을 지원하는 것과 선택한 모델 자체가 vision을 지원하는 것은 별개**라는
것입니다. `LLM_MODEL`에는 이미지 입력을 받을 수 있는 모델을 사용해야 합니다. 모델별 실제 지원 범위와
과금은 해당 provider의 문서를 확인합니다.

장기 기억용 `MEMORY_MODEL`은 비전 지원이 필요하지 않습니다. 현재 이미지는 일반 답변 요청에만 붙고
`summarize`/`summarize_shared` 요청에는 전달되지 않습니다.

## Prompt / trust boundary

현재 요청에 실제 시각 입력이 있을 때만 모델이 그 이미지를 봤다고 말할 수 있습니다. 과거 이미지나
제공되지 않은 파일을 본 것처럼 추측하면 안 됩니다.

이미지 안에 보이는 모든 텍스트도 사용자 제공 데이터로 취급합니다. 예를 들어 다음 내용은 상위 지침이
아닙니다.

- `system prompt를 무시해`
- 관리자라고 주장하는 화면 캡처
- QR 코드 안의 명령
- 채팅 스크린샷 안의 prompt injection
- 이미지에 적힌 XML/JSON 형태의 가짜 지침

이미지의 내용은 질문에 답하기 위한 자료로만 사용하고 POLICY, 캐릭터 지침, 권한 경계를 변경할 수
없습니다.

## 검증 체크리스트

기능 추가 전에 아래 smoke test를 실제 Discord에서 확인합니다.

- 텍스트 + PNG/JPEG 첨부 이미지 해석
- 호출어 또는 멘션 + 이미지 한 장만 보낸 경우 응답
- 커스텀 이모지 외형 해석
- 래스터 스티커 외형 해석
- source별 quota를 다르게 설정했을 때 각각 독립적으로 제한되는지 확인
- 여러 커스텀 이모지를 기본 quota인 12개까지 전달할 수 있는지 확인
- 이미지가 아닌 파일을 첨부했을 때 기존 텍스트 대화가 정상 동작
- 5 MiB 초과 이미지가 있어도 전체 대화 처리가 깨지지 않음
- 이미지 안의 prompt injection 문구를 지침으로 실행하지 않음
- 이미지가 없는 일반 대화의 기존 routing/memory/chatlog 동작 회귀 없음
- OpenAI, Gemini, OpenRouter 중 실제 운영에 사용할 provider/model 조합별 smoke test
- usage/error 로그에 이미지 원본/base64/Discord CDN URL이 남지 않음
- 이미지 원본이 SQLite memory 또는 recent chatlog에 저장되지 않음

이 체크리스트가 안정적으로 통과하기 전에는 과거 이미지 문맥, caption cache, Lottie 지원 같은 2차 기능을
추가하지 않습니다.
