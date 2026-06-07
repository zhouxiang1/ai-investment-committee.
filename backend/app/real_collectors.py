from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from xml.etree import ElementTree

import httpx

from .database import ROOT


USER_AGENT = os.getenv(
    "AI_COMMITTEE_DATA_USER_AGENT",
    "AI Investment Committee research collector contact@example.com",
)
REQUEST_TIMEOUT = float(os.getenv("AI_COMMITTEE_COLLECTOR_TIMEOUT", "12"))
MAX_NEWS_ITEMS = int(os.getenv("AI_COMMITTEE_MAX_NEWS_ITEMS", "5"))
MAX_SOCIAL_ITEMS = int(os.getenv("AI_COMMITTEE_MAX_SOCIAL_ITEMS", "8"))
MAX_FILING_ITEMS = int(os.getenv("AI_COMMITTEE_MAX_FILING_ITEMS", "3"))
MAX_RESEARCH_ITEMS = int(os.getenv("AI_COMMITTEE_MAX_RESEARCH_ITEMS", "3"))
MAX_PDF_BYTES = int(float(os.getenv("AI_COMMITTEE_MAX_PDF_MB", "50")) * 1024 * 1024)
EXCERPT_CHARS = int(os.getenv("AI_COMMITTEE_EVIDENCE_EXCERPT_CHARS", "1800"))
ENABLE_SEARCH_FALLBACK = os.getenv("AI_COMMITTEE_ENABLE_SEARCH_FALLBACK", "true").lower() not in {"0", "false", "no", "off"}
MAX_SEARCH_RESULTS = int(os.getenv("AI_COMMITTEE_MAX_SEARCH_RESULTS", "5"))
MAX_SEARCH_DOCUMENTS = int(os.getenv("AI_COMMITTEE_MAX_SEARCH_DOCUMENTS", "3"))


@dataclass
class SavedDocument:
    text_path: str | None = None
    binary_path: str | None = None
    text: str = ""
    truncated: bool = False


class ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        clean = data.strip()
        if clean:
            self.parts.append(clean)

    def text(self) -> str:
        return normalize_text(" ".join(self.parts))


def collect_real_world_sources(company: dict, security_id: str, run_id: str) -> dict:
    base_dir = ROOT / "data" / "raw_sources" / safe_slug(run_id)
    base_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "news": [],
        "social": [],
        "filings": [],
        "financial_statements": [],
        "research_reports": [],
        "technical_history": [],
        "valuation_history": [],
        "evidence": [],
        "gaps": [],
        "source_attempts": [],
        "company_profile": {},
        "financial_metrics": {},
        "financial_series": [],
        "documents_dir": str(base_dir),
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
        ticker = str(company.get("ticker") or "")
        market = company.get("market")
        if market == "US":
            run_collector(result, "SEC EDGAR", collect_us_sec, client, company, security_id, base_dir, result)
            run_collector(result, "StockTwits", collect_stocktwits, client, company, security_id, base_dir, result)
        elif market == "HK":
            run_collector(result, "HKEXnews", collect_hkex_announcements, client, company, security_id, base_dir, result)
            run_collector(result, "Eastmoney HK F10", collect_eastmoney_hk_financials, client, company, security_id, base_dir, result)
            run_collector(result, "Eastmoney Guba", collect_eastmoney_guba, client, company, security_id, base_dir, result)
        else:
            run_collector(result, "AKShare A Financials", collect_akshare_a_financials, client, company, security_id, base_dir, result)
            run_collector(result, "CNInfo", collect_cninfo_announcements, client, company, security_id, base_dir, result)
            run_collector(result, "Eastmoney Guba", collect_eastmoney_guba, client, company, security_id, base_dir, result)
        run_collector(result, "Yahoo Finance News", collect_yahoo_news, client, company, security_id, base_dir, result)
        run_collector(result, "Reddit public JSON", collect_reddit_posts, client, company, security_id, base_dir, result)
        run_collector(result, "Eastmoney Research", collect_eastmoney_research, client, company, security_id, base_dir, result)
        run_collector(result, "Yahoo Technical History", collect_yahoo_technical_history, client, company, security_id, base_dir, result)
        collect_missing_sources_with_search(client, company, security_id, base_dir, result)

    mark_missing_sources(result)
    return result


def run_collector(result: dict, source: str, collector, *args) -> None:
    before = category_counts(result)
    gap_count = len(result.get("gaps") or [])
    try:
        collector(*args)
    except Exception as exc:
        result.setdefault("gaps", []).append(f"{source} 适配器异常：{exc}")
        record_source_attempt(result, source, "failed", str(exc))
        return
    after = category_counts(result)
    deltas = {key: after[key] - before[key] for key in after if after[key] != before[key]}
    new_gaps = (result.get("gaps") or [])[gap_count:]
    if deltas:
        record_source_attempt(result, source, "done", json.dumps(deltas, ensure_ascii=False))
    elif new_gaps:
        record_source_attempt(result, source, "empty", "；".join(str(item) for item in new_gaps[:3]))
    else:
        record_source_attempt(result, source, "empty", "未产生新增证据")


def category_counts(result: dict) -> dict[str, int]:
    keys = ["news", "social", "filings", "financial_statements", "research_reports", "technical_history", "valuation_history", "evidence"]
    return {key: len(result.get(key) or []) for key in keys}


