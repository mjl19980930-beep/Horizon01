#!/usr/bin/env python3
"""Feishu field adapter for Weiyu-style AI topic cards."""

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
    "Title",
    "原文标题",
    "原文发表日期",
    "原文链接",
    "原文核心观点",
    "原文逐字稿/操作说明",
    "来源平台",
    "一级分类",
    "AI评分",
    "可靠性",
    "标签",
    "切入点",
    "选题标题",
    "开头钩子",
    "中间论证",
    "结尾收束",
    "选题受众",
    "选题目的",
    "适合内容形态",
    "状态",
    "去重Key",
    "AI摘要",
]

CARD_FIELDS = ["原文核心观点", "原文逐字稿/操作说明", "切入点", "选题标题", "开头钩子", "中间论证", "结尾收束", "选题受众", "选题目的"]
TRANSLATION_CACHE: dict[str, str] = {}
CARD_CACHE: dict[str, dict[str, str]] = {}

DEFAULT_X_QUERY = '((AI OR "artificial intelligence" OR ChatGPT OR Claude OR Gemini OR "AI agent" OR "AI tools" OR "AI workflow" OR "vibe coding") lang:en -is:retweet -is:reply)'
DEFAULT_YOUTUBE_QUERIES = ["AI tools tutorial", "ChatGPT tutorial AI workflow", "Claude AI agent tutorial"]


