# 히나 인물 정보 1차 보강 — 2026-10-15

## 범위와 적용 방식

기준 main: `874ae755a7a9bcdec8330512f2573e6b72af9dcc`. 기존 lore 157행 중 10행을 수정하고 2행을 추가했다.
캐릭터별 별도 파일이나 프롬프트 지침은 만들지 않았다. 이번 변경은 인물 관계에 필요한
학년·직책·확인 가능한 사건으로 한정한다.

코드 확인: `ai/llm.py:50-54`가 시작 시 정적 lore를 컴파일하고,
`ai/request_assembly.py:540-562`가 full 모드에서 캐릭터 지침 뒤의 안정적인 prefix에 넣는다.
`core/compact_lore.py`는 승인된 요약·인지 구분·시점만 전달하며 URL/keywords/claims는 전달하지 않는다.
`audience_only`는 제외한다. 이 문서도 프롬프트에 들어가지 않는다.
설정/코드만 확인했으며 실제 배포 서버의 RAG_MODE, LORE_PATH 또는 DB override는 확인하지 않았다.
배포 적용 시 패키지 lore를 사용하는 봇을 재시작해야 한다.

Retrieval v2 분석·수정·테스트, 전체 인물 도감, 실모델 응답 생성은 범위 밖이다.

## 자료와 판정

