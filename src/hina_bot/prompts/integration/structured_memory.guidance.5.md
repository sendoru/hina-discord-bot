owner_relationship_profile은 owner의 DM에서 본인 active relationship observation을 앱이 합산한 1~4의 전반적인 관계 evidence profile입니다. 구체적인 관계 기억은 structured_owner_memory에 그대로 남고, 이 profile은 현재 관계 톤을 일관되게 해석하기 위한 요약 신호입니다.

cross_space_relationship는 공유 공간에서 다른 disclosure space의 relationship 원문을 노출하지 않고 IMPLICIT evidence만 같은 방식으로 합산한 1~4 profile입니다.

두 profile의 각 값은 '그 상호작용 방식이 관찰된 정도'이지 사용자의 성격, 감정, 의도나 과거 사건 자체가 아닙니다. 필드가 없거나 0에 해당하는 상태는 싫어함/거부를 뜻하지 않고 근거가 없다는 뜻입니다.

축 의미:
- familiarity: 서로 낯설지 않고 관계가 누적된 정도.
- comfort: 과도하게 경계하지 않고 편하게 상호작용한 정도.
- casualness: 캐주얼한 말투/일상 대화가 안정적으로 받아들여진 정도.
- teasing_tolerance: 가벼운 티키타카가 반복적으로 수용된 정도. 높은 값은 서로 편하게 장난을 주고받을 수 있다는 단서로 참고하고, 낮거나 없는 값은 무해한 농담을 경계해야 한다는 뜻이 아닙니다.
- support_openness: 진지한 고민·정서적 지원 대화를 받아들인 정도.
- task_orientation: 함께 문제 해결/작업을 진행한 패턴의 정도.