def clean_text(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"<[^>]+>", "", text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def first_sentence(summary: str, fallback: str, limit: int = 160) -> str:
    text = clean_text(summary or fallback, 900)
    parts = re.split(r"[。.!?！？]\s*", text) if text else [fallback]
    return clean_text(parts[0] or text or fallback, limit)


def is_mostly_english(text: str) -> bool:
    return len(re.findall(r"[A-Za-z]", text or "")) >= 8 and not re.search(r"[\u4e00-\u9fff]", text or "")


def request_json(url: str, headers: dict[str, str] | None = None, payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = None
    method = "GET"
    final_headers = headers or {}
    if payload is not None:
        method = "POST"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers = {"Content-Type": "application/json; charset=utf-8", **final_headers}
    request = urllib.request.Request(url, data=data, headers=final_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def deepseek_chat(messages: list[dict[str, str]], max_tokens: int = 1000, temperature: float = 0.35) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    payload = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    result = request_json("https://api.deepseek.com/chat/completions", {"Authorization": f"Bearer {api_key}"}, payload, 35)
    return result["choices"][0]["message"]["content"]


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def translate_title(title: str) -> str:
    title = clean_text(title, 180)
    if not is_mostly_english(title):
        return ""
    if title in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[title]
    try:
        translated = clean_text(
            deepseek_chat(
                [
                    {"role": "system", "content": "你只做标题翻译。把英文标题翻译成自然、短、适合中文内容选题库的中文，不要解释，不要加引号。"},
                    {"role": "user", "content": title},
                ],
                max_tokens=120,
                temperature=0.1,
            ),
            120,
        )
    except Exception as exc:
        print(f"标题翻译失败，保留英文标题：{exc}")
        translated = ""
    TRANSLATION_CACHE[title] = translated
    return translated


def title_with_translation(title: str) -> str:
    title = clean_text(title, 180)
    translated = translate_title(title)
    return f"{title}（{translated}）" if translated and translated.lower() != title.lower() else title


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
    return clean_text(re.sub(r"[\[\]【】()（）]", " ", title), 28) or "这个AI热点"


def original_material(title: str, summary: str, source: str) -> str:
    body = clean_text(summary or title, 1200)
    if source.lower() in {"x", "twitter"}:
        return f"原帖正文：{body}"
    if source.lower() == "youtube":
        return f"视频简介/操作说明：{body}\n说明：当前未抓取完整字幕，先保留标题、简介和数据。"
    return f"原文摘要/操作说明：{body}\n说明：当前未抓取完整网页正文，先保留抓取摘要和来源链接。"


def fallback_card(title: str, summary: str, source: str, category: str, tags: list[str]) -> dict[str, str]:
    label = topic_label(title, tags)
    fact = first_sentence(summary, title, 180)
    if category == "AI工具":
        return {
            "原文核心观点": fact,
            "原文逐字稿/操作说明": original_material(title, summary, source),
            "切入点": f"从{label}具体能省掉哪一步工作切入，而不是只做工具上新播报。",
            "选题标题": f"{label}值不值得学？先用一个真实任务测出来",
            "开头钩子": f"又来了一个AI工具：{label}。先别收藏，先问一个问题：它到底能不能帮你少做一步？",
            "中间论证": f"1. 先讲原文事实：{fact}\n2. 再拆它适合解决的具体任务。\n3. 最后给小白一个测试动作：拿自己的真实场景跑一次。",
            "结尾收束": "别先追工具名单，先跑一个小任务。能被你改、能进你的流程，才值得继续学。",
            "选题受众": "AI小白、内容创作者、想用AI提升效率的人",
            "选题目的": "帮小白判断工具价值，并给出第一步可执行动作。",
        }
    if category == "AI教程":
        return {
            "原文核心观点": fact,
            "原文逐字稿/操作说明": original_material(title, summary, source),
            "切入点": f"从小白第一次上手{label}最容易卡住的步骤切入。",
            "选题标题": f"{label}入门第一步：别收藏教程，先跑通一个动作",
            "开头钩子": f"很多人不是学不会{label}，是一上来就想学全套。今天只拆第一步，先让它跑起来。",
            "中间论证": f"1. 原文讲了什么：{fact}\n2. 小白只需要先理解哪个关键动作。\n3. 给一个能照着做的小任务。",
            "结尾收束": "教程不是用来囤的，是用来跑通的。先完成一个最小动作，再往下学。",
            "选题受众": "AI小白、刚开始学工具的人、需要照着做的人",
            "选题目的": "降低上手门槛，把复杂教程拆成第一步动作。",
        }
    return {
        "原文核心观点": fact,
        "原文逐字稿/操作说明": original_material(title, summary, source),
        "切入点": f"从{label}对普通人的工作、学习或内容判断有什么影响切入。",
        "选题标题": f"{label}背后，普通人真正该看懂的一件事",
        "开头钩子": f"这个热点不要只当新闻看。关键是：{label}这件事，会不会改变你接下来用AI的方式？",
        "中间论证": f"1. 先把事实讲清楚：{fact}\n2. 再讲它改变了哪个具体动作或判断。\n3. 最后给一个普通人今天能试的小动作。",
        "结尾收束": "不要每天追一堆AI新闻，最后什么都没用上。挑一个离你最近的点，先做一次小测试。",
        "选题受众": "AI小白、内容创作者、关注AI趋势但不知道怎么用的人",
        "选题目的": "把热点翻译成小白能听懂的判断，最后落到可尝试动作。",
    }


def make_topic_card(title: str, summary: str, source: str, url: str, category: str, tags: list[str]) -> dict[str, str]:
    cache_key = base.stable_key(title, url or source)
    if cache_key in CARD_CACHE:
        return CARD_CACHE[cache_key]
    fallback = fallback_card(title, summary, source, category, tags)
    payload = {
        "原文标题": title_with_translation(title),
        "原始英文/原始标题": title,
        "原文摘要或正文": clean_text(summary, 1200),
        "来源平台": source,
        "原文链接": url,
        "一级分类": category,
        "标签": tags[:8],
    }
    try:
        raw = deepseek_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是AI内容选题编辑，要把一条原文内容转成飞书多维表格里的选题卡。"
                        "先尊重原文事实，再生成适合个人IP二次写稿的内容动作。禁止套模板，禁止不同话题复用同一开头。"
                        "输出严格JSON，字段只能是：原文核心观点、原文逐字稿/操作说明、切入点、选题标题、开头钩子、中间论证、结尾收束、选题受众、选题目的。"
                        "原文核心观点要准确概括原文，不要写营销话。原文逐字稿/操作说明如果没有完整逐字稿，就基于摘要写可回看的事实说明，并标明不是完整逐字稿。"
                        "切入点、选题标题、开头钩子、中间论证、结尾收束必须围绕这条原文具体内容生成。"
                        "语言借用喂鱼式的结构化、带教感、动作落点，但不要编造喂鱼自己的经历、学员案例或个人故事。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=1300,
            temperature=0.55,
        )
        parsed = extract_json_object(raw)
        card = {field: clean_text(str(parsed.get(field) or fallback[field]), 1200) for field in CARD_FIELDS}
    except Exception as exc:
        print(f"选题卡生成失败，使用兜底模板：{exc}")
        card = fallback
    for field in CARD_FIELDS:
        if not card.get(field):
            card[field] = fallback[field]
    CARD_CACHE[cache_key] = card
    return card


def social_score(metrics: dict[str, Any]) -> float:
    likes = int(metrics.get("like_count") or metrics.get("likes") or 0)
    reposts = int(metrics.get("retweet_count") or metrics.get("repost_count") or 0)
    replies = int(metrics.get("reply_count") or 0)
    quotes = int(metrics.get("quote_count") or 0)
    views = int(metrics.get("view_count") or metrics.get("views") or 0)
    raw = likes + reposts * 2 + replies * 2 + quotes * 2 + views // 3000
    return 6.0 if raw <= 0 else min(9.5, 6.0 + math.log10(raw + 1))


def env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(os.getenv(name) or default), high))
    except ValueError:
        return default


