#!/usr/bin/env python3
"""Stable entrypoint for Weiyu-style AI topic collection.

This wrapper keeps the existing collector intact, but makes production
adjustments for GitHub Actions:
1. Ask DeepSeek for strict JSON when generating topic cards.
2. Keep the enriched candidate pool smaller so the job does not hit timeout.
3. Filter weakly related non-AI items before they reach Feishu.
4. Write the first Feishu column as 标题, with source, link and reliability.
5. Add no-key RSS fallbacks for YouTube channels and RSSHub/custom social feeds.
"""

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import run_topic_collector_weiyu as weiyu


AI_RELEVANCE_PATTERN = re.compile(
    r"\b("
    r"ai|artificial intelligence|openai|chatgpt|gpt-?\d*|claude|anthropic|gemini|deepseek|llm|"
    r"large language model|agentic|ai agent|ai agents|codex|cursor|windsurf|vibe coding|"
    r"prompt|rag|mcp|model context protocol|transformer|hugging\s*face|langchain|langgraph|"
    r"machine learning|neural|diffusion|midjourney|sora|runway|perplexity|notebooklm|"
    r"copilot|foundation model|generative ai|semantic search|inference|fine[- ]?tuning|"
    r"gpu|hbm|nvidia|cuda|embedding|vector database|claude code|openai api"
    r")\b",
    re.IGNORECASE,
)

ZH_AI_RELEVANCE_PATTERN = re.compile(
    r"AI|人工智能|大模型|语言模型|智能体|提示词|工作流|自动化|开源模型|多模态|生成式|"
    r"深度学习|机器学习|神经网络|图像生成|视频生成|编程助手|代码助手|知识库|检索增强|"
    r"推理模型|微调|向量库|嵌入|算力|英伟达|高带宽内存|Claude|Gemini|OpenAI|DeepSeek"
)

KNOWN_AI_SOURCE_PATTERN = re.compile(
    r"openai|anthropic|claude|google ai|gemini|hugging face|tldr ai|the decoder|venturebeat ai|"
    r"simon willison|langchain|langgraph|transformers|deepseek|perplexity|nvidia",
    re.IGNORECASE,
)

CARD_PROMPT_MARKERS = ("输出严格JSON", "字段只能是")
TITLE_ALIASES = ("标题", "热点", "Title")

