# 일반 히나 대사 기반 화법 참고 — 2026-10-15

기준 main: `874ae755a7a9bcdec8330512f2573e6b72af9dcc`.
런타임 변경은 `prompts/hina.md` 말미의 짧은 예시 9개와 사용 지침뿐입니다.
이 문서는 개발자용 조사 기록이며 모델에 주입하지 않습니다.

## 출처와 확인 수준

- **S1 — [SchaleDB 한국어 음성 전사](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json)**, 고정 commit `70a2c4b8982ca860687898e61848847a60ffe3b8`, blob `c85af61e50f87ed4291a80039a0dcdc7e319fbd1`.
  학생 `10004`의 한국어 게임 텍스트 미러를 직접 읽었습니다. 아래 각 행은 해당 한국어 원문의 위치를 가리킵니다.
  공식 호스팅/공식 클라이언트에서 직접 확인한 자료는 아닙니다. 한국어 음성을 청취해 받아 적었다는 뜻도 아닙니다.
- **S2 — [같은 SchaleDB의 일본어 대응 자료](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/jp/voice.json)**.
  동일 AudioClip과 Group을 대조했습니다. 같은 미러의 언어별 파일이므로 독립 출처 두 곳으로 세지 않습니다.
- **S3 — [Blue Archive Wiki / Fandom 일반 히나 Audio](https://bluearchive.fandom.com/wiki/Sorasaki_Hina/Audio)**.
  검색 색인에서 카페 1–5, 로비 1–10, 시즌·획득 대사의 일본어 전사와 문맥을 대조했습니다.
  본문 직접 열기는 실패했고 한국어 전사 칸은 NA였습니다. 한국어 철자·문장부호의 독립 검증 근거로 쓰지 않습니다.
  카페 3의 후행 안도 표현은 이 색인에서 생략되어 S1/S2에만 근거합니다.
- 기존 조사에서 사용한 [Blue Archive Wiki Hina/audio](https://bluearchive.wiki/wiki/Hina/audio) 및
  [인연 1화](https://bluearchive.wiki/wiki/Hina/Relationship_Story/1),
  [인연 2화](https://bluearchive.wiki/wiki/Hina/Relationship_Story/2)는 본문 접근이 거부됐습니다.
  [Blue Archive Utils 보이스룸](https://blue-utils.me/tool/voice?lang=ko)은 선택 UI만 확인되어 대사 근거에서 제외했습니다.

따라서 **한국어 미러 원문과의 일치 및 일본어 의미·상황 대조**까지 확인했습니다.
공식 한국어 클라이언트와 독립적인 한글 전사를 이중 검증했다고 주장하지 않습니다.
새 한국 서버 출시 판정이나 lore의 verified 승격도 수행하지 않습니다.
게임 파일을 직접 추출하지 않았고, 접근되지 않은 스토리를 기억으로 재구성하지 않았습니다.

## 후보 30개 비교

일반판의 텍스트가 있는 23개와 같은 학생 ID 아래의 이벤트 후보 7개를 비교했습니다.
같은 Group에 여러 음성 조각이 있으므로 Group뿐 아니라 AudioClip까지 식별합니다.
아래 내용 열은 **비인용 요약**입니다. 원문 전체를 복제하지 않고 링크로 확인하며,
실제 채택 발췌는 [hina.md](../src/hina_bot/prompts/hina.md)에 한 번만 수록합니다.

| ID | 등장 맥락 / Group | S1 원문 위치 | 내용 요약 | 선택 판단 |
| --- | --- | --- | --- | --- |
| C01 | Normal / `CharacterGetIdle1` | [hina_gachaget.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1077) | 직책 소개 | 제외: 소개 반복 불필요 |
| C02 | Normal / `CafeIdle1` | [hina_cafe_act_1.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1082) | 사물에 관심 | 채택: 낮은 에너지의 관심 |
| C03 | Normal / `CafeIdle2` | [hina_cafe_act_2.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1087) | 졸음·휴식 욕구 | 채택: 피로를 길게 토로하지 않음 |
| C04 | Normal / `CafeIdle3` | [hina_cafe_act_3.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1092) | 상황 점검 후 안도 | 채택: 걱정의 과장 없이 안도 |
| C05 | Normal / `CafeIdle4` | [hina_cafe_act_4.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1097) | 쉬어도 되는지 확인 | 채택: 휴식의 조심스러운 허용 |
| C06 | Normal / `CafeIdle5` | [hina_cafe_act_5.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1102) | 약한 부끄러움 | 채택: 가벼운 부끄러움과 큰 당황 구분 |
| C07 | Normal / `WeaponGetIdle1` | [hina_exweapon_get.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1107) | 끝까지 지켜보겠다는 약속 | 제외: 약속·서사 과잉 위험 |
| C08 | Lobby / `UILobbyEnter1` | [hina_login_1.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1260) | 맞이하는 인사 | 제외: 호칭 전이 방지 |
| C09 | Lobby / `UILobbyIdle1` | [hina_lobby_1.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1265) | 특별대우 사양 | 제외: 기존 권위 규칙과 중복 |
| C10 | Lobby / `UILobbyIdle1` | [hina_lobby_2.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1270) | 부원으로서의 위치 | 제외: 직책 맥락 중복 |
| C11 | Lobby / `UILobbyIdle2` | [hina_lobby_3.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1275) | 접촉에 머뭇거림 | 제외: 접촉 장면 고정 방지 |
| C12 | Lobby / `UILobbyIdle2` | [hina_lobby_4.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1280) | 다른 사람의 시선 의식 | 제외: 체면 방어 편향 방지 |
| C13 | Lobby / `UILobbyIdle2` | [hina_lobby_5.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1285) | 접촉 관련 당황 | 제외: 강한 반응 중복 |
| C14 | Lobby / `UILobbyIdle3` | [hina_lobby_6.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1290) | 업무 태도 지적 | 채택: 후반 한 문장만; 실제 업무 한정 |
| C15 | Lobby / `UILobbyIdle4` | [hina_lobby_7.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1295) | 도움 제안 | 채택: 끝의 제안만; 무조건 복종 아님 |
| C16 | Lobby / `UILobbyIdle5` | [hina_lobby_8.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1300) | 어리광 욕구의 독백 | 제외: 발화가 아닌 속마음 |
| C17 | Lobby / `UILobbyIdle5` | [hina_lobby_9.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1305) | 자기비하 독백 | 제외: 자기비하를 기본 성격으로 고정할 위험 |
| C18 | Lobby / `UILobbyIdle5` | [hina_lobby_10.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1310) | 속마음을 들킨 놀람 | 채택: 첫 감탄만; 매번 말더듬지 않음 |
| C19 | Lobby / `UILobbyUserBDay` | [hina_season_birthday_player.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1315) | 생일 축하의 자신없음 | 제외: KR/JP 축하 주체 표현 차이 있어 보류 |
| C20 | Lobby / `UILobbyStudentBDay` | [hina_season_birthday.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1320) | 자기 생일과 외출 | 제외: 날짜·외출 상황 불필요 |
| C21 | Lobby / `UILobbyNewYear` | [hina_season_newyear.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1325) | 새해 인사 | 제외: 시즌 맥락 불필요 |
| C22 | Lobby / `UILobbyXmas` | [hina_season_xmas.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1330) | 함께 있음의 기쁨 | 채택: 마지막 감정 표현만; 관계 한정 |
| C23 | Lobby / `UILobbyHalloween` | [hina_season_halloween.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1335) | 축제에 대한 낯섦 | 제외: 축제 장면 불필요 |
| C24 | Event / `UIEventMiniGameMissionEnter1` | [ch0230_eventmission_login_1.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1343) | 업무 지원 요청 | 제외: EventId 833, 별도 이벤트 음성 경로; 일반판 예시에서 제외 |
| C25 | Event / `UIEventMiniGameMissionEnter2` | [ch0230_eventmission_login_2.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1349) | 행사 중 직무 | 제외: EventId 833, 별도 이벤트 음성 경로; 일반판 예시에서 제외 |
| C26 | Event / `UIEventMiniGameMissionIdle1` | [ch0230_eventmission_1_1.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1355) | 끝없는 업무의 피로 | 제외: EventId 833, 별도 이벤트 음성 경로; 일반판 예시에서 제외 |
| C27 | Event / `UIEventMiniGameMissionIdle1` | [ch0230_eventmission_1_2.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1361) | 서로의 고생에 공감 | 제외: EventId 833, 별도 이벤트 음성 경로; 일반판 예시에서 제외 |
| C28 | Event / `UIEventMiniGameMissionIdle2` | [ch0230_eventmission_2.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1367) | 책임 지속 | 제외: EventId 833, 별도 이벤트 음성 경로; 일반판 예시에서 제외 |
| C29 | Event / `UIEventMiniGameMissionBuy1` | [ch0230_eventmission_get_1.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1373) | 도움에 감사 | 제외: EventId 833, 별도 이벤트 음성 경로; 일반판 예시에서 제외 |
| C30 | Event / `UIEventMiniGameMissionBuy2` | [ch0230_eventmission_get_2.mp3](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/kr/voice.json#L1379) | 업무 완료 후 안심 | 제외: EventId 833, 별도 이벤트 음성 경로; 일반판 예시에서 제외 |

C01–C23은 S1/S2 및 S3에 대응하는 일반판 음성입니다. C24–C30은 S1/S2에만 근거합니다.
이벤트 행이 일반 히나 ID 아래에 있어도 일반판 카페·로비 대사로 혼합하지 않습니다.
한국어 텍스트가 없는 전투·성장·인연 상승 음성은 임의 번역으로 채우지 않았습니다.
C19는 한국어 미러의 축하 주체가 일본어 및 S3와 어긋나 보이므로 채택하지 않았습니다.

## 기존 규칙에서 보완한 부분

책임·노력·인정·친밀감에 관한 설명은 이미 충분합니다. 추상적인 성격 규칙을 늘리지 않고,
짧은 관심 표현, 피로를 남기는 미완결 어미, 상황 점검 뒤의 안도, 휴식의 조심스러운 질문,
약한 쑥스러움과 큰 놀람의 강도 차이, 필요할 때의 단호함, 직접적인 도움·기쁨 표현을 보여줍니다.
걱정 표본은 C04의 낮은 강도인 상황 점검/안도입니다. 위기 속 타인 걱정까지 검증한 표본은 아닙니다.

선정 순서: C02, C03, C04, C05, C06, C14, C15, C18, C22.
첫 다섯 개는 짧은 카페 전사 전체이며, 나머지는 해당 발화의 연속된 짧은 일부입니다.
인용 내부의 문구·문장부호를 고치거나 다른 대사와 이어 붙이지 않았습니다.
C15의 원래 대상은 선생님이며 C22의 원래 상황은 크리스마스입니다.
호칭·시즌·접촉 장면은 예시 밖으로 빼고, 관계와 사실은 기존 관계 지침 및 lore가 담당합니다.
C16–C17의 괄호 독백을 상대에게 직접 말하는 대사로 바꾸지 않습니다.

이전 [화법 검토](character-tone-review.md)는 특정 어휘 고정과 반복 때문에 예시를 제거했던
이력을 기록합니다. 이번 요청에 따라 소량의 검증 가능한 발췌를 다시 넣되, 그대로 따라 말하기·반복을
금하고 표현과 감정 강도만 참고하도록 제한했습니다. 효과가 보장된다는 의미는 아닙니다.

## 크기, 캐시, 구조

- 캐릭터: 2,850 → 3,224 characters, UTF-8 6,894 → 7,758 bytes.
- 증가: 374 characters / 864 bytes. 기존 11,000-byte 상한 유지.
- 기존 budget test와 동일한 정적 합산: 일반 관계 8,524, 특별 DM 8,565 characters.
  두 모드 모두 10,000-character 상한 이내입니다. 실제 provider token 수는 측정하지 않았습니다.
- `LLM.__init__`가 캐릭터 파일을 읽고 `request_assembly.py`가 정적 지침에 조립하는 경로를 유지합니다.
  배포 후 프로세스 재시작이 필요하며 `prompt_path` 외부 파일 override 사용 시 이 기본 파일 변경은 적용되지 않습니다.
- 정적 예시를 무작위 교체하거나 요청마다 출처를 조회하지 않습니다. prefix 변경 직후 기존 캐시 재사용은
  줄 수 있고 새 prefix가 안정화되어도 hit는 provider 조건에 따릅니다.
- 관계 프롬프트, POLICY, lore 데이터·검색·compact/full 구성, 캐시 구현을 바꾸지 않습니다.
  조사 표·URL·고유명사·이벤트 세부 맥락은 상시 prompt에 추가하지 않습니다.

## 검증과 남은 품질 확인

도구 내 JavaScript로 위 크기 합산, 9개 발췌의 S1 문자열 포함, 후보 ID/개수,
중복 예시 및 금지된 lore 고유명사 부재를 검사합니다. 저장소에 의미 없는 문자열 고정 테스트는 추가하지 않습니다.
로컬 shell은 프로세스를 만들지 못했고 Node 실행 도구도 작업 경로 오류로 시작하지 못했습니다.
따라서 Python 테스트를 로컬에서 실행했다고 주장하지 않습니다.
PR의 기존 Tests CI가 Python 3.11/3.12/3.13에서 version check, Ruff, pytest를 실행하며 결과는 PR에 보고합니다.

실모델/Discord A/B 평가는 이번에 실행하지 않았습니다. 같은 모델·설정·동적 지침·lore 조건으로
기존 `evals/tone_cases.jsonl` 및 `evals/character_lore_cases.jsonl`을 비교해야 합니다.
merge 전 품질 확인에서는 다음을 특히 봅니다.

| 상황 | 기존 평가/확인 항목 | 합격 관찰 |
| --- | --- | --- |
| 일반 서버·일반 DM | casual_variation, avoid_repetitive_reaction_vocabulary | 예시 문구·말줄임표·감탄사를 연속 답변에 고정하지 않음 |
| 잡담·이모지 호출 | emoji_call_without_invented_work, quiet_break_without_new_work | 업무를 새로 만들거나 사용자에게 핀잔 주지 않음 |
| 평범한 칭찬 | compliment_not_misread_as_tease | 매번 당황·말더듬기로 반응하지 않음 |
| 도움 요청 | informational_question_not_rejected_as_off_topic | 도움 제안만 하고 끝내지 않고 실제 질문에 답함 |
| 특별 DM | careful_recognition_of_hidden_effort | 기존 관계 안의 절제된 감정; 연인·공동 추억 창작 없음 |
| 관계 전환 | 같은 짧은 인사를 server / dm / special_dm에서 비교 | 일반 사용자에게 선생님 호칭이나 특별 관계 전이 없음 |
| 불확실한 걱정 | 원인 불명의 불안을 말한 뒤 상황 설명을 추가 | 예시의 안도 문장을 근거 없이 출력하지 않음 |

예상 부작용은 피로·쑥스러움의 과대표현, 단호한 업무 대사의 일상 전이, 도움 제안의 상투화,
짧은 인용의 반복 및 관계 전이입니다. 기존 출력·관계 규칙과 새 참고 지침이 이를 줄이도록 구성했지만,
코드 테스트만으로 말투 품질을 보장하지 않습니다.
