#!/usr/bin/env python3
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


FIELD_NAMES = [
    "日期",
    "一级分类",
    "标题",
    "一句话解释",
    "为什么适合AI小白",
    "可拍角度",
    "教程切入点",
    "工具/产品名",
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

OFFICIAL_HINTS = [
    "openai.com",
    "anthropic.com",
    "google",
    "microsoft",
    "github.com",
    "huggingface.co",
    "official-ai",
]

UNCERTAIN_HINTS = [
    "rumor",
    "leak",
    "reportedly",
    "allegedly",
    "sources say",
    "传闻",
    "爆料",
    "据称",
    "可能",
]


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量：{name}")
    return value


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def stable_key(title: str, url: str) -> str:
    raw = f"{title.strip().lower()}|{url.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def get_attr(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def metadata_of(item: Any) -> dict[str, Any]:
    metadata = get_attr(item, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def text_of(item: Any, *names: str, default: str = "") -> str:
    for name in names:
        value = get_attr(item, name)
        if value is not None and value != "":
            return str(value)
    return default


def score_of(item: Any) -> float:
    for name in ("ai_score", "importance_score", "score"):
        value = get_attr(item, name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 0.0


def tags_of(item: Any) -> list[str]:
    for name in ("ai_tags", "tags", "topics", "keywords"):
        value = get_attr(item, name)
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [v.strip() for v in re.split(r"[,，#\s]+", value) if v.strip()]
    return []


def source_label(item: Any) -> str:
    metadata = metadata_of(item)
    if metadata.get("feed_name"):
        return str(metadata["feed_name"])
    if metadata.get("subreddit"):
        return f"Reddit r/{metadata['subreddit']}"
    if metadata.get("repo"):
        return str(metadata["repo"])
    source_type = get_attr(item, "source_type", "unknown")
    return getattr(source_type, "value", str(source_type))


def classify_topic(title: str, summary: str, tags: list[str]) -> str:
    text = " ".join([title, summary, " ".join(tags)]).lower()
    if any(k in text for k in ["tutorial", "guide", "how to", "入门", "教程", "上手", "教学", "step-by-step"]):
        return "AI教程"
    if any(k in text for k in ["workflow", "prompt", "automation", "玩法", "自动化", "工作流", "用法", "实操"]):
        return "AI玩法"
    if any(k in text for k in ["tool", "app", "release", "launch", "sdk", "agent", "工具", "产品", "发布", "插件", "模型"]):
        return "AI工具"
    return "AI热点"


def content_shape(category: str, title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if category == "AI教程":
        return "教程"
    if any(k in text for k in ["github", "sdk", "api", "agent", "workflow", "工具", "插件", "工作流", "自动化"]):
        return "实操录屏"
    if any(k in text for k in ["vs", "compare", "benchmark", "对比", "评测", "测试"]):
        return "对比测评"
    return "口播"


def reliability(item: Any, source: str, url: str, summary: str) -> str:
    metadata = metadata_of(item)
    merged_sources = metadata.get("merged_sources") or []
    text = f"{source} {url} {summary} {' '.join(map(str, merged_sources))}".lower()
    if any(hint in text for hint in UNCERTAIN_HINTS):
        return "传闻待核验"
    if any(hint in text for hint in OFFICIAL_HINTS):
        return "可靠来源"
    if isinstance(merged_sources, list) and len(set(map(str, merged_sources))) >= 2:
        return "可靠来源"
    return "单一来源待核验"


def first_sentence(summary: str, title: str) -> str:
    cleaned = re.sub(r"\s+", " ", summary or "").strip()
    if not cleaned:
        return title[:120]
    parts = re.split(r"[。.!?！？]\s*", cleaned)
    return (parts[0] or cleaned)[:120]


def beginner_reason(category: str) -> str:
    reasons = {
        "AI工具": "它能让小白看到一个具体工具可以解决什么问题，比讲概念更容易理解。",
        "AI教程": "它可以直接做成一步一步的入门教学，适合降低上手门槛。",
        "AI玩法": "它适合展示普通人可以照着试的使用场景，不需要先懂技术原理。",
        "AI热点": "它适合用小白能听懂的方式解释为什么这件事和普通人有关。",
    }
    return reasons.get(category, reasons["AI热点"])


def shooting_angle(category: str, title: str) -> str:
    short = title[:36]
    if category == "AI工具":
        return f"这个工具到底能帮普通人省掉哪一步：用 {short} 做一次真实演示。"
    if category == "AI教程":
        return f"小白第一次上手 {short}，最容易卡在哪一步。"
    if category == "AI玩法":
        return f"把 {short} 拆成一个普通人今天就能试的玩法。"
    return f"{short} 和普通人有什么关系，用 3 个场景讲清楚。"


def tutorial_entry(category: str) -> str:
    if category in {"AI工具", "AI教程", "AI玩法"}:
        return "从“它能帮我做什么”开始，再做一次可复制的小操作。"
    return "先解释发生了什么，再讲它会影响哪些普通人的工作和学习。"


def tool_name(title: str, tags: list[str]) -> str:
    candidates = tags + re.findall(r"\b[A-Z][A-Za-z0-9_.-]{2,}\b", title)
    skip = {"AI", "API", "SDK", "LLM", "GPT"}
    for candidate in candidates:
        cleaned = candidate.strip("#,， ")
        if cleaned and cleaned.upper() not in skip:
            return cleaned[:60]
    return ""


def item_to_record(item: Any) -> dict[str, str]:
    title = text_of(item, "title", default="未命名")
    url = text_of(item, "url", "link")
    summary = text_of(item, "ai_summary", "summary", "description", "content")
    source = source_label(item)
    tags = tags_of(item)
    category = classify_topic(title, summary, tags)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    return {
        "日期": today,
        "一级分类": category,
        "标题": title[:180],
        "一句话解释": first_sentence(summary, title),
        "为什么适合AI小白": beginner_reason(category),
        "可拍角度": shooting_angle(category, title),
        "教程切入点": tutorial_entry(category),
        "工具/产品名": tool_name(title, tags),
        "来源平台": source,
        "原始链接": url,
        "AI评分": f"{score_of(item):.1f}",
        "可靠性": reliability(item, source, url, summary),
        "适合内容形态": content_shape(category, title, summary),
        "状态": "待筛选",
        "去重Key": stable_key(title, url),
        "标签": "、".join(tags[:8]),
        "AI摘要": summary[:800],
    }


def normalize_feishu_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("link") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


def extract_wiki_token(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    match = re.search(r"/wiki/([^/?#]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"wiki_token=([^&#]+)", value)
    if match:
        return urllib.parse.unquote(match.group(1))
    return value


class FeishuBitableClient:
    def __init__(self) -> None:
        self.app_id = required_env("FEISHU_APP_ID")
        self.app_secret = required_env("FEISHU_APP_SECRET")
        self.tenant_token = self._tenant_access_token()
        self.app_token = os.getenv("FEISHU_BITABLE_APP_TOKEN", "").strip()
        self.table_id = os.getenv("FEISHU_TABLE_ID", "").strip()

        if not self.app_token:
            wiki_token = extract_wiki_token(required_env("FEISHU_WIKI_TOKEN"))
            self.app_token = self._resolve_wiki_to_bitable_app_token(wiki_token)
            print("已通过 FEISHU_WIKI_TOKEN 解析到多维表格 app_token。")

        if not self.table_id:
            self.table_id = self._first_table_id()
            print(f"未提供 FEISHU_TABLE_ID，已默认使用第一张数据表：{self.table_id}")

    def _tenant_access_token(self) -> str:
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("code") != 0:
            raise RuntimeError(f"飞书鉴权失败：{json.dumps(result, ensure_ascii=False)}")
        return result["tenant_access_token"]

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {
            "Authorization": f"Bearer {self.tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"飞书请求失败 HTTP {exc.code}: {body}") from exc
        result = json.loads(body)
        if result.get("code") not in (0, None):
            raise RuntimeError(f"飞书接口报错：{json.dumps(result, ensure_ascii=False)}")
        return result

    def _resolve_wiki_to_bitable_app_token(self, wiki_token: str) -> str:
        query = urllib.parse.urlencode({"token": wiki_token})
        url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?{query}"
        result = self._request("GET", url)
        node = result.get("data", {}).get("node", {})
        obj_token = node.get("obj_token")
        obj_type = str(node.get("obj_type") or "").lower()
        if not obj_token:
            raise RuntimeError("wiki 链接解析失败：没有拿到 obj_token。请确认 FEISHU_WIKI_TOKEN 是多维表格页面的 wiki token。")
        if obj_type and obj_type not in {"bitable", "base"}:
            raise RuntimeError(f"这个 wiki 节点不是多维表格，飞书返回 obj_type={obj_type}。请打开真正的多维表格页面再复制 wiki 链接。")
        return obj_token

    def _base_url(self) -> str:
        return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}"

    def _first_table_id(self) -> str:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables?page_size=100"
        result = self._request("GET", url)
        items = result.get("data", {}).get("items", [])
        if not items:
            raise RuntimeError("这个多维表格里没有数据表，无法写入。请先在飞书里新建一张表。")
        table_id = items[0].get("table_id")
        if not table_id:
            raise RuntimeError("飞书返回的数据表没有 table_id，无法写入。")
        return table_id

    def list_fields(self) -> set[str]:
        fields: set[str] = set()
        page_token = ""
        while True:
            query = {"page_size": 100}
            if page_token:
                query["page_token"] = page_token
            result = self._request("GET", f"{self._base_url()}/fields?{urllib.parse.urlencode(query)}")
            data = result.get("data", {})
            for item in data.get("items", []):
                name = item.get("field_name")
                if name:
                    fields.add(name)
            if not data.get("has_more"):
                return fields
            page_token = data.get("page_token", "")

    def ensure_fields(self) -> None:
        existing = self.list_fields()
        missing = [name for name in FIELD_NAMES if name not in existing]
        if not missing:
            return
        if os.getenv("FEISHU_AUTO_CREATE_FIELDS", "true").lower() != "true":
            raise RuntimeError("飞书表格缺少字段：" + "、".join(missing))
        for name in missing:
            self._request("POST", f"{self._base_url()}/fields", {"field_name": name, "type": 1})

    def existing_dedup_keys(self) -> set[str]:
        keys: set[str] = set()
        page_token = ""
        while True:
            query = {"page_size": 500, "field_names": json.dumps(["去重Key"], ensure_ascii=False)}
            if page_token:
                query["page_token"] = page_token
            result = self._request("GET", f"{self._base_url()}/records?{urllib.parse.urlencode(query)}")
            data = result.get("data", {})
            for item in data.get("items", []):
                value = normalize_feishu_value(item.get("fields", {}).get("去重Key"))
                if value:
                    keys.add(value)
            if not data.get("has_more"):
                return keys
            page_token = data.get("page_token", "")

    def batch_create(self, records: list[dict[str, str]]) -> int:
        written = 0
        for start in range(0, len(records), 100):
            chunk = records[start : start + 100]
            if not chunk:
                continue
            payload = {"records": [{"fields": record} for record in chunk]}
            self._request("POST", f"{self._base_url()}/records/batch_create", payload)
            written += len(chunk)
        return written


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set(data.get("seen_keys", []))


def save_seen(path: Path, keys: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "seen_keys": sorted(keys),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def collect_with_horizon(hours: int) -> list[Any]:
    from src.orchestrator import HorizonOrchestrator
    from src.storage.manager import StorageManager

    storage = StorageManager(data_dir="data")
    config = storage.load_config()
    orchestrator = HorizonOrchestrator(config, storage)

    since = orchestrator._determine_time_window(hours)
    all_items = await orchestrator.fetch_all_sources(since)
    if not all_items:
        return []

    merged_items = orchestrator.merge_cross_source_duplicates(all_items)
    analyzed_items = await orchestrator._analyze_content(merged_items)
    threshold = config.filtering.ai_score_threshold
    important_items = [item for item in analyzed_items if item.ai_score and item.ai_score >= threshold]
    important_items.sort(key=lambda item: item.ai_score or 0, reverse=True)
    important_items = await orchestrator.merge_topic_duplicates(important_items)
    await orchestrator._enrich_important_items(important_items)
    return important_items


async def main() -> int:
    parser = argparse.ArgumentParser(description="Collect AI topics with Horizon and write them to Feishu Bitable.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    load_env_file(Path(".env"))
    required_env("OPENAI_API_KEY")
    required_env("FEISHU_APP_ID")
    required_env("FEISHU_APP_SECRET")
    if not os.getenv("FEISHU_BITABLE_APP_TOKEN") and not os.getenv("FEISHU_WIKI_TOKEN"):
        raise RuntimeError("缺少飞书目标：请设置 FEISHU_WIKI_TOKEN，或者同时设置 FEISHU_BITABLE_APP_TOKEN 和 FEISHU_TABLE_ID。")

    state_path = Path(".state/seen_topics.json")
    preview_path = Path("outputs/last_run_preview.json")
    summary_path = Path("outputs/last_run_summary.json")

    items = await collect_with_horizon(args.hours)
    records = [item_to_record(item) for item in items]
    records = sorted(records, key=lambda row: float(row.get("AI评分") or 0), reverse=True)[: args.limit]

    client = FeishuBitableClient()
    client.ensure_fields()

    local_seen = load_seen(state_path)
    remote_seen = client.existing_dedup_keys()
    known_keys = local_seen | remote_seen
    new_records = [record for record in records if record["去重Key"] not in known_keys]

    written = client.batch_create(new_records)
    if written:
        local_seen.update(record["去重Key"] for record in new_records)
        save_seen(state_path, local_seen)

    summary = {
        "hours": args.hours,
        "candidate_items": len(records),
        "new_records": len(new_records),
        "written_to_feishu": written,
        "skipped_duplicates": len(records) - len(new_records),
        "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    write_json(preview_path, new_records)
    write_json(summary_path, summary)

    print(f"候选选题：{len(records)}")
    print(f"新增选题：{len(new_records)}")
    print(f"写入飞书：{written}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        raise
