from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Any, Iterable

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "to", "with", "用", "把", "的", "了",
    "和", "与", "一个", "一篇", "这个", "那个", "请", "帮我", "需要", "进行",
}

ALIAS_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "润色": ("改写", "transform", "rewrite", "polish"),
    "改写": ("润色", "transform", "rewrite"),
    "网页": ("页面", "官网", "落地页", "landing page", "ui", "web design"),
    "页面": ("网页", "ui", "landing page"),
    "设计": ("design", "ui", "visual", "视觉"),
    "界面": ("ui", "ux", "界面设计"),
    "样式": ("css", "design", "ui", "视觉"),
    "调研": ("research", "survey", "search", "retrieve"),
    "搜索": ("search", "retrieve", "research"),
    "检索": ("search", "retrieve", "research"),
    "收集": ("collect", "retrieve", "search"),
    "资料": ("source", "research", "retrieve"),
    "来源": ("source", "research", "citation"),
    "引用": ("citation", "source", "reference"),
    "核查": ("verify", "check", "review", "audit"),
    "核实": ("verify", "check", "fact"),
    "事实": ("fact", "claim", "verify"),
    "市场": ("market", "research"),
    "竞品": ("competitor", "compare", "analysis"),
    "报告": ("report", "write", "generate"),
    "撰写": ("write", "generate", "draft"),
    "续写": ("continue", "write", "generate"),
    "对白": ("dialogue", "conversation"),
    "台词": ("dialogue", "conversation"),
    "对话": ("dialogue", "conversation"),
    "提取": ("extract", "transform"),
    "转换": ("convert", "transform"),
    "整理": ("organize", "transform", "format"),
    "检查": ("check", "verify", "review"),
    "审查": ("review", "audit", "verify"),
    "质量": ("quality", "verify", "check"),
    "测试": ("test", "testing", "verify"),
    "安全": ("security", "safety", "verify"),
    "图片": ("image", "picture", "photo"),
    "处理": ("process", "edit", "transform"),
    "调整": ("resize", "adjust", "transform"),
    "尺寸": ("size", "resize"),
    "格式": ("format", "convert"),
    "视频": ("video",),
    "音频": ("audio",),
    "AI味": ("ai tone", "de-ai", "tone"),
    "AI 味": ("ai tone", "de-ai", "tone"),
    "提示词优化": ("prompt optimization", "prompt optimizer"),
    "提示词": ("prompt", "prompting"),
    "三国": ("sanguo", "three kingdoms"),
    "红楼": ("hongloumeng", "red chamber"),
    "西游": ("xiyouji", "journey to the west"),
    "榜单": ("ranking", "list", "market"),
    "平台": ("platform", "source"),
    "分析": ("analyze", "analysis", "compare"),
    "对比": ("compare", "analyze"),
    "翻译": ("translate", "transform"),
    "总结": ("summarize", "transform"),
    "代码": ("code", "programming", "implementation"),
    "脚本": ("script", "code"),
    "写作": ("write", "generate", "content"),
    "文案": ("copywriting", "write", "content"),
    "小说": ("fiction", "story", "narrative"),
    "表格": ("table", "excel", "csv", "spreadsheet"),
    "幻灯片": ("ppt", "slide", "presentation"),
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]*")
_CJK_RE = re.compile(r"[㐀-鿿]+")
MODALITY_TERMS = ("pdf", "docx", "xlsx", "pptx", "html", "markdown")
INTENT_NAME_SEGMENTS: dict[str, tuple[str, ...]] = {
    "对白": ("dialogue",),
    "台词": ("dialogue",),
    "对话": ("dialogue",),
    "诗歌": ("poetry", "poem"),
    "诗词": ("poetry", "poem"),
    "海报": ("poster",),
    "图片": ("image",),
    "审查": ("review",),
    "提取": ("extract",),
    "转换": ("convert",),
    "测试": ("test",),
    "表格": ("table", "sheet"),
    "pdf": ("pdf",),
    "docx": ("doc", "docx"),
    "人物": ("character",),
}


