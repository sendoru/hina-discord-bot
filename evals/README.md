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
classifier 실패, smart 누락·과잉 선택 수를 집계합니다. 운영에서는 이미 물리적 load 또는 고정밀
로컬 semantic hint만으로 smart가 확정되면 reasoning 판정을 생략할 수 있지만, 이 평가는 classifier
자체의 회귀를 보기 위해 각 사례를 한 번 분류합니다. 이 평가는 유료 API 호출이므로 일반 테스트에는
포함되지 않습니다.

현재 `chat-hybrid-v4` classifier는 같은 응답에서 웹 검색 필요성도 분류하지만,
`hina-routing-eval`은 reasoning level과 model tier만 점수화합니다. 웹 검색 판정은 결정적 unit test와
`tests/ai/test_semantic_web_routing.py`에서 별도로 검증하며, 이 러너의 성공을 web routing 전체의
정확도 평가로 해석하지 않습니다.

## 일상 화법 평가

`hina-eval --cases evals/tone_cases.jsonl --provider <provider> --model <model>`로
화법 사례를 실행할 수 있어요. 모델 API 키와 비용이 필요하며 기본 단위 테스트에서는
실행하지 않아요. `--id <case-id>`로 일부만 실행할 수도 있어요.
결과 JSONL과 Markdown의 실제 응답을 `expected`와 비교해서 수동 평가해요.
이 러너의 정상 종료는 화법 PASS를 의미하지 않아요.

Gemini의 경우 `--gemini-thinking-level minimal|low|medium|high`로 fixed eval의 추론 강도를
명시할 수 있어요. 지정하지 않으면 `GEMINI_THINKING_LEVEL`, 그것도 없으면 `low`를 사용해요.
`CHAT_WEB_SEARCH=false`도 live eval 설정에 반영되므로 말투 비교에서는 검색 호출을 끌 수 있어요.

### GitHub Actions live tone 비교

`.github/workflows/live-tone-eval.yml`은 `workflow_dispatch`로만 실행돼요. push/PR에서는 모델 API를
자동 호출하지 않아요. Repository Actions secret에 `GEMINI_API_KEY`를 등록한 뒤 baseline/candidate
ref, suite 파일, 반복 횟수를 선택하면 같은 case ID 집합을 다음 네 조합으로 순차 실행해요. 현재 기본 설정에 맞춰 FAST는 `gemini-3.5-flash-lite` + `minimal`, SMART는 `gemini-3.6-flash` + `medium`으로 설정되어 있어요.

- baseline + FAST model/thinking
- baseline + SMART model/thinking
- candidate + FAST model/thinking
- candidate + SMART model/thinking

suite 파일은 한 줄에 case ID 하나를 적고 빈 줄과 `#` 주석을 사용할 수 있어요.
예: `evals/suites/ambiguous-input.txt`. 결과 JSONL/Markdown과 usage log는 하나의 Actions artifact로
14일간 보관해요. tone PASS/FAIL 자체는 자동 판정하지 않고 사람이 네 결과를 비교해요.

workflow는 각 ref의 `hina-eval`을 실제로 설치해 실행하므로 baseline/candidate ref 모두 이 live-eval
runner 기능(`--gemini-thinking-level`, `CHAT_WEB_SEARCH` 반영)을 포함한 커밋을 기반으로 두는 것을
권장해요. 오래된 ref를 비교해야 한다면 이 인프라 커밋을 동일하게 적용한 두 비교 브랜치를 만든 뒤
실행하면 돼요.

확률적인 회귀는 `--repeat 3`처럼 같은 사례를 반복해 확인할 수 있어요. 사례의 `validators`에는
`python_fenced_code`, `python_syntax`를 지정할 수 있고, 생성 코드를 실행하지 않은 채 마지막
응답의 Python 코드 블록 존재 여부와 구문만 검사해요. validator 실패는 JSONL과 Markdown
리포트에 별도로 기록돼요.

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

## 실서비스 유래 대화 품질 회귀

`production_quality_cases.jsonl`은 실제 대화에서 발견된 실패 모양을 개인정보가 남지 않도록
익명화·재서술한 작은 회귀 세트입니다. 원본 사용자 ID, 메시지 ID, 첨부 URL이나 대화 원문 전체를
복사하지 않고 다음과 같은 실패 구조만 보존합니다.

- 근거 없는 과거 연속성·약속 확정
- 가벼운 표현을 훈계로 전환
- 낯선 밈/조어에 반복적으로 대화 중단 요구
- 다국어 입력의 이해 가능한 부분까지 버림
- 짧지만 맥락 해석이 필요한 세계관 follow-up 회피
- 멀티봇 문장에서 호격 대상과 언급 대상을 혼동
- 근거 없는 지시어 보완
- assistant 호격을 이미지 subject의 자기 동일시 근거로 오인

이미지 회귀는 원본 운영 이미지를 저장하지 않고 `evals/fixtures/` 아래의 익명화된 합성 fixture를
base64 텍스트로 둡니다. case의 `visuals` 배열은 현재 single-turn eval에서만 지원하며
`fixture`, `mime_type`, 선택적인 `name`을 지정합니다. 러너는 이를 실제 `VisualInput`으로
복원하므로 provider에 전달되는 vision policy까지 함께 평가할 수 있습니다. Discord의 이미지
retrieval/causal continuity 자체는 이 eval 경로에서 흉내 내지 않습니다.

고정 FAST/SMART 비교만으로는 운영 routing 자체의 실패를 구분할 수 없으므로
`hina-eval`에 adaptive mode도 제공합니다.

```bash
CHAT_WEB_SEARCH=false hina-eval \
  --cases evals/production_quality_cases.jsonl \
  --routing-mode adaptive \
  --provider gemini \
  --fast-model gemini-3.5-flash-lite \
  --smart-model gemini-3.6-flash \
  --routing-classifier-mode active \
  --routing-classifier-provider gemini \
  --routing-classifier-model gemini-3.5-flash-lite
```

adaptive eval에서는 각 turn에 opaque `turn_id`를 부여하고 usage telemetry의 실제 `answer` row를
결과에 다시 연결합니다. Markdown 결과의 각 turn에는 선택된 tier/model, semantic level,
decision source가 표시됩니다. 따라서 같은 사례가 실패했을 때 다음처럼 구분할 수 있습니다.

- SMART에서는 괜찮고 adaptive가 FAST를 골라 실패: routing 후보
- FAST/SMART 모두 같은 방식으로 실패: prompt/context representation 후보
- adaptive가 SMART를 골라도 실패: 단순 tier 승격보다 context/policy 검토 우선

GitHub Actions의 `Live production quality eval` workflow는
`evals/suites/production-quality.txt`를 baseline/candidate 양쪽에 같은 adaptive 설정으로 실행합니다.
이 workflow의 새 CLI 옵션을 사용하므로 baseline과 candidate ref 모두 이 eval infrastructure를 포함한
커밋 이후를 사용해야 합니다. 결과 artifact에는 JSONL, Markdown report, usage log가 함께 남습니다.

이 세트는 운영 로그를 그대로 저장하는 archive가 아닙니다. 새로운 실패를 발견해도 재현에 필요한
최소 형태로 일반화할 수 있을 때만 case를 추가합니다.

