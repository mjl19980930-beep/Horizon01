#!/usr/bin/env python3
"""Feishu field adapter for Weiyu-style AI topic cards.

This wrapper keeps the original Horizon collection pipeline, changes the Feishu
fields into topic-card fields, and optionally adds X/YouTube discovery when the
corresponding API keys are configured.
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
    "热点", "时间", "切入点", "选题", "标题", "选题受众", "选题目的", "开头", "中间", "结尾", "逐字稿",
    "一级分类", "来源平台", "原始链接", "AI评分", "可靠性", "适合内容形态", "状态", "去重Key", "标签", "AI摘要",
]

CARD_FIELDS = ["热点", "切入点", "选题", "标题", "选题受众", "选题目的", "开头", "中间", "结尾"]
TRANSLATION_CACHE: dict[str, str] = {}
CARD_CACHE: dict[str, dict[str, str]] = {}

DEFAULT_X_QUERY = '((AI OR "artificial intelligence" OR ChatGPT OR Claude OR Gemini OR "AI agent" OR "AI tools" OR "AI workflow" OR "vibe coding") lang:en -is:retweet -is:reply)'
DEFAULT_YOUTUBE_QUERIES = ["AI tools tutorial", "ChatGPT tutorial AI workflow", "Claude AI agent tutorial"]


def clean_text(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"<[^>]+>", "", text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def first_sentence(summary: str, fallback: str, limit: int = 120) -> str:
    text = clean_text(summary or fallback, 500)
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
    req = urllib.request.Request(url, data=data, headers=final_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def deepseek_chat(messages: list[dict[str, str]], max_tokens: int = 900, temperature: float = 0.35) -> str:
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
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
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
    title = clean_text(title, 160)
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


def source_prefix(source: str, url: str) -> str:
    text = f"{source} {url}".lower()
    if "youtube" in text:
        return "YouTube热门视频"
    if "twitter" in text or "x" == source.lower():
        return "X热帖"
    if "github" in text:
        return "GitHub热门项目"
    if "hackernews" in text:
        return "Hacker News热议"
    if any(k in text for k in ["openai", "anthropic", "google", "hugging face"]):
        return "官方/技术圈动态"
    return "海外AI动态"


def fallback_card(title: str, summary: str, source: str, url: str, category: str, label: str) -> dict[str, str]:
    display_title = title_with_translation(title)
    fact = first_sentence(summary, title, 120)
    prefix = source_prefix(source, url)
    if category == "AI工具":
        return {
            "热点": f"{prefix}：{display_title}\n（{fact}）",
            "切入点": f"从“这个工具到底省掉哪一步”切入，而不是只讲{label}有多强。",
            "选题": f"{label}值不值得学？用一个真实任务判断它的价值",
            "标题": f"{label}又火了？别急着追新，先看它能不能帮你省掉这一步",
            "选题受众": "AI小白、内容创作者、想用AI提升效率的人",
            "选题目的": "帮小白判断一个工具值不值得学，以及第一步应该怎么试。",
            "开头": f"很多人看到{label}就开始焦虑：是不是又有新工具要学？先别急，工具本身不重要，重要的是它能不能解决你的一个具体问题。",
            "中间": f"可以拆三点：1. {fact}；2. 它适合解决什么具体任务；3. 小白第一次试用时要用自己的真实场景，而不是只看官方演示。",
            "结尾": "别先收藏一堆工具，先拿一个小任务跑通，再决定它值不值得继续学。",
        }
    if category == "AI教程":
        return {
            "热点": f"{prefix}：{display_title}\n（{fact}）",
            "切入点": f"从小白第一次上手{label}最容易卡住的地方切入。",
            "选题": f"{label}入门第一步：别看完教程，先跑通一个动作",
            "标题": f"别再收藏一堆AI教程了！{label}这件事，今天先跑通第一步",
            "选题受众": "AI小白、刚开始学工具的人、需要照着做的人",
            "选题目的": "降低上手门槛，把复杂信息拆成可以跟做的第一步。",
            "开头": f"很多人学AI卡住，不是因为笨，而是一上来就被教程吓住了。今天不讲全套，只讲{label}第一步怎么跑通。",
            "中间": f"可以拆三步：1. 先讲{fact}；2. 只保留小白必须知道的概念；3. 给一个可以跟做的小任务。",
            "结尾": "教程不是用来收藏的，是用来跑通第一步的。今天先完成一个最小动作。",
        }
    return {
        "热点": f"{prefix}：{display_title}\n（{fact}）",
        "切入点": "从普通人能拿走什么认知或动作切入，不把它当资讯复述。",
        "选题": f"{label}背后，普通人真正该看懂的一件事",
        "标题": "这个AI热点别只刷过去！真正值得讲的是普通人接下来怎么用",
        "选题受众": "AI小白、内容创作者、关注AI趋势但不知道怎么用的人",
        "选题目的": "把热点翻译成小白能听懂的判断，最后落到一个可尝试的动作。",
        "开头": f"这个热点不要只当新闻看。你真正要关心的是：{label}这件事，会不会影响你接下来做内容、学工具、用AI工作的方式。",
        "中间": f"可以拆三层：1. 发生了什么：{fact}；2. 它改变了哪个工作动作；3. 普通人现在可以先试一个什么小动作。",
        "结尾": "不要每天追一堆AI新闻，最后什么都没用上。先挑一个和你工作最接近的点，今天就做一次小测试。",
    }


def make_topic_card(title: str, summary: str, source: str, url: str, category: str, tags: list[str]) -> dict[str, str]:
    cache_key = base.stable_key(title, url or source)
    if cache_key in CARD_CACHE:
        return CARD_CACHE[cache_key]
    label = topic_label(title, tags)
    fallback = fallback_card(title, summary, source, url, category, label)
    prompt_payload = {
        "原始标题": title,
        "标题含中文翻译": title_with_translation(title),
        "摘要": clean_text(summary, 700),
        "来源": source,
        "链接": url,
        "分类": category,
        "标签": tags[:8],
    }
    try:
        raw = deepseek_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是AI内容选题编辑，负责把不同AI热点转成飞书选题卡。必须逐条根据事实生成，禁止套同一模板。"
                        "输出严格JSON，字段为：热点、切入点、选题、标题、选题受众、选题目的、开头、中间、结尾。"
                        "要求：1 热点保留原事件，不要每条都写外网热议；英文标题若有中文翻译，要放同一单元格括号里。"
                        "2 切入点必须和该话题强相关。3 选题和标题要具体，不能泛写AI热点。"
                        "4 开头必须围绕该话题，不同话题不能相同。5 中间给2到3个具体拆解点。"
                        "6 借用喂鱼式的结构化、带教感和动作落点，但不要编造喂鱼自己的经历、学员案例或个人故事。"
                        "7 面向AI小白，但可以保留高阶认知，用小白能理解的话解释。"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
            max_tokens=1100,
            temperature=0.55,
        )
        parsed = extract_json_object(raw)
        card = {field: clean_text(str(parsed.get(field) or fallback[field]), 900) for field in CARD_FIELDS}
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
    max_results = env_int("X_SEARCH_MAX_RESULTS", 50, 10, 100)
    start_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params = {
        "query": os.getenv("X_SEARCH_QUERY") or DEFAULT_X_QUERY,
        "max_results": str(max_results),
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
        items.append({
            "id": f"twitter:search:{tweet_id}",
            "source_type": "twitter",
            "title": f"@{username}: {clean_text(text, 80)}",
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
    per_query = env_int("YOUTUBE_RESULTS_PER_QUERY", 8, 1, 20)
    for query in youtube_queries()[:5]:
        params = {"key": api_key, "part": "snippet", "q": query, "type": "video", "order": os.getenv("YOUTUBE_ORDER") or "viewCount", "publishedAfter": published_after, "maxResults": str(per_query), "relevanceLanguage": "en", "safeSearch": "moderate"}
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
        title = clean_text(snippet.get("title", ""), 160)
        description = clean_text(snippet.get("description", ""), 500)
        if not title:
            continue
        published = snippet.get("publishedAt") or datetime.now(timezone.utc).isoformat()
        try:
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
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
    title = base.text_of(item, "title", default="未命名")
    url = base.text_of(item, "url", "link")
    summary = base.text_of(item, "ai_summary", "summary", "description", "content") or title
    source = base.source_label(item)
    metadata = base.metadata_of(item)
    if metadata.get("platform"):
        source = str(metadata["platform"])
    tags = base.tags_of(item)
    category = base.classify_topic(title, summary, tags)
    card = make_topic_card(title, summary, source, url, category, tags)
    return {
        **card,
        "时间": display_time(item),
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