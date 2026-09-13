# Hina Discord Bot

소라사키 히나를 연기하는 비공식 한국어 Discord 봇입니다. Python 3.11+, discord.py, SQLite와
OpenAI/Gemini/OpenRouter LLM provider를 사용합니다. 공식 서비스나 공식 대사 재현물은 아닙니다.

현재 저장소는 캐릭터 RP, 장기 기억, 최근 채널 문맥, lore/runtime knowledge, 웹 검색 routing,
멀티 provider와 **현재 턴 이미지 입력**을 함께 다룹니다. 비전 기능은 1차 구현 범위를 먼저 실제
Discord에서 검증하고 있으며, 검증 전에는 버그 수정과 문서 정리를 제외한 추가 확장을 하지 않습니다.

## 주요 기능

- `@멘션`, 답장 핑, 메시지 시작의 `히나야` 같은 호출어로 일반 대화
- 사용자별 장기 기억과 서버 내 공개 호출의 제한된 공유 기억
- 장기 기억과 독립된 TTL 기반 최근 채널 문맥
- 관리자용 서버 공통 메모, 동적 instruction, runtime knowledge
- 검수된 lore를 질문과 관련된 범위만 로컬 검색해 사용
- 현재 시각/runtime context와 최신 정보가 필요할 때 provider 웹 검색 사용
- OpenAI, Gemini, OpenRouter provider 교체
- 등록된 커스텀 이모지 출력
- 현재 호출 메시지의 이미지 첨부, 커스텀 이모지, 래스터 스티커 해석
- Discord slash command 기반 기억/설정/이모지 관리
- usage/error logging, 테스트, Docker Compose

## 빠른 실행