def fetch_x_recent(hours: int) -> list[dict[str, Any]]:
    token = os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN")
    if not token:
        return []
    start_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params = {
        "query": os.getenv("X_SEARCH_QUERY") or DEFAULT_X_QUERY,
        "max_results": str(env_int("X_SEARCH_MAX_RESULTS", 50, 10, 100)),
        "start_time": start_time,
        "tweet.fields": "created_at,public_metrics,author_id,lang",
        "expansions": "author_id",
        "user.fields": "username,name,verified,public_metrics",
        "sort_order": "recency",
    }
    try:
        result = request_json("https://api.twitter.com/2/tweets/search/recent?" + urllib.parse.urlencode(params), {"Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as exc:
        print(f"X抓取失败 HTTP {exc.code}，已跳过。")
        return []
    except Exception as exc:
        print(f"X抓取失败，已跳过：{exc}")
        return []
    users = {u.get("id"): u for u in result.get("includes", {}).get("users", [])}
    items: list[dict[str, Any]] = []
    for tweet in result.get("data", []) or []:
        text = clean_text(tweet.get("text", ""), 700)
        if not text:
            continue
        user = users.get(tweet.get("author_id"), {})
        username = user.get("username") or tweet.get("author_id") or "unknown"
        metrics = tweet.get("public_metrics") or {}
        tweet_id = tweet.get("id")
        try:
            published_at = datetime.fromisoformat((tweet.get("created_at") or "").replace("Z", "+00:00"))
        except ValueError:
            published_at = datetime.now(timezone.utc)
        items.append({
            "id": f"twitter:search:{tweet_id}",
            "source_type": "twitter",
            "title": f"@{username}: {clean_text(text, 90)}",
            "url": f"https://twitter.com/{username}/status/{tweet_id}",
            "content": text,
            "ai_summary": text,
            "published_at": published_at,
            "ai_score": social_score(metrics),
            "ai_tags": ["X", "Twitter", "AI热点"],
            "metadata": {"platform": "X", "username": username, **metrics},
        })
    print(f"   Found {len(items)} items from X recent search")
    return items


def youtube_queries() -> list[str]:
    raw = os.getenv("YOUTUBE_SEARCH_QUERIES", "").strip()
    return [q.strip() for q in re.split(r"[|\n]", raw) if q.strip()] if raw else DEFAULT_YOUTUBE_QUERIES


def fetch_youtube(hours: int) -> list[dict[str, Any]]:
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return []
    published_after = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    video_ids: list[str] = []
    snippets: dict[str, dict[str, Any]] = {}
    for query in youtube_queries()[:5]:
        params = {"key": api_key, "part": "snippet", "q": query, "type": "video", "order": os.getenv("YOUTUBE_ORDER") or "viewCount", "publishedAfter": published_after, "maxResults": str(env_int("YOUTUBE_RESULTS_PER_QUERY", 8, 1, 20)), "relevanceLanguage": "en", "safeSearch": "moderate"}
        try:
            result = request_json("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params))
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
        try:
            result = request_json("https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(params))
            for row in result.get("items", []) or []:
                stats[row.get("id")] = row
        except Exception as exc:
            print(f"YouTube统计补充失败，继续使用搜索结果：{exc}")
    items: list[dict[str, Any]] = []
    for video_id in video_ids:
        snippet = (stats.get(video_id, {}).get("snippet") or snippets.get(video_id) or {})
        statistic = stats.get(video_id, {}).get("statistics") or {}
        title = clean_text(snippet.get("title", ""), 180)
        description = clean_text(snippet.get("description", ""), 900)
        if not title:
            continue
        try:
            published_at = datetime.fromisoformat((snippet.get("publishedAt") or "").replace("Z", "+00:00"))
        except ValueError:
            published_at = datetime.now(timezone.utc)
        metrics = {"view_count": statistic.get("viewCount", 0), "like_count": statistic.get("likeCount", 0), "comment_count": statistic.get("commentCount", 0)}
        items.append({
            "id": f"youtube:video:{video_id}",
            "source_type": "youtube",
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "content": description or title,
            "ai_summary": description or title,
            "published_at": published_at,
            "ai_score": social_score(metrics),
            "ai_tags": ["YouTube", "AI教程", "AI玩法"],
            "metadata": {"platform": "YouTube", "channel": snippet.get("channelTitle", ""), **metrics},
        })
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
    raw_title = base.text_of(item, "title", default="未命名")
    title = title_with_translation(raw_title)
    url = base.text_of(item, "url", "link")
    summary = base.text_of(item, "ai_summary", "summary", "description", "content") or raw_title
    source = base.source_label(item)
    metadata = base.metadata_of(item)
    if metadata.get("platform"):
        source = str(metadata["platform"])
    tags = base.tags_of(item)
    category = base.classify_topic(raw_title, summary, tags)
    card = make_topic_card(raw_title, summary, source, url, category, tags)
    return {
        "Title": title,
        "原文标题": title,
        "原文发表日期": display_time(item),
        "原文链接": url,
        **card,
        "来源平台": source,
        "一级分类": category,
        "AI评分": f"{base.effective_score(item):.1f}",
        "可靠性": base.reliability(item, source, url, summary),
        "标签": "、".join(tags[:8]),
        "适合内容形态": base.content_shape(category, raw_title, summary),
        "状态": "待筛选",
        "去重Key": base.stable_key(raw_title, url or source),
        "AI摘要": clean_text(summary, 800),
    }


base.FIELD_NAMES = FIELD_NAMES
base.collect_with_horizon = collect_with_social
base.item_to_record = item_to_record


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.main()))