# Dotenv 관리 도구

두 명령은 프로젝트 루트에서 실행하며, 모두 .env.example의 **순서·주석·기본값**을 기준으로 출력합니다.
uv sync 또는 pip install -e . 후 사용할 수 있습니다. 파일에 Discord 토큰이나 API 키가 포함될 수
있으므로 **출력 파일과 백업 파일을 외부에 공개하거나 Git에 커밋하지 마세요.**

## 1. 기존 .env 정규화

    uv run hina-env-normalize --check
    uv run hina-env-normalize --write

    # 실제 설정이 .env.local에 있다면
    uv run hina-env-normalize --env .env.local --write

- 기본 동작은 --check입니다. 변경이 필요하면 종료 코드 1, 동일하면 0, 오류는 2입니다.
- --write는 변경된 경우에만 파일명.bak.<UTC 타임스탬프> 백업을 생성한 후 파일을 원자적으로 교체합니다.
- 템플릿에 선언된 변수만 남깁니다. 기존 .env에만 있는 변수는 **이름을 경고하고 제외**합니다.
- 기존 .env에 변수가 명시적으로 존재하면 빈 문자열이라도 그 값을 유지합니다.
- 템플릿에서 주석 처리된 변수는 값이 없으면 계속 주석으로 남고, 기존 파일에 값이 있으면 활성화합니다.
- 중복 변수나 해석할 수 없는 줄은 오류로 처리합니다. 출력에는 비밀값을 표시하지 않습니다.

## 2. DB runtime config 포함 운영 설정 내보내기

    uv run hina-env-export --db data/hina.sqlite3 --env .env --output .env.exported

    # 실제 설정이 .env.local에 있는 경우
    uv run hina-env-export --env .env.local --db data/hina.sqlite3 --output .env.exported

값 우선순위는 다음과 같습니다.

1. runtime_config의 **DB override**
2. runtime_config_startup의 **마지막 봇 시작 당시** 스냅샷 (runtime 지원 설정에 한함)
3. 지정한 --env 파일의 명시적 값
4. .env.example의 기본값

DISCORD_TOKEN, provider API 키, DATABASE_PATH, HISTORY_TURNS처럼 runtime 수정 대상이 아닌
변수는 --env와 템플릿에서 가져옵니다. --db를 생략하면 --env의 DATABASE_PATH를 사용하며,
없으면 템플릿 값을 사용합니다.

- DB는 **읽기 전용**으로 연결하며 수정하지 않습니다. 모든 runtime 설정은 기존
  RUNTIME_SETTING_SPECS의 이름과 자료형을 사용해 dotenv 형식으로 변환됩니다.
- 영역별 Gemini 저장 플래그가 inherit (null)이면 **변수를 주석 처리**합니다. inherit 문자열을
  환경변수 값으로 쓰지 않습니다.
- 알 수 없거나 손상된 DB 설정은 오류로 보고하여, 불완전한 추출 결과를 만들지 않습니다.
- 출력 파일이 이미 있으면 실패하며, 덮어쓰려면 --force가 필요합니다.
- 출력 파일에 secret 값이 포함되므로 유닉스 계열에서 0600 권한으로 기록합니다.
- .env.local, 프로세스 환경변수, 다른 호스트에서 주입된 startup-only secret은 DB에 기록되지
  않습니다. **실제로 사용한 비밀값을 모두 내보내려면 해당 값을 가진 파일을 --env로 지정**하세요.
- runtime_config_startup은 현재 실행 중인 프로세스의 실시간 상태가 아니라 가장 최근 봇 시작
  스냅샷입니다. 봇을 재시작하지 않고 .env를 변경했더라도 이 스냅샷 값이 우선됩니다.

두 도구 모두 .env.example에 없는 임의의 변수는 결과에 포함하지 않으므로, 새 설정은 먼저
.env.example에 선언해야 합니다. 관련 회귀 테스트는 runtime 설정에 대응하는 템플릿 키가
모두 존재하는지 확인합니다.