- [P: SchaleDB 한국어 게임 프로필 고정본](https://github.com/SchaleDB/SchaleDB/blob/b6b3633d71801234aadd9036c416432bc8464dc2/data/kr/students.json): 비공식 게임 데이터 미러.
  SchoolYear, CharacterAge, ProfileIntroduction만 대조했다. 미러를 공식 서비스로 표기하지 않는다.
  해당 기본 캐릭터의 한국어 데이터와 글로벌 출시 플래그를 확인했다.
- [A: 아코 공식 애니메이션 프로필](https://sh-anime.shochiku.co.jp/bluearchive-anime/character/amau-ako/),
  [H: 호시노 공식 애니메이션 프로필](https://sh-anime.shochiku.co.jp/bluearchive-anime/character/takanashi-hoshino/):
  게임 미러의 학년·나이·역할을 보조 대조했다. 애니메이션 고유 사건은 사용하지 않았다.
- [T1: Vol.1 2장 2화 일본어 대사 전사](https://wikiwiki.jp/ba_storytext/1-2-2%20%E3%82%B2%E3%83%98%E3%83%8A%E3%81%AE%E9%A2%A8%E7%B4%80%E5%A7%94%E5%93%A1%E9%95%B7),
  [T2: Vol.1 2장 11화 일본어 대사 전사](https://wikiwiki.jp/ba_storytext/1-2-11%20%E8%B2%A0%E3%81%91%E6%88%A6),
  [T3: Vol.3 3장 8화 일본어 대사 전사](https://wikiwiki.jp/ba_storytext/3-3-8%20%E7%81%AB%E3%81%A8%E7%81%B0%E3%81%AB%E6%9F%93%E3%81%BE%E3%82%8B%E6%97%A5%EF%BC%88%EF%BC%92%EF%BC%89):
  ba_storytext의 비공식 전사이며 공식 클라이언트 원문을 직접 실행해 확인한 것은 아니다.
- [W1: 히나 스토리 요약](https://bluearchive.fandom.com/wiki/Sorasaki_Hina/Story),
  [W2: 대책위원회 스토리 요약](https://bluearchive.fandom.com/wiki/The_Foreclosure_Task_Force/Story),
  [W3: 하스미 사건 요약](https://w.atwiki.jp/aniwotawiki/pages/52419.html):
  편집자의 성격 해석 대신 장면의 행동을 T1~T3와 대조했다.
  Fandom 직접 페이지는 robots 제한이 있어 검색 도구에 반환된 본문을 사용했다.
- [L: Vol.1 2장 플레이 기록](https://note.com/9ls/n/nb8d3bb9268ac):
  카이저 정보 전달 문장의 독립적인 보조 확인. 삽입 스크린샷은 가져오지 못했으므로
  스크린샷을 확인했다고 주장하지 않는다.
- [E: 에덴조약 편 에피소드·출시 목록](https://bluearchive.fandom.com/wiki/Treaty_of_Eden):
  3장 8화의 위치 및 글로벌 공개 범위 보조 확인. 신규 일본 서버 선행 사건은 추가하지 않았다.

프로필에는 `official_secondary`, 비공식 대사 전사와 사건 요약을 대조한 항목에는
`crosschecked`를 사용했다. `verified`로 승격한 항목은 없다.
`direct_experience`는 히나가 그 장면에 참여했다는 인지 구분이지 출처가 공식이라는 뜻이 아니다.

## 인물·항목별 변경

| 인물/항목 ID | 변경과 인지 범위 | 근거 |
| --- | --- | --- |
| 호시노 / `canon.hoshino.school_year_and_role` (신규) | 아비도스 3학년·17세, 히나와 같은 학년·나이. 대책위원회 역할과 동료를 지키는 행동. 공개 프로필 정보. | P: 10004/10005, H |
| 호시노 / `canon.hina.knows_hoshino_past_before_meeting` | 정보부에서 파악한 과거와 실제 대면 시점을 분리. 아코의 성급한 판단을 히나가 지적한 사건으로 구체화. | T1, T2, W1 |
| 호시노 / `canon.hina.first_meeting_with_hoshino_vol1` | 첫 대면이라는 히나의 발언 출처를 명시. 해당 장면에서 이름/성명으로 지칭한 사실만 기록. | T1, T2 |
| 카이저 정보 / `canon.hina_reports_kaiser_abidos_movement_directly` | 히나가 두 조직을 몰랐다는 잘못된 주어를 수정. 만마전·티파티가 모르는 **정보**라는 히나의 발언. | T1, W1, L |
| 아코 / `canon.ako.role_in_gehenna_and_relationship_with_hina` | 히나와 같은 3학년, 선임 행정관·비서, 업무상 지휘 관계. 기존 아비도스 무단 출동 질책 항목은 재작성하지 않고 유지. | P: 20008, A, T1/T2 |
| 이오리 / `canon.iori.prefect_team.field_role` | 2학년, 히나 휘하 행동대장. 모호한 현장 선봉 표현을 게임 프로필 직책으로 교체. | P: 10006, T1 |
| 치나츠 / `canon.chinatsu.prefect_team.medic_role` | 1학년, 히나 휘하 보건 담당. 규정·제지 역할을 간결히 유지. | P: 26000, T1 |
| 마코토 / `canon.gehenna.makoto.character_and_work_style` | 3학년·18세, 만마전 의장. 프로필 성향과 이부키·이로하에 대한 태도. 기존 선도부 견제 사건과 분리. | P: 20033; [마코토 위키](https://bluearchive.fandom.com/wiki/Hanuma_Makoto) |
| 세나 / `canon.gehenna.sena.emergency_medical_leader` | 3학년, 별도 조직인 응급의학부의 부장. 검증한 프로필 중심으로 수정. 기존 구급차 이송 사건 유지. | P: 20012, W1 |
| 카요코 / `canon.gehenna.kayoko.problem_solver_moderator` | 3학년·18세, 흥신소 68 과장. 모호한 실무 담당을 직책으로 교체. 과거 소속 추측은 추가하지 않음. | P: 13005 |
| 흥신소 68 / `canon.gehenna.problem_solver_68_not_always_hostile_to_hina` | 다른 동아리와는 항상 적대한다는 오독을 낳는 비교를 제거. 호시노 구출 때 각각 지원한 구체적 사건으로 대체. | W1, W2, [일본어 스토리 목록](https://wikiwiki.jp/ba_storytext/メインストーリー) 1-2-17~18 |
| 하스미·츠루기 / `canon.eden.justice_team.hina_cooperation` (신규) | 트리니티 3학년, 부부장/부장 구분과 조인식 습격 때 보호 인계·엄호만 간단히 추가. 개인적 친밀감은 부여하지 않음. | P: 13003/10013, T3, W1, W3 |

호시노의 3장 대치·후일담, 마코토의 선도부 견제, 세나의 이송, 아코의 무단 출동 제지는
기존 항목이 있어 중복 추가하지 않았다. 유메의 비공개 기억, 아리우스 측 밀담, 아코의 휴가
계획 배경 등 관객 정보도 새로 히나의 기억으로 넣지 않았다.

## 호칭과 미검증 사항

- T1/T2의 일본어 전사에서 히나는 호시노를 이름/성명으로 지칭한다. 이것을 한국어판의
  모든 장면에서 쓰는 고정 호칭이나 정확한 대사로 확대하지 않았다.
- 학년과 나이는 공개 프로필로 확인했다. 다른 학생이 쓰는 선배 호칭을 히나에게 옮기거나,
  18세라는 이유로 상급 학년으로 취급할 근거는 없다.
- 한국어판 장면별 정확한 호칭·어미, 모든 버전의 대사 차이는 미검증이다.
  실명별 금지 문구나 부정 예시는 프롬프트/lore에 추가하지 않았다.
- 범위 밖의 기존 전체 147개 항목을 이번에 재검증했다는 뜻은 아니다. 특히 3장 후일담의
  친밀도 해석, 유메 사건의 세부 인지 범위, 기존 추정 항목은 별도 검토가 남아 있다.
- 친밀한 접점의 충분한 교차 근거를 확보하지 못한 다른 학교 인물은 이번에 확장하지 않았다.

## 검증과 입력 크기

로컬 shell 실행은 프로세스 생성 실패, Node 실행은 sandbox 경로 오류로 사용할 수 없었다.
따라서 pytest와 저장소 Python 실행을 수행했다고 표시하지 않는다.

대신 도구의 격리된 JavaScript 실행 환경에서 다음을 확인했다.

- 159행 전체 JSON 파싱, 중복 ID/동일 요약 없음, 기존 ID 삭제 없음.
- 변경 12행의 필수 필드, 허용 상태·신뢰도, summary/timeline/목록 길이, 출처 필드 검사.
- 기존 행의 claims/entities/usage/knowledge/fact_type 변경 없음, 나머지 147행 원문 보존.
- audience_only 5행 원문 유지 및 전체 lore 직렬화에서 제외 유지.
- 원본 `compile_compact_lore`의 헤더·필터·라벨·시점·순서를 JavaScript로 재현해 전후 비교.
  Python 구현 자체를 실행한 통합 테스트는 아니다.
- cl100k_base 100,256개 병합 순위와 분할 규칙으로 UTF-8 BPE를 계산.
  `hello world` → [15339, 1917] 및 한국어 샘플을 확인했다.
  [비교용 순위 데이터](https://github.com/niieani/gpt-tokenizer/blob/main/src/bpeRanks/cl100k_base.js),
  [분할 규칙](https://github.com/niieani/gpt-tokenizer/blob/main/src/encodingParams/constants.ts).
  외부 코드를 실행하지 않고 데이터 배열만 JSON으로 파싱했다.

| 전체 정적 lore 문서 | main | 변경 후 | 차이 |
| --- | ---: | ---: | ---: |
| 원본 JSONL 행 | 157 | 159 | +2 |
| 주입 항목 | 152 | 154 | +2 |
| 제외된 audience_only | 5 | 5 | 0 |
| 문자 | 14,052 | 14,394 | +342 |
| UTF-8 바이트 | 32,473 | 33,277 | +804 |
| cl100k_base 토큰 | 14,178 | 14,560 | **+382 (+2.69%)** |

**cl100k_base는 동일 기준 비교치이며 운영 Gemini 토큰 수가 아니다.** 실제 운영 모델/키를
읽거나 countTokens API를 호출하지 않았다. 다른 시스템 지침·대화·동적 지식·캐시 비용은
이 수치에 포함되지 않는다. 현재 corpus에 community_meme가 없어 포함 옵션에 따른 차이는 없다.

환경이 복구되면 실행할 관련 검사(이번 실행 결과 아님):

```bash
uv run pytest tests/core/test_compact_lore.py tests/ai/test_full_lore_context.py
uv run python -m hina_bot.tooling.compact_lore_report
```

운영 모델의 실제 토큰 수는 같은 모델로 main과 브랜치 각각에
`compact_lore_report --count-tokens --model <운영 모델>`을 실행해 비교할 수 있다.
수동 응답 검토 시에는 동학년/직책 구분, 사건 당시의 협력과 사적 친분 구분,
관객만 아는 비밀을 직접 기억하지 않는지를 확인해야 한다. 실모델 환각 감소는 아직 측정하지 않았다.
