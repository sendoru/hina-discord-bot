# Discord 사용자 식별

사용자 식별(identity resolution)은 현재 요청에 나온 이름을 Discord가 알고 있는 사용자 ID와 연결합니다.
이 과정은 기록이나 기억 조회와 독립적입니다. 예를 들어 `2_718281 핑해줘`에서 사용자를 식별했다고 해서
그 사람의 과거 대화나 공개 요약을 조회할 권한까지 생기지는 않습니다.

## 현재 멤버 소스

`DISCORD_MEMBERS_INTENT=true`를 설정하고 Discord Developer Portal에서
**Server Members Intent**를 켠 뒤 봇을 재시작합니다. 이 설정은 `/config`로 바꾸는 runtime 설정이
아니라 startup-only 설정입니다. 기본값은 false이며, 기존 배포가 설정하지 않은 privileged intent를
예기치 않게 요청하지 않도록 합니다. 활성화 전에 Portal 권한과 필요한 intent 심사를 모두 갖춰야 하며,
그렇지 않으면 Discord가 Gateway 연결을 4014 코드로 종료할 수 있습니다.

클라이언트는 discord.py의 members intent 캐시와 시작 시 chunking을 사용합니다. 해당 intent가 켜져 있고
`guild.chunked`가 true일 때만 디렉터리를 완전한 것으로 취급합니다. 따라서 서버 멤버 수가 많을수록
시작 시간과 메모리 사용량이 늘어날 수 있습니다. 개별 메시지를 처리하는 경로에서는 전체 멤버 fetch나
chunk를 실행하지 않습니다. 시작 시 chunking에 실패했거나 guild를 사용할 수 없으면 부분 캐시를 완전한
디렉터리로 간주하지 않습니다. Gateway의 멤버 업데이트로 현재 메타데이터를 유지하며, 별도의 영구
별칭 데이터베이스는 만들지 않습니다. Presences Intent는 필요하지 않습니다.

일반 guild 텍스트 채널에서는 `channel.permissions_for(member).view_channel`이 true인 현재 사람 멤버만
후보가 됩니다. 호출자 역시 해당 채널을 볼 수 있어야 합니다. 히나 계정과 다른 봇은 제외합니다.
비공개 스레드는 멤버십 의미가 별도이므로 텍스트 기반 식별을 보류하고, 현재 메시지에 실제로 포함된
Discord mention 재사용만 허용합니다. 호출자 자신의 username도 식별할 수 있지만, 이것이
현재 화자(current speaker)의 정체성을 바꾸지는 않습니다.

현재 별칭은 `display_name`, `global_name`, `name` 순서로 모으고 중복을 제거한 뒤 개수를 제한합니다.
이 현재 별칭들은 과거 표시 이름보다 우선하며, 과거 이름 네 개 때문에 현재 이름이 밀려나지 않습니다.

## 로컬 매칭과 시맨틱 폴백

내용이 있는 모든 guild 호출은 로컬 이름 근거를 확인할 수 있습니다. 기존의 프로필 질의 전용 정규식은
더 이상 사용자 식별의 진입 조건이 아니므로, 특정 동사를 추가로 맞출 필요가 없습니다.

완전한 디렉터리가 있으면 provider에 보낼 후보 수 제한을 적용하기 전에 현재 채널에서 보이는 전체 후보를
대상으로 로컬 매칭을 수행합니다. NFKC 정규화, 대소문자 접기(case folding), 공백 정규화를 사용하되
underscore 같은 username 구분자는 보존합니다. 일치는 단어/username 경계 또는 한국어 조사 경계를
포함한 전체 별칭에 걸쳐야 합니다. 영숫자 세 글자 미만의 짧은 별칭, 숫자로만 된 별칭, 임의의 부분 문자열은
자동으로 식별하지 않습니다. 정규화된 별칭이 중복되거나 한 요청에서 여러 사람을 지칭하면 모호한 것으로
처리하며, 첫 번째 멤버를 임의로 고르지 않습니다. 결과에는 현재 요청에서 실제로 사용된 원래 참조 구간을
그대로 유지합니다.

현재 별칭으로 유일한 정확 일치를 찾지 못하면 로컬 어휘/음성 검색으로 후보를 좁힙니다.
한글 음절의 로마자 변환, 표시 이름 token, 보수적인 철자 유사도는 검색 키로만 사용하며 사용자 ID를
직접 확정하는 근거는 아닙니다. 예를 들어 `센돌`로 `sendol`을 후보에 올릴 수는 있지만, 두 이름이
같은 사람을 뜻하는지는 기존 시맨틱 resolver가 최종 판단합니다. 이는 완전한 음역 시스템이 아니므로
익숙하지 않은 변형은 식별하지 못할 수 있습니다.

시맨틱 resolver에는 관련성이 있는 shortlist만 보내며, 최대 32명·사용자당 이름 4개로 제한합니다.
shortlist가 이보다 크면 모호한 것으로 처리하고 경쟁 후보를 조용히 잘라내지 않습니다. shortlist가
없으면 identity provider 호출도 하지 않습니다. 최종 답변 모델에는 멤버 디렉터리 전체를 전달하지 않습니다.

## 과거 기록 폴백