def collect_us_sec(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    ticker = str(company.get("ticker") or "").split(".")[0].upper()
    cik = lookup_sec_cik(client, ticker)
    if not cik:
        result["gaps"].append(f"SEC CIK 未匹配到 {ticker}")
        return

    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    facts = request_json(client, facts_url)
    if facts:
        facts_doc = save_json(base_dir / "financials", f"sec_companyfacts_{ticker}.json", facts)
        metrics, series = extract_sec_financial_metrics(facts, company)
        if metrics:
            metrics_doc = save_text(
                base_dir / "financials",
                f"sec_financial_metrics_{ticker}.json",
                json.dumps({"metrics": metrics, "series": series}, ensure_ascii=False, indent=2),
            )
            result["financial_metrics"] = merge_metric_defaults(result.get("financial_metrics") or {}, metrics)
            result["financial_series"] = merge_financial_series(result.get("financial_series") or [], series)
            item = {
                "evidence_id": "ev_financial_metrics_sec",
                "company_id": company["id"],
                "security_id": security_id,
                "category": "metric",
                "title": "美国证监会结构化财务指标",
                "summary": sec_metrics_summary(company, metrics, series),
                "raw_value": json.dumps(metrics, ensure_ascii=False),
                "normalized_value": json.dumps(metrics, ensure_ascii=False),
                "unit": metrics.get("currency") or "USD",
                "period": metrics.get("period"),
                "date": metrics.get("filed") or utc_now_iso(),
                "source_provider": "SEC Companyfacts",
                "source_url": facts_url,
                "source_document_id": metrics_doc.text_path,
                "extracted_quote": excerpt(json.dumps({"metrics": metrics, "series": series[:5]}, ensure_ascii=False, indent=2)),
                "confidence": 0.91,
                "freshness_score": freshness_from_date(metrics.get("filed") or metrics.get("period")),
                "created_at": utc_now_iso(),
            }
            result["evidence"].append(item)
        financials = build_sec_financial_statement_items(facts)
        for statement_type, payload in financials:
            if not payload["facts"]:
                continue
            statement_doc = save_text(
                base_dir / "financials",
                f"sec_{statement_type}_{ticker}.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
            item = {
                "evidence_id": f"ev_financial_{statement_type}_sec",
                "company_id": company["id"],
                "security_id": security_id,
                "category": "financial_statement",
                "title": f"美国证监会结构化财报：{payload['label']}",
                "summary": sec_statement_summary(payload),
                "raw_value": json.dumps(payload["facts"], ensure_ascii=False),
                "normalized_value": json.dumps(payload["latest"], ensure_ascii=False),
                "unit": payload.get("unit"),
                "period": payload.get("period"),
                "date": payload.get("filed") or utc_now_iso(),
                "source_provider": "SEC Companyfacts",
                "source_url": facts_url,
                "source_document_id": statement_doc.text_path or facts_doc.text_path,
                "extracted_quote": excerpt(json.dumps(payload["latest"], ensure_ascii=False, indent=2)),
                "confidence": 0.93,
                "freshness_score": freshness_from_date(payload.get("filed")),
                "created_at": utc_now_iso(),
            }
            result["financial_statements"].append(item)
            result["evidence"].append(item)
    else:
        result["gaps"].append("SEC Companyfacts 未返回可解析财务三表")

    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    submissions = request_json(client, submissions_url)
    if not submissions:
        result["gaps"].append("SEC submissions 未返回公告/申报列表")
        return
    save_json(base_dir / "filings", f"sec_submissions_{ticker}.json", submissions)
    filing_texts: list[str] = []
    for filing in latest_sec_filings(submissions):
        filing_url = sec_filing_url(cik, filing)
        text = fetch_readable_text(client, filing_url)
        if text:
            filing_texts.append(text[:30000])
        saved = save_text(base_dir / "filings", f"sec_{filing['form']}_{filing['accession']}.txt", text)
        item = {
            "evidence_id": f"ev_filing_sec_{safe_slug(filing['form'])}",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "filing",
            "title": f"美国证监会{sec_form_label(filing['form'])}原文",
            "summary": f"{company['name']} 最新{sec_form_label(filing['form'])}，申报日期={filing.get('filing_date')}，已抓取申报正文 {len(text)} 字符。",
            "raw_value": json.dumps(filing, ensure_ascii=False),
            "normalized_value": filing.get("form"),
            "unit": "text",
            "period": filing.get("report_date"),
            "date": filing.get("filing_date") or utc_now_iso(),
            "source_provider": "美国证监会公告系统",
            "source_url": filing_url,
            "source_document_id": saved.text_path,
            "extracted_quote": excerpt(text),
            "confidence": 0.94 if text else 0.72,
            "freshness_score": freshness_from_date(filing.get("filing_date")),
            "created_at": utc_now_iso(),
        }
        result["filings"].append(item)
        result["evidence"].append(item)
    profile = build_sec_company_profile(company, submissions, filing_texts)
    if profile:
        result["company_profile"] = profile
        profile_doc = save_text(
            base_dir / "profile",
            f"sec_company_profile_{ticker}.json",
            json.dumps(profile, ensure_ascii=False, indent=2),
        )
        profile_item = {
            "evidence_id": "ev_profile_sec",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "industry",
            "title": "SEC 公司行业与业务画像",
            "summary": profile.get("summary") or f"SEC submissions 行业信息：{profile.get('sic_description') or 'N/A'}。",
            "raw_value": json.dumps(profile, ensure_ascii=False),
            "normalized_value": json.dumps(
                {"industry": profile.get("industry"), "sector": profile.get("sector"), "tags": profile.get("tags")},
                ensure_ascii=False,
            ),
            "unit": "profile",
            "period": utc_now_iso(),
            "date": utc_now_iso(),
            "source_provider": "SEC Submissions + Filing Text",
            "source_url": submissions_url,
            "source_document_id": profile_doc.text_path,
            "extracted_quote": excerpt(profile.get("description") or profile.get("summary") or ""),
            "confidence": profile.get("confidence", 0.76),
            "freshness_score": freshness_from_date(profile.get("filed") or utc_now_iso()),
            "created_at": utc_now_iso(),
        }
        result["evidence"].append(profile_item)


def collect_yahoo_news(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    symbol = yahoo_symbol(company)
    if not symbol:
        result["gaps"].append("Yahoo Finance 新闻源缺少可用证券代码")
        return
    feed_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(symbol)}&region=US&lang=en-US"
    try:
        response = client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    except Exception as exc:
        result["gaps"].append(f"Yahoo Finance 新闻 RSS 抓取失败：{exc}")
        return
    items = root.findall(".//item")[:MAX_NEWS_ITEMS]
    if not items:
        result["gaps"].append("Yahoo Finance 新闻 RSS 未返回新闻条目")
        return
    for index, item in enumerate(items, start=1):
        title = node_text(item, "title") or "Yahoo Finance news"
        link = node_text(item, "link")
        pub_date = parse_rss_date(node_text(item, "pubDate"))
        description = strip_html(node_text(item, "description"))
        article_text = fetch_readable_text(client, link) if link else ""
        text = article_text if len(article_text) >= 300 else description
        saved = save_text(base_dir / "news", f"yahoo_news_{index}.txt", text)
        evidence = {
            "evidence_id": f"ev_news_yahoo_{index}",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "news",
            "title": title,
            "summary": f"Yahoo Finance 新闻：{description[:220] or title}",
            "raw_value": title,
            "normalized_value": description,
            "unit": "text",
            "period": pub_date,
            "date": pub_date or utc_now_iso(),
            "source_provider": "Yahoo Finance RSS",
            "source_url": link or feed_url,
            "source_document_id": saved.text_path,
            "extracted_quote": excerpt(text),
            "confidence": 0.78 if len(article_text) >= 300 else 0.58,
            "freshness_score": freshness_from_date(pub_date),
            "created_at": utc_now_iso(),
        }
        result["news"].append(evidence)
        result["evidence"].append(evidence)
        maybe_add_research_from_news(result, evidence, text)


def collect_stocktwits(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    ticker = str(company.get("ticker") or "").split(".")[0].upper()
    if not re.fullmatch(r"[A-Z0-9.]{1,12}", ticker):
        return
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    data = request_json(client, url, params={"limit": MAX_SOCIAL_ITEMS}, headers={"User-Agent": "Mozilla/5.0"})
    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not messages:
        result["gaps"].append("StockTwits 未返回社媒消息")
        return
    simplified = []
    for message in messages[:MAX_SOCIAL_ITEMS]:
        body = normalize_text(strip_html(message.get("body") or ""))
        if not body:
            continue
        simplified.append(
            {
                "id": message.get("id"),
                "created_at": message.get("created_at"),
                "user": (message.get("user") or {}).get("username"),
                "sentiment": (message.get("entities") or {}).get("sentiment"),
                "body": body,
            }
        )
    if not simplified:
        result["gaps"].append("StockTwits 消息为空")
        return
    saved = save_json(base_dir / "social", f"stocktwits_{ticker}.json", simplified)
    text = "\n\n".join(f"{item.get('created_at')}: {item['body']}" for item in simplified)
    evidence = {
        "evidence_id": "ev_social_stocktwits_recent",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "social",
        "title": "StockTwits 最新讨论",
        "summary": f"抓取 StockTwits {len(simplified)} 条公开讨论，用于观察散户情绪和短线争议。",
        "raw_value": json.dumps(simplified, ensure_ascii=False),
        "normalized_value": text[:1200],
        "unit": "posts",
        "period": simplified[0].get("created_at"),
        "date": simplified[0].get("created_at") or utc_now_iso(),
        "source_provider": "StockTwits",
        "source_url": url,
        "source_document_id": saved.text_path,
        "extracted_quote": excerpt(text),
        "confidence": 0.7,
        "freshness_score": freshness_from_date(simplified[0].get("created_at")),
        "created_at": utc_now_iso(),
    }
    result["social"].append(evidence)
    result["evidence"].append(evidence)


def maybe_add_research_from_news(result: dict, news_evidence: dict, text: str) -> None:
    if len(result.get("research_reports") or []) >= MAX_RESEARCH_ITEMS:
        return
    haystack = f"{news_evidence.get('title') or ''} {news_evidence.get('summary') or ''} {text[:1200]}".lower()
    keywords = [
        "analyst",
        "price target",
        "rating",
        "upgrade",
        "downgrade",
        "initiated coverage",
        "morgan stanley",
        "goldman",
        "jpmorgan",
        "bank of america",
        "ark",
    ]
    if not any(keyword in haystack for keyword in keywords):
        return
    index = len(result.get("research_reports") or []) + 1
    item = {
        **news_evidence,
        "evidence_id": f"ev_research_yahoo_{index}",
        "category": "research",
        "title": f"美股券商/分析师观点索引：{news_evidence.get('title')}",
        "summary": "Yahoo 新闻中识别到券商评级、目标价或机构观点关键词；作为研报索引增强，不等同于完整付费研报全文。",
        "source_provider": "Yahoo Finance Analyst News",
        "confidence": min(0.62, float(news_evidence.get("confidence") or 0.58)),
    }
    result["research_reports"].append(item)
    result["evidence"].append(item)


def collect_reddit_posts(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    query = f"{company.get('ticker')} {company.get('name')} stock"
    url = "https://www.reddit.com/search.json"
    data = request_json(
        client,
        url,
        params={"q": query, "sort": "new", "limit": MAX_SOCIAL_ITEMS},
        headers={"User-Agent": "AIInvestmentCommittee/0.1 by research collector"},
    )
    children = ((data.get("data") or {}).get("children") or []) if isinstance(data, dict) else []
    posts = []
    for child in children[:MAX_SOCIAL_ITEMS]:
        item = child.get("data") or {}
        title = normalize_text(item.get("title") or "")
        body = normalize_text(item.get("selftext") or "")
        if not title and not body:
            continue
        posts.append(
            {
                "id": item.get("id"),
                "subreddit": item.get("subreddit"),
                "created_utc": item.get("created_utc"),
                "title": title,
                "body": body,
                "score": item.get("score"),
                "url": "https://www.reddit.com" + item.get("permalink", ""),
            }
        )
    if not posts:
        result["gaps"].append("Reddit 公开搜索未返回可用社媒内容")
        return
    saved = save_json(base_dir / "social", "reddit_search.json", posts)
    text = "\n\n".join(f"{post['title']}\n{post['body']}" for post in posts)
    evidence = {
        "evidence_id": "ev_social_reddit_recent",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "social",
        "title": "Reddit 最新公开讨论",
        "summary": f"抓取 Reddit 公开搜索 {len(posts)} 条帖子/正文，用于交叉观察社媒分歧。",
        "raw_value": json.dumps(posts, ensure_ascii=False),
        "normalized_value": text[:1200],
        "unit": "posts",
        "period": utc_now_iso(),
        "date": utc_now_iso(),
        "source_provider": "Reddit public JSON",
        "source_url": f"{url}?q={quote_plus(query)}",
        "source_document_id": saved.text_path,
        "extracted_quote": excerpt(text),
        "confidence": 0.62,
        "freshness_score": 0.72,
        "created_at": utc_now_iso(),
    }
    result["social"].append(evidence)
    result["evidence"].append(evidence)


def collect_eastmoney_guba(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    market = company.get("market")
    raw_code = re.sub(r"\D", "", str(company.get("ticker") or "").split(".")[0])
    if market == "HK" and raw_code:
        code = "HK" + raw_code.zfill(5)
    elif len(raw_code) == 6:
        code = raw_code
    else:
        result["gaps"].append("东方财富股吧缺少可识别 A 股/港股代码")
        return
    url = f"https://guba.eastmoney.com/list,{code}.html"
    try:
        response = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        match = re.search(r"var article_list=(\{.*?\});\s*var other_list=", response.text, flags=re.S)
        if not match:
            result["gaps"].append("东方财富股吧页面未找到 article_list")
            return
        payload = json.loads(match.group(1))
    except Exception as exc:
        result["gaps"].append(f"东方财富股吧抓取失败：{exc}")
        return
    posts = []
    for item in payload.get("re", [])[:MAX_SOCIAL_ITEMS]:
        title = normalize_text(item.get("post_title") or "")
        content = normalize_text(item.get("post_content") or "")
        if not title and not content:
            continue
        post_id = item.get("post_id")
        posts.append(
            {
                "post_id": post_id,
                "title": title,
                "content": content,
                "user": item.get("user_nickname") or (item.get("post_user") or {}).get("user_nickname"),
                "published_at": item.get("post_publish_time") or item.get("post_display_time"),
                "last_time": item.get("post_last_time"),
                "click_count": item.get("post_click_count"),
                "comment_count": item.get("post_comment_count"),
                "url": f"https://guba.eastmoney.com/news,{code},{post_id}.html" if post_id else url,
            }
        )
    if not posts:
        result["gaps"].append("东方财富股吧未解析出可用帖子")
        return
    saved = save_json(base_dir / "social", f"eastmoney_guba_{code}.json", posts)
    text = "\n\n".join(f"{post['published_at']} {post['title']}\n{post['content']}" for post in posts)
    evidence = {
        "evidence_id": "ev_social_eastmoney_guba_recent",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "social",
        "title": "东方财富股吧最新讨论",
        "summary": f"抓取东方财富股吧 {len(posts)} 条公开帖子，包含标题、正文片段、发布时间和互动数。",
        "raw_value": json.dumps(posts, ensure_ascii=False),
        "normalized_value": text[:1200],
        "unit": "posts",
        "period": posts[0].get("published_at"),
        "date": posts[0].get("published_at") or utc_now_iso(),
        "source_provider": "Eastmoney Guba",
        "source_url": url,
        "source_document_id": saved.text_path,
        "extracted_quote": excerpt(text),
        "confidence": 0.68,
        "freshness_score": freshness_from_date(posts[0].get("published_at")),
        "created_at": utc_now_iso(),
    }
    result["social"].append(evidence)
    result["evidence"].append(evidence)


def collect_missing_sources_with_search(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    if not ENABLE_SEARCH_FALLBACK:
        record_source_attempt(result, "Search fallback", "skipped", "AI_COMMITTEE_ENABLE_SEARCH_FALLBACK=false")
        return
    missing_docs = not result.get("filings") or not result.get("financial_statements")
    missing_news = not result.get("news")
    if not missing_docs and not missing_news:
        return
    if missing_docs:
        collect_search_documents(client, company, security_id, base_dir, result)
    if missing_news and len(result.get("news") or []) < 1:
        collect_search_news(client, company, security_id, base_dir, result)


def collect_search_documents(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    queries = filing_search_queries(company)
    seen_urls: set[str] = set()
    before_filings = len(result.get("filings") or [])
    before_financials = len(result.get("financial_statements") or [])
    for query in queries:
        for search_provider in [search_duckduckgo, search_bing]:
            records = search_provider(client, query, base_dir)
            record_source_attempt(result, search_provider.__name__, "done" if records else "empty", query)
            for record in records:
                url = clean_search_url(record.get("url") or "")
                if not url or url in seen_urls or not useful_document_url(url, record.get("title") or ""):
                    continue
                seen_urls.add(url)
                if add_search_document_evidence(client, company, security_id, base_dir, result, record, len(seen_urls)):
                    if result.get("filings") and result.get("financial_statements"):
                        return
                if len(seen_urls) >= MAX_SEARCH_DOCUMENTS:
                    break
            if len(seen_urls) >= MAX_SEARCH_DOCUMENTS or (result.get("filings") and result.get("financial_statements")):
                break
        if len(seen_urls) >= MAX_SEARCH_DOCUMENTS or (result.get("filings") and result.get("financial_statements")):
            break
    if len(result.get("filings") or []) == before_filings:
        result["gaps"].append("搜索 fallback 未补齐公告/年报原文")
    if len(result.get("financial_statements") or []) == before_financials:
        result["gaps"].append("搜索 fallback 未补齐财报三表原文")


def collect_search_news(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    query = f"{company.get('name')} {company.get('ticker')} latest earnings news"
    seen_urls: set[str] = set()
    before = len(result.get("news") or [])
    for search_provider in [search_duckduckgo, search_bing]:
        records = search_provider(client, query, base_dir)
        record_source_attempt(result, search_provider.__name__, "done" if records else "empty", query)
        for record in records[:MAX_SEARCH_RESULTS]:
            url = clean_search_url(record.get("url") or "")
            if not url or url in seen_urls or not useful_news_url(url):
                continue
            seen_urls.add(url)
            text = fetch_readable_text(client, url)
            if len(text) < 180:
                continue
            saved = save_text(base_dir / "news", f"search_news_{len(result.get('news') or []) + 1}.txt", text)
            evidence = {
                "evidence_id": f"ev_news_search_{len(result.get('news') or []) + 1}",
                "company_id": company["id"],
                "security_id": security_id,
                "category": "news",
                "title": f"搜索补齐新闻：{record.get('title') or url}",
                "summary": f"互联网搜索 fallback 抓取新闻/事件正文，来源 {urlparse(url).netloc}。",
                "raw_value": json.dumps(record, ensure_ascii=False),
                "normalized_value": record.get("title") or "",
                "unit": "text",
                "period": utc_now_iso(),
                "date": utc_now_iso(),
                "source_provider": "Internet Search Fallback",
                "source_url": url,
                "source_document_id": saved.text_path,
                "extracted_quote": excerpt(text),
                "confidence": 0.58,
                "freshness_score": 0.62,
                "created_at": utc_now_iso(),
            }
            result["news"].append(evidence)
            result["evidence"].append(evidence)
            return
    if len(result.get("news") or []) == before:
        result["gaps"].append("搜索 fallback 未补齐新闻/事件正文")


def add_search_document_evidence(
    client: httpx.Client,
    company: dict,
    security_id: str,
    base_dir: Path,
    result: dict,
    record: dict,
    index: int,
) -> bool:
    url = clean_search_url(record.get("url") or "")
    title = normalize_text(record.get("title") or f"Search fallback document {index}")
    try:
        if url.lower().endswith(".pdf"):
            saved = fetch_pdf_document(client, url, base_dir / "filings", f"search_filing_{index}")
            text = saved.text
        else:
            text = fetch_readable_text(client, url)
            saved = save_text(base_dir / "filings", f"search_filing_{index}.txt", text) if text else SavedDocument()
    except Exception as exc:
        record_source_attempt(result, "Search document fetch", "failed", f"{url}: {exc}")
        return False
    if len(text or "") < 200:
        record_source_attempt(result, "Search document fetch", "empty", url)
        return False
    provider = search_document_provider(url)
    confidence = search_document_confidence(url, title)
    filing = {
        "evidence_id": f"ev_filing_search_{index}",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "filing",
        "title": f"搜索补齐公告/财报：{title}",
        "summary": f"官方源缺口后，通过互联网搜索 fallback 抓取可回溯文档，来源 {provider}。",
        "raw_value": json.dumps(record, ensure_ascii=False),
        "normalized_value": title,
        "unit": "text",
        "period": utc_now_iso(),
        "date": utc_now_iso(),
        "source_provider": provider,
        "source_url": url,
        "source_document_id": saved.text_path or saved.binary_path,
        "extracted_quote": excerpt(text),
        "confidence": confidence,
        "freshness_score": 0.58,
        "created_at": utc_now_iso(),
    }
    result["filings"].append(filing)
    result["evidence"].append(filing)
    for statement_index, statement in enumerate(financial_statement_snippets(text, source="search"), start=1):
        statement_item = {
            **filing,
            "evidence_id": f"ev_financial_search_{index}_{statement_index}_{statement['kind']}",
            "category": "financial_statement",
            "title": f"搜索补齐财报三表：{statement['title']}",
            "summary": f"从搜索补齐文档《{title}》抽取 {statement['title']} 附近原文片段。",
            "raw_value": statement["text"],
            "normalized_value": statement["title"],
            "extracted_quote": excerpt(statement["text"]),
            "confidence": max(0.52, confidence - 0.04),
        }
        result["financial_statements"].append(statement_item)
        result["evidence"].append(statement_item)
    return True


def filing_search_queries(company: dict) -> list[str]:
    name = str(company.get("name") or "")
    ticker = str(company.get("ticker") or "")
    market = company.get("market")
    if market == "US":
        symbol = ticker.split(".")[0].upper()
        return [
            f"site:sec.gov {symbol} 10-K annual report",
            f"{name} {symbol} annual report pdf",
            f"site:investor.apple.com {symbol} annual report pdf" if symbol == "AAPL" else f"{name} investor relations annual report pdf",
        ]
    if market == "HK":
        code = re.sub(r"\D", "", ticker).zfill(5)
        return [
            f"site:hkexnews.hk {code} annual report pdf",
            f"{name} {ticker} annual report pdf",
            f"{name} investor relations annual report pdf",
        ]
    code = re.sub(r"\D", "", ticker)
    return [
        f"site:cninfo.com.cn {code} 年报 pdf",
        f"{name} {code} 年报 pdf",
        f"{name} {code} 季报 公告 pdf",
    ]


def search_duckduckgo(client: httpx.Client, query: str, base_dir: Path) -> list[dict]:
    try:
        response = client.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        response.raise_for_status()
        save_text(base_dir / "search", f"duckduckgo_{safe_slug(query)}.html", response.text)
        return parse_duckduckgo_results(response.text)
    except Exception:
        return []


def search_bing(client: httpx.Client, query: str, base_dir: Path) -> list[dict]:
    try:
        response = client.get(
            "https://www.bing.com/search",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        response.raise_for_status()
        save_text(base_dir / "search", f"bing_{safe_slug(query)}.html", response.text)
        return parse_bing_results(response.text)
    except Exception:
        return []


def parse_duckduckgo_results(text: str) -> list[dict]:
    records = []
    for match in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, flags=re.S):
        url = clean_search_url(html.unescape(match.group(1)))
        title = strip_html(match.group(2))
        if url and title:
            records.append({"title": title, "url": url})
        if len(records) >= MAX_SEARCH_RESULTS:
            break
    return dedupe_search_records(records)


def parse_bing_results(text: str) -> list[dict]:
    records = []
    for match in re.finditer(r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, flags=re.S):
        url = clean_search_url(html.unescape(match.group(1)))
        title = strip_html(match.group(2))
        if url and title:
            records.append({"title": title, "url": url})
        if len(records) >= MAX_SEARCH_RESULTS:
            break
    return dedupe_search_records(records)


def dedupe_search_records(records: list[dict]) -> list[dict]:
    seen: set[str] = set()
    output = []
    for record in records:
        url = clean_search_url(record.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        output.append({**record, "url": url})
    return output


def clean_search_url(url: str) -> str:
    if not url:
        return ""
    url = html.unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return url


def useful_document_url(url: str, title: str = "") -> bool:
    text = f"{url} {title}".lower()
    blocked = ["facebook.com", "twitter.com", "x.com", "linkedin.com", "youtube.com", "reddit.com"]
    if any(domain in text for domain in blocked):
        return False
    keywords = ["annual", "10-k", "10q", "10-q", "report", "results", "filing", "announcement", "年报", "季报", "公告", "pdf"]
    return any(keyword in text for keyword in keywords)


def useful_news_url(url: str) -> bool:
    blocked = ["facebook.com", "twitter.com", "x.com", "linkedin.com", "youtube.com"]
    return not any(domain in url.lower() for domain in blocked)


def search_document_provider(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "sec.gov" in host:
        return "SEC EDGAR Search Fallback"
    if "cninfo.com.cn" in host or "static.cninfo.com.cn" in host:
        return "CNInfo Search Fallback"
    if "hkexnews.hk" in host:
        return "HKEXnews Search Fallback"
    return f"Internet Search Fallback ({host or 'unknown'})"


def search_document_confidence(url: str, title: str) -> float:
    text = f"{url} {title}".lower()
    if any(domain in text for domain in ["sec.gov", "cninfo.com.cn", "hkexnews.hk"]):
        return 0.82
    if any(keyword in text for keyword in ["investor", "ir.", "annual report", "年报", "pdf"]):
        return 0.68
    return 0.56


def record_source_attempt(result: dict, source: str, status: str, detail: str = "") -> None:
    result.setdefault("source_attempts", []).append(
        {
            "source": source,
            "status": status,
            "detail": detail[:500],
            "created_at": utc_now_iso(),
        }
    )


def collect_akshare_a_financials(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    code = re.sub(r"\D", "", str(company.get("ticker") or "").split(".")[0])
    if company.get("market") != "A" or len(code) != 6:
        result["gaps"].append("AKShare A 股适配器缺少可识别的 6 位 A 股代码")
        return
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        result["gaps"].append(f"AKShare 未安装或不可用：{exc}")
        return

    financial_records: list[dict] = []
    if hasattr(ak, "stock_financial_abstract"):
        try:
            financial_df = ak.stock_financial_abstract(symbol=code)
            financial_records = dataframe_records(financial_df)
        except Exception as exc:
            result["gaps"].append(f"AKShare 财务摘要抓取失败：{exc}")
    else:
        result["gaps"].append("AKShare 当前版本缺少 stock_financial_abstract 接口")

    if financial_records:
        saved = save_json(base_dir / "financials", f"akshare_financial_abstract_{code}.json", financial_records[:80])
        metrics = extract_akshare_financial_metrics(financial_records)
        if metrics:
            metrics.update(
                {
                    "period": metrics.get("period") or infer_latest_period(financial_records),
                    "report_type": "AKShare financial abstract",
                    "currency": "CNY",
                    "source_provider": "AKShare",
                    "source_url": f"https://akshare.akfamily.xyz/data/stock/stock.html#id7",
                    "fetched_at": utc_now_iso(),
                }
            )
            result["financial_metrics"] = {**(result.get("financial_metrics") or {}), **metrics}
        item = {
            "evidence_id": "ev_financial_akshare_abstract",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "financial_statement",
            "title": "AKShare A股财务摘要",
            "summary": akshare_financial_summary(company, metrics),
            "raw_value": json.dumps(financial_records[:30], ensure_ascii=False),
            "normalized_value": json.dumps(metrics, ensure_ascii=False),
            "unit": "CNY/percent",
            "period": metrics.get("period") if metrics else infer_latest_period(financial_records),
            "date": metrics.get("period") if metrics else utc_now_iso(),
            "source_provider": "AKShare",
            "source_url": "https://akshare.akfamily.xyz/data/stock/stock.html#id7",
            "source_document_id": saved.text_path,
            "extracted_quote": excerpt(json.dumps({"metrics": metrics, "sample": financial_records[:5]}, ensure_ascii=False, indent=2)),
            "confidence": 0.78 if metrics else 0.62,
            "freshness_score": freshness_from_date(metrics.get("period") if metrics else ""),
            "created_at": utc_now_iso(),
        }
        result["financial_statements"].append(item)
        result["evidence"].append(item)
    else:
        result["gaps"].append("AKShare 未返回可用 A 股财务摘要")

    valuation_records: list[dict] = []
    if hasattr(ak, "stock_a_lg_indicator"):
        try:
            valuation_df = ak.stock_a_lg_indicator(symbol=code)
            valuation_records = dataframe_records(valuation_df)
        except Exception as exc:
            result["gaps"].append(f"AKShare 历史估值抓取失败：{exc}")
    else:
        result["gaps"].append("AKShare 当前版本缺少 stock_a_lg_indicator 接口")
    if not valuation_records:
        result["gaps"].append("AKShare 未返回历史估值序列")
        return

    valuation_stats = valuation_history_stats(valuation_records)
    saved = save_json(base_dir / "valuation", f"akshare_valuation_history_{code}.json", valuation_records[-1500:])
    if not valuation_stats:
        result["gaps"].append("AKShare 历史估值序列缺少可解析 PE/PB 字段")
        return
    valuation_item = {
        "evidence_id": "ev_valuation_history_akshare",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "valuation_history",
        "title": "AKShare 历史估值分位",
        "summary": (
            f"{company['name']} 历史估值样本 {valuation_stats.get('sample_count')} 条，"
            f"PE_TTM 分位 {valuation_stats.get('pe_ttm_percentile')}%，PB 分位 {valuation_stats.get('pb_percentile')}%。"
        ),
        "raw_value": json.dumps(valuation_records[-60:], ensure_ascii=False),
        "normalized_value": json.dumps(valuation_stats, ensure_ascii=False),
        "unit": "percentile",
        "period": valuation_stats.get("period"),
        "date": valuation_stats.get("period") or utc_now_iso(),
        "source_provider": "AKShare",
        "source_url": "https://akshare.akfamily.xyz/data/stock/stock.html#id7",
        "source_document_id": saved.text_path,
        "extracted_quote": excerpt(json.dumps(valuation_stats, ensure_ascii=False, indent=2)),
        "confidence": 0.76,
        "freshness_score": freshness_from_date(valuation_stats.get("period")),
        "created_at": utc_now_iso(),
    }
    result["valuation_history"].append(valuation_item)
    result["evidence"].append(valuation_item)


def dataframe_records(value: Any) -> list[dict]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            records = value.to_dict(orient="records")
            return [dict(item) for item in records if isinstance(item, dict)]
        except Exception:
            return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def extract_akshare_financial_metrics(records: list[dict]) -> dict:
    metrics = {
        "gross_margin": metric_from_ak_records(records, ["销售毛利率", "毛利率", "gross"]),
        "net_margin": metric_from_ak_records(records, ["销售净利率", "净利率", "net margin"]),
        "roe": metric_from_ak_records(records, ["净资产收益率", "roe"]),
        "revenue": metric_from_ak_records(records, ["营业总收入", "营业收入", "revenue"]),
        "net_income": metric_from_ak_records(records, ["归母净利润", "净利润", "net income"]),
        "operating_cash_flow": metric_from_ak_records(records, ["经营现金流量净额", "经营活动现金流", "operating cash"]),
        "total_assets": metric_from_ak_records(records, ["资产总计", "总资产", "total assets"]),
        "total_liabilities": metric_from_ak_records(records, ["负债合计", "总负债", "total liabilities"]),
        "debt_to_asset_ratio": metric_from_ak_records(records, ["资产负债率", "debt"]),
        "period": infer_latest_period(records),
    }
    return {key: value for key, value in metrics.items() if value not in (None, "")}


def metric_from_ak_records(records: list[dict], aliases: list[str]) -> float | None:
    alias_text = [alias.lower() for alias in aliases]
    row_candidates = []
    for record in records:
        joined = " ".join(str(key) + " " + str(value) for key, value in record.items()).lower()
        if any(alias in joined for alias in alias_text):
            row_candidates.append(record)
    for record in row_candidates or records[:5]:
        keyed = value_from_alias_key(record, alias_text)
        if keyed is not None:
            return keyed
        latest = latest_numeric_from_record(record)
        if latest is not None and record in row_candidates:
            return latest
    return None


def value_from_alias_key(record: dict, aliases: list[str]) -> float | None:
    for key, value in record.items():
        key_text = str(key).lower()
        if any(alias in key_text for alias in aliases):
            number = parse_number(value)
            if number is not None:
                return number
    return None


def latest_numeric_from_record(record: dict) -> float | None:
    dated_values: list[tuple[str, float]] = []
    undated_values: list[float] = []
    for key, value in record.items():
        number = parse_number(value)
        if number is None:
            continue
        key_text = str(key)
        if re.search(r"\d{4}[-/年]?\d{0,2}", key_text):
            dated_values.append((key_text, number))
        else:
            undated_values.append(number)
    if dated_values:
        dated_values.sort(key=lambda item: item[0], reverse=True)
        return dated_values[0][1]
    return undated_values[0] if undated_values else None


def valuation_history_stats(records: list[dict]) -> dict:
    sorted_records = sorted(records, key=lambda item: str(item.get("date") or item.get("日期") or item.get("trade_date") or item.get("交易日期") or ""))
    pe_values = series_from_records(sorted_records, ["pe_ttm", "市盈率ttm", "pe"])
    pb_values = series_from_records(sorted_records, ["pb", "市净率"])
    stats: dict[str, Any] = {"sample_count": max(len(pe_values), len(pb_values)), "period": infer_latest_period(sorted_records)}
    if pe_values:
        current = pe_values[-1]
        stats["pe_ttm_current"] = round(current, 3)
        stats["pe_ttm_percentile"] = percentile_rank(pe_values, current)
        stats["pe_percentile"] = stats["pe_ttm_percentile"]
    if pb_values:
        current = pb_values[-1]
        stats["pb_current"] = round(current, 3)
        stats["pb_percentile"] = percentile_rank(pb_values, current)
    return stats if any(key.endswith("percentile") for key in stats) else {}


def series_from_records(records: list[dict], aliases: list[str]) -> list[float]:
    output = []
    aliases = [alias.lower() for alias in aliases]
    for record in records:
        value = value_from_alias_key(record, aliases)
        if value is not None and value > 0:
            output.append(value)
    return output


def percentile_rank(values: list[float], current: float) -> float:
    if not values:
        return 0
    below_or_equal = sum(1 for value in values if value <= current)
    return round(below_or_equal / len(values) * 100, 1)


def parse_number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def infer_latest_period(records: list[dict]) -> str:
    candidates: list[str] = []
    for record in records:
        for key, value in record.items():
            key_text = str(key)
            value_text = str(value)
            if re.search(r"(date|日期|报告期|截止)", key_text, flags=re.I) and re.match(r"\d{4}", value_text):
                candidates.append(parse_datetime_prefix(value_text))
            if re.match(r"\d{4}[-/年]?\d{0,2}", key_text):
                candidates.append(parse_datetime_prefix(key_text.replace("年", "-").replace("月", "")))
    candidates = [item for item in candidates if item]
    return sorted(candidates)[-1] if candidates else ""


def akshare_financial_summary(company: dict, metrics: dict) -> str:
    if not metrics:
        return f"{company['name']} AKShare 财务摘要已抓取，但未解析出核心指标。"
    return (
        f"{company['name']} {metrics.get('period') or '最新'} AKShare 财务摘要："
        f"毛利率 {metrics.get('gross_margin', 'N/A')}%，净利率 {metrics.get('net_margin', 'N/A')}%，"
        f"ROE {metrics.get('roe', 'N/A')}%，经营现金流 {metrics.get('operating_cash_flow', 'N/A')}。"
    )


def collect_cninfo_announcements(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    ticker = str(company.get("ticker") or "")
    code = re.sub(r"\D", "", ticker.split(".")[0])
    if len(code) != 6:
        result["gaps"].append("巨潮公告仅支持可识别的 6 位 A 股代码")
        return
    org_id = lookup_cninfo_org_id(client, code)
    if not org_id:
        result["gaps"].append(f"巨潮未匹配到 {code} 的 orgId")
        return
    end = date.today()
    start = end - timedelta(days=540)
    column = "sse" if code.startswith("6") else "szse"
    data = request_json(
        client,
        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        method="POST",
        data={
            "stock": f"{code},{org_id}",
            "searchkey": "",
            "plate": "",
            "category": "category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh",
            "trade": "",
            "column": column,
            "pageNum": "1",
            "pageSize": str(MAX_FILING_ITEMS),
            "tabName": "fulltext",
            "sortName": "",
            "sortType": "",
            "limit": "",
            "seDate": f"{start.isoformat()}~{end.isoformat()}",
            "isHLtitle": "true",
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    announcements = data.get("announcements") or [] if isinstance(data, dict) else []
    if not announcements:
        result["gaps"].append("巨潮未返回最近财报/公告正文")
        return
    save_json(base_dir / "filings", f"cninfo_announcements_{code}.json", announcements)
    for index, announcement in enumerate(announcements[:MAX_FILING_ITEMS], start=1):
        pdf_url = urljoin("https://static.cninfo.com.cn/", announcement.get("adjunctUrl") or "")
        title = strip_html(announcement.get("announcementTitle") or f"CNInfo announcement {index}")
        published = millis_to_date(announcement.get("announcementTime"))
        saved = fetch_pdf_document(client, pdf_url, base_dir / "filings", f"cninfo_{announcement.get('announcementId') or index}")
        item = {
            "evidence_id": f"ev_filing_cninfo_{index}",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "filing",
            "title": f"巨潮公告：{title}",
            "summary": f"{company['name']} 公告/财报《{title}》，已保存 PDF{'并抽取文本' if saved.text else ''}。",
            "raw_value": json.dumps(announcement, ensure_ascii=False),
            "normalized_value": title,
            "unit": "text",
            "period": published,
            "date": published or utc_now_iso(),
            "source_provider": "CNInfo 巨潮资讯",
            "source_url": pdf_url,
            "source_document_id": saved.text_path or saved.binary_path,
            "extracted_quote": excerpt(saved.text),
            "confidence": 0.91 if saved.text else 0.78,
            "freshness_score": freshness_from_date(published),
            "created_at": utc_now_iso(),
        }
        result["filings"].append(item)
        result["evidence"].append(item)
        for statement in financial_statement_snippets(saved.text, source="cninfo"):
            statement_item = {
                **item,
                "evidence_id": f"ev_financial_{statement['kind']}_cninfo_{index}",
                "category": "financial_statement",
                "title": f"巨潮财报三表：{statement['title']}",
                "summary": f"从《{title}》抽取 {statement['title']} 附近原文片段。",
                "raw_value": statement["text"],
                "normalized_value": statement["title"],
                "source_document_id": saved.text_path or saved.binary_path,
                "extracted_quote": excerpt(statement["text"]),
                "confidence": 0.82,
            }
            result["financial_statements"].append(statement_item)
            result["evidence"].append(statement_item)


def collect_hkex_announcements(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    code = hk_code(company)
    if not code:
        result["gaps"].append("HKEX 公告缺少可识别港股代码")
        return
    stock_id = lookup_hkex_stock_id(client, code)
    if not stock_id:
        result["gaps"].append(f"HKEXnews 未匹配到 {code} 的 stockId")
        return

    docs: list[dict] = []
    for title in ["annual report", "interim report", "annual results", "business update", ""]:
        docs.extend(query_hkex_title_search(client, stock_id, title=title, row_range=8))
    docs = dedupe_hkex_docs(docs)
    if not docs:
        result["gaps"].append("HKEXnews 未返回最近公告/财报")
        return

    docs.sort(key=lambda doc: (hkex_doc_priority(doc), parse_hkex_datetime(doc.get("DATE_TIME"))), reverse=True)
    save_json(base_dir / "filings", f"hkex_announcements_{code}.json", docs)
    for index, doc in enumerate(docs[: max(MAX_FILING_ITEMS, 3)], start=1):
        file_link = html.unescape(str(doc.get("FILE_LINK") or ""))
        pdf_url = urljoin("https://www1.hkexnews.hk/", file_link)
        title = normalize_text(strip_html(html.unescape(doc.get("TITLE") or f"HKEX announcement {index}")))
        published = hkex_datetime_to_iso(doc.get("DATE_TIME"))
        saved = fetch_pdf_document(client, pdf_url, base_dir / "filings", f"hkex_{doc.get('NEWS_ID') or index}")
        item = {
            "evidence_id": f"ev_filing_hkex_{index}",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "filing",
            "title": f"HKEX 公告：{title}",
            "summary": f"{company['name']} 港交所公告《{title}》，类别 {strip_html(doc.get('LONG_TEXT') or doc.get('SHORT_TEXT') or '')}，已保存 PDF{'并抽取文本' if saved.text else ''}。",
            "raw_value": json.dumps(doc, ensure_ascii=False),
            "normalized_value": title,
            "unit": "text",
            "period": published,
            "date": published or utc_now_iso(),
            "source_provider": "HKEXnews",
            "source_url": pdf_url,
            "source_document_id": saved.text_path or saved.binary_path,
            "extracted_quote": excerpt(saved.text),
            "confidence": 0.94 if saved.text else 0.8,
            "freshness_score": freshness_from_date(published),
            "created_at": utc_now_iso(),
        }
        result["filings"].append(item)
        result["evidence"].append(item)
        for statement in financial_statement_snippets(saved.text, source="hkex"):
            statement_item = {
                **item,
                "evidence_id": f"ev_financial_{statement['kind']}_hkex_{index}",
                "category": "financial_statement",
                "title": f"HKEX 财报三表：{statement['title']}",
                "summary": f"从港交所公告《{title}》抽取 {statement['title']} 附近原文片段。",
                "raw_value": statement["text"],
                "normalized_value": statement["title"],
                "source_document_id": saved.text_path or saved.binary_path,
                "extracted_quote": excerpt(statement["text"]),
                "confidence": 0.86,
            }
            result["financial_statements"].append(statement_item)
            result["evidence"].append(statement_item)


def collect_eastmoney_hk_financials(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    code = hk_code(company)
    if not code:
        result["gaps"].append("东方财富港股 F10 缺少可识别港股代码")
        return
    secucode = f"{code}.HK"
    source_url = f"https://emweb.securities.eastmoney.com/PC_HKF10/pages/home/index.html?code={code}&type=web&color=w#/newFinancialAnalysis"
    indicator_rows = eastmoney_datacenter_rows(
        client,
        "RPT_HKF10_FN_MAININDICATOR",
        secucode,
        page_size=4,
        sort_columns="STD_REPORT_DATE",
    )
    if indicator_rows:
        save_json(base_dir / "financials", f"eastmoney_hk_indicator_{code}.json", indicator_rows)
        latest = indicator_rows[0]
        metrics = {
            "gross_margin": latest.get("GROSS_PROFIT_RATIO"),
            "net_margin": latest.get("NET_PROFIT_RATIO"),
            "roe": latest.get("ROE_AVG") or latest.get("ROE_YEARLY"),
            "pe_ratio": latest.get("PE_TTM"),
            "pb_ratio": latest.get("PB_TTM"),
            "revenue": latest.get("OPERATE_INCOME"),
            "net_income": latest.get("HOLDER_PROFIT"),
            "operating_cash_flow": latest.get("NETCASH_OPERATE"),
            "total_assets": latest.get("TOTAL_ASSETS"),
            "total_liabilities": latest.get("TOTAL_LIABILITIES"),
            "debt_to_asset_ratio": latest.get("DEBT_ASSET_RATIO"),
            "period": parse_datetime_prefix(latest.get("STD_REPORT_DATE") or latest.get("REPORT_DATE")),
            "report_type": latest.get("REPORT_TYPE"),
            "currency": "CNY",
            "source_provider": "Eastmoney HK F10",
            "source_url": source_url,
            "fetched_at": utc_now_iso(),
        }
        result["financial_metrics"] = metrics
        result["financial_series"] = eastmoney_hk_indicator_series(indicator_rows)
        item = {
            "evidence_id": "ev_financial_indicator_eastmoney_hk",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "financial_statement",
            "title": "东方财富港股 F10：主要财务指标",
            "summary": eastmoney_hk_indicator_summary(company, metrics),
            "raw_value": json.dumps(latest, ensure_ascii=False),
            "normalized_value": json.dumps(metrics, ensure_ascii=False),
            "unit": "CNY/percent",
            "period": metrics.get("period"),
            "date": metrics.get("period") or utc_now_iso(),
            "source_provider": "Eastmoney HK F10",
            "source_url": source_url,
            "source_document_id": save_json(base_dir / "financials", f"eastmoney_hk_main_indicator_{code}.json", latest).text_path,
            "extracted_quote": excerpt(json.dumps(metrics, ensure_ascii=False, indent=2)),
            "confidence": 0.86,
            "freshness_score": freshness_from_date(metrics.get("period")),
            "created_at": utc_now_iso(),
        }
        result["financial_statements"].append(item)
        result["evidence"].append(item)
    else:
        result["gaps"].append("东方财富港股 F10 未返回主要财务指标")

    statement_reports = [
        ("income_statement", "利润表", "RPT_HKF10_FN_INCOME_PC"),
        ("balance_sheet", "资产负债表", "RPT_HKF10_FN_BALANCE_PC"),
        ("cash_flow", "现金流量表", "RPT_HKF10_FN_CASHFLOW_PC"),
    ]
    for kind, label, report_name in statement_reports:
        rows = eastmoney_datacenter_rows(client, report_name, secucode, page_size=120, sort_columns="REPORT_DATE")
        if not rows:
            result["gaps"].append(f"东方财富港股 F10 未返回{label}")
            continue
        latest_rows = latest_report_rows(rows)
        statement_metrics = extract_eastmoney_hk_statement_metrics(kind, latest_rows)
        if statement_metrics:
            result["financial_metrics"] = merge_metric_defaults(result.get("financial_metrics") or {}, statement_metrics)
        saved = save_json(base_dir / "financials", f"eastmoney_hk_{kind}_{code}.json", latest_rows)
        period = parse_datetime_prefix((latest_rows[0] or {}).get("REPORT_DATE") if latest_rows else "")
        item = {
            "evidence_id": f"ev_financial_{kind}_eastmoney_hk",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "financial_statement",
            "title": f"东方财富港股 F10：{label}",
            "summary": f"{company['name']} {period or '最新'} {label}，抓取结构化科目 {len(latest_rows)} 条：{summarize_financial_rows(latest_rows)}",
            "raw_value": json.dumps(latest_rows, ensure_ascii=False),
            "normalized_value": json.dumps(financial_rows_to_dict(latest_rows), ensure_ascii=False),
            "unit": "CNY",
            "period": period,
            "date": period or utc_now_iso(),
            "source_provider": "Eastmoney HK F10",
            "source_url": source_url,
            "source_document_id": saved.text_path,
            "extracted_quote": excerpt(json.dumps(financial_rows_to_dict(latest_rows), ensure_ascii=False, indent=2)),
            "confidence": 0.84,
            "freshness_score": freshness_from_date(period),
            "created_at": utc_now_iso(),
        }
        result["financial_statements"].append(item)
        result["evidence"].append(item)


def collect_eastmoney_research(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    code = re.sub(r"\D", "", str(company.get("ticker") or "").split(".")[0])
    if len(code) != 6:
        result["gaps"].append("东方财富公开研报接口当前仅对 6 位 A 股代码启用；港股/美股研报需接入券商或付费数据源")
        return
    end = date.today()
    start = end - timedelta(days=540)
    data = request_json(
        client,
        "https://reportapi.eastmoney.com/report/list",
        params={
            "pageNo": 1,
            "pageSize": MAX_RESEARCH_ITEMS,
            "qType": 0,
            "code": code,
            "beginTime": start.isoformat(),
            "endTime": end.isoformat(),
        },
        headers={"User-Agent": "Mozilla/5.0"},
    )
    reports = data.get("data") or [] if isinstance(data, dict) else []
    if not reports:
        result["gaps"].append("东方财富未返回公开研报索引")
        return
    save_json(base_dir / "research", f"eastmoney_reports_{code}.json", reports)
    for index, report in enumerate(reports[:MAX_RESEARCH_ITEMS], start=1):
        info_code = report.get("infoCode")
        encoded = str(report.get("encodeUrl") or "").rstrip("=")
        pdf_url = f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf?{encoded}.pdf" if info_code and encoded else ""
        saved = fetch_pdf_document(client, pdf_url, base_dir / "research", f"eastmoney_{info_code or index}") if pdf_url else SavedDocument()
        authors = "、".join(report.get("author") or [])
        title = report.get("title") or f"Eastmoney research {index}"
        published = parse_datetime_prefix(report.get("publishDate"))
        item = {
            "evidence_id": f"ev_research_eastmoney_{index}",
            "company_id": company["id"],
            "security_id": security_id,
            "category": "research",
            "title": f"公开研报：{title}",
            "summary": f"{report.get('orgSName') or report.get('orgName') or '券商'} {authors} 发布《{title}》，评级 {report.get('emRatingName') or 'N/A'}。",
            "raw_value": json.dumps(report, ensure_ascii=False),
            "normalized_value": json.dumps(
                {
                    "rating": report.get("emRatingName"),
                    "eps_this_year": report.get("predictThisYearEps"),
                    "pe_this_year": report.get("predictThisYearPe"),
                },
                ensure_ascii=False,
            ),
            "unit": "text",
            "period": published,
            "date": published or utc_now_iso(),
            "source_provider": "Eastmoney Research",
            "source_url": pdf_url or "https://reportapi.eastmoney.com/report/list",
            "source_document_id": saved.text_path or saved.binary_path,
            "extracted_quote": excerpt(saved.text),
            "confidence": 0.78 if saved.text else 0.62,
            "freshness_score": freshness_from_date(published),
            "created_at": utc_now_iso(),
        }
        result["research_reports"].append(item)
        result["evidence"].append(item)


def collect_yahoo_technical_history(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> None:
    symbol = yahoo_symbol(company)
    if not symbol:
        result["gaps"].append("历史 K 线缺少可用 Yahoo 证券代码")
        return
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}"
    data = request_json(
        client,
        url,
        params={"range": "6mo", "interval": "1d", "events": "history"},
        headers={"User-Agent": "Mozilla/5.0"},
    )
    chart = ((data.get("chart") or {}).get("result") or [])
    if not chart:
        if collect_eastmoney_technical_history(client, company, security_id, base_dir, result):
            return
        result["gaps"].append("Yahoo 历史 K 线未返回可用数据")
        return
    series = normalize_yahoo_chart(chart[0])
    if len(series) < 30:
        result["gaps"].append("历史 K 线长度不足，无法计算稳定技术指标")
        return
    analysis = analyze_price_history(series)
    saved = save_json(
        base_dir / "technical",
        f"yahoo_history_{safe_slug(symbol)}.json",
        {"symbol": symbol, "series": series, "analysis": analysis},
    )
    item = {
        "evidence_id": "ev_technical_history",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "technical",
        "title": "六个月日线技术指标",
        "summary": technical_summary_text(analysis),
        "raw_value": json.dumps(series[-30:], ensure_ascii=False),
        "normalized_value": json.dumps(analysis, ensure_ascii=False),
        "unit": "OHLCV",
        "period": f"{series[0]['date']}~{series[-1]['date']}",
        "date": series[-1]["date"],
        "source_provider": "Yahoo Finance Chart",
        "source_url": url,
        "source_document_id": saved.text_path,
        "extracted_quote": excerpt(json.dumps(analysis, ensure_ascii=False, indent=2)),
        "confidence": 0.78,
        "freshness_score": freshness_from_date(series[-1]["date"]),
        "created_at": utc_now_iso(),
    }
    result["technical_history"].append(item)
    result["evidence"].append(item)
    if not result.get("valuation_history"):
        valuation_item = build_price_proxy_valuation_history(company, security_id, base_dir, symbol, series, analysis, url, "Yahoo Finance Chart")
        if valuation_item:
            result["valuation_history"].append(valuation_item)
            result["evidence"].append(valuation_item)


def collect_eastmoney_technical_history(client: httpx.Client, company: dict, security_id: str, base_dir: Path, result: dict) -> bool:
    secid = eastmoney_kline_secid(company)
    if not secid:
        return False
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    data = request_json(
        client,
        url,
        params={
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": "20200101",
            "end": "20500101",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    rows = normalize_eastmoney_klines(((data.get("data") or {}).get("klines") or []) if isinstance(data, dict) else [])
    if len(rows) < 30:
        return False
    series = rows[-180:]
    analysis = analyze_price_history(series)
    saved = save_json(
        base_dir / "technical",
        f"eastmoney_history_{safe_slug(secid)}.json",
        {"secid": secid, "series": series, "analysis": analysis},
    )
    source_url = f"{url}?secid={quote_plus(secid)}"
    item = {
        "evidence_id": "ev_technical_history",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "technical",
        "title": "东方财富日线技术指标",
        "summary": technical_summary_text(analysis),
        "raw_value": json.dumps(series[-30:], ensure_ascii=False),
        "normalized_value": json.dumps(analysis, ensure_ascii=False),
        "unit": "OHLCV",
        "period": f"{series[0]['date']}~{series[-1]['date']}",
        "date": series[-1]["date"],
        "source_provider": "Eastmoney Kline",
        "source_url": source_url,
        "source_document_id": saved.text_path,
        "extracted_quote": excerpt(json.dumps(analysis, ensure_ascii=False, indent=2)),
        "confidence": 0.76,
        "freshness_score": freshness_from_date(series[-1]["date"]),
        "created_at": utc_now_iso(),
    }
    result["technical_history"].append(item)
    result["evidence"].append(item)
    if not result.get("valuation_history"):
        valuation_item = build_price_proxy_valuation_history(company, security_id, base_dir, secid, series, analysis, source_url, "Eastmoney Kline")
        if valuation_item:
            result["valuation_history"].append(valuation_item)
            result["evidence"].append(valuation_item)
    return True


def normalize_eastmoney_klines(klines: list[str]) -> list[dict]:
    rows = []
    for line in klines:
        parts = str(line or "").split(",")
        if len(parts) < 6:
            continue
        close = parse_number(parts[2])
        if close is None:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": parse_number(parts[1]),
                "close": round(close, 4),
                "high": parse_number(parts[3]),
                "low": parse_number(parts[4]),
                "volume": parse_number(parts[5]) or 0,
            }
        )
    return rows


def eastmoney_kline_secid(company: dict) -> str | None:
    market = str(company.get("market") or "").upper()
    ticker = str(company.get("ticker") or "").upper().strip()
    exchange = str(company.get("exchange") or "").upper()
    if market == "US" and re.fullmatch(r"[A-Z0-9.]{1,12}", ticker.replace("-", ".")):
        return f"106.{ticker.replace('-', '.')}"
    if market == "HK":
        code = re.sub(r"\D", "", ticker.split(".")[0])
        return f"116.{code.zfill(5)}" if code else None
    if market == "A":
        code = ticker.split(".")[0]
        if not re.fullmatch(r"\d{6}", code):
            return None
        prefix = "1" if exchange.startswith("SSE") or code.startswith("6") else "0"
        return f"{prefix}.{code}"
    return None


def mark_missing_sources(result: dict) -> None:
    required = [
        ("news", "新闻全文"),
        ("social", "社媒内容"),
        ("filings", "公告/监管文件正文"),
        ("financial_statements", "财报三表"),
        ("research_reports", "研报全文/索引"),
        ("valuation_history", "历史估值分位"),
    ]
    for key, label in required:
        if not result.get(key):
            result["gaps"].append(f"未采集到{label}")
    result["gaps"] = sorted(set(result["gaps"]))


def lookup_sec_cik(client: httpx.Client, ticker: str) -> str | None:
    data = request_json(client, "https://www.sec.gov/files/company_tickers.json")
    if not isinstance(data, dict):
        return None
    for item in data.values():
        if str(item.get("ticker", "")).upper() == ticker.upper():
            return str(item.get("cik_str", "")).zfill(10)
    return None


def build_sec_financial_statement_items(facts: dict) -> list[tuple[str, dict]]:
    taxonomy = sec_financial_taxonomy(facts)
    statements = [
        (
            "income_statement",
            "利润表",
            ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenue", "RevenueFromContractsWithCustomers"],
            ["GrossProfit", "OperatingIncomeLoss", "ProfitLossFromOperatingActivities", "NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "EarningsPerShareDiluted", "DilutedEarningsLossPerShare"],
        ),
        (
            "balance_sheet",
            "资产负债表",
            ["Assets", "Liabilities", "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity", "EquityAttributableToOwnersOfParent", "CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents", "Cash"],
            ["AssetsCurrent", "CurrentAssets", "LiabilitiesCurrent", "CurrentLiabilities", "InventoryNet", "Inventories"],
        ),
        (
            "cash_flow",
            "现金流量表",
            ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivities", "CashFlowsFromUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivities"],
            ["PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "FreeCashFlow"],
        ),
    ]
    output = []
    for key, label, primary, secondary in statements:
        facts_payload: dict[str, Any] = {}
        latest_payload: dict[str, Any] = {}
        latest_filed = ""
        latest_period = ""
        latest_unit = ""
        for fact_name in primary + secondary:
            latest = latest_sec_named_fact(taxonomy, fact_name)
            if latest:
                facts_payload[fact_name] = latest
                latest_payload[fact_name] = latest.get("val")
                latest_filed = max(latest_filed, latest.get("filed") or "")
                latest_period = latest.get("end") or latest_period
                latest_unit = latest.get("unit") or latest_unit
        output.append(
            (
                key,
                {
                    "label": label,
                    "facts": facts_payload,
                    "latest": latest_payload,
                    "filed": latest_filed,
                    "period": latest_period,
                    "unit": latest_unit,
                },
            )
        )
    return output


def extract_sec_financial_metrics(facts: dict, company: dict) -> tuple[dict, list[dict]]:
    taxonomy = sec_financial_taxonomy(facts)
    revenue_fact = latest_sec_fact(taxonomy, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenue", "RevenueFromContractsWithCustomers"])
    gross_fact = latest_sec_fact(taxonomy, ["GrossProfit"])
    operating_income_fact = latest_sec_fact(taxonomy, ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"])
    net_income_fact = latest_sec_fact(taxonomy, ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLoss"])
    eps_fact = latest_sec_fact(taxonomy, ["EarningsPerShareDiluted", "EarningsPerShareBasic", "DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"])
    shares_fact = latest_sec_fact(taxonomy, ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingDiluted", "AdjustedWeightedAverageShares", "WeightedAverageShares"])
    operating_cash_fact = latest_sec_fact(taxonomy, ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"])
    capex_fact = latest_sec_fact(taxonomy, ["PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"])
    assets_fact = latest_sec_fact(taxonomy, ["Assets"], instant=True)
    liabilities_fact = latest_sec_fact(taxonomy, ["Liabilities"], instant=True)
    equity_fact = latest_sec_fact(taxonomy, ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "EquityAttributableToOwnersOfParent", "Equity"], instant=True)
    cash_fact = latest_sec_fact(taxonomy, ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents", "Cash"], instant=True)

    revenue = fact_value(revenue_fact)
    gross_profit = fact_value(gross_fact)
    operating_income = fact_value(operating_income_fact)
    net_income = fact_value(net_income_fact)
    operating_cash_flow = fact_value(operating_cash_fact)
    capex = fact_value(capex_fact)
    total_assets = fact_value(assets_fact)
    total_liabilities = fact_value(liabilities_fact)
    equity = fact_value(equity_fact)
    cash = fact_value(cash_fact)
    diluted_eps = fact_value(eps_fact)
    diluted_shares = fact_value(shares_fact)
    period_days = fact_duration_days(net_income_fact or revenue_fact)
    annualization = 365 / period_days if period_days and period_days < 330 else 1
    price = parse_number((company.get("snapshot") or {}).get("price"))

    metrics: dict[str, Any] = {
        "source_provider": "SEC Companyfacts",
        "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(facts.get('cik') or '').zfill(10)}.json" if facts.get("cik") else "",
        "period": (net_income_fact or revenue_fact or assets_fact or {}).get("end"),
        "filed": latest_filed_date([revenue_fact, net_income_fact, assets_fact, operating_cash_fact]),
        "report_type": (net_income_fact or revenue_fact or {}).get("form"),
        "currency": (revenue_fact or assets_fact or {}).get("unit") or "USD",
        "revenue": revenue,
        "net_income": net_income,
        "operating_cash_flow": operating_cash_flow,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "shareholders_equity": equity,
        "cash_and_equivalents": cash,
        "eps_diluted": diluted_eps,
        "diluted_shares": diluted_shares,
    }
    if operating_cash_flow is not None and capex is not None:
        metrics["free_cash_flow"] = round(operating_cash_flow - abs(capex), 2)
        metrics["capex"] = capex
    if revenue:
        if gross_profit is not None:
            metrics["gross_margin"] = round(gross_profit / revenue * 100, 2)
            metrics["gross_margin_basis"] = "GrossProfit / Revenues"
        elif operating_income is not None:
            metrics["gross_margin"] = round(operating_income / revenue * 100, 2)
            metrics["gross_margin_basis"] = "SEC 未披露 GrossProfit，使用 OperatingIncomeLoss / Revenues 作为保守利润率代理"
            metrics["gross_margin_proxy"] = True
        if net_income is not None:
            metrics["net_margin"] = round(net_income / revenue * 100, 2)
    if equity and net_income is not None:
        metrics["roe"] = round(net_income * annualization / equity * 100, 2)
        metrics["roe_basis"] = "latest period net income annualized / shareholders equity" if annualization != 1 else "annual net income / shareholders equity"
    if total_assets and total_liabilities is not None:
        metrics["debt_to_asset_ratio"] = round(total_liabilities / total_assets * 100, 2)
    quote_currency = str(((company.get("snapshot") or {}).get("raw_data") or {}).get("quote_currency") or "USD").upper()
    statement_currency = str(metrics.get("currency") or "USD").upper()
    same_currency_as_quote = statement_currency == quote_currency
    if price and diluted_eps and same_currency_as_quote:
        eps_annualized = diluted_eps * annualization
        if eps_annualized > 0:
            metrics["pe_ratio"] = round(price / eps_annualized, 2)
            metrics["pe_basis"] = "latest diluted EPS annualized" if annualization != 1 else "annual diluted EPS"
    elif price and diluted_eps and not same_currency_as_quote:
        metrics["price_multiple_note"] = f"报价币种 {quote_currency} 与财报币种 {statement_currency} 不一致，SEC 衍生 PE/PB/市值不覆盖行情源估值。"
    if price and diluted_shares and same_currency_as_quote:
        metrics["market_cap"] = round(price * diluted_shares / 100000000, 2)
        metrics["market_cap_unit"] = "亿美元"
    if price and equity and diluted_shares and same_currency_as_quote:
        book_value_per_share = equity / diluted_shares
        if book_value_per_share > 0:
            metrics["pb_ratio"] = round(price / book_value_per_share, 2)

    metrics = {key: value for key, value in metrics.items() if value not in (None, "", {})}
    return metrics, build_sec_financial_series(taxonomy)


def sec_financial_taxonomy(facts: dict) -> dict:
    facts_map = facts.get("facts") or {}
    output: dict[str, Any] = {}
    for namespace in ["us-gaap", "ifrs-full"]:
        for key, value in (facts_map.get(namespace) or {}).items():
            output.setdefault(key, value)
    return output


def latest_sec_named_fact(taxonomy: dict, name: str) -> dict | None:
    latest = latest_us_gaap_fact(taxonomy.get(name) or {})
    if latest:
        latest["fact_name"] = name
    return latest


def latest_sec_fact(us_gaap: dict, names: list[str], *, instant: bool = False, annual: bool = False) -> dict | None:
    candidates: list[dict] = []
    for name in names:
        for item in sec_fact_candidates(us_gaap.get(name) or {}):
            item = {**item, "fact_name": name}
            if instant and item.get("start"):
                continue
            if annual and not is_annual_sec_fact(item):
                continue
            candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=sec_fact_sort_key, reverse=True)
    return candidates[0]


def sec_fact_candidates(fact: dict) -> list[dict]:
    output: list[dict] = []
    for unit, values in (fact.get("units") or {}).items():
        for value in values:
            if value.get("val") in (None, ""):
                continue
            item = dict(value)
            item["unit"] = unit
            output.append(item)
    return output


def sec_fact_sort_key(item: dict) -> tuple[str, str, int, str]:
    duration = fact_duration_days(item) or 0
    return (str(item.get("filed") or ""), str(item.get("end") or ""), duration, str(item.get("form") or ""))


def is_annual_sec_fact(item: dict) -> bool:
    if str(item.get("fp") or "").upper() == "FY" or str(item.get("form") or "").upper() in {"10-K", "20-F"}:
        return True
    duration = fact_duration_days(item)
    return bool(duration and duration >= 330)


def fact_duration_days(item: dict | None) -> int | None:
    if not item or not item.get("start") or not item.get("end"):
        return None
    try:
        start = datetime.fromisoformat(str(item["start"])[:10])
        end = datetime.fromisoformat(str(item["end"])[:10])
    except Exception:
        return None
    return max(1, (end - start).days + 1)


def fact_value(item: dict | None) -> float | None:
    return parse_number((item or {}).get("val"))


def latest_filed_date(items: list[dict | None]) -> str:
    dates = [str(item.get("filed") or "") for item in items if item]
    return max(dates) if dates else ""


def build_sec_financial_series(us_gaap: dict) -> list[dict]:
    annual_rows: dict[str, dict[str, Any]] = {}
    fact_map = {
        "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenue", "RevenueFromContractsWithCustomers"],
        "net_income": ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
        "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"],
        "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingDiluted", "AdjustedWeightedAverageShares", "WeightedAverageShares"],
    }
    for target, names in fact_map.items():
        for name in names:
            for fact in sec_fact_candidates(us_gaap.get(name) or {}):
                if not is_annual_sec_fact(fact):
                    continue
                end = str(fact.get("end") or "")
                if not end:
                    continue
                row = annual_rows.setdefault(
                    end,
                    {
                        "period": end,
                        "report_type": fact.get("form") or fact.get("fp") or "annual",
                        "filed": fact.get("filed"),
                    },
                )
                if row.get(target) in (None, ""):
                    row[target] = fact_value(fact)
    return sorted(
        [{key: value for key, value in row.items() if value not in (None, "")} for row in annual_rows.values()],
        key=lambda item: str(item.get("period") or ""),
        reverse=True,
    )


def merge_financial_series(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_period = {str(item.get("period") or index): dict(item) for index, item in enumerate(existing or []) if isinstance(item, dict)}
    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("period") or len(by_period))
        by_period[key] = {**by_period.get(key, {}), **item}
    return sorted(by_period.values(), key=lambda item: str(item.get("period") or ""), reverse=True)


def sec_metrics_summary(company: dict, metrics: dict, series: list[dict]) -> str:
    parts = [
        f"收入={metrics.get('revenue')}",
        f"净利润={metrics.get('net_income')}",
        f"净利率={metrics.get('net_margin')}%",
        f"ROE={metrics.get('roe')}%",
        f"经营现金流={metrics.get('operating_cash_flow')}",
    ]
    if metrics.get("gross_margin_proxy"):
        parts.append("毛利率字段为营业利润率代理")
    if series:
        parts.append(f"年度序列={len(series)}年")
    return f"{company.get('name')} SEC Companyfacts 衍生财务指标：" + "；".join(parts)


def build_sec_company_profile(company: dict, submissions: dict, filing_texts: list[str]) -> dict:
    sic = normalize_text(submissions.get("sicDescription") or "")
    text = normalize_text(" ".join(filing_texts[:2])).lower()
    name = submissions.get("name") or company.get("name")
    profile: dict[str, Any] = {
        "name": name,
        "sic": submissions.get("sic"),
        "sic_description": sic,
        "fiscal_year_end": submissions.get("fiscalYearEnd"),
        "filed": latest_submission_filed_date(submissions),
        "confidence": 0.74,
    }
    tags: list[str] = []
    industry = sic or company.get("industry")
    sector = company.get("sector") or "美股"
    description = f"{name} 的 SEC 行业分类为 {sic}。" if sic else str(company.get("description") or "")
    if any(keyword in text for keyword in ["usdc", "stablecoin", "digital currency", "blockchain", "crypto asset"]):
        industry = "数字资产基础设施"
        sector = "金融科技"
        tags.extend(["稳定币", "支付网络", "金融科技", "平台", "监管敏感", "高增长"])
        description = (
            f"{name} 的 SEC 文件多次出现 USDC、stablecoin、digital currency 等关键词，"
            "业务画像归类为稳定币/数字资产金融基础设施，核心变量包括储备收益、流通规模、监管准入和赎回风险。"
        )
        profile["confidence"] = 0.86
    elif "bank" in sic.lower() or "finance" in sic.lower() or "investment" in sic.lower():
        sector = "金融"
        tags.extend(["金融", "监管敏感"])
    elif "software" in sic.lower() or "services" in sic.lower():
        sector = "信息技术"
        tags.extend(["平台", "服务"])
    profile.update(
        {
            "industry": industry,
            "sector": sector,
            "description": description,
            "tags": list(dict.fromkeys(tags)),
            "summary": f"SEC 行业分类 {sic or 'N/A'}；系统结合申报文本关键词识别为 {industry} / {sector}。",
        }
    )
    return profile


def latest_submission_filed_date(submissions: dict) -> str:
    dates = ((submissions.get("filings") or {}).get("recent") or {}).get("filingDate") or []
    return max([str(item) for item in dates if item], default="")


def latest_us_gaap_fact(fact: dict) -> dict | None:
    units = fact.get("units") or {}
    candidates = []
    for unit, values in units.items():
        for value in values:
            if value.get("val") in (None, ""):
                continue
            item = dict(value)
            item["unit"] = unit
            candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.get("filed") or "", item.get("end") or "", item.get("form") or ""), reverse=True)
    return candidates[0]


def normalize_yahoo_chart(chart: dict) -> list[dict]:
    timestamps = chart.get("timestamp") or []
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    volumes = quote.get("volume") or []
    gmtoffset = int((chart.get("meta") or {}).get("gmtoffset") or 0)
    rows = []
    for index, ts in enumerate(timestamps):
        close = number_at(closes, index)
        if close is None:
            continue
        day = datetime.fromtimestamp(int(ts) + gmtoffset, tz=timezone.utc).date().isoformat()
        rows.append(
            {
                "date": day,
                "open": number_at(opens, index),
                "high": number_at(highs, index),
                "low": number_at(lows, index),
                "close": round(close, 4),
                "volume": number_at(volumes, index) or 0,
            }
        )
    return rows


def analyze_price_history(rows: list[dict]) -> dict:
    closes = [float(row["close"]) for row in rows if row.get("close") is not None]
    volumes = [float(row.get("volume") or 0) for row in rows if row.get("close") is not None]
    latest = closes[-1]
    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    rsi6 = rsi(closes, 6)
    rsi12 = rsi(closes, 12)
    macd_data = macd(closes)
    volume_ratio_5 = round((volumes[-1] / moving_average(volumes, 5)), 2) if len(volumes) >= 5 and moving_average(volumes, 5) else None
    support = sorted({round(value, 2) for value in [ma5, ma10, ma20, min(closes[-20:])] if value}, reverse=True)
    resistance = sorted({round(value, 2) for value in [max(closes[-20:]), max(closes[-60:]) if len(closes) >= 60 else max(closes)] if value})
    trend = classify_trend(ma5, ma10, ma20, latest)
    return {
        "样本天数": len(rows),
        "最新交易日": rows[-1]["date"],
        "最新收盘价": round(latest, 2),
        "均线": {"5日": round(ma5, 2), "10日": round(ma10, 2), "20日": round(ma20, 2), "60日": round(ma60, 2), "120日": round(ma120, 2) if ma120 else None},
        "均线状态": trend,
        "乖离率": {
            "相对5日": pct(latest, ma5),
            "相对20日": pct(latest, ma20),
            "相对60日": pct(latest, ma60),
        },
        "量能": {"当日量比5日均量": volume_ratio_5, "状态": classify_volume(closes, volumes)},
        "MACD": macd_data,
        "RSI": {"6日": rsi6, "12日": rsi12, "状态": classify_rsi(rsi6)},
        "支撑位": support[:3],
        "压力位": resistance[-3:],
        "信号摘要": build_technical_signal(trend, rsi6, volume_ratio_5, latest, ma5, ma20),
    }


def moving_average(values: list[float], window: int) -> float:
    if not values:
        return 0
    sample = values[-window:] if len(values) >= window else values
    return sum(sample) / len(sample)


def ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(value * alpha + output[-1] * (1 - alpha))
    return output


def macd(values: list[float]) -> dict:
    if len(values) < 35:
        return {"DIF": None, "DEA": None, "柱": None, "状态": "样本不足"}
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = ema(dif, 9)
    bar = (dif[-1] - dea[-1]) * 2
    if dif[-1] > dea[-1] > 0:
        status = "多头区间"
    elif dif[-1] > dea[-1]:
        status = "金叉修复"
    elif dif[-1] < dea[-1] < 0:
        status = "空头区间"
    else:
        status = "动能走弱"
    return {"DIF": round(dif[-1], 4), "DEA": round(dea[-1], 4), "柱": round(bar, 4), "状态": status}


def rsi(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for before, after in zip(values[-period - 1 : -1], values[-period:]):
        change = after - before
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def pct(current: float, base: float) -> float | None:
    if not base:
        return None
    return round((current / base - 1) * 100, 2)


def classify_trend(ma5: float, ma10: float, ma20: float, latest: float) -> str:
    if ma5 > ma10 > ma20 and latest >= ma5:
        return "强势多头"
    if ma5 > ma10 > ma20:
        return "多头排列"
    if ma5 < ma10 < ma20:
        return "空头排列"
    if abs(ma5 - ma20) / ma20 < 0.025 if ma20 else False:
        return "均线缠绕"
    return "震荡分化"


def classify_volume(closes: list[float], volumes: list[float]) -> str:
    if len(closes) < 6 or len(volumes) < 6:
        return "样本不足"
    ratio = volumes[-1] / moving_average(volumes, 5) if moving_average(volumes, 5) else 0
    rising = closes[-1] >= closes[-2]
    if ratio >= 1.5 and rising:
        return "放量上涨"
    if ratio >= 1.5 and not rising:
        return "放量下跌"
    if ratio <= 0.7 and rising:
        return "缩量上涨"
    if ratio <= 0.7 and not rising:
        return "缩量回调"
    return "量能正常"


def classify_rsi(value: float | None) -> str:
    if value is None:
        return "样本不足"
    if value >= 70:
        return "超买"
    if value <= 30:
        return "超卖"
    if value >= 55:
        return "偏强"
    if value <= 45:
        return "偏弱"
    return "中性"


def build_technical_signal(trend: str, rsi6: float | None, volume_ratio_5: float | None, latest: float, ma5: float, ma20: float) -> str:
    points = [f"趋势为{trend}"]
    if rsi6 is not None:
        points.append(f"6日相对强弱指标为{rsi6}")
    if volume_ratio_5 is not None:
        points.append(f"当日量比5日均量为{volume_ratio_5}")
    if ma5 and latest > ma5:
        points.append("价格站上5日均线")
    elif ma20 and latest < ma20:
        points.append("价格低于20日均线")
    return "，".join(points)


def technical_summary_text(analysis: dict) -> str:
    ma = analysis.get("均线", {})
    return (
        f"{analysis.get('样本天数')} 个交易日技术分析："
        f"收盘 {analysis.get('最新收盘价')}，"
        f"5/20/60日均线分别为 {ma.get('5日')}/{ma.get('20日')}/{ma.get('60日')}，"
        f"{analysis.get('信号摘要')}。"
    )


def build_price_proxy_valuation_history(
    company: dict,
    security_id: str,
    base_dir: Path,
    symbol: str,
    series: list[dict],
    analysis: dict,
    source_url: str,
    source_provider: str,
) -> dict | None:
    closes = [float(row["close"]) for row in series if row.get("close") is not None]
    if len(closes) < 30:
        return None
    latest = closes[-1]
    below_or_equal = sum(1 for value in closes if value <= latest)
    percentile = round(below_or_equal / len(closes) * 100, 2)
    payload = {
        "sample_count": len(closes),
        "period": f"{series[0]['date']}~{series[-1]['date']}",
        "price_percentile": percentile,
        "pe_ttm_percentile": percentile,
        "pb_percentile": percentile,
        "latest_close": round(latest, 4),
        "proxy": True,
        "method": "Yahoo 历史收盘价分位代理估值分位；未取得历史 PE/PB 序列时使用，置信度低于正式估值序列。",
        "technical_signal": analysis.get("信号摘要"),
    }
    saved = save_json(base_dir / "valuation", f"yahoo_price_percentile_{safe_slug(symbol)}.json", payload)
    return {
        "evidence_id": "ev_valuation_history_yahoo_proxy",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "valuation_history",
        "title": f"{source_provider} 历史价格分位估值代理",
        "summary": (
            f"{company.get('name')} {len(closes)} 个交易日收盘价分位 {percentile}%；"
            "因未取得历史 PE/PB 序列，使用价格分位作为低置信估值代理。"
        ),
        "raw_value": json.dumps(series[-60:], ensure_ascii=False),
        "normalized_value": json.dumps(payload, ensure_ascii=False),
        "unit": "percentile_proxy",
        "period": payload["period"],
        "date": series[-1]["date"],
        "source_provider": source_provider,
        "source_url": source_url,
        "source_document_id": saved.text_path,
        "extracted_quote": excerpt(json.dumps(payload, ensure_ascii=False, indent=2)),
        "confidence": 0.62,
        "freshness_score": freshness_from_date(series[-1]["date"]),
        "created_at": utc_now_iso(),
    }


def number_at(values: list, index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_sec_filings(submissions: dict) -> list[dict]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    forms = recent.get("form") or []
    primary_docs = recent.get("primaryDocument") or []
    filing_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    wanted = {"10-K", "10-Q", "8-K"}
    seen: set[str] = set()
    output = []
    for idx, accession in enumerate(accessions):
        form = forms[idx] if idx < len(forms) else ""
        if form not in wanted or form in seen:
            continue
        output.append(
            {
                "form": form,
                "accession": accession,
                "primary_document": primary_docs[idx] if idx < len(primary_docs) else "",
                "filing_date": filing_dates[idx] if idx < len(filing_dates) else "",
                "report_date": report_dates[idx] if idx < len(report_dates) else "",
            }
        )
        seen.add(form)
        if len(output) >= MAX_FILING_ITEMS:
            break
    return output


def sec_filing_url(cik: str, filing: dict) -> str:
    accession = str(filing.get("accession") or "")
    accession_no_dash = accession.replace("-", "")
    primary = filing.get("primary_document") or f"{accession}.txt"
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dash}/{primary}"


def lookup_cninfo_org_id(client: httpx.Client, code: str) -> str | None:
    data = request_json(client, "https://www.cninfo.com.cn/new/data/szse_stock.json", headers={"User-Agent": "Mozilla/5.0"})
    for item in (data.get("stockList") or []) if isinstance(data, dict) else []:
        if str(item.get("code")) == code:
            return item.get("orgId")
    return None


def hk_code(company: dict) -> str:
    digits = re.sub(r"\D", "", str(company.get("ticker") or "").split(".")[0])
    return digits.zfill(5) if digits else ""


def lookup_hkex_stock_id(client: httpx.Client, code: str) -> str | None:
    candidates = [code, code.lstrip("0") or code]
    for endpoint in ["prefix.do", "partial.do"]:
        for value in candidates:
            payload = request_jsonp(
                client,
                f"https://www1.hkexnews.hk/search/{endpoint}",
                params={"callback": "callback", "lang": "EN", "type": "A", "name": value, "market": "SEHK"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=EN"},
            )
            for item in payload.get("stockInfo") or []:
                item_code = re.sub(r"\D", "", str(item.get("code") or "")).zfill(5)
                if item_code == code:
                    return str(item.get("stockId"))
    return None


def query_hkex_title_search(client: httpx.Client, stock_id: str, *, title: str = "", row_range: int = 8) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=730)
    data = request_json(
        client,
        "https://www1.hkexnews.hk/search/titleSearchServlet.do",
        params={
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": stock_id,
            "documentType": "-1",
            "fromDate": start.strftime("%Y%m%d"),
            "toDate": end.strftime("%Y%m%d"),
            "title": title,
            "searchType": "0",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": str(row_range),
            "lang": "E",
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId={stock_id}",
            "Accept": "application/json,*/*",
        },
    )
    raw = data.get("result") if isinstance(data, dict) else ""
    if not raw or raw in {"null", "[]"}:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def dedupe_hkex_docs(docs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    output = []
    for doc in docs:
        key = str(doc.get("FILE_LINK") or doc.get("NEWS_ID") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(doc)
    return output


def hkex_doc_priority(doc: dict) -> int:
    text = f"{doc.get('TITLE') or ''} {doc.get('LONG_TEXT') or ''} {doc.get('SHORT_TEXT') or ''}".lower()
    if "annual report" in text:
        return 100
    if "interim report" in text:
        return 95
    if "annual results" in text or "final results" in text:
        return 90
    if "quarterly results" in text or "interim results" in text:
        return 86
    if "business update" in text:
        return 82
    if "announcement" in text:
        return 70
    return 50


def parse_hkex_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min
    for fmt in ["%d/%m/%Y %H:%M", "%d/%m/%Y"]:
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return datetime.min


def hkex_datetime_to_iso(value: str | None) -> str:
    parsed = parse_hkex_datetime(value)
    if parsed == datetime.min:
        return ""
    return parsed.isoformat(timespec="minutes")


def eastmoney_datacenter_rows(
    client: httpx.Client,
    report_name: str,
    secucode: str,
    *,
    page_size: int = 80,
    sort_columns: str = "REPORT_DATE",
) -> list[dict]:
    data = request_json(
        client,
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": report_name,
            "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")',
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortColumns": sort_columns,
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://emweb.securities.eastmoney.com/"},
    )
    rows = ((data.get("result") or {}).get("data") or []) if isinstance(data, dict) else []
    return rows if isinstance(rows, list) else []


def latest_report_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    latest = max(str(row.get("REPORT_DATE") or row.get("STD_REPORT_DATE") or "") for row in rows)
    return [row for row in rows if str(row.get("REPORT_DATE") or row.get("STD_REPORT_DATE") or "") == latest]


def financial_rows_to_dict(rows: list[dict]) -> dict:
    output: dict[str, Any] = {}
    for row in rows:
        name = row.get("STD_ITEM_NAME") or row.get("ITEM_NAME") or row.get("STD_ITEM_CODE")
        if name:
            output[str(name)] = row.get("AMOUNT")
    return output


def merge_metric_defaults(base: dict, defaults: dict) -> dict:
    merged = dict(base or {})
    for key, value in defaults.items():
        if value in (None, "", 0):
            continue
        if merged.get(key) in (None, "", 0):
            merged[key] = value
    return merged


def eastmoney_hk_indicator_series(rows: list[dict]) -> list[dict]:
    series = []
    for row in rows:
        period = parse_datetime_prefix(row.get("STD_REPORT_DATE") or row.get("REPORT_DATE"))
        if not period:
            continue
        item = {
            "period": period,
            "report_type": row.get("REPORT_TYPE"),
            "revenue": parse_number(row.get("OPERATE_INCOME")),
            "net_income": parse_number(row.get("HOLDER_PROFIT")),
            "operating_cash_flow": parse_number(row.get("NETCASH_OPERATE")),
            "gross_margin": parse_number(row.get("GROSS_PROFIT_RATIO")),
            "net_margin": parse_number(row.get("NET_PROFIT_RATIO")),
            "roe": parse_number(row.get("ROE_AVG") or row.get("ROE_YEARLY")),
        }
        series.append({key: value for key, value in item.items() if value not in (None, "")})
    return sorted(series, key=lambda item: str(item.get("period") or ""), reverse=True)


def extract_eastmoney_hk_statement_metrics(kind: str, rows: list[dict]) -> dict:
    values = financial_rows_to_dict(rows)
    if not values:
        return {}
    metrics: dict[str, Any] = {}
    if kind == "income_statement":
        metrics["revenue"] = amount_by_alias(values, ["营业额", "营业收入", "收入", "收益", "Revenue"])
        metrics["net_income"] = amount_by_alias(values, ["股东应占溢利", "公司拥有人应占", "净利润", "期内溢利", "年内溢利", "Profit attributable"])
    elif kind == "balance_sheet":
        total_assets = amount_by_alias(values, ["总资产", "资产总额", "资产合计", "Total assets"])
        total_liabilities = amount_by_alias(values, ["总负债", "负债总额", "负债合计", "Total liabilities"])
        metrics["total_assets"] = total_assets
        metrics["total_liabilities"] = total_liabilities
        if total_assets and total_liabilities:
            metrics["debt_to_asset_ratio"] = round(total_liabilities / total_assets * 100, 2)
    elif kind == "cash_flow":
        operating_cash_flow = amount_by_alias(
            values,
            [
                "经营业务现金净额",
                "经营活动现金流量净额",
                "经营活动产生的现金流量净额",
                "经营所得现金净额",
                "经营产生现金",
                "Net cash from operating",
            ],
        )
        capex = amount_by_alias(values, ["购买物业、厂房及设备", "购建固定资产", "资本开支", "Purchase of property"])
        metrics["operating_cash_flow"] = operating_cash_flow
        if operating_cash_flow is not None and capex is not None:
            metrics["free_cash_flow"] = round(operating_cash_flow - abs(capex), 2)
    return {key: value for key, value in metrics.items() if value not in (None, "")}


def amount_by_alias(values: dict[str, Any], aliases: list[str]) -> float | None:
    normalized = {normalize_financial_label(key): value for key, value in values.items()}
    for alias in aliases:
        target = normalize_financial_label(alias)
        for key, value in normalized.items():
            if target and target in key:
                number = parse_number(value)
                if number is not None:
                    return number
    return None


def normalize_financial_label(value: Any) -> str:
    return re.sub(r"[\s:：()（）/／,，、_-]+", "", str(value or "").lower())


def summarize_financial_rows(rows: list[dict]) -> str:
    wanted = ["营业额", "营业总收入", "收入", "净利润", "股东应占溢利", "总资产", "总负债", "经营业务现金净额", "经营产生现金", "期末现金"]
    values = financial_rows_to_dict(rows)
    parts = []
    for key in wanted:
        if key in values and values[key] is not None:
            parts.append(f"{key}={values[key]}")
    if not parts:
        parts = [f"{key}={value}" for key, value in list(values.items())[:6]]
    return "；".join(parts[:6])


def eastmoney_hk_indicator_summary(company: dict, metrics: dict) -> str:
    return (
        f"{company['name']} {metrics.get('period') or '最新'} 主要财务指标："
        f"收入 {metrics.get('revenue')}，归母利润 {metrics.get('net_income')}，"
        f"毛利率 {metrics.get('gross_margin')}%，净利率 {metrics.get('net_margin')}%，"
        f"ROE {metrics.get('roe')}%，经营现金流 {metrics.get('operating_cash_flow')}。"
    )


def request_json(
    client: httpx.Client,
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    headers: dict | None = None,
) -> dict:
    try:
        response = client.request(method, url, params=params, data=data, headers=headers)
        response.raise_for_status()
        text = response.text.strip()
        if text.startswith("datatable"):
            text = text[text.find("(") + 1 : text.rfind(")")]
        return json.loads(text)
    except Exception:
        return {}


def request_jsonp(
    client: httpx.Client,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
) -> dict:
    try:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        text = response.text.strip()
        if "(" in text and text.endswith(");"):
            text = text[text.find("(") + 1 : -2]
        return json.loads(text)
    except Exception:
        return {}


def fetch_readable_text(client: httpx.Client, url: str) -> str:
    if not url:
        return ""
    try:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type.lower() or url.lower().endswith(".pdf"):
            return extract_pdf_text(response.content)
        parser = ReadableHTMLParser()
        parser.feed(response.text)
        return parser.text()
    except Exception:
        return ""


def fetch_pdf_document(client: httpx.Client, url: str, folder: Path, stem: str) -> SavedDocument:
    if not url:
        return SavedDocument()
    try:
        response = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        if len(response.content) > MAX_PDF_BYTES:
            binary_path = save_bytes(folder, f"{stem}.pdf", response.content[:MAX_PDF_BYTES])
            note = f"PDF 超过 {MAX_PDF_BYTES} bytes，已保存前段二进制，未抽取完整文本。"
            text_doc = save_text(folder, f"{stem}.txt", note)
            return SavedDocument(text_path=text_doc.text_path, binary_path=binary_path, text=note, truncated=True)
        binary_path = save_bytes(folder, f"{stem}.pdf", response.content)
        text = extract_pdf_text(response.content)
        text_doc = save_text(folder, f"{stem}.txt", text) if text else SavedDocument()
        return SavedDocument(text_path=text_doc.text_path, binary_path=binary_path, text=text)
    except Exception as exc:
        text_doc = save_text(folder, f"{stem}.error.txt", f"PDF 抓取失败：{exc}")
        return SavedDocument(text_path=text_doc.text_path, text="")


def extract_pdf_text(content: bytes) -> str:
    try:
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        from pypdf import PdfReader
    except Exception:
        return "PDF 已下载，但当前环境未安装 pypdf，无法抽取文本。请安装 requirements.txt 后重新采集。"
    try:
        reader = PdfReader(BytesIO(content))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return normalize_text("\n".join(parts))
    except Exception as exc:
        return f"PDF 文本抽取失败：{exc}"


def financial_statement_snippets(text: str, *, source: str) -> list[dict]:
    if not text:
        return []
    patterns = [
        ("income_statement", "利润表", [r"合并利润表", r"利润表", r"Consolidated Statements? of (Operations|Income)"]),
        ("balance_sheet", "资产负债表", [r"合并资产负债表", r"资产负债表", r"Consolidated Balance Sheets?"]),
        ("cash_flow", "现金流量表", [r"合并现金流量表", r"现金流量表", r"Consolidated Statements? of Cash Flows?"]),
    ]
    snippets = []
    for kind, title, regexes in patterns:
        match = None
        for pattern in regexes:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                break
        if not match:
            continue
        start = max(0, match.start() - 200)
        end = min(len(text), match.start() + 3500)
        snippets.append({"kind": kind, "title": title, "text": text[start:end], "source": source})
    return snippets


def save_json(folder: Path, filename: str, value: Any) -> SavedDocument:
    return save_text(folder, filename, json.dumps(value, ensure_ascii=False, indent=2))


def save_text(folder: Path, filename: str, text: str) -> SavedDocument:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe_filename(filename)
    path.write_text(text or "", encoding="utf-8")
    return SavedDocument(text_path=str(path), text=text or "")


def save_bytes(folder: Path, filename: str, content: bytes) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe_filename(filename)
    path.write_bytes(content)
    return str(path)


def sec_statement_summary(payload: dict) -> str:
    facts = payload.get("latest") or {}
    pairs = [f"{sec_fact_label(key)}={value}" for key, value in facts.items()]
    return f"{payload.get('label')} 最新美国证监会结构化财报字段：{'; '.join(pairs[:6])}"


SEC_FACT_LABELS = {
    "Revenues": "营业收入",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "客户合同收入",
    "SalesRevenueNet": "销售收入净额",
    "GrossProfit": "毛利润",
    "OperatingIncomeLoss": "营业利润",
    "NetIncomeLoss": "净利润",
    "EarningsPerShareDiluted": "摊薄每股收益",
    "Assets": "总资产",
    "AssetsCurrent": "流动资产",
    "Liabilities": "总负债",
    "LiabilitiesCurrent": "流动负债",
    "StockholdersEquity": "股东权益",
    "CashAndCashEquivalentsAtCarryingValue": "现金及现金等价物",
    "InventoryNet": "存货净额",
    "NetCashProvidedByUsedInOperatingActivities": "经营活动现金流",
    "NetCashProvidedByUsedInInvestingActivities": "投资活动现金流",
    "NetCashProvidedByUsedInFinancingActivities": "筹资活动现金流",
    "PaymentsToAcquirePropertyPlantAndEquipment": "购建固定资产支出",
    "FreeCashFlow": "自由现金流",
}


def sec_fact_label(key: str) -> str:
    return SEC_FACT_LABELS.get(str(key), str(key))


def sec_form_label(form: str) -> str:
    labels = {
        "10-K": "年报（10-K）",
        "10-Q": "季报（10-Q）",
        "8-K": "重大事项公告（8-K）",
        "20-F": "年报（20-F）",
        "6-K": "境外发行人公告（6-K）",
        "F-1": "招股书（F-1）",
        "S-1": "招股书（S-1）",
    }
    normalized = str(form or "").upper()
    return labels.get(normalized, f"申报文件（{form}）")


def yahoo_symbol(company: dict) -> str:
    ticker = str(company.get("ticker") or "").upper()
    market = company.get("market")
    if market == "A":
        code = ticker.split(".")[0]
        suffix = ".SS" if code.startswith("6") else ".SZ"
        return code + suffix
    if market == "HK":
        digits = re.sub(r"\D", "", ticker.split(".")[0])
        if not digits:
            return ticker
        if len(digits) == 5 and digits.startswith("0"):
            digits = digits[1:]
        elif len(digits) < 4:
            digits = digits.zfill(4)
        return f"{digits}.HK"
    return ticker


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = ReadableHTMLParser()
    parser.feed(html.unescape(value))
    return parser.text()


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"[\t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    value = re.sub(r"[ ]{2,}", " ", value)
    return value.strip()


def excerpt(value: str, max_chars: int = EXCERPT_CHARS) -> str:
    clean = normalize_text(value)
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 20].rstrip() + "\n...[truncated]"


def node_text(item: ElementTree.Element, name: str) -> str:
    node = item.find(name)
    return node.text or "" if node is not None else ""


def parse_rss_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.strptime(value[:25], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    except Exception:
        return value


def parse_datetime_prefix(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    match = re.match(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else text


def millis_to_date(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return ""


def freshness_from_date(value: str | None) -> float:
    if not value:
        return 0.55
    try:
        parsed_text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(parsed_text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        days = max(0, (datetime.now(timezone.utc) - parsed).days)
    except Exception:
        try:
            parsed_date = date.fromisoformat(str(value)[:10])
            days = max(0, (date.today() - parsed_date).days)
        except Exception:
            return 0.55
    if days <= 3:
        return 0.98
    if days <= 30:
        return 0.88
    if days <= 120:
        return 0.76
    if days <= 365:
        return 0.65
    return 0.5


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_slug(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", text)
    return text.strip("_")[:100] or "unknown"


def safe_filename(value: str) -> str:
    stem, dot, suffix = value.partition(".")
    stem = safe_slug(stem)
    suffix = re.sub(r"[^a-zA-Z0-9]+", "", suffix)[:12]
    return f"{stem}.{suffix}" if dot and suffix else stem
