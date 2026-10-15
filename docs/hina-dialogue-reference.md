# 히나 화법 참고 — 실제 한국어 스토리·모모톡 대사 조사

## 현행 선정 (2026-10-15)

기존의 카페/로비 짧은 음성 발췌 9개와 일본어 스토리 장면의 한국어 요약 5개를 교체했습니다. **현재 캐릭터 프롬프트에는 한국어 스토리·모모톡에서 직접 검증한 짧은 대사 13개**만 있습니다. 인물 관계나 기존 lore 자료를 바꾸는 작업은 아닙니다.

- **출처:** [ba-archive/blue-archive](https://github.com/ba-archive/blue-archive), 고정 커밋 `4ddc9ee241f6028d724346acda190155dc98aaf6`. **비공식 게임 데이터 미러**입니다. 공식 한국어 클라이언트를 직접 켜서 검증했다고 주장하지 않습니다.
- 인연/메인 스토리는 `content[index].ScriptKr`의 화자 이름과 발화를 사용합니다. `TextKr`가 공란이어도 `ScriptKr`에 한국어 텍스트가 있습니다. `#3;em`, `#wait` 등 연출 명령은 발화가 아닙니다.
- 모모톡은 `10004.yml`의 `MessageKR` 가운데 `CharacterId=10004`이며 `MessageCondition != Answer`인 히나의 메시지입니다. **선생님의 선택 답변은 제외**했습니다.
- `히나 잠옷`은 동일 캐릭터의 특정 장면에 해당하지만 해당 장면의 감정·접촉·사건은 일반화하지 않습니다. `히나타`는 다른 사람입니다.
- 대사는 발화 습관을 위한 참고용입니다. 그 문장을 반복하거나 선생님 관계·당시의 직무·신체 상황을 모든 사용자에게 전이하지 않습니다.

## 선정 대사 13개: 근거와 발화 맥락

| 말투에 참고할 특성 | 원문 (파일 및 위치) | 활용 범위 |
| --- | --- | --- |
| 판단·지시 | [main·content[1]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/main/33200.json#L39) | 위기 속 업무 |
| 협력 수락 | [main·content[12]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/main/33200.json#L239) | 위기 속 업무 |
| 의견 인정 | [f42·content[50]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json#L922) | 인연 스토리의 대화 |
| 판단 이유 설명 | [f42·content[52]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json#L958) | 인연 스토리의 대화 |
| 다른 취향 존중 | [f45·content[47]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json#L868) | 인연 스토리의 대화 |
| 자연스러운 제안 | [f45·content[48]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json#L886) | 인연 스토리의 대화 |
| 피로를 짧게 설명 | [f46·content[10]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100046.json#L201) | 인연 스토리의 대화 |
| 사소한 수긍 | [f46·content[17]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100046.json#L327) | 인연 스토리의 대화 |
| 사과를 누그러뜨림 | [f48·content[70]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100048.json#L1282) | 인연 스토리의 대화 |
| 낯선 활동 수용 | [f415·content[138]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/1000415.json#L2508) | 사적 인연 장면 |
| 상대 수면시간 배려 | [momo·content[5]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml#L157) | 모모톡 대화 |
| 자신의 작은 바람 | [momo·content[18]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml#L399) | 모모톡 대화 |
| 감정 표현 정정 | [momo·content[80]](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml#L1541) | 모모톡 대화 |

링크가 가리키는 각 `ScriptKr` 또는 `MessageKR`가 프롬프트 발췌의 원문입니다. 여러 행을 조합하거나 임의로 한국어 번역을 새로 만들지 않았습니다. 문장부호와 말줄임표도 원본을 유지했습니다.

## 검토 후보 전체 — 기존 음성 자료와 별도의 한국어 대사 후보

| 자료 | 원문 데이터 항목 | 선정 여부·사유 |
| --- | --- | --- |
| main | [main:1](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/main/33200.json) (`content[1]` 또는 `MessageKR` 1) | 채택 |
| main | [main:12](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/main/33200.json) (`content[12]` 또는 `MessageKR` 12) | 채택 |
| main | [main:18](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/main/33200.json) (`content[18]` 또는 `MessageKR` 18) | 제외 — 단문 |
| f42 | [f42:13](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[13]` 또는 `MessageKR` 13) | 제외 — 사과 중복 |
| f42 | [f42:19](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[19]` 또는 `MessageKR` 19) | 제외 — 장면 농담 |
| f42 | [f42:33](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[33]` 또는 `MessageKR` 33) | 제외 — 과도한 당황 |
| f42 | [f42:40](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[40]` 또는 `MessageKR` 40) | 제외 — 긴 업무 설명 |
| f42 | [f42:50](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[50]` 또는 `MessageKR` 50) | 채택 |
| f42 | [f42:52](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[52]` 또는 `MessageKR` 52) | 채택 |
| f42 | [f42:53](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[53]` 또는 `MessageKR` 53) | 제외 — 관점 설명 중복 |
| f42 | [f42:62](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100042.json) (`content[62]` 또는 `MessageKR` 62) | 제외 — 피로 중복 |
| f43 | [f43:7](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100043.json) (`content[7]` 또는 `MessageKR` 7) | 제외 — 선생·장소 맥락 |
| f43 | [f43:13](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100043.json) (`content[13]` 또는 `MessageKR` 13) | 제외 — 특별 관계 |
| f43 | [f43:23](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100043.json) (`content[23]` 또는 `MessageKR` 23) | 제외 — 놀림 오해 |
| f43 | [f43:40](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100043.json) (`content[40]` 또는 `MessageKR` 40) | 제외 — 가십 상황 |
| f43 | [f43:42](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100043.json) (`content[42]` 또는 `MessageKR` 42) | 제외 — 시간 조정 |
| f43 | [f43:45](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100043.json) (`content[45]` 또는 `MessageKR` 45) | 제외 — 상대 호칭에 의존 |
| f45 | [f45:13](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[13]` 또는 `MessageKR` 13) | 제외 — 업무 반복 |
| f45 | [f45:29](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[29]` 또는 `MessageKR` 29) | 제외 — 보편적 질문 |
| f45 | [f45:39](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[39]` 또는 `MessageKR` 39) | 제외 — 특정 물건 |
| f45 | [f45:47](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[47]` 또는 `MessageKR` 47) | 채택 |
| f45 | [f45:48](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[48]` 또는 `MessageKR` 48) | 채택 |
| f45 | [f45:55](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[55]` 또는 `MessageKR` 55) | 제외 — 취향 부정 중복 |
| f45 | [f45:75](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[75]` 또는 `MessageKR` 75) | 제외 — 자책 과잉 |
| f45 | [f45:85](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[85]` 또는 `MessageKR` 85) | 제외 — 음료 고정 |
| f45 | [f45:92](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100045.json) (`content[92]` 또는 `MessageKR` 92) | 제외 — 나이·관계 맥락 |
| f46 | [f46:10](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100046.json) (`content[10]` 또는 `MessageKR` 10) | 채택 |
| f46 | [f46:15](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100046.json) (`content[15]` 또는 `MessageKR` 15) | 제외 — 장소 맥락 |
| f46 | [f46:16](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100046.json) (`content[16]` 또는 `MessageKR` 16) | 제외 — 상황 한정 농담 |
| f46 | [f46:17](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100046.json) (`content[17]` 또는 `MessageKR` 17) | 채택 |
| f46 | [f46:38](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100046.json) (`content[38]` 또는 `MessageKR` 38) | 제외 — 특별 상대 맥락 |
| f48 | [f48:22](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100048.json) (`content[22]` 또는 `MessageKR` 22) | 제외 — 도움 거절 장면 |
| f48 | [f48:67](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100048.json) (`content[67]` 또는 `MessageKR` 67) | 제외 — 업무 고정 |
| f48 | [f48:70](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100048.json) (`content[70]` 또는 `MessageKR` 70) | 채택 |
| f48 | [f48:73](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100048.json) (`content[73]` 또는 `MessageKR` 73) | 제외 — 독백 |
| f48 | [f48:76](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/100048.json) (`content[76]` 또는 `MessageKR` 76) | 제외 — 친밀감 전이 |
| f415 | [f415:18](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/1000415.json) (`content[18]` 또는 `MessageKR` 18) | 제외 — 보지 못한 외형 단서 |
| f415 | [f415:33](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/1000415.json) (`content[33]` 또는 `MessageKR` 33) | 제외 — 특별 관계 |
| f415 | [f415:85](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/1000415.json) (`content[85]` 또는 `MessageKR` 85) | 제외 — 식사 고정 |
| f415 | [f415:131](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/1000415.json) (`content[131]` 또는 `MessageKR` 131) | 제외 — 수락 중복 |
| f415 | [f415:138](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/story/favor/10004/1000415.json) (`content[138]` 또는 `MessageKR` 138) | 채택 |
| momo | [momo:0](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[0]` 또는 `MessageKR` 0) | 제외 — 상대 호칭 고정 |
| momo | [momo:4](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[4]` 또는 `MessageKR` 4) | 제외 — 장소 약속 |
| momo | [momo:5](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[5]` 또는 `MessageKR` 5) | 채택 |
| momo | [momo:6](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[6]` 또는 `MessageKR` 6) | 제외 — 배려 중복 |
| momo | [momo:7](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[7]` 또는 `MessageKR` 7) | 제외 — 배려 중복 |
| momo | [momo:10](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[10]` 또는 `MessageKR` 10) | 제외 — 짧은 감사 |
| momo | [momo:16](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[16]` 또는 `MessageKR` 16) | 제외 — 특별 만남 |
| momo | [momo:18](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[18]` 또는 `MessageKR` 18) | 채택 |
| momo | [momo:28](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[28]` 또는 `MessageKR` 28) | 제외 — 감정 표현 과잉 |
| momo | [momo:53](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[53]` 또는 `MessageKR` 53) | 제외 — 일정 약속 |
| momo | [momo:70](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[70]` 또는 `MessageKR` 70) | 제외 — 업무 과잉 |
| momo | [momo:80](https://github.com/ba-archive/blue-archive/blob/4ddc9ee241f6028d724346acda190155dc98aaf6/apps/blue-archive-story-viewer/public/config/yaml/momotalk/10004.yml) (`content[80]` 또는 `MessageKR` 80) | 채택 |

## 프롬프트 길이 및 검증

- 변경 후 `hina.md`: **3,590자 / UTF-8 8,633바이트**, 기존 11,000바이트 상한 이내. 기존 캐릭터 지침은 그대로 유지하며 화법 예시 영역만 교체했습니다.
- 후보 **53개**, 선정 **13개**. 메인 스토리 2개, 인연 스토리 8개, 모모톡 3개입니다.
- 출처 검증은 해당 비공식 저장소의 고정 리비전 및 한국어 발화 필드 기준으로 수행했습니다. 파일 내용 테스트와 실제 Gemini A/B 검증은 구별해야 합니다.
- 예상 부작용: 대사 자체의 반복, 말줄임표 과잉, 선생님 호칭 전이, 특정 시점의 감정·업무 상황 과잉 재현. 동일 질문 세트로 기존 `evals/tone_cases.jsonl`과 실제 응답을 비교해야 합니다.

## 이전 음성·일본어 장면 조사 (이력 자료)

아래에는 과거 음성 대사 후보 30개, 일본어 전사 장면 5개를 그대로 보존합니다. **여기서 말하는 '현재 9개 채택'이나 과거 크기 수치는 현행 `hina.md`에 적용되지 않습니다.**

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

## 스토리 대화 흐름 추가 조사 — 5장면

기존 30개 후보는 한국어 음성 전사에서 뽑은 **표면적인 짧은 표현**입니다. 이를 대화 전체의 대표 표본으로 간주하면 지나치게 짧은 응답, 과도한 말줄임표, 감정 표현 반복을 강화할 수 있어, 별도로 실제 스토리의 **상황→대응→다음 발화**를 확인했습니다. 추가 장면은 일본어 게임 스토리의 비공식 전사에 근거하며, 공식 한국어 번역을 검증한 자료가 아닙니다. 따라서 아래 장면을 근거로 새로운 한국어 '원문 대사'를 만들어 넣지 않았습니다.

| 장면 | 대사 전사의 출처 | 관찰 가능한 대화 전개 | 반영할 때의 한계 |
| --- | --- | --- | --- |
| S31 — 메인 스토리 Vol.1 Ch.2 Ep.2 | [선도부장의 상황 판단](https://wikiwiki.jp/ba_storytext/1-2-2%20%E3%82%B2%E3%83%98%E3%83%8A%E3%81%AE%E9%A2%A8%E7%B4%80%E5%A7%94%E5%93%A1%E9%95%B7) | 이미 파악한 일은 장황하게 다시 듣지 않고 권한과 책임을 구분해 후속 조치를 정함 | 무고한 상대에게도 항상 의심·명령하도록 일반화하지 않음 |
| S32 — 그룹 스토리 선도부 정례회의 | [주간 정례회의](https://wikiwiki.jp/ba_storytext/%E9%A2%A8%E7%B4%80%E5%A7%94%E5%93%A1%E4%BC%9A%E3%83%BB%E9%80%B1%E6%AC%A1%E5%AE%9A%E4%BE%8B) | 짧게 피곤함을 드러내면서도 보고를 이어 듣고 원인을 질문함 | 대화에 없는 업무를 지어내거나 모든 잡담을 피로·한숨으로 시작하지 않음 |
| S33 — 최종편 작전 준비 | [제3 생텀 준비](https://wikiwiki.jp/ba_storytext/F-2-3%20%E4%BD%9C%E6%88%A6%E6%BA%96%E5%82%99%EF%BC%88%EF%BC%91%EF%BC%89) | 자신에 대한 뜻밖의 긍정적 평가를 듣고 잠시 말문을 고른 후 다시 임무 전달로 복귀함 | 모든 칭찬을 과장된 쑥스러움으로 만들지 않음 |
| S34 — 미니 스토리 | [주인 없는 선물 (1)](https://wikiwiki.jp/ba_storytext/%E4%B8%BB%E3%81%AA%E3%81%8D%E3%83%97%E3%83%AC%E3%82%BC%E3%83%B3%E3%83%88%EF%BC%88%EF%BC%91%EF%BC%89) | 가벼운 용건을 말하다 상대에게 필요한 것까지 먼저 묻고, 그 대답을 예상한 듯 호응하며 작은 기대를 표현함 | 원래 상대와 특별한 관계를 모든 사용자에게 전이하지 않음 |
| S35 — 메인 스토리 Vol.1 Ch.3 Ep.41 | [남겨진 것](https://wikiwiki.jp/ba_storytext/1-3-41%20%E6%AE%8B%E3%81%95%E3%82%8C%E3%81%9F%E3%82%82%E3%81%AE) | 곤란한 사정을 짧게 설명하다 필요한 순간 상대에게 직접 도움을 부탁함 | 한 장면의 난처함을 항상 부끄러워하는 성격으로 일반화하지 않음 |

이 다섯 장면을 성격의 새로운 사실이나 장면 재현 지시로 저장하지 않았습니다. **문장 형태만 따라 하는 짧은 발췌**와 달리, 어느 순간에 어떤 정보를 확인하고 어떤 방향으로 대화를 진행하는지 보완하는 자료입니다. 런타임에는 인물 이름·에피소드·미러 URL을 넣지 않고, 대화 전개 특징만 짧게 담았습니다. 한국어 대사를 추가 확인할 수 있으면 별도 한국어 원문 출처를 확보한 후에만 발췌로 승격해야 합니다.

## 기존 규칙에서 보완한 부분

책임·노력·인정·친밀감에 관한 설명은 이미 충분합니다. 짧은 음성 전사만으로는 문장 간 연결과 상황 판단을 보여주기 어렵다는 한계가 있어 S31–S35의 스토리 대화 전개를 별도로 보완했습니다. 추상적인 성격 규칙을 늘리지 않고,
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

- 캐릭터: 2,850 → 3,675 characters, UTF-8 6,894 → 8,829 bytes.
- 증가: 825 characters / 1,935 bytes. 기존 11,000-byte 상한 유지.
- 기존 budget test와 동일한 정적 합산: 일반 관계 8,975, 특별 DM 9,016 characters.
  두 모드 모두 10,000-character 상한 이내입니다. 실제 provider token 수는 측정하지 않았습니다.
- `LLM.__init__`가 캐릭터 파일을 읽고 `request_assembly.py`가 정적 지침에 조립하는 경로를 유지합니다.
  배포 후 프로세스 재시작이 필요하며 `prompt_path` 외부 파일 override 사용 시 이 기본 파일 변경은 적용되지 않습니다.
- 정적 예시를 무작위 교체하거나 요청마다 출처를 조회하지 않습니다. prefix 변경 직후 기존 캐시 재사용은
  줄 수 있고 새 prefix가 안정화되어도 hit는 provider 조건에 따릅니다.
- 관계 프롬프트, POLICY, lore 데이터·검색·compact/full 구성, 캐시 구현을 바꾸지 않습니다.
  조사 표·URL·고유명사·이벤트 세부 맥락은 상시 prompt에 추가하지 않습니다.

최근 main의 lore `summary`·`timeline`·`semantic_text` 길이 제한 완화는 이 캐릭터 프롬프트 경로와 무관합니다. 이 변경은 `hina.md`의 별도 크기 상한 및 상시 static prompt 예산 내에서 구성했습니다.

## 검증과 남은 품질 확인

도구 내 JavaScript로 UTF-8 크기, 최신 main의 정적 프롬프트와 합산, 9개 발췌의 S1 문자열 포함, 후보 ID/개수,
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

