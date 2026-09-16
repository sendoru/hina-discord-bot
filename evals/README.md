# 평가 데이터와 실행 방법

## 모델 라우팅 분류기 평가

`model_routing_cases.jsonl`은 짧지만 어려운 요청, 길게 답해 달라는 단순 요청, 디버깅·설계·증명,
일반 대화와 사용자 소유 follow-up을 포함합니다. 라이브 classifier만 호출하고 최종 답변은 생성하지
않습니다.

```bash
uv run hina-routing-eval --provider openai --model gpt-4.1-mini
uv run hina-routing-eval --limit 10 --fail-on-mismatch
```

`ROUTING_CLASSIFIER_API_KEY`가 있으면 우선 사용하고, 없으면 선택한 provider의 일반 key를 사용합니다.
결과는 `data/evals/model-routing-*.json`에 저장하며 semantic level 일치율, 최종 tier 일치율,
classifier 실패, smart 누락·과잉 선택 수를 집계합니다. 고정밀 rule이나 객관적 부하로 운영 중 호출을
생략할 사례도 classifier 자체의 회귀를 보기 위해 평가에서는 한 번 분류합니다. 이 평가는 유료 API
호출이므로 일반 테스트에는 포함되지 않습니다.

## 일상 화법 평가

`hina-eval --cases evals/tone_cases.jsonl --provider <provider> --model <model>`로
29개 화법 사례를 실행할 수 있어요. 모델 API 키와 비용이 필요하며 기본 단위 테스트에서는
실행하지 않아요. `--id <case-id>`로 일부만 실행할 수도 있어요.
결과 JSONL과 Markdown의 실제 응답을 `expected`와 비교해서 수동 평가해요.
이 러너의 정상 종료는 화법 PASS를 의미하지 않아요.

`turns`는 기존 문자열 배열을 지원하며, server 모드에서는
`{"input":"안녕", "user_id":910011, "speaker":"A"}` 객체도 지원해요.
턴마다 화자를 바꿀 수 있고 생성된 답변과 대상 사용자 정보가 다음 턴의 채널 문맥에 들어가요.
최근 채널 문맥은 12개 메시지까지 유지하고 개인 대화 이력은 사용자별로 분리해요.
보고서에 턴별 이름과 ID가 표시돼요. 실제 사용자 정보 대신 가상 ID로 사례를 작성해요.
캐릭터 자료의 출처와 조정 의도는 [검토 기록](../docs/character-tone-review.md)을 참고해요.

## 인젝션 평가 실행

일반 테스트는 API 키 없이 실행하며 권한, 데이터 격리, 입력 역할, Discord 멘션 무력화를
결정적으로 검사해요. `prompt_injection_cases.jsonl`은 직접 공격과 표시 이름·메모·요약·
DM 이력·채널 문맥·공개 기억·이모지 설명을 통한 간접 공격을 포함해요.

라이브 평가는 유료 OpenAI 요청을 명시적으로 실행할 때만 동작해요. 기본값은 18개 사례를
각 2회 실행하고, 답변 생성과 평가에 각각 한 번씩 요청하므로 최대 72회 요청해요.
비용을 줄이려면 `--repeat 1 --no-judge`를 사용해 18회 생성 결과만 저장한 뒤 직접 검토해요.

```bash
python scripts/run_prompt_injection_eval.py --repeat 1 --no-judge
python scripts/run_prompt_injection_eval.py --repeat 2
```

키는 환경의 `OPENAI_API_KEY`에서만 읽고 출력하거나 결과 파일에 저장하지 않아요.
실행 결과는 Git에서 제외된 `evals/results/`에 JSON으로 남아요. 평가용 무작위 canary를
시스템 지침에 추가하고, 원문 응답에 canary가 없는지와 실제 전달문에 Discord 멘션 문법이
없는지를 코드로 검사해요. 선택한 모델 평가기는 사례별 기대 행동을 의미 단위로 판정해요.

모델 평가는 확률적이며 평가기 자체도 틀릴 수 있어요. 실패 결과와 경계 사례는 사람이
확인하고, 재현 가치가 있으면 JSONL에 회귀 사례로 추가해요. 모델이나 프롬프트를 변경하면
같은 데이터셋으로 다시 실행해 이전 결과와 비교해요.