def normalize_text(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def tokenize(text: Any) -> list[str]:
    lowered = normalize_text(text)
    tokens: list[str] = []
    for word in _TOKEN_RE.findall(lowered):
        if word in STOPWORDS:
            continue
        if len(word) == 1 and word not in {"a", "i"}:
            continue
        tokens.append(word)
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            tokens.append(word[:-1])
    for run in _CJK_RE.findall(lowered):
        if run in STOPWORDS:
            continue
        if len(run) <= 4:
            tokens.append(run)
        for index in range(len(run) - 1):
            bigram = run[index : index + 2]
            if bigram not in STOPWORDS:
                tokens.append(bigram)
    return tokens


def expand_query(query: Any) -> list[str]:
    lowered = normalize_text(query)
    tokens = tokenize(lowered)
    extras: list[str] = []
    for key, expansions in ALIAS_EXPANSIONS.items():
        if key in lowered:
            for expansion in expansions:
                extras.extend(tokenize(expansion))
    return list(dict.fromkeys(tokens + extras))


def _text_of(entry: dict[str, Any]) -> str:
    name = str(entry.get("name") or entry.get("id") or "")
    description = str(entry.get("description") or "")
    keywords = " ".join(str(value) for value in (entry.get("keywords") or []))
    capabilities = " ".join(str(value) for value in (entry.get("capabilities") or []))
    relative = str(entry.get("relative_path") or "")
    return " ".join([name, name, name, keywords, keywords, description, capabilities, relative])


class BM25Index:
    def __init__(self, documents: list[list[str]], keys: list[str], *, k1: float = 1.5, b: float = 0.75):
        self.documents = documents
        self.keys = keys
        self.k1 = k1
        self.b = b
        self.n = len(documents)
        self.doc_len = [len(document) for document in documents]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 1.0
        self.term_freq = [Counter(document) for document in documents]
        self.doc_freq: Counter[str] = Counter()
        for document in documents:
            self.doc_freq.update(set(document))
        self.idf = {
            term: math.log(1.0 + (self.n - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in self.doc_freq.items()
        }

    def raw_score(self, query_tokens: Iterable[str], index: int) -> float:
        if not 0 <= index < self.n:
            return 0.0
        score = 0.0
        frequencies = self.term_freq[index]
        length = self.doc_len[index] or 1
        for term in query_tokens:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            idf = self.idf.get(term, 0.0)
            denominator = frequency + self.k1 * (1.0 - self.b + self.b * length / self.avgdl)
            score += idf * (frequency * (self.k1 + 1.0)) / denominator
        return score

    def normalized_scores(self, query_tokens: Iterable[str]) -> dict[str, float]:
        tokens = list(query_tokens)
        scores: dict[str, float] = {}
        for index, key in enumerate(self.keys):
            raw = self.raw_score(tokens, index)
            scores[key] = raw / (raw + 8.0) if raw > 0 else 0.0
        return scores


def _normalized_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").lower()


def route(
    entries: list[dict[str, Any]],
    need: str,
    top_k: int = 5,
    types: Iterable[str] | None = None,
    capabilities: Iterable[str] | None = None,
    domains: Iterable[str] | None = None,
    min_score: float = 0.0,
    exclude_path_contains: Iterable[str] | None = None,
    exclude_id_prefixes: Iterable[str] | None = None,
) -> dict[str, Any]:
    allowed_types = {item for item in types} if types else None
    required_capabilities = {item for item in capabilities} if capabilities else None
    allowed_domains = {str(item).lower() for item in domains} if domains else None
    path_excludes = tuple(_normalized_path(item) for item in (exclude_path_contains or ()) if str(item).strip())
    id_excludes = tuple(str(item) for item in (exclude_id_prefixes or ()) if str(item).strip())
    candidates = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("status") != "stale"
        and (allowed_types is None or entry.get("type") in allowed_types)
        and (
            required_capabilities is None
            or bool(required_capabilities & {str(item) for item in (entry.get("capabilities") or [])})
        )
        and (
            allowed_domains is None
            or str(entry.get("domain") or "general").lower() in allowed_domains
        )
        and not any(pattern in _normalized_path(entry.get("path")) for pattern in path_excludes)
        and not any(str(entry.get("id") or "").startswith(prefix) for prefix in id_excludes)
    ]
    if not candidates:
        return {"schema": "caprouter.route.v1", "need": need, "count": 0, "results": []}

    documents = [tokenize(_text_of(entry)) for entry in candidates]
    keys = [str(entry.get("id")) for entry in candidates]
    index = BM25Index(documents, keys)
    query_tokens = expand_query(need)
    scores = index.normalized_scores(query_tokens)

    lowered_need = normalize_text(need)
    boosted: list[tuple[float, dict[str, Any]]] = []
    for entry in candidates:
        key = str(entry.get("id"))
        score = scores.get(key, 0.0)
        name = normalize_text(entry.get("name"))
        if name and name in lowered_need:
            score += 0.25
        keyword_hits = [kw for kw in (entry.get("keywords") or []) if normalize_text(kw) and normalize_text(kw) in lowered_need]
        if keyword_hits:
            score += min(0.16, 0.08 * len(keyword_hits))
        name_segments = [segment for segment in re.split(r"[-_]+", name) if segment]
        segment_hits = [segment for segment in name_segments if segment in lowered_need]
        if segment_hits:
            score += min(0.24, 0.08 * len(segment_hits))
        entry_text = f"{name} {normalize_text(entry.get('description'))}"
        modality_hits = [term for term in MODALITY_TERMS if term in lowered_need and term in entry_text]
        if modality_hits:
            score += min(0.2, 0.1 * len(modality_hits))
        intent_segments: list[str] = []
        for key, segments in INTENT_NAME_SEGMENTS.items():
            if key in lowered_need:
                intent_segments.extend(segment for segment in segments if segment in name_segments)
        if intent_segments:
            score += min(0.24, 0.12 * len(set(intent_segments)))
        if score > min_score:
            boosted.append((score, entry))

    boosted.sort(key=lambda item: (-item[0], str(item[1].get("id"))))
    query_set = set(query_tokens)
    results: list[dict[str, Any]] = []
    for score, entry in boosted[: max(1, top_k)]:
        document_tokens = set(tokenize(_text_of(entry)))
        matched = [token for token in query_tokens if token in document_tokens]
        results.append(
            {
                "id": entry.get("id"),
                "type": entry.get("type"),
                "name": entry.get("name"),
                "path": entry.get("path"),
                "description": entry.get("description"),
                "domain": entry.get("domain") or "general",
                "capabilities": entry.get("capabilities") or [],
                "status": entry.get("status"),
                "score": round(score, 4),
                "why": list(dict.fromkeys(matched))[:5],
            }
        )
    return {"schema": "caprouter.route.v1", "need": need, "count": len(results), "results": results}
