# 취향 축: 축 이름 -> (왼쪽 극, 오른쪽 극)
AXES = {
    "practical_emotional": ("practical", "emotional"),
    "home_outdoor": ("home", "outdoor"),
    "minimal_maximal": ("minimal", "maximal"),
    "classic_trendy": ("classic", "trendy"),
    "object_experience": ("object", "experience"),
    "solo_together": ("solo", "together"),
}
AXIS_TAGS = frozenset(tag for poles in AXES.values() for tag in poles)

POLE_LABELS = {
    "practical": "실용",
    "emotional": "감성",
    "home": "집콕",
    "outdoor": "외출",
    "minimal": "미니멀",
    "maximal": "맥시멀",
    "classic": "클래식",
    "trendy": "트렌디",
    "object": "물건",
    "experience": "경험",
    "solo": "혼자",
    "together": "함께",
}

CATEGORIES = {
    "scent": "향",
    "food": "먹거리",
    "fashion": "패션잡화",
    "tech": "테크/가젯",
    "beauty": "뷰티",
    "interior": "홈인테리어",
    "hobby": "취미",
    "stationery": "문구",
}

# 세부 태그 -> 상위 카테고리
SUBTAGS = {
    "perfume": "scent",
    "candle": "scent",
    "dessert": "food",
    "coffee": "food",
    "tea": "food",
    "alcohol": "food",
    "sports": "hobby",
    "camping": "hobby",
    "game": "hobby",
    "book": "hobby",
    "music": "hobby",
}

SUBTAG_LABELS = {
    "perfume": "향수",
    "candle": "캔들",
    "dessert": "디저트",
    "coffee": "커피",
    "tea": "차",
    "alcohol": "술",
    "sports": "운동",
    "camping": "캠핑",
    "game": "게임",
    "book": "독서",
    "music": "음악",
}

ALL_TAGS = AXIS_TAGS | CATEGORIES.keys() | SUBTAGS.keys()


def category_of(tag: str) -> str | None:
    if tag in CATEGORIES:
        return tag
    return SUBTAGS.get(tag)
