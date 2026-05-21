#!/usr/bin/env python3
"""Feishu field adapter for Weiyu-style AI topic cards.

This script reuses the existing Horizon collection pipeline and only replaces
how each selected item is converted into Feishu Bitable fields.
"""

import asyncio
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import run_topic_collector as base


FIELD_NAMES = [
    "热点",
    "时间",
    "切入点",
    "选题",
    "标题",
    "选题受众",
    "选题目的",
    "开头",
    "中间",
    "结尾",
    "逐字稿",
    "一级分类",
    "来源平台",
    "原始链接",
    "AI评分",
    "可靠性",
    "适合内容形态",
    "状态",
    "去重Key",
    "标签",
    "AI摘要",
]


def clean_text(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"<[^>]+>", "", text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def first_sentence(summary: str, fallback: str, limit: int = 120) -> str:
    text = clean_text(summary or fallback, 500)
    if not text:
        return clean_text(fallback, limit)
    parts = re.split(r"[。.!?！？]\s*", text)
    return clean_text(parts[0] or text, limit)


def display_time(item: Any) -> str:
    for name in ("published_at", "created_at", "updated_at", "timestamp", "time"):
        value = base.get_attr(item, name)
        if value:
            return str(value).replace("T", " ")[:16]
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y/%m/%d %H:%M")


def topic_label(title: str, tags: list[str]) -> str:
    tool = base.tool_name(title, tags)
    if tool:
        return clean_text(tool, 28)
    title = re.sub(r"[\[\]【】()（）]", " ", title)
    return clean_text(title, 28) or "这个AI热点"


def hot_field(title: str, summary: str) -> str:
    explanation = first_sentence(summary, title, 120)
    if explanation and explanation.lower() not in title.lower():
        return f"外网热议“{clean_text(title, 120)}”\n（{explanation}）"
    return f"外网热议“{clean_text(title, 140)}”"


def topic_plan(category: str, label: str) -> str:
    if category == "AI工具":
        return f"{label}工具判断 + 小白上手方法"
    if category == "AI教程":
        return f"{label}入门教程 + 第一步实操"
    if category == "AI玩法":
        return f"{label}玩法拆解 + 普通人可复制动作"
    return "AI认知纠偏 + 热点背后的使用判断"


def creator_title(category: str, label: str, summary: str) -> str:
    text = f"{label} {summary}".lower()
    if any(k in text for k in ["replace", "replacement", "替代", "取代", "thinking", "思考"]):
        return "AI不是来替你思考的！小白真正该学的是怎么把它当脚手架"
    if category == "AI工具":
        return f"{label}又火了？别急着追新，先看它到底能帮你省掉哪一步"
    if category == "AI教程":
        return f"别再收藏一堆AI教程了！{label}这件事，今天先跑通第一步"
    if category == "AI玩法":
        return f"这个AI玩法别光看热闹！普通人照着做，先从{label}开始"
    return "这个AI热点别只刷过去！真正值得讲的是普通人接下来怎么用"


def audience(category: str) -> str:
    if category == "AI工具":
        return "AI初学者、内容创作者、想用AI提升效率的人"
    if category == "AI教程":
        return "AI小白、刚开始学工具的人、需要照着做的人"
    if category == "AI玩法":
        return "内容创作者、IP从业者、AI初学者"
    return "AI小白、内容创作者、关注AI趋势但不知道怎么用的人"


def purpose(category: str) -> str:
    if category == "AI热点":
        return "把热点翻译成小白能听懂的判断，避免只追新闻，最后落到一个可尝试的动作。"
    if category == "AI工具":
        return "帮小白判断这个工具值不值得学、适合解决什么问题、第一步应该怎么试。"
    if category == "AI教程":
        return "降低上手门槛，把复杂信息拆成可以跟做的第一步。"
    return "把新玩法拆成普通人能复用的流程，重点讲清楚场景、动作和边界。"


def entry_angle(category: str, label: str) -> str:
    if category == "AI工具":
        return f"不要从“{label}有多强”讲起，先问：它到底替普通人省掉了哪一个具体步骤？"
    if category == "AI教程":
        return f"不要做功能介绍，直接从小白第一次打开{label}最容易卡住的地方切入。"
    if category == "AI玩法":
        return f"不要讲概念，拆成一个今天就能试的动作：输入什么、让AI做什么、最后检查什么。"
    return "不要把它当普通新闻念，切到普通人最关心的：这件事会改变哪个具体工作动作。"


def opening(category: str, label: str) -> str:
    if category == "AI工具":
        return f"很多人看到{label}又开始焦虑：是不是又有新工具要学？先别急。工具多不重要，重要的是它能不能帮你少走一步。"
    if category == "AI教程":
        return f"很多人学AI卡住，不是因为笨，而是一上来就被一堆教程吓住了。今天这个选题就讲{label}，只讲第一步怎么跑通。"
    if category == "AI玩法":
        return f"AI玩法每天都在变，但小白真正需要的不是收藏，而是照着做一遍。今天这个玩法，可以拆成一个很简单的动作。"
    return "这个AI热点不要只当新闻看。你真正要关心的是：它会不会影响你接下来做内容、学工具、用AI工作的方式。"


def middle(category: str, label: str, summary: str) -> str:
    fact = first_sentence(summary, label, 120)
    return (
        f"可以拆三个部分：\n"
        f"1. 先讲发生了什么：{fact}\n"
        f"2. 再讲小白最容易误解的地方：不要把它当成万能答案，要看它能解决哪一个具体问题。\n"
        f"3. 最后给一个动作：选一个自己的真实场景，用{label}跑一遍，再看结果能不能被自己修改和使用。"
    )


def ending(category: str) -> str:
    if category == "AI热点":
        return "不要每天追一堆AI新闻，最后什么都没用上。先挑一个和你工作最接近的点，今天就拿它做一次小测试。"
    return "不要只收藏，也不要一上来就追求全自动。先把一个小流程跑通，再决定这个工具或者玩法值不值得继续学。"


def item_to_record(item: Any) -> dict[str, str]:
    title = base.text_of(item, "title", default="未命名")
    url = base.text_of(item, "url", "link")
    summary = base.text_of(item, "ai_summary", "summary", "description", "content") or title
    source = base.source_label(item)
    tags = base.tags_of(item)
    category = base.classify_topic(title, summary, tags)
    label = topic_label(title, tags)

    return {
        "热点": hot_field(title, summary),
        "时间": display_time(item),
        "切入点": entry_angle(category, label),
        "选题": topic_plan(category, label),
        "标题": creator_title(category, label, summary),
        "选题受众": audience(category),
        "选题目的": purpose(category),
        "开头": opening(category, label),
        "中间": middle(category, label, summary),
        "结尾": ending(category),
        "逐字稿": "",
        "一级分类": category,
        "来源平台": source,
        "原始链接": url,
        "AI评分": f"{base.effective_score(item):.1f}",
        "可靠性": base.reliability(item, source, url, summary),
        "适合内容形态": base.content_shape(category, title, summary),
        "状态": "待筛选",
        "去重Key": base.stable_key(title, url or source),
        "标签": "、".join(tags[:8]),
        "AI摘要": clean_text(summary, 800),
    }


base.FIELD_NAMES = FIELD_NAMES
base.item_to_record = item_to_record


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.main()))
