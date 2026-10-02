# 현재 채널 멤버 조회 도구

일반 답변 모델은 guild 텍스트 채널에서 `get_current_channel_members` 로컬 function tool을
`tool_choice=auto`로 사용할 수 있습니다. 이 도구는 사용자가 말한 사람을 Discord 사용자와
연결하거나 정확한 user mention ID가 필요한 경우를 위한 것입니다.

## Discord 전제

봇은 Server Members Intent를 항상 요청합니다. Discord Developer Portal에서도
**Server Members Intent**를 켜야 하며 변경 후 봇을 재시작해야 합니다.

멤버 디렉터리는 discord.py의 guild member cache와 시작 시 chunking을 사용합니다.
`guild.chunked`가 true일 때만 완전한 디렉터리로 취급합니다. 디렉터리가 불완전하면
도구는 부분 명단을 반환하지 않고 `member_directory_incomplete` 오류로 종료합니다.

## 반환 범위

도구에는 인자가 없습니다. 현재 요청의 guild와 채널은 애플리케이션이 고정하므로 모델이 다른
guild/channel ID를 지정할 수 없습니다.

반환 대상은 현재 채널에 `view_channel` 권한이 있는 일반 사용자입니다. 히나 자신과 다른 봇은
제외합니다. 각 사용자에 대해 다음 필드만 반환합니다.

- `user_id`: Discord user ID
- `server_nickname`: 현재 guild에서 설정된 nickname
- `global_name`: Discord global display name
- `username`: Discord username

서버 닉네임이 없으면 `server_nickname`은 빈 문자열입니다. 자연어 별명과 Discord 이름이 완전히
같지 않더라도 대화 모델이 현재 요청의 문맥과 이름 정보를 함께 보고 같은 사람인지 판단할 수 있습니다.
여러 후보가 그럴듯하면 임의의 user ID를 만들거나 선택하지 않고 사용자에게 확인하도록 지시합니다.

## 요청과 token 비용

멤버 목록은 모든 answer request에 선제적으로 붙이지 않습니다. 첫 answer request에는 function schema만
포함하고, 모델이 실제로 도구를 호출한 경우에만 현재 채널 멤버 목록이 function output으로 다음 model
round에 전달됩니다. 따라서 일반 잡담에서는 roster token 비용이 발생하지 않습니다.

도구 호출은 기존 bounded local-function loop를 사용합니다. 모델이 반복해서 로컬 도구만 호출하면
일반 local tool round limit이 적용됩니다.

## Privacy와 mention

`EXTERNAL_CONTEXT_POLICY=bot_interactions_only`에서도 이 도구의 현재 Discord identity metadata는
모델이 필요하다고 선택한 경우 provider로 전달될 수 있습니다. 이는 다른 사용자의 대화 내용, 장기 기억,
relationship profile이나 cross-channel public memory를 허용하는 것과는 별개입니다. 해당 데이터들은
기존 egress policy를 그대로 따릅니다.

도구에서 확인한 `user_id`는 사용자가 그 사람을 명시적으로 불러 달라거나 핑해 달라고 요청한 경우
`<@user_id>` 형태로 사용할 수 있습니다. 일반 사용자 mention의 실제 활성화 여부는
`ALLOW_USER_MENTIONS`를 따르며, @everyone/@here/role mention은 기존 output safety 경계에서 계속
비활성화됩니다.
