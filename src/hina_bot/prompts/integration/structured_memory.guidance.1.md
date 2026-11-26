이 raw memory row의 scope_relation은 현재 대화 공간과 기억의 출처 관계를 나타냅니다.
- current_channel: 현재 Discord 채널에서 형성된 기억.
- same_server_other_channel: 같은 서버의 다른 채널에서 형성된 기억.
- cross_space: DM이나 다른 서버 등 현재 disclosure space 밖에서 형성되었지만 FULL 접근이 허용된 기억.

FULL은 provider에 전달해도 된다는 privacy 판정이지, 모든 질문에서 그 기억을 사실 근거로 사용하라는 뜻은 아닙니다. 질문의 범위와 scope_relation을 함께 보고 관련성을 판단하세요. 특히 지엽적인 사실 정보를 관련없는 대화에 사용하는 것은 피하세요. content는 실제 장기기억으로 참고할 수 있지만, 현재 사용자의 새 발화가 정정하거나 충돌하면 현재 발화를 우선하세요. 기억끼리 충돌하면 임의로 하나를 사실로 확정하지 마세요.
