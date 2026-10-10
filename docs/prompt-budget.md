# Prompt budget

런타임 입력 토큰을 줄이기 위해 항상 주입되는 prompt와 질문별로 검색되는 reference의 역할을 분리합니다.

## 항상 주입하는 내용

- 고정 신뢰·보안·몰입 경계(`prompts/integration/base.md` → `POLICY`)
- 히나의 핵심 성격, 말투, 반복 연출 방지, 소수의 선택적 장난 규칙(`prompts/hina.md`)
- 현재 대화의 관계 모드(`ordinary_relationship.md` 또는 `special_dm.md`)
- 현재 시각과 지역 fallback에 필요한 짧은 runtime context

## 파일 배치

런타임 answer에 주입되는 정적 자연어 정책은 `src/hina_bot/prompts/integration/`에 둡니다.
Python 모듈은 상수 이름과 조건부 조립 로직만 유지하고, `load_prompt()`로 리소스를 읽습니다.
현재 시각·기능 상태처럼 런타임 값을 삽입하는 instruction builder는 코드에 남깁니다.

## lore로만 두는 내용

인물별 관계, 직책, 프로필, 사건, 장비, 시점·인지 범위처럼 질문에 따라 필요한 사실은
`src/hina_bot/data/lore.jsonl`에 두고 관련 질문에서만 검색합니다. 같은 사실을 `hina.md`에 다시
상시 기록하지 않습니다.

특히 아코·마코토·호시노·이부키·세나·이오리·치나츠 같은 특정 인물과의 관계나 역할은
캐릭터 prompt에 고정하지 않습니다. 관계의 사실성과 시점은 lore reference가 담당하고, 캐릭터
prompt는 그 사실에 반응하는 히나의 일반적인 성격만 담당합니다.

## 의도적으로 유지하는 중복 방지 경계

`POLICY`에는 prompt injection, 내부 지침 비공개, 실제 기능의 정직한 표현, 몰입, lore 해석처럼
모든 요청에 필요한 고정 invariant만 둡니다. 특정 Discord 상호작용이나 화자 귀속처럼 별도
integration policy가 담당하는 규칙과 우회 방식·키워드 예시 나열은 base policy에서 반복하지
않습니다.

고정 경계의 의미를 유지하면서도 같은 원칙을 여러 문장으로 반복하지 않도록 크기 회귀를
테스트합니다.

## 회귀 방지

`tests/ai/test_prompt_budget.py`는 base policy와 캐릭터·관계 prompt뿐 아니라 일반 응답에 항상
주입되는 정적 prompt 묶음의 전체 크기도 확인합니다. 현재 상한은 관계 prompt를 포함해 10,000
characters이며, runtime/capability처럼 요청마다 생성되는 짧은 동적 지침과 memory/search/vision 같은
조건부 지침은 이 합계에서 제외합니다. 개별 byte 수와 전체 character 수는 provider별 실제 token
수와 같지는 않지만 tokenizer에 종속되지 않는 간단한 크기 회귀 지표로 사용합니다.

실제 merge 전에는 기존 `evals/character_lore_cases.jsonl`의 캐릭터·관계·몰입 사례를 확인하고,
특히 일반 서버/DM/특별 DM에서 캐릭터성이나 관계 검색 품질이 떨어지지 않는지 smoke test합니다.