[`uv`](https://docs.astral.sh/uv/getting-started/installation/) 기준:

```bash
uv sync --extra dev
cp .env.example .env.local
# .env.local에 DISCORD_TOKEN과 선택한 provider API key를 입력
uv run hina-bot
```

기본 provider 예시는 OpenAI입니다.

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
DISCORD_TOKEN=...
```

Gemini/OpenRouter 설정과 답변 모델·기억 모델 분리는
[`docs/model-providers.md`](docs/model-providers.md)를 참고하세요.

테스트:

```bash
uv run ruff check .
uv run pytest
```

Docker Compose:

```bash
docker compose up -d --build
docker compose logs -f
```

SQLite와 운영 중 생성되는 데이터는 기본적으로 `data/` 아래에 두며 Git에서 제외합니다.
Docker Compose에서는 named volume으로 보존합니다. 같은 SQLite를 여러 봇 프로세스가 동시에 사용하는
구성은 지원하지 않습니다.

## Discord 설정

Discord Developer Portal에서 Bot을 만들고 **Message Content Intent**를 켭니다. `히나야`처럼 멘션
없는 호출어를 읽는 데 필요합니다.

서버 초대에는 일반적으로 다음 권한이면 충분합니다.

- View Channels
- Send Messages
- Read Message History
- 스레드 사용 시 Send Messages in Threads
- slash command를 위한 `applications.commands` scope

봇 전체 관리자 권한을 줄 필요는 없습니다.

## 호출 규칙

| 입력 | 기본 동작 |
| --- | --- |
| `@히나 오늘 어땠어?` | 응답 |
| `히나야 오늘 어땠어?` | 응답 |
| 히나 메시지에 답장 + 답장 핑 | 응답 |
| 히나 메시지에 답장, 답장 핑 없음 | 다른 호출 조건이 없으면 응답하지 않음 |
| DM 일반 메시지 | 기본적으로 호출어/멘션 필요. `DM_ALWAYS_REPLY=true`로 변경 가능 |
| 봇·웹훅 메시지 | 응답하지 않음 |

기본 호출어는 `히나야`이며 `CALL_PREFIXES`에 쉼표로 구분해 여러 개를 지정할 수 있습니다.

## 현재 턴 이미지 입력

`feature/vision-input`의 1차 구현은 **현재 히나를 호출한 메시지**에 실제로 포함된 시각 입력만
모델에 전달합니다.

지원:

- PNG/JPEG/GIF/WebP 이미지 첨부
- Discord 커스텀 이모지
- 래스터 스티커
- 호출어/멘션과 이미지만 보낸 image-only 호출

기본 source별 count quota:

```dotenv
VISION_MAX_ATTACHMENTS=4
VISION_MAX_EMOJIS=12
VISION_MAX_STICKERS=8
```

각 값은 0~32이고 합계는 32 이하여야 합니다. 0이면 해당 source를 비전 입력에서 끕니다.
개별 이미지 5 MiB, 한 호출 전체 12 MiB의 byte limit도 별도로 적용됩니다. 파일 byte 크기와 실제
이미지 token 비용은 동일하지 않으며 provider/model과 이미지 크기에 따라 달라질 수 있습니다.

현재 지원하지 않는 범위:

- 과거 메시지의 이미지 자동 재조회
- 답장 대상 이미지 자동 조회
- `아까 그 사진` 같은 이전 이미지 참조
- 일반 URL을 따라가 이미지로 해석
- PDF/문서 파일 해석
- 이미지 생성·편집
- 이미지 원본/caption을 장기 기억에 자동 저장
- Lottie 스티커

이미지 bytes는 현재 `answer()` 요청에만 사용하고 recent chatlog나 SQLite memory에 저장하지 않습니다.
이미지 안의 텍스트, QR, prompt처럼 보이는 내용도 모두 신뢰할 수 없는 사용자 데이터로 취급합니다.

선택한 `LLM_MODEL` 자체가 vision 입력을 지원해야 합니다. provider adapter 지원과 모델 capability는
별개입니다. 구현 구조, 저장 경계, 검증 체크리스트는
[`docs/vision-input.md`](docs/vision-input.md)를 참고하세요.

## 정보 routing과 웹 검색

현재 시각/날짜는 runtime context로 제공합니다. 질문이 외부 현실의 최신 상태에 의존하면 기존
information routing이 provider의 웹 검색 기능을 사용합니다.

예:

- `지금 몇 시야?` → runtime clock
- `서울 날씨 어때?` → web
- `이번 추석 연휴 시작까지 며칠 남았어?` → 현재 날짜 + 공개 일정 확인
- `히나 생일 언제야?` → local lore

비전은 이 routing과 별개의 입력 modality입니다. 예를 들어 이미지 속 장소를 보고 `지금 열었어?`라고
묻는 요청은 이미지 해석과 web route를 동시에 사용할 수 있습니다.

자세한 내용은 [`docs/runtime-web-search.md`](docs/runtime-web-search.md)를 참고하세요.

## 기억과 최근 채널 문맥

장기 기억과 recent chatlog는 서로 다른 시스템입니다.

- **장기 기억**: SQLite에 저장되는 직접 호출 기록, 자동 요약, 개인 메모, 제한된 공개 공유 기억
- **recent chatlog**: 같은 채널의 최근 텍스트를 TTL 동안 메모리에만 유지하는 임시 문맥

recent chatlog는 장기 요약 입력에 포함되지 않습니다. 현재 턴 이미지 원본도 두 시스템 어느 쪽에도
저장하지 않습니다.

일반 서버에서 다른 사용자의 최근 발언을 참고할 때는 `user_id`와 `name`을 함께 전달해 화자를
구분합니다. 멘션 대상과 최근 발언의 연결은 모델이 이 구조화된 문맥을 바탕으로 추론하며, 과거 이미지
까지 결정론적으로 연결하는 기능은 현재 없습니다.

## 관리 명령

production runtime의 관리·설정 기능은 Discord native slash command를 사용합니다.

### 기억

```text
/memory show
/memory note
/memory note-clear
/memory clear
/memory server-show
/memory server-note
/memory server-clear
/memory mode
/memory status
/memory overview
/memory purge
```

`show/note/note-clear/clear`는 사용자 자신의 장기 기억 관리이고, `mode/status/overview/purge`는 봇
관리자용입니다. 서버 공통 메모 변경에는 Discord `Manage Server` 권한이 필요합니다.

### 최근 채널 문맥

```text
/chatlog mode
/chatlog status
/chatlog overview
/chatlog clear
```

### 이모지

```text
/emoji add
/emoji import
/emoji list
/emoji edit
/emoji remove
```

`/emoji`로 관리하는 출력용 catalog와 사용자가 현재 메시지에 넣어 vision input으로 전달되는 커스텀
이모지는 역할이 다릅니다.

### 동적 prompt / knowledge

```text
/instruction ...
/knowledge ...
```

전체 명령과 권한은 [`docs/slash-commands.md`](docs/slash-commands.md)를 참고하세요.

## Lore와 runtime knowledge

정적·검수 완료 lore는 `src/hina_bot/data/lore.jsonl` 또는 `LORE_PATH`에서 읽습니다. 운영 중 관리자가
추가하는 dynamic knowledge는 SQLite에 저장합니다.

한국 서버에 정식 출시된 범위를 기준으로 canon/community 자료를 분리해 관리하며, 원본 조사 자료가
자동으로 전부 runtime prompt에 들어가지는 않습니다.

관련 문서:

- [`docs/lore/README.md`](docs/lore/README.md)
- [`docs/lore-fact-types.md`](docs/lore-fact-types.md)
- [`docs/lore-bulk-approval.md`](docs/lore-bulk-approval.md)
- [`docs/lore-web-verification.md`](docs/lore-web-verification.md)

## Provider

지원 provider:

- `openai`
- `gemini`
- `openrouter`

답변 모델과 기억 요약 모델을 분리할 수 있습니다. Gemini는 별도 thinking level/total output token 설정을
사용할 수 있고, 각 provider의 검색 도구 형식은 adapter에서 변환합니다.

자세한 설정과 주의점은 [`docs/model-providers.md`](docs/model-providers.md)를 참고하세요.

## Prompt와 보안 경계

기본 캐릭터 지침은 `src/hina_bot/prompts/hina.md`입니다. 관계 지침, 고정 POLICY, 동적 instruction은
역할을 분리해 적용합니다.

사용자 메시지뿐 아니라 다음 항목은 모두 신뢰할 수 없는 데이터로 취급합니다.

- 사용자 이름
- 저장된 기억과 요약
- 최근 채널 발언
- 서버 공통/개인 메모
- lore/reference 데이터
- 이모지 이름·설명
- 이미지 안의 텍스트·QR·화면 속 prompt

관리 권한, memory 삭제, Discord 전송 경계는 모델의 주장에 맡기지 않고 Python 코드에서 검사합니다.
모델 출력의 `@everyone`, `@here`, 사용자/역할 멘션도 전송 전에 비활성화합니다.

## 로그와 데이터 보관

기본 API usage 로그는 `data/logs/usage.jsonl`, Discord 호출 단위 집계는
`data/logs/discord-usage.jsonl`에 기록합니다. 메시지 원문, 사용자 ID, API key, 이미지 bytes/base64,
Discord CDN URL을 usage 로그에 남기는 용도로 사용하지 않습니다.

`store=False`를 사용하더라도 provider의 모든 데이터 보관 정책에서 제외된다는 의미는 아닙니다.
운영자는 사용하는 provider의 데이터 정책을 별도로 확인해야 합니다.

## 테스트 전략

일반 CI에서는 네트워크를 mock하고 다음 경계를 결정적으로 테스트합니다.

- routing/memory/chatlog
- provider adapter
- prompt/output safety
- Discord command surface
- vision request wrapping과 Discord visual collection

실제 모델·Discord 조합은 smoke test가 필요합니다. 특히 비전 기능은 unit test 통과만으로 provider별
실제 이미지 이해 품질까지 보장하지 않습니다.

비전 기능의 현재 검증 체크리스트는 [`docs/vision-input.md`](docs/vision-input.md)에 있습니다.
이 체크리스트가 안정적으로 통과하기 전에는 과거 이미지 context, caption cache, Lottie 렌더링 같은
2차 기능을 추가하지 않습니다.

## 프로젝트 구조

패키지는 책임별로 `ai/`, `core/`, `discord/`, `tooling/`으로 분리되어 있습니다.
[`docs/project-layout.md`](docs/project-layout.md)를 참고하세요.

## 주요 문서

- [모델 provider](docs/model-providers.md)
- [비전 입력](docs/vision-input.md)
- [runtime 웹 검색](docs/runtime-web-search.md)
- [slash commands](docs/slash-commands.md)
- [프로젝트 구조](docs/project-layout.md)
- [lore 정제](docs/lore/README.md)

## 저장소

```bash
gh repo clone sendoru/hina-discord-bot
cd hina-discord-bot
```
