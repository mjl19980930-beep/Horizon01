#!/usr/bin/env python3
"""Feishu field adapter for Weiyu-style AI topic cards.

This script reuses the existing Horizon collection pipeline and only replaces
how each selected item is converted into Feishu Bitable fields. It also adds
optional X and YouTube discovery sources when the required API keys are present.
"""

import asyncio
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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

TRANSLATION_CACHE: dict[str, str] = {}

DEFAULT_X_QUERY = '((AI OR "artificial intelligence" OR ChatGPT OR Claude OR Gemini OR "AI agent" OR "AI tools" OR "AI workflow" OR "vibe coding") lang:en -is:retweet -is:reply)'
DEFAULT_YOUTUBE_QUERIES = [
    "AI tools tutorial",
    "ChatGPT tutorial AI workflow",
    "Claude AI agent tutorial",
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


def is_mostly_english(text: str) -> bool:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    letters = len(re.findall(r"[A-Za-z]", text or ""))
    return letters >= 8 and cjk == 0


def request_json(url: str, headers: dict[str, str] | None = None, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = None
    method = "GET"
    final_headers = headers or {}
    if payload is not None:
        method = "POST"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers = {"Content-Type": "application/json; charset=utf-8", **final_headers}
    req = urllib.request.Request(url, data=data, headers=final_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def translate_title(title: str) -> str:
    title = clean_text(title, 180)
    if not is_mostly_english(title):
        return ""
    if title in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[title]
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return ""
    payload = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "temperature": 0.1,
        "max_tokens": 120,
        "messages": [
            {"role": "system", "content": "你只做标题翻译。把英文标题翻译成自然、短、适合中文内容选题库的中文，不要解释，不要加引号。"},
            {"role": "user", "content": title},
        ],
    }
    try:
        result = request_json(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            payload=payload,
            timeout=20,
        )
        translated = clean_text(result["choices"][0]["message"]["content"], 120)
    except Exception as exc:
        print(f"标题翻译失败，保留英文标题：{exc}")
        translated = ""
    TRANSLATION_CACHE[title] = translated
    return translated


def title_with_translation(title: str) -> str:
    title = clean_text(title, 160)
    translated = translate_title(title)
    if translated and translated.lower() != title.lower():
        return f"{title}（{translated}）"
    return title


def display_time(item: Any) -> str:
    for name in ("published_at", "created_at", "updated_at", "timestamp", "time"):
        value = base.get_attr(item, name)
        if value:
            if isinstance(value, datetime):
                return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y/%m/%d %H:%M")
            return str(value).replace("T", " ")[:16]
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y/%m/%d %H:%M")


def topic_label(title: str, tags: list[str]) -> str:
    tool = base.tool_name(title, tags)
    if tool:
        return clean_text(tool, 28)
    title = re.sub(r"[\[\]【】()（）]", " ", title)
    return clean_text(title, 28) or "这个AI热点"


def hot_field(title: str, summary: str) -> str:
    display_title = title_with_translation(title)
    explanation = first_sentence(summary, title, 120)
    if explanation and explanation.lower() not in title.lower():
        return f"外网热议“{display_title}”\n（{explanation}）"
    return f"外网热议“{display_title}”"


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


def social_score(metrics: dict[str, Any]) -> float:
    likes = int(metrics.get("like_count") or metrics.get("likes") or 0)
    reposts = int(metrics.get("retweet_count") or metrics.get("repost_count") or 0)
    replies = int(metrics.get("reply_count") or 0)
    quotes = int(metrics.get("quote_count") or 0)
    views = int(metrics.get("view_count") or metrics.get("views") or 0)
    raw = likes + reposts * 2 + replies * 2 + quotes * 2 + views // 3000
    if raw <= 0:
        return 6.0
    return min(9.5, 6.0 + math.log10(raw + 1))


def fetch_x_recent(hours: int) -> list[dict[str, Any]]:
    token = os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN")
    if not token:
        return []
    max_results = max(10, min(int(os.getenv("X_SEARCH_MAX_RESULTS", "50")), 100))
    start_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    query = os.getenv("X_SEARCH_QUERY", DEFAULT_X_QUERY)
    params = {
        "query": query,
        "max_results": str(max_results),
        "start_time": start_time,
        "tweet.fields": "created_at,public_metrics,author_id,lang",
        "expansions": "author_id",
        "user.fields": "username,name,verified,public_metrics",
        "sort_order": "recency",
    }
    url = "https://api.twitter.com/2/tweets/search/recent?" + urllib.parse.urlencode(params)
    try:
        result = request_json(url, headers={"Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as exc:
        print(f"X抓取失败 HTTP {exc.code}，已跳过。")
        return []
    except Exception as exc:
        print(f"X抓取失败，已跳过：{exc}")
        return []

    users = {u.get("id"): u for u in result.get("includes", {}).get("users", [])}
    items: list[dict[str, Any]] = []
    for tweet in result.get("data", []) or []:
        text = clean_text(tweet.get("text", ""), 500)
        if not text:
            continue
        user = users.get(tweet.get("author_id"), {})
        username = user.get("username") or tweet.get("author_id") or "unknown"
        metrics = tweet.get("public_metrics") or {}
        tweet_id = tweet.get("id")
        published = tweet.get("created_at") or datetime.now(timezone.utc).isoformat()
        try:
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            published_at = datetime.now(timezone.utc)
        items.append(
            {
                "id": f"twitter:search:{tweet_id}",
                "source_type": "twitter",
                "title": f"@{username}: {clean_text(text, 80)}",
                "url": f"https://twitter.com/{username}/status/{tweet_id}",
                "content": text,
                "ai_summary": text,
                "author": user.get("name") or username,
                "published_at": published_at,
                "ai_score": social_score(metrics),
                "ai_tags": ["X", "Twitter", "AI热点"],
                "metadata": {"platform": "X", "username": username, **metrics},
            }
        )
    print(f"   Found {len(items)} items from X recent search")
    return items


def youtube_queries() -> list[str]:
    raw = os.getenv("YOUTUBE_SEARCH_QUERIES", "").strip()
    if not raw:
        return DEFAULT_YOUTUBE_QUERIES
    return [q.strip() for q in re.split(r"[|\n]", raw) if q.strip()]


def fetch_youtube(hours: int) -> list[dict[str, Any]]:
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return []
    published_after = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    video_ids: list[str] = []
    snippets: dict[str, dict[str, Any]] = {}
    per_query = max(1, min(int(os.getenv("YOUTUBE_RESULTS_PER_QUERY", "8")), 20))

    for query in youtube_queries()[:5]:
        params = {
            "key": api_key,
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": os.getenv("YOUTUBE_ORDER", "viewCount"),
            "publishedAfter": published_after,
            "maxResults": str(per_query),
            "relevanceLanguage": "en",
            "safeSearch": "moderate",
        }
        url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
        try:
            result = request_json(url)
        except urllib.error.HTTPError as exc:
            print(f"YouTube抓取失败 HTTP {exc.code}，已跳过这个关键词：{query}")
            continue
        except Exception as exc:
            print(f"YouTube抓取失败，已跳过这个关键词：{query}，{exc}")
            continue
        for row in result.get("items", []) or []:
            video_id = row.get("id", {}).get("videoId")
            if video_id and video_id not in snippets:
                video_ids.append(video_id)
                snippets[video_id] = row.get("snippet", {})

    stats: dict[str, dict[str, Any]] = {}
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start : start + 50]
        params = {"key": api_key, "part": "statistics,snippet", "id": ",".join(chunk)}
        url = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(params)
        try:
            result = request_json(url)
        except Exception as exc:
            print(f"YouTube统计补充失败，继续使用搜索结果：{exc}")
            continue
        for row in result.get("items", []) or []):
            stats[row.get("id")] = row

    items: list[dict[str, Any]] = []
    for video_id in video_ids:
        snippet = (stats.get(video_id, {}).get("snippet") or snippets.get(video_id) or {})
        statistic = stats.get(video_id, {}).get("statistics") or {}
        title = clean_text(snippet.get("title", ""), 160)
        description = clean_text(snippet.get("description", ""), 500)
        if not title:
            continue
        published = snippet.get("publishedAt") or datetime.now(timezone.utc).isoformat()
        try:
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            published_at = datetime.now(timezone.utc)
        metrics = {
            "view_count": statistic.get("viewCount", 0),
            "like_count": statistic.get("likeCount", 0),
            "comment_count": statistic.get("commentCount", 0),
        }
        items.append(
            {
                "id": f"youtube:video:{video_id}",
                "source_type": "youtube",
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "content": description or title,
                "ai_summary": description or title,
                "author": snippet.get("channelTitle", "YouTube"),
                "published_at": published_at,
                "ai_score": social_score(metrics),
                "ai_tags": ["YouTube", "AI教程", "AI玩法"],
                "metadata": {"platform": "YouTube", "channel": snippet.get("channelTitle", ""), **metrics},
            }
        )
    print(f"   Found {len(items)} items from YouTube search")
    return items


def fetch_social_sources(hours: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    x_items = fetch_x_recent(hours)
    youtube_items = fetch_youtube(hours)
    items = x_items + youtube_items
    return items, {"x_items": len(x_items), "youtube_items": len(youtube_items), "social_items": len(items)}


_ORIGINAL_COLLECT_WITH_HORIZON = base.collect_with_horizon


async def collect_with_social(hours: int, limit: int) -> tuple[list[Any], dict[str, Any]]:
    items, metrics = await _ORIGINAL_COLLECT_WITH_HORIZON(hours, limit)
    social_items, social_metrics = await asyncio.to_thread(fetch_social_sources, hours)
    metrics.update(social_metrics)
    if social_items:
        items = base.sort_items(list(items) + social_items)
        metrics["selected_items"] = len(items)
    return items, metrics


def item_to_record(item: Any) -> dict[str, str]:
    title = base.text_of(item, "title", default="未命名")
    url = base.text_of(item, "url", "link")
    summary = base.text_of(item, "ai_summary", "summary", "description", "content") or title
    source = base.source_label(item)
    metadata = base.metadata_of(item)
    if metadata.get("platform"):
        source = str(metadata["platform"])
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
base.collect_with_horizon = collect_with_social
base.item_to_record = item_to_record


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.main()))