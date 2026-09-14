from __future__ import annotations

from dataclasses import dataclass


TAXONOMY_VERSION = "taxonomy.v1"


@dataclass(frozen=True)
class TaxonomyTopic:
    key: str
    label: str
    parent_key: str | None
    level: int


_TOPIC_TREE: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "technology": ("科技", (("ai", "人工智能"), ("programming", "编程"), ("hardware", "硬件"), ("consumer_electronics", "消费电子"), ("internet", "互联网"))),
    "science": ("科学", (("physics", "物理"), ("astronomy", "天文"), ("biology", "生物"), ("medicine", "医学"), ("engineering", "工程"))),
    "knowledge": ("知识", (("history", "历史"), ("humanities", "人文"), ("law", "法律"), ("education", "教育"), ("language", "语言"))),
    "business_finance": ("商业财经", (("economics", "经济"), ("investing", "投资"), ("entrepreneurship", "创业"), ("workplace", "职场"))),
    "gaming": ("游戏", (("gameplay", "游戏实况"), ("esports", "电竞"), ("game_review", "游戏评测"), ("game_development", "游戏开发"))),
    "anime_comics": ("动画漫画", (("anime", "动画"), ("comics", "漫画"), ("virtual_creator", "虚拟主播"))),
    "film_tv": ("影视", (("movie", "电影"), ("tv_series", "剧集"), ("documentary", "纪录片"), ("film_commentary", "影视评论"))),
    "music": ("音乐", (("performance", "演奏"), ("music_theory", "音乐理论"), ("instruments", "乐器"))),
    "sports": ("运动", (("ball_sports", "球类"), ("motorsport", "赛车"), ("outdoor", "户外"), ("fitness", "健身"))),
    "lifestyle": ("生活", (("daily_life", "日常"), ("home", "家居"), ("fashion", "时尚"), ("beauty", "美妆"), ("relationships", "情感"))),
    "food": ("美食", (("cooking", "烹饪"), ("restaurant_review", "探店"), ("food_culture", "饮食文化"))),
    "travel": ("旅行", (("destination", "目的地"), ("travel_guide", "旅行攻略"))),
    "automotive": ("汽车交通", (("cars", "汽车"), ("motorcycles", "摩托车"), ("transport", "交通"))),
    "news_society": ("时事社会", (("current_affairs", "时事"), ("society", "社会"))),
    "entertainment": ("娱乐", (("celebrity", "明星"), ("variety", "综艺"), ("comedy", "喜剧"))),
    "animals": ("动物", (("pets", "宠物"), ("wildlife", "野生动物"))),
    "other": ("其他", (("uncategorized", "未分类"),)),
}


def taxonomy_topics() -> tuple[TaxonomyTopic, ...]:
    topics: list[TaxonomyTopic] = []
    for root_key, (root_label, children) in _TOPIC_TREE.items():
        topics.append(TaxonomyTopic(root_key, root_label, None, 1))
        topics.extend(
            TaxonomyTopic(
                key=f"{root_key}.{child_key}",
                label=child_label,
                parent_key=root_key,
                level=2,
            )
            for child_key, child_label in children
        )
    return tuple(topics)


TAXONOMY_TOPICS = taxonomy_topics()
TAXONOMY_BY_KEY = {topic.key: topic for topic in TAXONOMY_TOPICS}
TAXONOMY_LEAF_KEYS = frozenset(
    topic.key for topic in TAXONOMY_TOPICS if topic.level == 2
)
