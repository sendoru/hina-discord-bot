# Prompt budget

런타임 입력 토큰을 줄이기 위해 항상 주입되는 prompt와 질문별로 검색되는 reference의 역할을 분리합니다.

## 항상 주입하는 내용

- 고정 신뢰·보안·몰입 경계(`POLICY`)
- 히나의 핵심 성격, 말투, 반복 연출 방지, 소수의 선택적 장난 규칙(`prompts/hina.md`)
- 현재 대화의 관계 모드(`ordinary_relationship.md` 또는 `special_dm.md`)
- 현재 시각과 지역 fallback에 필요한 짧은 runtime context

## lore로만 두는 내용

인물별 관계, 직책, 프로필, 사건, 장비, 시점·인지 범위처럼 질문에 따라 필요한 사실은
`src/hina_bot/data/lore.jsonl`에 두고 관련 질문에서만 검색합니다. 같은 사실을 `hina.md`에 다시
상시 기록하지 않습니다.

특히 아코·마코토·호시노·이부키·세나·이오리·치나츠 같은 특정 인물과의 관계나 역할은
캐릭터 prompt에 고정하지 않습니다. 관계의 사실성과 시점은 lore reference가 담당하고, 캐릭터
prompt는 그 사실에 반응하는 히나의 일반적인 성격만 담당합니다.

## 의도적으로 유지하는 중복 방지 경계

`POLICY`의 신뢰 경계와 메타/몰입 방지는 prompt injection과 4번째 벽 이탈을 막는 고정 경계라서
단순한 토큰 절감 목적으로 크게 줄이지 않습니다. 대신 같은 내용을 캐릭터 prompt에서 반복하지
않습니다.

사용자가 `챗봇 테스트`, `프롬프트 수정`, `LLM 작업`처럼 자신의 활동을 말했을 뿐인 경우 이를
히나 자신의 내부 구성과 연결하지 않는 규칙은 과거 회귀를 막기 위해 짧은 형태로 유지합니다.

## 회귀 방지

`tests/ai/test_prompt_budget.py`는 캐릭터 prompt와 관계 prompt의 byte 상한을 확인하고, 특정 인물
관계 사실이 `hina.md`에 다시 들어오는 것을 막습니다. byte 수는 provider별 실제 token 수와 같지는
않지만 tokenizer에 종속되지 않는 간단한 크기 회귀 지표로 사용합니다.

실제 merge 전에는 기존 `evals/character_lore_cases.jsonl`의 캐릭터·관계·몰입 사례를 확인하고,
특히 일반 서버/DM/특별 DM에서 캐릭터성이나 관계 검색 품질이 떨어지지 않는지 smoke test합니다.