STABLE_FIELD_NAMES = [
    "标题",
    "原文标题",
    "原文发表日期",
    "原文链接",
    "论证源",
    "内容来源",
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


def _looks_like_card_prompt(messages: list[dict[str, str]]) -> bool:
    system_text = "\n".join(message.get("content", "") for message in messages if message.get("role") == "system")
    return any(marker in system_text for marker in CARD_PROMPT_MARKERS)


def deepseek_chat_stable(messages: list[dict[str, str]], max_tokens: int = 1000, temperature: float = 0.35) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    payload: dict[str, Any] = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if _looks_like_card_prompt(messages):
        payload["response_format"] = {"type": "json_object"}
    result = weiyu.request_json(
        "https://api.deepseek.com/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        payload,
        45,
    )
    return result["choices"][0]["message"]["content"]


def _strip_json_fence(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _loose_card_fields(text: str) -> dict[str, str]:
    body = _strip_json_fence(text)
    result: dict[str, str] = {}
    field_positions: list[tuple[str, int, int]] = []
    for field in weiyu.CARD_FIELDS:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*', body)
        if match:
            field_positions.append((field, match.start(), match.end()))
    field_positions.sort(key=lambda row: row[1])
    for index, (field, _field_start, value_start) in enumerate(field_positions):
        value_end = field_positions[index + 1][1] if index + 1 < len(field_positions) else len(body)
        value = body[value_start:value_end].strip().rstrip(",").strip()
        if value.startswith('"'):
            value = value[1:]
        if value.endswith('"'):
            value = value[:-1]
        value = value.replace('\\"', '"').replace('\\n', '\n')
        value = re.sub(r"\n\s*\n+", "\n", value).strip()
        if value:
            result[field] = value
    return result


def extract_json_object_stable(text: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        loose = _loose_card_fields(cleaned)
        if loose:
            return loose
        raise


def translate_title_stable(title: str) -> str:
    # Avoid a separate title-translation API call. The topic-card call already
    # generates a Chinese, source-specific angle; item_to_record_stable uses it
    # as the bracketed Chinese hint for English source titles.
    return ""


def title_with_translation_stable(title: str) -> str:
    return weiyu.clean_text(title, 180)


def _candidate_text(item: Any) -> str:
    base = weiyu.base
    pieces = [
        base.text_of(item, "title", default=""),
        base.text_of(item, "url", "link"),
        base.text_of(item, "ai_summary", "summary", "description", "content"),
        " ".join(base.tags_of(item)),
        str(base.metadata_of(item).get("platform") or ""),
        str(base.get_attr(item, "source_type") or ""),
        base.source_label(item),
    ]
    return "\n".join(piece for piece in pieces if piece)


def is_ai_relevant_item(item: Any) -> bool:
    text = _candidate_text(item)
    if not text.strip():
        return False
    if KNOWN_AI_SOURCE_PATTERN.search(text):
        return True
    return bool(AI_RELEVANCE_PATTERN.search(text) or ZH_AI_RELEVANCE_PATTERN.search(text))


def _internal_limit(user_limit: int) -> int:
    raw = os.getenv("HORIZON_INTERNAL_LIMIT", "10")
    try:
        value = int(raw)
    except ValueError:
        value = 10
    return max(5, min(value, max(user_limit, 5)))


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [part.strip() for part in re.split(r"[\n,|]+", raw) if part.strip()]


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _fetch_text_url(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Horizon01 AI topic collector/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _descendant_text(element: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in element.iter():
        if _local_name(child.tag) in wanted and child.text and child.text.strip():
            return weiyu.clean_text(child.text, 1200)
    return ""


def _atom_link(entry: ET.Element) -> str:
    fallback = ""
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        if not href:
            continue
        if child.attrib.get("rel") in ("alternate", None, ""):
            return href
        fallback = fallback or href
    return fallback


def _source_type_from_url(url: str, feed_hint: str) -> tuple[str, str, list[str]]:
    text = f"{url} {feed_hint}".lower()
    if "youtube.com" in text or "youtu.be" in text or "youtube" in text:
        return "youtube_rss", "YouTube", ["YouTube", "AI教程", "AI玩法"]
    if "twitter.com" in text or "x.com" in text or "twitter" in text or "/x/" in text:
        return "twitter_rss", "X", ["X", "Twitter", "AI热点"]
    return "social_rss", "Social RSS", ["AI热点", "RSS"]


def _feed_items_from_xml(xml_text: str, feed_url: str, hours: int, feed_hint: str = "") -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"RSS解析失败，已跳过：{feed_url}，{exc}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows: list[ET.Element] = []
    if _local_name(root.tag) == "feed":
        rows = [child for child in root if _local_name(child.tag) == "entry"]
    else:
        rows = [child for child in root.iter() if _local_name(child.tag) == "item"]

    items: list[dict[str, Any]] = []
    feed_title = _descendant_text(root, "title") or feed_hint or feed_url
    for row in rows[:40]:
        title = _descendant_text(row, "title")
        link = _atom_link(row) or _descendant_text(row, "link")
        summary = _descendant_text(row, "summary", "description", "content", "encoded") or title
        published_at = _parse_datetime(_descendant_text(row, "published", "updated", "pubDate", "date"))
        if published_at:
            published_at = published_at.astimezone(timezone.utc)
            if published_at < cutoff:
                continue
        else:
            published_at = datetime.now(timezone.utc)
        if not title or not link:
            continue
        source_type, platform, tags = _source_type_from_url(link, f"{feed_url} {feed_hint} {feed_title}")
        item = {
            "id": f"{source_type}:{weiyu.base.stable_key(title, link)}",
            "source_type": source_type,
            "title": weiyu.clean_text(title, 180),
            "url": link,
            "content": summary,
            "ai_summary": summary,
            "published_at": published_at,
            "ai_score": 7.0,
            "ai_tags": tags,
            "metadata": {"platform": platform, "feed_name": feed_title, "feed_url": feed_url},
        }
        if is_ai_relevant_item(item):
            items.append(item)
    return items


def _rsshub_feed_urls() -> list[str]:
    urls: list[str] = []
    base_url = os.getenv("RSSHUB_BASE_URL", "").strip().rstrip("/")
    for route in _env_list("RSSHUB_ROUTES"):
        if route.startswith("http://") or route.startswith("https://"):
            urls.append(route)
        elif base_url:
            urls.append(f"{base_url}/{route.lstrip('/')}")
    return urls


def fetch_youtube_channel_rss(hours: int) -> list[dict[str, Any]]:
    channel_ids = _env_list("YOUTUBE_CHANNEL_IDS")
    items: list[dict[str, Any]] = []
    for channel_id in channel_ids:
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={urllib.parse.quote(channel_id)}"
        try:
            items.extend(_feed_items_from_xml(_fetch_text_url(feed_url), feed_url, hours, "YouTube channel RSS"))
        except urllib.error.HTTPError as exc:
            print(f"YouTube频道RSS抓取失败 HTTP {exc.code}，已跳过：{channel_id}")
        except Exception as exc:
            print(f"YouTube频道RSS抓取失败，已跳过：{channel_id}，{exc}")
    if channel_ids:
        print(f"   Found {len(items)} items from YouTube channel RSS")
    return items


def fetch_custom_social_rss(hours: int) -> list[dict[str, Any]]:
    feed_urls = _env_list("SOCIAL_RSS_URLS") + _rsshub_feed_urls()
    items: list[dict[str, Any]] = []
    for feed_url in feed_urls:
        try:
            items.extend(_feed_items_from_xml(_fetch_text_url(feed_url), feed_url, hours))
        except urllib.error.HTTPError as exc:
            print(f"社交RSS抓取失败 HTTP {exc.code}，已跳过：{feed_url}")
        except Exception as exc:
            print(f"社交RSS抓取失败，已跳过：{feed_url}，{exc}")
    if feed_urls:
        print(f"   Found {len(items)} items from custom/RSSHub social feeds")
    return items


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = weiyu.base.stable_key(weiyu.base.text_of(item, "title"), weiyu.base.text_of(item, "url", "link"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def fetch_social_sources_stable(hours: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    has_x_token = bool(os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN"))
    has_youtube_key = bool(os.getenv("YOUTUBE_API_KEY", "").strip())
    if not has_x_token:
        print("X/Twitter 未配置官方API：可用 RSSHub_ROUTES 或 SOCIAL_RSS_URLS 作为无Key兜底。")
    if not has_youtube_key:
        print("YouTube 未配置官方API：可用 YOUTUBE_CHANNEL_IDS 或 SOCIAL_RSS_URLS 作为无Key兜底。")

    official_items, official_metrics = weiyu.fetch_social_sources(hours)
    youtube_rss_items = fetch_youtube_channel_rss(hours)
    custom_rss_items = fetch_custom_social_rss(hours)
    fallback_items = youtube_rss_items + custom_rss_items
    items = _dedupe_items(list(official_items) + fallback_items)

    fallback_x_count = sum(1 for item in fallback_items if weiyu.base.get_attr(item, "source_type") == "twitter_rss")
    fallback_youtube_count = sum(1 for item in fallback_items if weiyu.base.get_attr(item, "source_type") == "youtube_rss")
    metrics = dict(official_metrics)
    metrics["x_rss_items"] = fallback_x_count
    metrics["youtube_rss_items"] = fallback_youtube_count
    metrics["custom_rss_items"] = len(custom_rss_items)
    metrics["x_items"] = int(metrics.get("x_items", 0)) + fallback_x_count
    metrics["youtube_items"] = int(metrics.get("youtube_items", 0)) + fallback_youtube_count
    metrics["social_items"] = len(items)
    return items, metrics


async def collect_with_social_stable(hours: int, limit: int) -> tuple[list[Any], dict[str, Any]]:
    # The original collector enriches roughly limit * 3 items. For a manual
    # limit of 20 that can become 60 deep-enriched records, which is why the
    # GitHub job sometimes reaches the 45-minute timeout. We still write up to
    # the user's requested limit, but only deep-enrich a tighter candidate pool.
    base_limit = _internal_limit(limit)
    items, metrics = await weiyu._ORIGINAL_COLLECT_WITH_HORIZON(hours, base_limit)
    social_items, social_metrics = await asyncio.to_thread(fetch_social_sources_stable, hours)
    metrics.update(social_metrics)
    if social_items:
        items = weiyu.base.sort_items(list(items) + social_items)
    relevant_items = [item for item in items if is_ai_relevant_item(item)]
    if relevant_items:
        items = relevant_items
    target_pool_size = max(limit, base_limit * 3)
    items = weiyu.base.sort_items(list(items))[:target_pool_size]
    metrics["selected_items"] = len(items)
    metrics["ai_relevant_items"] = len(relevant_items)
    metrics["internal_limit"] = base_limit
    metrics["target_pool_size"] = target_pool_size
    return items, metrics


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _title_hint_from_record(record: dict[str, str]) -> str:
    for field in ("选题标题", "原文核心观点", "切入点"):
        value = weiyu.clean_text(record.get(field, ""), 80)
        if _has_chinese(value):
            value = re.split(r"[。！？!?\n]", value)[0].strip()
            value = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", value)
            return weiyu.clean_text(value, 60)
    return ""


def _domain_from_url(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _evidence_sources(item: Any, source: str, url: str) -> str:
    metadata = weiyu.base.metadata_of(item)
    merged = metadata.get("merged_sources") or []
    pieces: list[str] = []
    if isinstance(merged, list):
        pieces.extend(str(value) for value in merged if str(value).strip())
    for key in ("feed_name", "repo", "channel", "username", "platform"):
        value = metadata.get(key)
        if value:
            pieces.append(str(value))
    if source:
        pieces.append(source)
    domain = _domain_from_url(url)
    if domain:
        pieces.append(domain)
    deduped: list[str] = []
    for piece in pieces:
        cleaned = weiyu.clean_text(piece, 80)
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return "、".join(deduped[:6]) or "单一来源"


def _title_block(title: str, evidence: str, source: str, url: str, reliability: str) -> str:
    return "\n".join(
        [
            f"标题：{title}",
            f"论证源：{evidence or '单一来源'}",
            f"内容来源：{source or '未标注'}",
            f"原文链接：{url or '未抓到'}",
            f"可靠性：{reliability or '待验证'}",
        ]
    )


def item_to_record_stable(item: Any) -> dict[str, str]:
    record = weiyu.item_to_record(item)
    raw_title = weiyu.clean_text(weiyu.base.text_of(item, "title", default="未命名"), 180)
    current_title = record.get("原文标题") or record.get("Title") or raw_title
    if weiyu.is_mostly_english(raw_title) and "（" not in current_title:
        hint = _title_hint_from_record(record)
        if hint:
            current_title = f"{raw_title}（{hint}）"
            record["原文标题"] = current_title
    source = record.get("来源平台") or weiyu.base.source_label(item)
    url = record.get("原文链接") or weiyu.base.text_of(item, "url", "link")
    reliability = record.get("可靠性") or weiyu.base.reliability(item, source, url, record.get("AI摘要", ""))
    evidence = _evidence_sources(item, source, url)
    primary_title = _title_block(current_title, evidence, source, url, reliability)
    record["标题"] = primary_title
    record["Title"] = primary_title
    record["热点"] = primary_title
    record["原文标题"] = current_title
    record["论证源"] = evidence
    record["内容来源"] = source
    return record


def _field_items(self: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query: dict[str, Any] = {"page_size": 100}
        if page_token:
            query["page_token"] = page_token
        result = self._request("GET", f"{self._base_url()}/fields?{urllib.parse.urlencode(query)}")
        data = result.get("data", {})
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            return items
        page_token = data.get("page_token", "")


def ensure_fields_stable(self: Any) -> None:
    items = _field_items(self)
    by_name = {item.get("field_name"): item for item in items if item.get("field_name")}
    if "标题" not in by_name:
        for legacy_name in ("热点", "Title"):
            legacy = by_name.get(legacy_name)
            field_id = legacy.get("field_id") if isinstance(legacy, dict) else None
            if not field_id:
                continue
            try:
                self._request("PUT", f"{self._base_url()}/fields/{field_id}", {"field_name": "标题"})
                print(f"已将飞书字段 {legacy_name} 重命名为 标题。")
                return ensure_fields_stable(self)
            except Exception as exc:
                print(f"字段 {legacy_name} 重命名为 标题失败，继续尝试创建/写入标题字段：{exc}")
    existing = set(by_name)
    missing = [name for name in STABLE_FIELD_NAMES if name not in existing]
    if not missing:
        return
    if os.getenv("FEISHU_AUTO_CREATE_FIELDS", "true").lower() != "true":
        raise RuntimeError("飞书表格缺少字段：" + "、".join(missing))
    for name in missing:
        self._request("POST", f"{self._base_url()}/fields", {"field_name": name, "type": 1})


def batch_create_stable(self: Any, records: list[dict[str, str]]) -> int:
    allowed_fields = {item.get("field_name") for item in _field_items(self) if item.get("field_name")}
    written = 0
    for start in range(0, len(records), 100):
        chunk = records[start : start + 100]
        if not chunk:
            continue
        filtered = []
        for record in chunk:
            fields = {key: value for key, value in record.items() if key in allowed_fields}
            for alias in TITLE_ALIASES:
                if alias in allowed_fields and alias not in fields:
                    fields[alias] = record.get("标题") or record.get("Title") or record.get("原文标题") or ""
            filtered.append({"fields": fields})
        self._request("POST", f"{self._base_url()}/records/batch_create", {"records": filtered})
        written += len(filtered)
    return written


weiyu.FIELD_NAMES = STABLE_FIELD_NAMES
weiyu.base.FIELD_NAMES = STABLE_FIELD_NAMES
weiyu.deepseek_chat = deepseek_chat_stable
weiyu.extract_json_object = extract_json_object_stable
weiyu.translate_title = translate_title_stable
weiyu.title_with_translation = title_with_translation_stable
weiyu.base.collect_with_horizon = collect_with_social_stable
weiyu.base.item_to_record = item_to_record_stable
weiyu.base.FeishuBitableClient.ensure_fields = ensure_fields_stable
weiyu.base.FeishuBitableClient.batch_create = batch_create_stable


if __name__ == "__main__":
    raise SystemExit(asyncio.run(weiyu.base.main()))