`shared_calls`는 현재 guild 디렉터리가 아니라 제한된 과거 이름 근거로만 사용합니다. 최근 256개 행,
최대 32명으로 제한합니다. 각 과거 이름은 자신의 출처 채널을 기준으로 필터링하며, 그 채널이 현재도
공개/읽기 가능하고 호출자에게도 보여야 합니다. 같은 사용자의 다른 공개 기록 하나가 있다고 해서
숨겨진 다른 출처 채널의 별칭까지 허용되지는 않습니다.

완전한 디렉터리가 있으면 현재 보이는 멤버에 과거 별칭을 보조적으로 붙여 시맨틱 매칭에 사용할 수 있지만,
현재 별칭을 먼저 검사합니다. 이미 서버를 떠났거나 현재 채널을 볼 수 없는 멤버는 폴백 후보에 들어갈 수
없습니다.

완전한 디렉터리가 없으면 로컬에서 먼저 좁힌 과거 identity만 기존 시맨틱 경로를 사용할 수 있습니다.
최대 8명까지 timeout과 일반 request concurrency 제한 아래에서 `fetch_member(id)`로 다시 검증합니다.
provider에 보내기 전에 현재 서버 멤버인지, 봇이 아닌지, 현재 채널을 볼 수 있는지를 모두 확인해야 합니다.
캐시에 없는 과거 후보를 현재 멤버라고 임의로 가정하지 않습니다. 이 경로는 유지되는 완전한 디렉터리보다
의도적으로 불완전하며, 모든 비활성 멤버를 찾아내거나 guild 전체에서 닉네임이 유일한지 보장할 수 없습니다.

## 답변 문맥과 조회 범위

request-scoped `resolved_identities` 필드는 선택된 identity를 최대 두 개까지 전달합니다. 각 항목에는
`user_id`, 요청에서 가져온 `reference`, 최대 네 개의 `names`가 들어갑니다.
이는 실제 Discord mention만을 뜻하는 `current_interaction.mentions`와 별개입니다. memory나 chat log가
꺼져 있어도 사용할 수 있으며, 해당 턴이 정상 종료되거나 예외로 끝나면 반드시 초기화합니다.
routing의 context size 계산에도 최종 답변 요청에 허용된 것과 동일한 identity 데이터를 포함합니다.

- 호출/핑: 식별된 identity만 전달하며 추가 target history나 target public memory는 조회하지 않습니다.
- 최근 발언 조회: identity와 제한된 현재 채널 target history를 사용하며 target public memory는 조회하지 않습니다.
- 프로필/깊은 기록/공개 기억 조회: 별도로 선택된 history와 권한이 확인된 public memory를 사용합니다.

기존의 일반 recent-context hydration과 현재 화자 자신의 기억 사용 방식은 바뀌지 않습니다.
식별된 identity를 전달하는 데 이 정보들은 필요하지 않습니다. 다른 사람을 식별해도
`current_speaker`는 바뀌지 않습니다.

## Privacy, mention, observability

`EXTERNAL_CONTEXT_POLICY=bot_interactions_only`에서는 멤버 디렉터리를 읽거나 provider를 호출하기 전에
텍스트 기반 cross-user 식별을 계속 차단합니다. 최종 `apply_context_policy` 경계에서도
`resolved_identities`를 독립적으로 제거하므로, adapter 단계에서 실수로 주입된 값도 외부로 나가지
않습니다. 명시적인 제3자 Discord mention은 텍스트 조회를 건너뛰고 기존 #250의 재사용 정책을 유지합니다.
일반 사용자 mention은 계속 `ALLOW_USER_MENTIONS`로 제어하며, everyone/here/role mention은 실제 알림으로
동작하지 않게 유지합니다.

`identity.resolution` telemetry는 기존 outcome/count와 HMAC group에 더해
`resolution_method`(`exact`, `semantic`, `policy`)와 `directory_complete`를 기록합니다.
정확 일치는 `resolver_invoked=false`, `evidence_source=member_directory`이고,
시맨틱 식별은 `resolver_invoked=true`, `evidence_source=resolver_derived`입니다.
로컬 결과는 provider 호출로 집계하지 않습니다. 최종 context provenance에는 원래 이름이나 참조 원문 없이
선택·차단된 identity 개수만 기록합니다. 이 관측 정보로 assistant 출력에서 새 별칭을 학습하지 않습니다.

## Discord 참고 자료

- [discord.py intent, 멤버 캐시와 조회](https://discordpy.readthedocs.io/en/stable/intents.html)
- [Discord privileged intent 설정](https://docs.discord.com/developers/events/gateway#privileged-intents)
- [Guild 멤버 요청](https://docs.discord.com/developers/events/gateway-events#request-guild-members)
- [전체 멤버 요청 rate limit](https://docs.discord.com/developers/change-log#introducing-rate-limit-when-requesting-all-guild-members)

Gateway를 통한 guild 전체 멤버 요청은 bot 하나당 guild별 30초에 한 번으로 제한됩니다.
prefix query와 `fetch_member(id)`는 목적이 서로 다르며, 완전한 정규화 별칭 디렉터리를 대신할 수 없습니다.
봇은 개별 사용자 메시지를 처리할 때 guild 전체 멤버 요청을 실행하지 않습니다.
