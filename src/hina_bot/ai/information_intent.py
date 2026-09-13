import re

_PERSONAL_CONTEXT_QUERY = re.compile(
    r"(?:내\s*(?:생일|이름|취향|정보|기억)|나에\s*대해|내가\s*(?:말한|얘기한)|"
    r"기억해|기억하고|방금|아까|저번에|전에\s*말한|우리\s*(?:대화|얘기))",
    re.IGNORECASE,
)
_SELF_IDENTITY_QUERY = re.compile(
    r"^\s*(?:너|넌|니가|네가|너는)\b.*(?:누구|정체|AI|봇|모델)", re.IGNORECASE
)
_RELATION_EVENT_QUERY = re.compile(
    r"(?:만나(?:본|봤|난)\s*적|만난\s*적|대화한\s*적|마주친\s*적).*(?:있|없)|"
    r"(?:무슨|어떤)\s*(?:사이|관계)|(?:친해|친한|친분|접점|서로\s*알)|"
    r"(?:그때|당시|사건|스토리|에피소드).*(?:했|알|봤|만났|참여)|"
    r"(?:들어가(?:본|봤|간)|방문한|가본)\s*적.*(?:있|없)",
    re.IGNORECASE,
)
_SIMPLE_WORLD_FACT_QUERY = re.compile(
    r"(?:어느\s*조직|어디\s*소속|소속이야|직책|학년|나이|몇\s*살|생일|키|무기|"
    r"총\s*이름|무슨\s*총|헤일로|날개|뿔|취미|학교|부서).*(?:뭐|무엇|어디|몇|언제|이야|야|해|있)?|"
    r"(?:누구야|누구지|누구인지)",
    re.IGNORECASE,
)
_SELF_PROFILE_QUERY = re.compile(
    r"^\s*(?:히나야[,!~\s]*)?(?:(?:지금|오늘|현재)\s*)?"
    r"(?:(?:(?:소라사키\s*)?히나|너|넌|너는|너의|네|니)(?:은|는|이|가|의)?\s*)?"
    r"(?:(?:지금|오늘|현재)\s*)?"
    r"(?:생일|나이|몇\s*살|키|학년|소속|직책|취미|고유\s*무기|무기|"
    r"(?:기관)?총\s*이름|무슨\s*총|학교|부서|헤일로|날개|뿔)",
    re.IGNORECASE,
)

_PROFILE_FIELDS = (
    (r"생일", "생일"),
    (r"(?:나이|몇\s*살)", "나이"),
    (r"키", "키"),
    (r"학년", "학년"),
    (r"(?:소속|학교|부서|직책)", "소속 직책 학교 부서"),
    (r"(?:고유\s*무기|무기|(?:기관)?총\s*이름|무슨\s*총)", "고유무기 무기 총 이름"),
    (r"취미", "취미"),
    (r"헤일로", "헤일로"),
    (r"날개", "날개"),
    (r"뿔", "뿔"),
)


def personal_context(content: str) -> bool:
    return bool(_PERSONAL_CONTEXT_QUERY.search(content))


def self_identity(content: str) -> bool:
    return bool(_SELF_IDENTITY_QUERY.search(content))


def relation_or_event(content: str) -> bool:
    return bool(_RELATION_EVENT_QUERY.search(content))


def self_profile(content: str) -> bool:
    return bool(_SELF_PROFILE_QUERY.search(content))


def simple_world_fact(content: str) -> bool:
    return bool(_SIMPLE_WORLD_FACT_QUERY.search(content))


def world_fact(content: str) -> bool:
    if personal_context(content) or self_identity(content):
        return False
    return self_profile(content) or relation_or_event(content) or simple_world_fact(content)


def lore_query(content: str, *, profile: bool, relation: bool) -> str:
    if profile:
        fields = []
        for pattern, canonical in _PROFILE_FIELDS:
            if re.search(pattern, content, re.IGNORECASE):
                fields.extend(canonical.split())
        suffix = " ".join(dict.fromkeys(fields)) or content.strip()
        return f"소라사키 히나 {suffix}".strip()
    if relation:
        hints = []
        if re.search(r"만나|마주|대면|대화|친분|접점|서로\s*알", content):
            hints += ["직접", "만남", "대면", "대화", "관계", "접점"]
        if re.search(r"들어가|방문|의장실|찾아가|출입", content):
            hints += ["방문", "출입", "직접"]
        if re.search(r"사건|그때|당시|참여|스토리|에피소드|전투|대치", content):
            hints += ["사건", "참여", "행적", "직접", "경험"]
        if hints:
            return content + " " + " ".join(dict.fromkeys(hints))
    return content
