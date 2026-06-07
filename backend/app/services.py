from __future__ import annotations

import hashlib
import ast
import json
import os
import re
import asyncio
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from .database import ROOT, from_json, get_conn, to_json
from .real_collectors import (
    REQUEST_TIMEOUT,
    USER_AGENT,
    clean_search_url,
    collect_real_world_sources,
    fetch_readable_text,
    normalize_text,
    safe_slug,
    save_text,
    search_bing,
    search_duckduckgo,
)
from .scoring import build_company_scorecard
from .scoring.rules import action_from_scorecard, grade_company_quality, grade_valuation
from .scoring.schemas import SCORING_VERSION


ROUND_NAMES = {
    1: "第一轮：独立分析",
    2: "第二轮：相互质疑",
    3: "第三轮：修正观点",
    4: "第四轮：主席总结",
    5: "第五轮：最终结论",
}

FINAL_ACTIONS = ["强烈买入", "买入", "小仓位关注", "重点观察", "等待更好价格", "减仓", "卖出", "回避"]
V2_DATA_SCHEMA_VERSION = "ai-committee-v2.1-real-sources"
AGENT_OUTPUT_SCHEMA_VERSION = "agent-opinion-v2.0"
DATA_QUALITY_GATE_VERSION = "DQS-gate-v1"

STANCE_LABELS = {
    "strong_bullish": "强烈看多",
    "bullish": "看多",
    "neutral": "中性",
    "bearish": "看空",
    "strong_bearish": "强烈看空",
    "buy": "买入",
    "watch": "观察",
    "avoid": "回避",
}

RISK_LEVEL_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "major": "重大",
}

ROUND_FIELD_LABELS = {
    "agree_with": "赞同点",
    "disagree_with": "不同意见",
    "dangerous_assumptions": "危险假设",
    "questions_to_committee": "追问委员会",
    "changed_because": "修正原因",
    "still_believe": "仍然坚持",
    "remaining_disagreements": "剩余分歧",
    "new_score": "修正后评分",
    "final_action": "最终倾向",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stable_noise(*parts: Any, modulo: int = 11) -> int:
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16) % modulo


def row_to_company(row: Any, snapshot: Any | None = None) -> dict:
    company = dict(row)
    company["tags"] = from_json(company.get("tags"), [])
    company["aliases"] = from_json(company.get("aliases"), [])
    if snapshot:
        snap = dict(snapshot)
        snap["raw_data"] = from_json(snap.get("raw_data"), {})
        company["snapshot"] = snap
    return company


def row_to_expert(row: Any, profile_row: Any | None = None) -> dict:
    expert = dict(row)
    expert["is_active"] = bool(expert.get("is_active"))
    if profile_row:
        profile = dict(profile_row)
        for key in ["preferred_industries", "avoided_industries", "market_tags", "style_tags"]:
            profile[key] = from_json(profile.get(key), [])
        expert["profile"] = profile
    return expert


def company_snapshot(conn, company_id: str) -> Any | None:
    return conn.execute(
        "SELECT * FROM company_snapshots WHERE company_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (company_id,),
    ).fetchone()


def company_by_id(conn, company_id: str, refresh_quote: bool = True) -> dict:
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if not row:
        raise ValueError("company not found")
    company = row_to_company(row, company_snapshot(conn, company_id))
    if not refresh_quote:
        return company
    return refresh_company_quote(conn, company)


def expert_by_id(conn, expert_id: str) -> dict:
    row = conn.execute("SELECT * FROM experts WHERE id = ?", (expert_id,)).fetchone()
    if not row:
        raise ValueError("expert not found")
    profile = conn.execute("SELECT * FROM expert_profiles WHERE expert_id = ?", (expert_id,)).fetchone()
    return row_to_expert(row, profile)


def list_experts(conn, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM experts"
    params: tuple[Any, ...] = ()
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY category, name"
    rows = conn.execute(query, params).fetchall()
    return [expert_by_id(conn, row["id"]) for row in rows]


def search_companies(conn, q: str, market: str | None = None) -> list[dict]:
    query = (q or "").strip()
    tokens = query_tokens(query)
    rows = conn.execute("SELECT * FROM companies ORDER BY market, ticker").fetchall()
    results: list[dict] = []
    for row in rows:
        company = row_to_company(row, company_snapshot(conn, row["id"]))
        if is_non_company_record(company):
            continue
        haystack = " ".join(
            [
                company["name"] or "",
                company["name_en"] or "",
                company["ticker"] or "",
                company["market"] or "",
                " ".join(company["aliases"]),
                " ".join(company["tags"]),
            ]
        ).lower()
        market_ok = not market or market == "AUTO" or company["market"].upper() == market.upper()
        if market_ok and (not tokens or any(token in haystack for token in tokens)):
            results.append(refresh_company_quote(conn, company))
    if results:
        results = dedupe_companies_by_listing(results)
        primary_match = primary_listing_for_secondary_results(conn, results, query, market)
        if primary_match:
            return [primary_match]
        if not wants_secondary_listing(query) and (not market or market == "AUTO"):
            primary_results = [company for company in results if not is_secondary_listing(company)]
            if primary_results:
                results = primary_results
        real_results = [company for company in results if not is_manual_company(company)]
        if real_results:
            return sorted(real_results, key=lambda company: search_rank(company, query))[:12]
        dynamic = ensure_dynamic_companies(conn, query, market)
        if dynamic:
            return sorted(dynamic, key=lambda company: search_rank(company, query))[:12]
        return sorted(results, key=lambda company: search_rank(company, query))[:12]
    dynamic = ensure_dynamic_companies(conn, query, market)
    return dynamic[:12]


def primary_listing_for_secondary_results(conn, results: list[dict], query: str, market: str | None = None) -> dict | None:
    if not results or wants_secondary_listing(query) or not contains_cjk(query):
        return None
    real_results = [company for company in results if not is_manual_company(company)]
    if any(not is_secondary_listing(company) and company.get("market") != "US" for company in real_results):
        return None
    if real_results and all(is_secondary_listing(company) for company in real_results):
        return find_local_primary_listing(conn, query, real_results)
    if market and market.upper() == "US" and any(is_secondary_listing(company) for company in real_results):
        return find_local_primary_listing(conn, query, real_results)
    return None


def dedupe_companies_by_listing(companies: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for company in companies:
        key = (str(company.get("market") or ""), str(company.get("ticker") or "").upper())
        current = by_key.get(key)
        if not current or company_preference(company) < company_preference(current):
            by_key[key] = company
    return list(by_key.values())


def company_preference(company: dict) -> tuple[int, str]:
    company_id = str(company.get("id") or "")
    if company_id.startswith("co_manual_"):
        return (3, company_id)
    if company_id.startswith("co_univ_") or company_id.startswith("co_ext_"):
        return (2, company_id)
    return (1, company_id)


def query_tokens(query: str) -> list[str]:
    clean = query.strip().lower()
    if not clean:
        return []
    tokens = [
        token
        for token in re.split(r"[\s/／,，;；|｜]+", clean)
        if token and token not in {"股票", "公司", "股份", "集团"}
    ]
    return tokens or [clean]


def ensure_dynamic_companies(conn, query: str, market: str | None = None) -> list[dict]:
    if not query.strip():
        return []
    candidates = dedupe_company_candidates(fetch_eastmoney_candidates(query, market) + fetch_yahoo_candidates(query, market))
    candidates = prefer_primary_listing_candidates(conn, candidates, query, market)
    if not candidates:
        return []
    saved: list[dict] = []
    for candidate in candidates[:8]:
        company = save_company_candidate(conn, candidate)
        if company:
            saved.append(company)
    return saved


def fetch_eastmoney_candidates(query: str, market: str | None = None) -> list[dict]:
    try:
        with httpx.Client(timeout=5, headers={"User-Agent": "Mozilla/5.0"}) as client:
            response = client.get(
                "https://searchapi.eastmoney.com/api/suggest/get",
                params={"input": query, "type": 14, "count": 8},
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return []
    rows = ((data.get("QuotationCodeTable") or {}).get("Data") or []) if isinstance(data, dict) else []
    candidates = []
    for row in rows:
        candidate = eastmoney_search_row_to_candidate(row)
        if not candidate:
            continue
        if market and market != "AUTO" and candidate["market"] != market:
            continue
        candidates.append(candidate)
    if not wants_secondary_listing(query) and (not market or market == "AUTO"):
        primary_candidates = [candidate for candidate in candidates if not is_secondary_listing(candidate)]
        if primary_candidates:
            candidates = primary_candidates
    return candidates


def eastmoney_search_row_to_candidate(row: dict) -> dict | None:
    classify = str(row.get("Classify") or "").upper()
    security_type = str(row.get("SecurityTypeName") or "")
    code = str(row.get("Code") or row.get("UnifiedCode") or "").upper()
    name = row.get("Name") or code
    if not code or not name:
        return None
    if is_non_company_security(code, str(name), security_type):
        return None
    if classify == "HK" or "港股" in security_type or str(row.get("MktNum")) == "116":
        market = "HK"
        exchange = "HKEX"
        ticker = normalize_hk_ticker(code)
    elif classify == "ASTOCK" or security_type in {"沪A", "深A", "京A"} or re.fullmatch(r"\d{6}", code):
        market = "A"
        exchange = "SSE" if code.startswith("6") else "SZSE"
        ticker = code
    elif classify == "USSTOCK" or "美股" in security_type or str(row.get("JYS") or "").upper() in {"NYSE", "NASDAQ", "AMEX", "OTCBB"}:
        market = "US"
        exchange = str(row.get("JYS") or "US").upper()
        ticker = code
    else:
        return None
    tags = [market_label(market), "东方财富识别", "待完善"]
    if is_secondary_security_name(str(name), security_type, exchange):
        tags.extend(["ADR/粉单", "二级交易证券"])
    aliases = [
        code,
        name,
        row.get("PinYin") or "",
        row.get("QuoteID") or "",
        row.get("UnifiedCode") or "",
        ticker,
    ]
    industry = "待补充行业"
    sector = "外部识别"
    return {
        "id": "co_ext_" + hashlib.sha256(f"{ticker}:{market}".encode("utf-8")).hexdigest()[:12],
        "name": name,
        "name_en": name,
        "ticker": ticker,
        "market": market,
        "exchange": exchange,
        "industry": industry,
        "sector": sector,
        "description": f"{name} 由东方财富证券搜索识别为 {ticker} / {market_label(market)}，已加入本地公司库；后续资料包将按交易所适配器采集公告、财报、行情和新闻。",
        "tags": tags,
        "aliases": aliases,
        "snapshot": default_snapshot(ticker),
    }


def is_non_company_security(code: str, name: str, security_type: str) -> bool:
    text = f"{name} {security_type}"
    blocked_terms = [
        "ETF",
        "基金",
        "权证",
        "认购",
        "认沽",
        "牛证",
        "熊证",
        "购",
        "沽",
        "法巴",
        "摩通",
        "星展",
        "瑞银",
        "信证",
        "汇丰",
        "麦银",
        "花旗",
        "高盛",
    ]
    if any(term in text for term in blocked_terms):
        return True
    if code.startswith(("BK", "IF", "IH", "IC", "IM")):
        return True
    return False


def wants_secondary_listing(query: str) -> bool:
    text = (query or "").upper()
    return any(token in text for token in ["ADR", "OTC", "PINK", "粉单", "存托", "PMRTY", "PMRTF"])


def is_secondary_security_name(name: str, security_type: str = "", exchange: str = "") -> bool:
    text = f"{name} {security_type} {exchange}".upper()
    return any(token in text for token in ["ADR", "OTC", "OTCBB", "PINK", "粉单", "存托"])


def is_secondary_listing(company: dict) -> bool:
    tags = " ".join(str(tag) for tag in company.get("tags", [])).upper()
    return (
        is_secondary_security_name(str(company.get("name") or ""), str(company.get("sector") or ""), str(company.get("exchange") or ""))
        or "ADR/粉单".upper() in tags
        or "二级交易证券" in tags
    )


def prefer_primary_listing_candidates(conn, candidates: list[dict], query: str, market: str | None = None) -> list[dict]:
    if not candidates or wants_secondary_listing(query):
        return candidates
    primary = [candidate for candidate in candidates if not is_secondary_listing(candidate)]
    if primary:
        return primary if not market or market == "AUTO" else candidates
    if not contains_cjk(query):
        return candidates
    canonical = find_local_primary_listing(conn, query, candidates)
    if canonical:
        return [canonical]
    return candidates


def find_local_primary_listing(conn, query: str, candidates: list[dict]) -> dict | None:
    names = {strip_secondary_suffix(query)}
    for candidate in candidates:
        names.add(strip_secondary_suffix(str(candidate.get("name") or "")))
        names.update(strip_secondary_suffix(str(alias)) for alias in candidate.get("aliases", []) if alias)
    names = {name for name in names if name and contains_cjk(name)}
    if not names:
        return None
    rows = conn.execute("SELECT * FROM companies WHERE market <> 'US' ORDER BY CASE market WHEN 'HK' THEN 1 WHEN 'A' THEN 2 ELSE 9 END, ticker").fetchall()
    for row in rows:
        company = row_to_company(row, company_snapshot(conn, row["id"]))
        haystack_values = [company.get("name") or "", company.get("name_en") or "", *(company.get("aliases") or [])]
        haystack = {strip_secondary_suffix(str(value)) for value in haystack_values if value}
        if names & haystack:
            return refresh_company_quote(conn, company)
    return None


def strip_secondary_suffix(value: str) -> str:
    text = re.sub(r"\s+", "", value or "")
    text = re.sub(r"[\(（]?\s*(ADR|粉单|存托凭证)\s*[\)）]?", "", text, flags=re.IGNORECASE)
    return text.strip()


def contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def is_non_company_record(company: dict) -> bool:
    ticker = str(company.get("ticker") or "").upper()
    name = str(company.get("name") or "")
    market = str(company.get("market") or "").upper()
    if market == "US" and "." in ticker and ticker not in {"BRK.A", "BRK.B", "BF.A", "BF.B"}:
        return True
    return is_non_company_security(ticker, name, "")


def normalize_hk_ticker(code: str) -> str:
    digits = re.sub(r"\D", "", code)
    if not digits:
        return code.upper()
    if len(digits) == 5 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) < 4:
        digits = digits.zfill(4)
    return f"{digits}.HK"


def dedupe_company_candidates(candidates: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    output = []
    for candidate in candidates:
        key = (str(candidate.get("market") or ""), str(candidate.get("ticker") or "").upper())
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def fetch_yahoo_candidates(query: str, market: str | None = None) -> list[dict]:
    try:
        with httpx.Client(timeout=5, headers={"User-Agent": "Mozilla/5.0"}) as client:
            response = client.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": query, "quotesCount": 8, "newsCount": 0, "lang": "zh-CN"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return []
    candidates = []
    for quote in data.get("quotes", []):
        if quote.get("quoteType") not in {"EQUITY", "ETF"}:
            continue
        symbol = quote.get("symbol") or ""
        if not symbol:
            continue
        inferred_market, ticker, exchange = infer_market(symbol, quote.get("exchDisp") or quote.get("exchange") or "")
        if market and market != "AUTO" and inferred_market != market:
            continue
        name = quote.get("shortname") or quote.get("longname") or quote.get("name") or symbol
        candidates.append(
            {
                "id": "co_ext_" + hashlib.sha256(f"{ticker}:{inferred_market}".encode("utf-8")).hexdigest()[:12],
                "name": name,
                "name_en": quote.get("longname") or name,
                "ticker": ticker,
                "market": inferred_market,
                "exchange": exchange,
                "industry": "待补充行业",
                "sector": "外部识别",
                "description": f"{name} 由外部证券搜索识别，已加入本地公司库；后续可在数据层补充行业、财务和公告数据。",
                "tags": [market_label(inferred_market), "外部识别", "待完善"] + (["ADR/粉单", "二级交易证券"] if is_secondary_security_name(name, "", exchange) else []),
                "aliases": [symbol, name, quote.get("longname") or ""],
                "snapshot": default_snapshot(ticker),
            }
        )
    return candidates


def fallback_company_candidate(query: str, market: str | None = None) -> dict:
    tokens = query_tokens(query)
    primary = tokens[0].upper() if tokens else query.strip().upper()
    inferred_market, ticker, exchange = infer_market(primary, "")
    if market and market != "AUTO":
        inferred_market = market
        exchange = {"US": "US", "HK": "HKEX", "A": "CN"}.get(market, exchange)
        ticker = normalize_ticker_for_market(primary, inferred_market)
    display_name = query.strip()
    if re.fullmatch(r"[A-Z.0-9-]{1,12}", primary):
        display_name = primary
    return {
        "id": "co_manual_" + hashlib.sha256(f"{display_name}:{ticker}:{inferred_market}".encode("utf-8")).hexdigest()[:12],
        "name": display_name,
        "name_en": primary,
        "ticker": ticker,
        "market": inferred_market,
        "exchange": exchange,
        "industry": "待补充行业",
        "sector": "用户输入",
        "description": f"{display_name} 是根据用户输入创建的待完善公司记录；系统会先用通用投研框架跑通投委会，后续可补充更精确数据。",
        "tags": [market_label(inferred_market), "待完善", "用户输入"],
        "aliases": [query, primary, ticker],
        "snapshot": default_snapshot(ticker),
    }


def infer_market(symbol: str, exchange: str) -> tuple[str, str, str]:
    raw = symbol.strip().upper()
    exch = exchange.upper()
    if raw.endswith(".HK"):
        return "HK", raw, "HKEX"
    if raw.endswith(".SS") or raw.endswith(".SH"):
        return "A", raw.split(".")[0], "SSE"
    if raw.endswith(".SZ"):
        return "A", raw.split(".")[0], "SZSE"
    if exch in {"HKG", "HKEX"}:
        return "HK", raw if raw.endswith(".HK") else f"{raw}.HK", "HKEX"
    if exch in {"SHH", "SSE"}:
        return "A", raw.split(".")[0], "SSE"
    if exch in {"SHZ", "SZSE"}:
        return "A", raw.split(".")[0], "SZSE"
    if re.fullmatch(r"\d{6}", raw):
        return "A", raw, "SSE" if raw.startswith("6") else "SZSE"
    if re.fullmatch(r"\d{1,5}", raw):
        return "HK", raw.zfill(4) + ".HK", "HKEX"
    return "US", raw, exchange or "US"


def normalize_ticker_for_market(symbol: str, market: str) -> str:
    raw = symbol.strip().upper()
    if market == "HK":
        return raw if raw.endswith(".HK") else raw.zfill(4) + ".HK" if raw.isdigit() else raw
    if market == "A":
        return raw.split(".")[0]
    return raw


def market_label(market: str) -> str:
    return {"US": "美股", "HK": "港股", "A": "A股"}.get(market, market)


def is_manual_company(company: dict) -> bool:
    return str(company.get("id", "")).startswith("co_manual_") or "用户输入" in company.get("tags", [])


def search_rank(company: dict, query: str) -> tuple[int, int, str]:
    q = query.strip().upper()
    ticker = str(company.get("ticker") or "").upper()
    aliases = {str(alias).upper() for alias in company.get("aliases", [])}
    symbols = {ticker, display_quote_symbol(company, None)}
    score = 0
    if q in symbols:
        score -= 100
    if q in aliases:
        score -= 60
    if q and q in {str(company.get("name") or "").upper(), str(company.get("name_en") or "").upper()}:
        score -= 50
    market = str(company.get("market") or "").upper()
    if q.endswith(".HK") and market == "HK":
        score -= 25
    if (q.endswith(".SH") or q.endswith(".SZ") or re.fullmatch(r"\d{6}", q)) and market == "A":
        score -= 25
    if wants_secondary_listing(query):
        if is_secondary_listing(company):
            score -= 70
        else:
            score += 35
        base_query = strip_secondary_suffix(query)
        if base_query and base_query in str(company.get("name") or ""):
            score -= 40
    if is_manual_company(company):
        score += 100
    market_order = {"A": 0, "HK": 1, "US": 2}.get(market, 9)
    return score, market_order, ticker


def live_quote_enabled() -> bool:
    return os.getenv("AI_COMMITTEE_LIVE_QUOTES", "true").lower() in {"1", "true", "yes", "on"}


def quote_max_age_seconds() -> int:
    try:
        return max(30, int(os.getenv("AI_COMMITTEE_QUOTE_MAX_AGE_SECONDS", "300")))
    except ValueError:
        return 300


def refresh_company_quote(conn, company: dict) -> dict:
    if not live_quote_enabled():
        return company
    snapshot = company.get("snapshot") or {}
    raw = snapshot.get("raw_data") or {}
    if is_fresh_quote(raw):
        return company
    quote = fetch_tencent_quote(company) or fetch_eastmoney_quote(company)
    if not quote:
        return company
    insert_quote_snapshot(conn, company, snapshot, quote)
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company["id"],)).fetchone()
    return row_to_company(row, company_snapshot(conn, company["id"]))


def is_fresh_quote(raw: dict) -> bool:
    fetched_at = raw.get("quote_fetched_at")
    if raw.get("quote_source") not in {"Eastmoney", "Tencent Finance"} or not fetched_at:
        return False
    if raw.get("quote_currency") not in {"CNY", "HKD", "USD"}:
        return False
    try:
        parsed = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return age < quote_max_age_seconds()


def fetch_eastmoney_quote(company: dict) -> dict | None:
    secid = eastmoney_secid(company)
    if not secid:
        return None
    fields = ",".join(
        [
            "f43",
            "f44",
            "f45",
            "f46",
            "f48",
            "f57",
            "f58",
            "f59",
            "f60",
            "f116",
            "f117",
            "f162",
            "f167",
            "f169",
            "f170",
            "f292",
        ]
    )
    try:
        with httpx.Client(timeout=4, headers={"User-Agent": "Mozilla/5.0"}) as client:
            response = client.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": secid, "fields": fields},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None
    data = payload.get("data")
    if payload.get("rc") != 0 or not data:
        return None
    precision = int(data.get("f59") or (3 if company.get("market") == "HK" else 2))
    price = eastmoney_scaled(data.get("f43"), precision)
    if price is None:
        return None
    market = (company.get("market") or "").upper()
    return {
        "price": price,
        "previous_close": eastmoney_scaled(data.get("f60"), precision),
        "market_cap": eastmoney_money_to_yi(data.get("f116")),
        "float_market_cap": eastmoney_money_to_yi(data.get("f117")),
        "pe_ratio": eastmoney_ratio(data.get("f162")),
        "pb_ratio": eastmoney_ratio(data.get("f167")),
        "price_change": eastmoney_scaled(data.get("f169"), precision),
        "price_change_pct": eastmoney_ratio(data.get("f170")),
        "turnover": eastmoney_money_to_yi(data.get("f48")),
        "quote_source": "Eastmoney",
        "quote_symbol": display_quote_symbol(company, data.get("f57")),
        "quote_secid": secid,
        "quote_name": data.get("f58") or company.get("name"),
        "quote_currency": {"A": "CNY", "HK": "HKD", "US": "USD"}.get(market, ""),
        "market_cap_unit": {"A": "亿元人民币", "HK": "亿港元", "US": "亿美元"}.get(market, "亿"),
        "quote_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "quote_status": data.get("f292"),
        "precision": precision,
    }


def fetch_tencent_quote(company: dict) -> dict | None:
    symbol = tencent_symbol(company)
    if not symbol:
        return None
    try:
        with httpx.Client(timeout=5, headers={"User-Agent": "Mozilla/5.0"}) as client:
            response = client.get("https://qt.gtimg.cn/q=" + symbol)
            response.raise_for_status()
            text = response.text
    except Exception:
        return None
    match = re.search(r'v_[a-z0-9]+="([^"]+)"', text, flags=re.IGNORECASE)
    if not match:
        return None
    fields = match.group(1).split("~")
    market = (company.get("market") or "").upper()
    if len(fields) < 46:
        return None
    price = to_float(fields[3])
    if price is None:
        return None
    if market == "A":
        market_cap = to_float_at(fields, 45)
        float_market_cap = to_float_at(fields, 44)
        pb_ratio = to_float_at(fields, 46)
        pe_ratio = to_float_at(fields, 52)
        currency = to_string_at(fields, 82) or "CNY"
        quote_time = to_string_at(fields, 30)
    elif market == "HK":
        market_cap = to_float_at(fields, 45)
        float_market_cap = to_float_at(fields, 44)
        pb_ratio = to_float_at(fields, 58) or to_float_at(fields, 43)
        pe_ratio = to_float_at(fields, 39)
        currency = to_string_at(fields, 75) or "HKD"
        quote_time = to_string_at(fields, 30)
    else:
        market_cap = to_float_at(fields, 45) or to_float_at(fields, 53)
        float_market_cap = to_float_at(fields, 44)
        pb_ratio = to_float_at(fields, 51)
        pe_ratio = to_float_at(fields, 65) or to_float_at(fields, 39)
        currency = "USD"
        quote_time = to_string_at(fields, 30)
    return {
        "price": price,
        "previous_close": to_float_at(fields, 4),
        "market_cap": market_cap,
        "float_market_cap": float_market_cap,
        "pe_ratio": pe_ratio,
        "pb_ratio": pb_ratio,
        "price_change": to_float_at(fields, 31),
        "price_change_pct": to_float_at(fields, 32),
        "turnover": round((to_float_at(fields, 37) or 0) / 100000000, 2) if market == "HK" else to_float_at(fields, 37),
        "quote_source": "Tencent Finance",
        "quote_symbol": display_quote_symbol(company, fields[2] if len(fields) > 2 else company.get("ticker")),
        "quote_secid": symbol,
        "quote_name": company.get("name"),
        "quote_currency": currency,
        "market_cap_unit": {"A": "亿元人民币", "HK": "亿港元", "US": "亿美元"}.get(market, "亿"),
        "quote_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "quote_market_time": quote_time,
        "precision": 3 if market == "HK" else 2,
    }


def tencent_symbol(company: dict) -> str | None:
    market = (company.get("market") or "").upper()
    ticker = (company.get("ticker") or "").upper().strip()
    exchange = (company.get("exchange") or "").upper()
    if market == "A":
        code = ticker.split(".")[0]
        if not re.fullmatch(r"\d{6}", code):
            return None
        prefix = "sh" if exchange.startswith("SSE") or code.startswith("6") else "sz"
        return f"{prefix}{code}"
    if market == "HK":
        code = re.sub(r"\D", "", ticker.split(".")[0])
        if not code:
            return None
        return f"hk{code.zfill(5)}"
    if market == "US" and re.fullmatch(r"[A-Z0-9.]{1,12}", ticker):
        return "us" + ticker.replace(".", "")
    return None


def to_float(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_float_at(values: list[str], index: int) -> float | None:
    if index >= len(values):
        return None
    value = to_float(values[index])
    return round(value, 2) if value is not None else None


def to_string_at(values: list[str], index: int) -> str:
    return values[index] if index < len(values) else ""


def insert_quote_snapshot(conn, company: dict, old_snapshot: dict, quote: dict) -> None:
    raw_data = {
        **(old_snapshot.get("raw_data") or {}),
        **quote,
        "listing_market": company.get("market"),
        "listing_exchange": company.get("exchange"),
        "listing_ticker": company.get("ticker"),
    }
    conn.execute(
        """
        INSERT INTO company_snapshots (
            id, company_id, snapshot_date, price, market_cap, pe_ratio, pb_ratio,
            gross_margin, net_margin, roe, raw_data
        )
        VALUES (?, ?, DATE('now'), ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            company["id"],
            quote["price"],
            choose_quote_value(quote.get("market_cap"), old_snapshot.get("market_cap")),
            choose_quote_value(quote.get("pe_ratio"), old_snapshot.get("pe_ratio")),
            choose_quote_value(quote.get("pb_ratio"), old_snapshot.get("pb_ratio")),
            old_snapshot.get("gross_margin"),
            old_snapshot.get("net_margin"),
            old_snapshot.get("roe"),
            to_json(raw_data),
        ),
    )


def choose_quote_value(primary: Any, fallback: Any) -> Any:
    if primary is None:
        return fallback
    if isinstance(primary, (int, float)) and primary <= 0:
        return fallback
    return primary


def eastmoney_secid(company: dict) -> str | None:
    market = (company.get("market") or "").upper()
    ticker = (company.get("ticker") or "").upper().strip()
    exchange = (company.get("exchange") or "").upper()
    if market == "A":
        code = ticker.split(".")[0]
        if not re.fullmatch(r"\d{6}", code):
            return None
        prefix = "1" if exchange.startswith("SSE") or code.startswith("6") else "0"
        return f"{prefix}.{code}"
    if market == "HK":
        code = re.sub(r"\D", "", ticker.split(".")[0])
        if not code:
            return None
        return f"116.{code.zfill(5)}"
    if market == "US":
        symbol = ticker.replace("-", ".")
        if not re.fullmatch(r"[A-Z0-9.]{1,12}", symbol):
            return None
        return f"106.{symbol}"
    return None


def display_quote_symbol(company: dict, raw_code: Any) -> str:
    market = (company.get("market") or "").upper()
    code = str(raw_code or company.get("ticker") or "").upper()
    if market == "HK":
        digits = re.sub(r"\D", "", code)
        return f"{digits.zfill(5)}.HK"
    if market == "A":
        suffix = ".SH" if (company.get("exchange") or "").upper().startswith("SSE") else ".SZ"
        return f"{code}{suffix}" if "." not in code else code
    return code


def eastmoney_scaled(value: Any, precision: int) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return round(float(value) / (10**precision), precision)
    except (TypeError, ValueError):
        return None


def eastmoney_ratio(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return round(float(value) / 100, 2)
    except (TypeError, ValueError):
        return None


def eastmoney_money_to_yi(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return round(float(value) / 100000000, 2)
    except (TypeError, ValueError):
        return None


def default_snapshot(seed: str) -> dict:
    return {
        "price": None,
        "market_cap": None,
        "pe_ratio": None,
        "pb_ratio": None,
        "gross_margin": None,
        "net_margin": None,
        "roe": None,
        "quote_source": "待采集",
        "quote_note": "外部搜索新建证券尚未完成真实行情/财务采集，禁止使用占位估值。"
    }


def save_company_candidate(conn, item: dict) -> dict | None:
    conn.execute(
        """
        INSERT INTO companies (id, name, name_en, ticker, market, exchange, industry, sector, description, tags, aliases)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            name_en = excluded.name_en,
            ticker = excluded.ticker,
            market = excluded.market,
            exchange = excluded.exchange,
            industry = excluded.industry,
            sector = excluded.sector,
            description = excluded.description,
            tags = excluded.tags,
            aliases = excluded.aliases,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            item["id"],
            item["name"],
            item["name_en"],
            item["ticker"],
            item["market"],
            item["exchange"],
            item["industry"],
            item["sector"],
            item["description"],
            to_json([tag for tag in item["tags"] if tag]),
            to_json([alias for alias in item["aliases"] if alias]),
        ),
    )
    if not conn.execute("SELECT 1 FROM company_snapshots WHERE company_id = ?", (item["id"],)).fetchone():
        snap = item["snapshot"]
        conn.execute(
            """
            INSERT INTO company_snapshots (
                id, company_id, snapshot_date, price, market_cap, pe_ratio, pb_ratio,
                gross_margin, net_margin, roe, raw_data
            )
            VALUES (?, ?, DATE('now'), ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                item["id"],
                snap["price"],
                snap["market_cap"],
                snap["pe_ratio"],
                snap["pb_ratio"],
                snap["gross_margin"],
                snap["net_margin"],
                snap["roe"],
                to_json(snap),
            ),
        )
    return company_by_id(conn, item["id"])


def company_tags(company: dict) -> dict:
    tags = company.get("tags", [])
    industry_tags = [company.get("industry"), company.get("sector")] + [tag for tag in tags if tag not in ["美股", "港股", "A股", "中概股"]]
    style_tags = [tag for tag in tags if tag in ["现金流", "护城河", "高ROE", "高增长", "高估值", "低估值", "平台", "品牌", "成长股"]]
    risk_tags = []
    if "高估值" in tags:
        risk_tags.extend(["估值压力", "预期过高"])
    if "周期" in tags or company.get("industry") in ["半导体", "动力电池", "新能源汽车"]:
        risk_tags.extend(["周期波动", "价格竞争"])
    if company.get("market") == "US" and "中概股" in tags:
        risk_tags.extend(["监管", "地缘政治"])
    if not risk_tags:
        risk_tags = ["需求放缓", "竞争加剧", "估值回落"]
    market_map = {"US": "美股", "HK": "港股", "A": "A股"}
    return {
        "industry_tags": list(dict.fromkeys([tag for tag in industry_tags if tag])),
        "style_tags": style_tags or ["质量", "估值", "成长"],
        "risk_tags": list(dict.fromkeys(risk_tags)),
        "market_tags": [market_map.get(company.get("market"), company.get("market")), company.get("sector")],
    }


def expert_fit(company: dict, expert: dict) -> dict:
    profile = expert.get("profile", {})
    tags = company_tags(company)
    all_company_tags = set(tags["industry_tags"] + tags["style_tags"] + tags["risk_tags"] + tags["market_tags"])
    preferred = set(profile.get("preferred_industries", []))
    styles = set(profile.get("style_tags", []))
    markets = set(profile.get("market_tags", []))
    industry_match = len(all_company_tags & preferred) * 18
    style_match = len(all_company_tags & styles) * 12
    market_label = {"US": "美股", "HK": "港股", "A": "A股"}.get(company.get("market"), "")
    market_match = 12 if market_label in markets or "中概股" in markets and "中概股" in all_company_tags else 0
    risk_match = 8 if any(tag in profile.get("strengths", "") for tag in tags["risk_tags"]) else 4
    diversity = 6 if expert.get("category") != "投资大师" else 2
    noise = stable_noise(company["id"], expert["id"], modulo=6)
    score = min(98, 52 + industry_match + style_match + market_match + risk_match + diversity + noise)
    reason_bits = []
    overlap = list((all_company_tags & (preferred | styles)) or preferred)[:3]
    if overlap:
        reason_bits.append("匹配标签：" + "、".join(overlap))
    reason_bits.append(profile.get("investment_philosophy", "其公开框架可补充本次分析"))
    return {
        "expert": expert,
        "fit_score": score,
        "reason": "；".join(reason_bits),
    }


def recommend_experts(conn, company: dict, limit: int = 10) -> list[dict]:
    ranked = [expert_fit(company, expert) for expert in list_experts(conn)]
    if company.get("id") == "co_moutai_a":
        by_id = {item["expert"]["id"]: item for item in ranked}
        curated_ids = [
            "warren_buffett",
            "charlie_munger",
            "duan_yongping",
            "peter_lynch",
            "aswath_damodaran",
            "howard_marks",
            "zhang_lei",
            "li_lu",
            "qiu_guolu",
            "jeremy_grantham",
        ]
        curated = []
        for index, expert_id in enumerate(curated_ids):
            item = by_id[expert_id]
            item = {**item, "fit_score": max(item["fit_score"], 94 - index * 2)}
            curated.append(item)
        return curated[:limit]
    ranked.sort(key=lambda item: item["fit_score"], reverse=True)
    masters = [item for item in ranked if item["expert"]["category"] == "投资大师"]
    specialists = [item for item in ranked if item["expert"]["category"] != "投资大师"]
    mixed = masters[:7] + specialists[:3]
    mixed.sort(key=lambda item: item["fit_score"], reverse=True)
    return mixed[:limit]


def recommend_chairman(company: dict, experts: list[dict]) -> dict:
    if company.get("id") == "co_moutai_a":
        duan = next((expert for expert in experts if expert["id"] == "duan_yongping"), None)
        if duan:
            return {
                "expert": duan,
                "reason": "该公司属于高端消费品牌，长期竞争优势、用户心智、商业模式稳定性和现金流质量是核心问题，段永平的框架匹配度最高。",
                "score": 96.0,
            }
    ranked = []
    for expert in experts:
        fit = expert_fit(company, expert)["fit_score"]
        profile = expert.get("profile", {})
        framework = 12 if any(tag in profile.get("style_tags", []) for tag in company_tags(company)["style_tags"]) else 6
        judgment = 10 if expert["id"] in ["duan_yongping", "warren_buffett", "charlie_munger", "li_lu", "aswath_damodaran"] else 7
        noise = stable_noise(company["ticker"], expert["id"], "chair", modulo=5)
        score = fit * 0.4 + framework * 3 + judgment * 2 + noise
        ranked.append((score, expert))
    ranked.sort(key=lambda item: item[0], reverse=True)
    chair = ranked[0][1]
    return {
        "expert": chair,
        "reason": f"{chair['name']} 对 {company['industry']} / {company['sector']} 的核心问题匹配度最高，能够把商业质量、估值和风险约束整合成可执行判断。",
        "score": round(ranked[0][0], 1),
    }


def make_data_pack(company: dict) -> dict:
    snap = company.get("snapshot", {})
    raw_quote = snap.get("raw_data", {}) or {}
    security_id = f"sec_{slug_key(company.get('market'))}_{slug_key(company.get('ticker'))}"
    run_id = f"run_{date.today().strftime('%Y%m%d')}_{slug_key(company.get('ticker'))}_{hashlib.sha256((company.get('id') or company.get('ticker') or '').encode('utf-8')).hexdigest()[:6]}"
    external_sources = collect_company_sources(company, security_id, run_id)
    company = enrich_company_from_external_profile(company, external_sources)
    tags = company_tags(company)
    snap = merge_collected_financial_metrics(snap, external_sources)
    snap = merge_financial_series_snapshot(snap, external_sources)
    raw_quote = snap.get("raw_data", {}) or {}
    company = {**company, "snapshot": snap}
    price = snap.get("price") or 0
    pe = snap.get("pe_ratio") or 0
    pb = snap.get("pb_ratio") or 0
    gross = snap.get("gross_margin") or 0
    net_margin = snap.get("net_margin") or 0
    roe = snap.get("roe") or 0
    currency = raw_quote.get("quote_currency") or {"A": "CNY", "HK": "HKD", "US": "USD"}.get(company.get("market"), "")
    growth_hint = "高增长" if "高增长" in company.get("tags", []) or pe > 35 else "稳健增长"
    valuation_tone = "估值偏高" if pe > 35 else "估值处于可研究区间" if pe > 15 else "估值偏低但需验证基本面"
    enrich_peer_sources(company, snap, security_id, external_sources)
    evidence_store = build_evidence_store(company, security_id, snap, raw_quote, tags, external_sources.get("evidence", []))
    evidence_index = [
        {
            "evidence_id": item["evidence_id"],
            "category": item["category"],
            "title": item["title"],
            "summary": item["summary"],
            "confidence": item["confidence"],
            "freshness_score": item["freshness_score"],
            "source_provider": item["source_provider"],
            "source_url": item.get("source_url"),
            "source_document_id": item.get("source_document_id"),
        }
        for item in evidence_store
    ]
    valuation_summary = build_valuation_summary(company, snap, currency, external_sources)
    quality = assess_data_quality(company, snap, raw_quote, evidence_store, external_sources)
    financial_evidence_ids = evidence_ids_by_category(evidence_store, "financial_statement")
    filing_evidence_ids = evidence_ids_by_category(evidence_store, "filing")
    news_evidence_ids = evidence_ids_by_category(evidence_store, "news")
    social_evidence_ids = evidence_ids_by_category(evidence_store, "social")
    research_evidence_ids = evidence_ids_by_category(evidence_store, "research")
    peer_evidence_ids = evidence_ids_by_category(evidence_store, "peer")
    technical_evidence_ids = evidence_ids_by_category(evidence_store, "technical")
    valuation_history = parsed_evidence_json(first_evidence_by_category(evidence_store, "valuation_history"), "normalized_value")
    technical_history = parsed_evidence_json(first_evidence_by_category(evidence_store, "technical"), "normalized_value")
    financial_series = external_sources.get("financial_series") or []
    financial_coverage = "、".join(financial_evidence_ids[:4]) if financial_evidence_ids else "暂无真实财报三表证据"
    news_titles = evidence_titles(evidence_store, news_evidence_ids, limit=3)
    social_titles = evidence_titles(evidence_store, social_evidence_ids, limit=2)
    research_titles = evidence_titles(evidence_store, research_evidence_ids, limit=2)
    fundamental = {
        "agent": "财务质量与商业模式研究员",
        "status": "done",
        "role_type": "analyst",
        "evidence_ids": ["ev_profile_identity", "ev_financial_margin_roe", "ev_business_description", "ev_risk_tags"] + financial_evidence_ids[:6] + filing_evidence_ids[:3],
        "revenue_trend": f"{localize_research_term(company['industry'])} 业务维持 {growth_hint}，收入弹性取决于核心产品周期和行业景气。",
        "profit_trend": f"利润率以毛利率 {gross}%、净利率 {net_margin}% 和 ROE {roe}% 为主要观察点。",
        "gross_margin": f"{gross}%",
        "net_margin": f"{net_margin}%",
        "roe": f"{roe}%",
        "roic": f"{max(8, round(roe * 0.72, 1))}%",
        "free_cash_flow": f"现金流判断优先引用真实财报三表证据：{financial_coverage}。若缺失则不得作高置信结论。",
        "debt_level": f"资产负债结构优先引用公告、监管文件和结构化财报原文：{', '.join(filing_evidence_ids[:3]) or '暂无公告正文证据'}。",
        "business_segments": [localize_research_term(company["industry"]), localize_research_term(company["sector"]), "核心主营业务"],
        "key_risks": tags["risk_tags"],
        "management_guidance": "管理层指引只允许来自公告、美国证监会文件、财报或电话会原文；当前资料包已把未采集到的来源列入缺口。",
        "fundamental_summary": build_fundamental_brief(company, snap, financial_evidence_ids, filing_evidence_ids, research_titles, valuation_tone),
        "quality_score": min(92, max(35, int(52 + (roe or 8) * 1.4 + (gross or 20) * 0.35 - (8 if pe > 45 else 0)))),
    }
    sentiment = {
        "agent": "新闻事件与市场情绪研究员",
        "status": "done",
        "role_type": "analyst",
        "evidence_ids": ["ev_sentiment_debate", "ev_risk_tags"] + news_evidence_ids[:5] + social_evidence_ids[:3],
        "news_summary": real_news_summary(news_titles, social_titles),
        "positive_sentiment": ["龙头地位仍被认可", "现金流或增长质量支撑长期关注", "若业绩超预期可能触发重估"],
        "negative_sentiment": tags["risk_tags"][:3],
        "main_debates": real_debate_points(news_titles, social_titles, tags["risk_tags"]),
        "analyst_rating_changes": research_titles or ["未采集到公开研报评级变化，禁止编造券商观点"],
        "social_media_heat": f"已采集社媒证据 {len(social_evidence_ids)} 条来源；不足时只作为弱信号。",
        "sentiment_score": min(88, max(42, int(62 + stable_noise(company["id"], "sentiment", modulo=24) - (8 if pe > 45 else 0)))),
    }
    macro = {
        "agent": "宏观行业与竞争格局研究员",
        "status": "done",
        "role_type": "analyst",
        "evidence_ids": ["ev_macro_industry", "ev_risk_tags"] + news_evidence_ids[:3] + filing_evidence_ids[:2],
        "macro_events": macro_events_from_sources(news_titles, filing_evidence_ids),
        "industry_cycle": build_macro_brief(company, news_titles, filing_evidence_ids),
        "policy_risks": tags["risk_tags"][:3] or ["监管口径变化", "行业景气波动"],
        "regulatory_changes": filing_evidence_ids[:3] or ["暂无公告正文证据，需补采后判断监管变化"],
        "competitive_landscape": f"竞争格局判断优先引用公告、新闻和研报证据；当前已采集新闻 {len(news_evidence_ids)} 条、研报 {len(research_evidence_ids)} 条。",
        "supply_chain_factors": ["原材料价格", "渠道库存", "核心供应商稳定性"],
        "macro_impact_score": min(90, max(35, 66 - (10 if "高估值" in company.get("tags", []) else 0) + stable_noise(company["id"], "macro", modulo=16))),
    }
    technical = build_technical_agent(company, price, pe, technical_history, technical_evidence_ids)
    if not technical:
        technical = {
            "agent": "技术面与交易结构研究员",
            "status": "done",
            "role_type": "analyst",
            "evidence_ids": ["ev_quote_latest", "ev_technical_levels"],
            "price_trend": "中期趋势需要等待成交量确认，短期波动可能围绕业绩和宏观预期展开。",
            "moving_averages": {"20日均线": round(price * 0.98, 2), "60日均线": round(price * 0.94, 2), "120日均线": round(price * 0.9, 2)},
            "volume_analysis": "量能未显示单边一致预期，价格突破需要成交量放大确认。",
            "rsi": str(48 + stable_noise(company["id"], "rsi", modulo=18)),
            "macd": "接近零轴，趋势尚未完全确认。",
            "support_levels": [round(price * 0.92, 2), round(price * 0.85, 2)],
            "resistance_levels": [round(price * 1.08, 2), round(price * 1.16, 2)],
            "volatility": "波动率中等偏高，适合用条件单和分批观察降低时点风险。",
            "technical_score": min(86, max(38, int(58 + stable_noise(company["id"], "technical", modulo=22) - (7 if pe > 40 else 0)))),
        }
    analyst_pack = {
        "run_id": run_id,
        "company": {
            "company_id": company["id"],
            "name": company["name"],
            "ticker": company["ticker"],
            "market": company["market"],
            "exchange": company["exchange"],
            "industry": company["industry"],
            "sector": company["sector"],
            "description": company["description"],
        },
        "security": {
            "security_id": security_id,
            "ticker": company["ticker"],
            "exchange": company["exchange"],
            "market": company["market"],
            "currency": currency,
            "quote_symbol": raw_quote.get("quote_symbol") or display_quote_symbol(company, None),
        },
        "market_snapshot": {
            "price": price,
            "previous_close": raw_quote.get("previous_close"),
            "price_change_pct": raw_quote.get("price_change_pct"),
            "market_cap": snap.get("market_cap"),
            "currency": currency,
            "as_of": raw_quote.get("quote_fetched_at") or snap.get("snapshot_date") or date.today().isoformat(),
            "source_evidence_ids": ["ev_quote_latest", "ev_ownership_market_cap"],
        },
        "financial_summary": {
            "gross_margin": gross,
            "net_margin": net_margin,
            "roe": roe,
            "pb_ratio": pb,
            "pe_ratio": pe,
            "revenue": snap.get("revenue"),
            "net_income": snap.get("net_income"),
            "operating_cash_flow": snap.get("operating_cash_flow"),
            "free_cash_flow": snap.get("free_cash_flow"),
            "total_assets": snap.get("total_assets"),
            "total_liabilities": snap.get("total_liabilities"),
            "shareholders_equity": snap.get("shareholders_equity"),
            "debt_to_asset_ratio": snap.get("debt_to_asset_ratio"),
            "eps_diluted": snap.get("eps_diluted"),
            "diluted_shares": snap.get("diluted_shares"),
            "source_evidence_ids": ["ev_financial_margin_roe", "ev_valuation_pe_pb"],
        },
        "financial_series": financial_series,
        "valuation_summary": valuation_summary,
        "valuation_history": valuation_history,
        "peer_summary": {
            "status": "done" if peer_evidence_ids else "missing",
            "summary": peer_summary_text(external_sources.get("peer_data") or []),
            "source_evidence_ids": peer_evidence_ids or ["ev_macro_industry"],
        },
        "news_summary": {
            "status": "done" if news_evidence_ids else "missing",
            "summary": sentiment["news_summary"],
            "source_evidence_ids": sentiment["evidence_ids"],
        },
        "macro_summary": {
            "summary": macro["industry_cycle"],
            "source_evidence_ids": macro["evidence_ids"],
        },
        "risk_summary": {
            "risk_tags": tags["risk_tags"],
            "source_evidence_ids": ["ev_risk_tags"],
        },
        "evidence_index": evidence_index,
        "user_focus": ["估值是否合理", "增长能否兑现", "风险是否补偿充分"],
    }
    source_records = [
        f"{item['evidence_id']} · {item['source_provider']} · {item['title']}"
        for item in evidence_store
    ]
    data_pack = {
        "schema_version": V2_DATA_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "data_plan": build_data_plan(company, raw_quote, evidence_store, quality, external_sources),
        "data_quality": quality,
        "collection_summary": build_collection_summary(external_sources),
        "fundamental": fundamental,
        "sentiment": sentiment,
        "macro": macro,
        "technical": technical,
        "analyst_pack": analyst_pack,
        "market_snapshot": analyst_pack["market_snapshot"],
        "financial_summary": analyst_pack["financial_summary"],
        "financial_series": financial_series,
        "valuation_summary": valuation_summary,
        "valuation_history": valuation_history,
        "risk_summary": analyst_pack["risk_summary"],
        "evidence_store": evidence_store,
        "evidence_index": evidence_index,
        "chart_specs": build_chart_specs(company, analyst_pack, technical),
        "decision_log": {
            "run_id": run_id,
            "ticker": company["ticker"],
            "analysis_date": date.today().isoformat(),
            "current_price": price,
            "fair_value_base": valuation_summary["fair_value_range"]["base"],
            "main_thesis": f"{company['name']} 的核心分歧是增长质量、估值隐含预期和风险补偿。",
            "monitoring_indicators": ["收入增速", "毛利率", "ROE", "估值分位", "行业竞争格局"],
            "review_date": review_date_iso(90),
        },
        "source_records": source_records,
    }
    scorecard = build_company_scorecard(company, data_pack)
    data_pack["scorecard"] = scorecard
    data_pack["analyst_pack"]["scorecard"] = {
        "scoring_version": scorecard.get("scoring_version"),
        "data_quality_score": scorecard.get("data_quality_score"),
        "company_quality_score": scorecard.get("company_quality_score"),
        "valuation_attractiveness_score": scorecard.get("valuation_attractiveness_score"),
        "investment_action_score": scorecard.get("investment_action_score"),
        "final_action": scorecard.get("final_action"),
        "bucket_scores": scorecard.get("bucket_scores"),
        "valuation_bucket_scores": scorecard.get("valuation_bucket_scores"),
        "red_flags": scorecard.get("red_flags"),
        "missing_metrics": scorecard.get("missing_metrics"),
    }
    return data_pack


def slug_key(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def review_date_iso(days: int) -> str:
    return (date.fromordinal(date.today().toordinal() + days)).isoformat()


def merge_collected_financial_metrics(snapshot: dict, external_sources: dict | None) -> dict:
    metrics = (external_sources or {}).get("financial_metrics") or {}
    if not metrics:
        return snapshot
    merged = dict(snapshot or {})
    raw = dict(merged.get("raw_data") or {})
    field_map = {
        "gross_margin": "gross_margin",
        "net_margin": "net_margin",
        "roe": "roe",
        "pe_ratio": "pe_ratio",
        "pb_ratio": "pb_ratio",
        "market_cap": "market_cap",
        "revenue": "revenue",
        "net_income": "net_income",
        "operating_cash_flow": "operating_cash_flow",
        "free_cash_flow": "free_cash_flow",
        "total_assets": "total_assets",
        "total_liabilities": "total_liabilities",
        "shareholders_equity": "shareholders_equity",
        "eps_diluted": "eps_diluted",
        "diluted_shares": "diluted_shares",
        "debt_to_asset_ratio": "debt_to_asset_ratio",
    }
    for source_key, target_key in field_map.items():
        value = to_float(metrics.get(source_key))
        if value is not None and value != 0:
            merged[target_key] = round(value, 2)
    raw.update(
        {
            "financial_source": metrics.get("source_provider"),
            "financial_period": metrics.get("period"),
            "financial_report_type": metrics.get("report_type"),
            "financial_fetched_at": metrics.get("fetched_at"),
            "financial_currency": metrics.get("currency"),
            "financial_source_url": metrics.get("source_url"),
        }
    )
    merged["raw_data"] = raw
    return merged


def merge_financial_series_snapshot(snapshot: dict, external_sources: dict | None) -> dict:
    series = (external_sources or {}).get("financial_series") or []
    if not isinstance(series, list) or not series:
        return snapshot
    latest = latest_financial_series_row(series)
    if not latest:
        return snapshot
    merged = dict(snapshot or {})
    raw = dict(merged.get("raw_data") or {})
    fields = [
        "revenue",
        "net_income",
        "operating_cash_flow",
        "free_cash_flow",
        "gross_margin",
        "net_margin",
        "roe",
        "total_assets",
        "total_liabilities",
        "shareholders_equity",
        "debt_to_asset_ratio",
        "eps_diluted",
        "diluted_shares",
    ]
    for field in fields:
        if merged.get(field) not in (None, "", 0):
            continue
        value = to_float(latest.get(field))
        if value is not None and value != 0:
            merged[field] = round(value, 2)
    raw.setdefault("financial_source", latest.get("source_provider") or "财务序列回填")
    raw.setdefault("financial_period", latest.get("period"))
    raw.setdefault("financial_report_type", latest.get("report_type"))
    merged["raw_data"] = raw
    return merged


def latest_financial_series_row(series: list[dict]) -> dict:
    rows = [item for item in series if isinstance(item, dict)]
    if not rows:
        return {}
    annual_rows = [
        item
        for item in rows
        if str(item.get("period") or "").endswith("12-31")
        or "年报" in str(item.get("report_type") or "")
        or "annual" in str(item.get("report_type") or "").lower()
        or str(item.get("report_type") or "").lower() in {"fy", "year", "10-k"}
    ]
    candidates = annual_rows or rows
    return max(candidates, key=lambda item: str(item.get("period") or ""))


def enrich_company_from_external_profile(company: dict, external_sources: dict | None) -> dict:
    profile = (external_sources or {}).get("company_profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    inferred = infer_company_profile_from_text(company)
    if inferred:
        profile = {**inferred, **{key: value for key, value in profile.items() if value not in (None, "", [])}}
    if not profile:
        return company
    enriched = dict(company or {})
    profile_industry = str(profile.get("industry") or "").strip()
    profile_sector = str(profile.get("sector") or "").strip()
    profile_description = str(profile.get("description") or "").strip()
    current_industry = str(enriched.get("industry") or "")
    current_sector = str(enriched.get("sector") or "")
    should_override_industry = not current_industry or "待补充" in current_industry or profile.get("confidence", 0) >= 0.82
    should_override_sector = not current_sector or "外部识别" in current_sector or profile.get("confidence", 0) >= 0.82
    if profile_industry and should_override_industry:
        enriched["industry"] = profile_industry
    if profile_sector and should_override_sector:
        enriched["sector"] = profile_sector
    if profile_description and (not enriched.get("description") or profile.get("confidence", 0) >= 0.82):
        enriched["description"] = profile_description
    merged_tags = list(enriched.get("tags") or [])
    for tag in profile.get("tags") or []:
        if tag and tag not in merged_tags:
            merged_tags.append(tag)
    if merged_tags:
        enriched["tags"] = merged_tags
    aliases = list(enriched.get("aliases") or [])
    for alias in [profile.get("sic_description"), profile.get("industry"), profile.get("sector")]:
        if alias and alias not in aliases:
            aliases.append(alias)
    if aliases:
        enriched["aliases"] = aliases
    return enriched


def infer_company_profile_from_text(company: dict) -> dict:
    text = " ".join(
        str(item or "")
        for item in [
            company.get("name"),
            company.get("name_en"),
            company.get("industry"),
            company.get("sector"),
            company.get("description"),
            " ".join(company.get("tags") or []),
        ]
    )
    rules = [
        (["农夫山泉", "NONGFU", "饮料", "瓶装水"], "饮料", "消费品牌", ["饮料", "消费", "品牌", "现金流"]),
        (["泡泡玛特", "POP MART", "潮玩", "IP"], "潮流玩具/IP消费", "可选消费", ["潮流玩具", "IP", "消费", "品牌"]),
        (["小米", "XIAOMI", "手机", "IoT"], "消费电子/汽车", "科技硬件", ["消费电子", "IoT", "新能源汽车"]),
    ]
    for keywords, industry, sector, tags in rules:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return {
                "industry": industry,
                "sector": sector,
                "description": company.get("description") or f"{company.get('name')} 属于 {industry} / {sector}，需要结合公告、财报和新闻继续验证。",
                "tags": tags,
                "confidence": 0.72,
            }
    return {}


def freshness_from_any(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value)[:10])
        days = max(0, (datetime.now() - parsed).days)
    except Exception:
        return None
    if days <= 120:
        return 0.92
    if days <= 365:
        return 0.82
    if days <= 730:
        return 0.68
    return 0.55


def build_evidence_store(company: dict, security_id: str, snap: dict, raw_quote: dict, tags: dict, external_evidence: list[dict] | None = None) -> list[dict]:
    quote_provider = raw_quote.get("quote_source") or snap.get("quote_source") or "本地快照"
    quote_freshness = quote_freshness_score(raw_quote, snap)
    quote_confidence = evidence_confidence(quote_provider, quote_freshness, 0.9 if snap.get("price") else 0.45, 0.65)
    market = company.get("market") or ""
    currency = raw_quote.get("quote_currency") or {"A": "CNY", "HK": "HKD", "US": "USD"}.get(market, "")
    financial_provider = raw_quote.get("financial_source") or "本地财务快照"
    financial_source_url = "local://company_snapshots"
    financial_freshness = 0.72
    if financial_provider != "本地财务快照":
        financial_source_url = raw_quote.get("financial_source_url") or quote_source_url(company, raw_quote)
        financial_freshness = freshness_from_any(raw_quote.get("financial_period")) or 0.86
    base_evidence = [
        evidence_item(
            company,
            security_id,
            "profile_identity",
            "industry",
            "公司主体与上市证券识别",
            f"{company['name']} / {company.get('ticker')} / {company.get('market')} / {company.get('exchange')}，行业为 {company.get('industry')}。",
            raw_value=company.get("ticker"),
            normalized_value=raw_quote.get("quote_symbol") or display_quote_symbol(company, None),
            unit="ticker",
            source_provider="本地公司库",
            source_url="local://companies",
            confidence=evidence_confidence("本地公司库", 0.88, 0.95, 0.55),
            freshness_score=0.88,
        ),
        evidence_item(
            company,
            security_id,
            "quote_latest",
            "price",
            "最新证券报价",
            f"最新股价 {snap.get('price', 'N/A')} {currency}，报价证券 {raw_quote.get('quote_symbol') or display_quote_symbol(company, None)}，来源 {quote_provider}。",
            raw_value=snap.get("price"),
            normalized_value=snap.get("price"),
            unit=currency,
            period=raw_quote.get("quote_market_time") or snap.get("snapshot_date") or date.today().isoformat(),
            date=raw_quote.get("quote_fetched_at") or snap.get("snapshot_date") or date.today().isoformat(),
            source_provider=quote_provider,
            source_url=quote_source_url(company, raw_quote),
            confidence=quote_confidence,
            freshness_score=quote_freshness,
        ),
        evidence_item(
            company,
            security_id,
            "ownership_market_cap",
            "metric",
            "市值与流通市值口径",
            f"当前市值约 {snap.get('market_cap', 'N/A')} {raw_quote.get('market_cap_unit', '')}；该字段用于估值与流动性判断。",
            raw_value=snap.get("market_cap"),
            normalized_value=snap.get("market_cap"),
            unit=raw_quote.get("market_cap_unit", ""),
            source_provider=quote_provider,
            source_url=quote_source_url(company, raw_quote),
            confidence=evidence_confidence(quote_provider, quote_freshness, 0.78 if snap.get("market_cap") else 0.4, 0.58),
            freshness_score=quote_freshness,
        ),
        evidence_item(
            company,
            security_id,
            "valuation_pe_pb",
            "metric",
            "相对估值倍数",
            f"市盈率 {snap.get('pe_ratio', 'N/A')} 倍，市净率 {snap.get('pb_ratio', 'N/A')} 倍；用于判断价格是否已反映较强增长预期。",
            raw_value=json.dumps({"pe_ratio": snap.get("pe_ratio"), "pb_ratio": snap.get("pb_ratio")}, ensure_ascii=False),
            normalized_value=json.dumps({"pe_ratio": snap.get("pe_ratio"), "pb_ratio": snap.get("pb_ratio")}, ensure_ascii=False),
            unit="multiple",
            source_provider=quote_provider,
            source_url=quote_source_url(company, raw_quote),
            confidence=evidence_confidence(quote_provider, quote_freshness, 0.8 if snap.get("pe_ratio") else 0.45, 0.6),
            freshness_score=quote_freshness,
        ),
        evidence_item(
            company,
            security_id,
            "financial_margin_roe",
            "metric",
            "盈利能力快照",
            f"毛利率 {snap.get('gross_margin', 'N/A')}%，净利率 {snap.get('net_margin', 'N/A')}%，ROE {snap.get('roe', 'N/A')}%。",
            raw_value=json.dumps({"gross_margin": snap.get("gross_margin"), "net_margin": snap.get("net_margin"), "roe": snap.get("roe")}, ensure_ascii=False),
            normalized_value=json.dumps({"gross_margin": snap.get("gross_margin"), "net_margin": snap.get("net_margin"), "roe": snap.get("roe")}, ensure_ascii=False),
            unit="percent",
            period=raw_quote.get("financial_period"),
            source_provider=financial_provider,
            source_url=financial_source_url,
            confidence=evidence_confidence(financial_provider, financial_freshness, 0.86 if financial_provider != "本地财务快照" else 0.76, 0.72 if financial_provider != "本地财务快照" else 0.52),
            freshness_score=financial_freshness,
        ),
        evidence_item(
            company,
            security_id,
            "business_description",
            "industry",
            "商业模式与行业定位",
            company.get("description") or f"{company['name']} 属于 {company.get('industry')} / {company.get('sector')}。",
            raw_value=company.get("description"),
            normalized_value=company.get("industry"),
            unit="text",
            source_provider="本地公司库",
            source_url="local://companies",
            confidence=evidence_confidence("本地公司库", 0.82, 0.88, 0.55),
            freshness_score=0.82,
        ),
        evidence_item(
            company,
            security_id,
            "risk_tags",
            "calculation",
            "风险标签与待验证事项",
            f"系统根据行业、市场和公司标签识别风险：{'、'.join(tags['risk_tags'])}。",
            raw_value=json.dumps(company.get("tags", []), ensure_ascii=False),
            normalized_value=json.dumps(tags["risk_tags"], ensure_ascii=False),
            unit="tags",
            source_provider="系统规则计算",
            source_url="local://rules/risk_tags",
            confidence=evidence_confidence("系统规则计算", 0.8, 0.86, 0.5),
            freshness_score=0.8,
        ),
        evidence_item(
            company,
            security_id,
            "macro_industry",
            "macro",
            "宏观行业变量",
            f"{company.get('industry')} 的核心外部变量包括利率、需求周期、政策口径和竞争格局。",
            raw_value=company.get("industry"),
            normalized_value=json.dumps(tags["market_tags"], ensure_ascii=False),
            unit="text",
            source_provider="系统行业框架",
            source_url="local://frameworks/macro_industry",
            confidence=evidence_confidence("系统行业框架", 0.78, 0.72, 0.45),
            freshness_score=0.78,
        ),
        evidence_item(
            company,
            security_id,
            "sentiment_debate",
            "sentiment",
            "市场分歧议题",
            "系统规则只提供待验证分歧主题；真实新闻和社媒证据由外部采集适配器补充，缺失时不得作高置信情绪结论。",
            raw_value=json.dumps(tags["risk_tags"], ensure_ascii=False),
            normalized_value="bull_bear_debate_topics",
            unit="text",
            source_provider="系统情绪框架",
            source_url="local://frameworks/sentiment",
            confidence=evidence_confidence("系统情绪框架", 0.65, 0.62, 0.35),
            freshness_score=0.65,
        ),
        evidence_item(
            company,
            security_id,
            "technical_levels",
            "calculation",
            "技术面支撑压力估算",
            f"基于当前价格估算 MA20/MA60/MA120、支撑位和压力位，作为交易结构辅助，不替代基本面判断。",
            raw_value=snap.get("price"),
            normalized_value=json.dumps({"ma20": round((snap.get("price") or 0) * 0.98, 2), "ma60": round((snap.get("price") or 0) * 0.94, 2)}, ensure_ascii=False),
            unit=currency,
            source_provider="系统技术面计算",
            source_url="local://calculations/technical_levels",
            confidence=evidence_confidence("系统技术面计算", quote_freshness, 0.8 if snap.get("price") else 0.4, 0.52),
            freshness_score=quote_freshness,
        ),
    ]
    return dedupe_evidence(base_evidence + (external_evidence or []))


def enrich_peer_sources(company: dict, snap: dict, security_id: str, external_sources: dict) -> None:
    peers = collect_peer_data(company, limit=8)
    if not peers:
        external_sources.setdefault("peer_data", [])
        external_sources.setdefault("gaps", []).append("未匹配到本地同业估值样本")
        return
    peer_payload = {
        "target": {
            "name": company.get("name"),
            "ticker": company.get("ticker"),
            "market": company.get("market"),
            "industry": company.get("industry"),
            "sector": company.get("sector"),
            "pe_ratio": snap.get("pe_ratio"),
            "pb_ratio": snap.get("pb_ratio"),
            "roe": snap.get("roe"),
            "gross_margin": snap.get("gross_margin"),
        },
        "peers": peers,
        "statistics": peer_statistics(peers),
        "method": "same-market industry/sector/tag matched peer snapshot",
    }
    item = {
        "evidence_id": "ev_peer_comparable_set",
        "company_id": company["id"],
        "security_id": security_id,
        "category": "peer",
        "title": "同业估值与质量样本",
        "summary": peer_summary_text(peers),
        "raw_value": json.dumps(peer_payload, ensure_ascii=False),
        "normalized_value": json.dumps(peer_payload["statistics"], ensure_ascii=False),
        "unit": "peer_snapshot",
        "period": date.today().isoformat(),
        "date": utc_now_iso(),
        "source_provider": "本地公司库 + 行情估值快照",
        "source_url": "local://companies/peer_comparables",
        "source_document_id": "",
        "extracted_quote": text_excerpt(json.dumps(peer_payload, ensure_ascii=False, indent=2)),
        "confidence": 0.74 if len(peers) >= 3 else 0.62,
        "freshness_score": 0.82,
        "created_at": utc_now_iso(),
    }
    external_sources["peer_data"] = peers
    external_sources.setdefault("evidence", []).append(item)


def collect_peer_data(company: dict, limit: int = 8) -> list[dict]:
    target_tags = set(company.get("tags") or [])
    market_tags = {"美股", "港股", "A股", "港股映射", "A股映射", "中概股"}
    useful_target_tags = target_tags - market_tags
    peers: list[tuple[int, dict]] = []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.*, s.price, s.market_cap, s.pe_ratio, s.pb_ratio, s.gross_margin,
                   s.net_margin, s.roe, s.snapshot_date, s.raw_data
            FROM companies c
            LEFT JOIN company_snapshots s ON s.company_id = c.id
            WHERE c.id <> ? AND c.market = ?
            """,
            (company.get("id"), company.get("market")),
        ).fetchall()
    for row in rows:
        peer = dict(row)
        peer_tags = set(from_json(peer.get("tags"), []) or [])
        useful_peer_tags = peer_tags - market_tags
        score = 0
        if peer.get("industry") and peer.get("industry") == company.get("industry"):
            score += 6
        if peer.get("sector") and peer.get("sector") == company.get("sector"):
            score += 4
        overlap = useful_target_tags & useful_peer_tags
        score += min(4, len(overlap)) * 2
        if not score and broad_sector_key(peer) == broad_sector_key(company):
            score += 2
        if score <= 0:
            continue
        peers.append((score, peer))
    if len(peers) < 3:
        with get_conn() as conn:
            fallback_rows = conn.execute(
                """
                SELECT c.*, s.price, s.market_cap, s.pe_ratio, s.pb_ratio, s.gross_margin,
                       s.net_margin, s.roe, s.snapshot_date, s.raw_data
                FROM companies c
                LEFT JOIN company_snapshots s ON s.company_id = c.id
                WHERE c.id <> ? AND c.market = ?
                ORDER BY COALESCE(s.market_cap, 0) DESC
                LIMIT 20
                """,
                (company.get("id"), company.get("market")),
            ).fetchall()
        seen = {peer["id"] for _, peer in peers}
        for row in fallback_rows:
            peer = dict(row)
            if peer["id"] in seen:
                continue
            peers.append((1, peer))
            seen.add(peer["id"])
            if len(peers) >= 3:
                break
    peers.sort(key=lambda item: (item[0], float(item[1].get("market_cap") or 0)), reverse=True)
    output = []
    for score, peer in peers[:limit]:
        output.append(
            {
                "company_id": peer.get("id"),
                "name": peer.get("name"),
                "ticker": peer.get("ticker"),
                "market": peer.get("market"),
                "industry": peer.get("industry"),
                "sector": peer.get("sector"),
                "match_score": score,
                "price": peer.get("price"),
                "market_cap": peer.get("market_cap"),
                "pe_ratio": peer.get("pe_ratio"),
                "pb_ratio": peer.get("pb_ratio"),
                "gross_margin": peer.get("gross_margin"),
                "net_margin": peer.get("net_margin"),
                "roe": peer.get("roe"),
                "snapshot_date": peer.get("snapshot_date"),
            }
        )
    return output


def text_excerpt(value: str, max_chars: int = 1800) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars]


def broad_sector_key(company: dict) -> str:
    text = f"{company.get('industry', '')} {company.get('sector', '')} {' '.join(company.get('tags') or [])}"
    for key in ["互联网", "半导体", "AI", "云", "电商", "汽车", "电池", "银行", "保险", "白酒", "消费", "医药", "地产", "硬件", "软件"]:
        if key in text:
            return key
    return str(company.get("sector") or company.get("industry") or "")


def peer_statistics(peers: list[dict]) -> dict:
    def median_metric(key: str) -> float | None:
        values = sorted(float(peer[key]) for peer in peers if isinstance(peer.get(key), (int, float)) and peer.get(key) not in (0, None))
        if not values:
            return None
        mid = len(values) // 2
        return round(values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2, 2)

    return {
        "peer_count": len(peers),
        "median_pe": median_metric("pe_ratio"),
        "median_pb": median_metric("pb_ratio"),
        "median_roe": median_metric("roe"),
        "median_gross_margin": median_metric("gross_margin"),
    }


def peer_summary_text(peers: list[dict]) -> str:
    if not peers:
        return "未形成同业样本；需要补充同行公司估值、盈利能力和市值数据。"
    stats = peer_statistics(peers)
    names = "、".join(str(peer.get("name")) for peer in peers[:5])
    return (
        f"已形成 {len(peers)} 家同业样本：{names}。"
        f"样本 PE 中位数 {stats.get('median_pe') or 'N/A'}，PB 中位数 {stats.get('median_pb') or 'N/A'}，"
        f"ROE 中位数 {stats.get('median_roe') or 'N/A'}。"
    )


def evidence_item(
    company: dict,
    security_id: str,
    key: str,
    category: str,
    title: str,
    summary: str,
    raw_value: Any = None,
    normalized_value: Any = None,
    unit: str | None = None,
    period: str | None = None,
    date: str | None = None,
    source_provider: str = "系统",
    source_url: str | None = None,
    source_document_id: str | None = None,
    extracted_quote: str | None = None,
    confidence: float = 0.6,
    freshness_score: float = 0.6,
) -> dict:
    return {
        "evidence_id": f"ev_{key}",
        "company_id": company["id"],
        "security_id": security_id,
        "category": category,
        "title": title,
        "summary": summary,
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "unit": unit,
        "period": period,
        "date": date or utc_now_iso(),
        "source_provider": source_provider,
        "source_url": source_url,
        "source_document_id": source_document_id,
        "extracted_quote": extracted_quote,
        "confidence": round(max(0, min(1, confidence)), 2),
        "freshness_score": round(max(0, min(1, freshness_score)), 2),
        "created_at": utc_now_iso(),
    }


def quote_freshness_score(raw_quote: dict, snap: dict) -> float:
    fetched_at = raw_quote.get("quote_fetched_at")
    if fetched_at:
        try:
            parsed = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
            if age_hours <= 2:
                return 0.98
            if age_hours <= 24:
                return 0.9
            if age_hours <= 72:
                return 0.72
        except ValueError:
            pass
    if snap.get("snapshot_date") == date.today().isoformat():
        return 0.72
    return 0.55


def evidence_confidence(source_provider: str, freshness_score: float, completeness_score: float, cross_validation_score: float) -> float:
    provider = source_provider or ""
    if provider in {"SEC", "SEC Companyfacts", "SEC EDGAR", "HKEXnews", "交易所公告", "官方公告"}:
        source_score = 0.95
    elif provider in {"Eastmoney", "Tencent Finance", "Eastmoney HK F10", "Eastmoney Financials"}:
        source_score = 0.82
    elif provider in {"本地公司库", "本地财务快照"}:
        source_score = 0.66
    elif provider.startswith("系统"):
        source_score = 0.56
    else:
        source_score = 0.5
    return source_score * 0.35 + freshness_score * 0.25 + cross_validation_score * 0.25 + completeness_score * 0.15


def quote_source_url(company: dict, raw_quote: dict) -> str:
    provider = raw_quote.get("quote_source")
    if provider == "Tencent Finance":
        symbol = raw_quote.get("quote_secid") or tencent_symbol(company)
        return f"https://gu.qq.com/{symbol}" if symbol else "https://gu.qq.com/"
    if provider == "Eastmoney":
        secid = raw_quote.get("quote_secid") or eastmoney_secid(company)
        return f"https://quote.eastmoney.com/unify/r/{secid}" if secid else "https://quote.eastmoney.com/"
    return "local://company_snapshots"


def assess_data_quality(company: dict, snap: dict, raw_quote: dict, evidence_store: list[dict], external_sources: dict | None = None) -> dict:
    external_sources = external_sources or {}
    required = ["price", "market_cap", "pe_ratio", "pb_ratio", "gross_margin", "roe"]
    present = [key for key in required if snap.get(key) not in (None, "", 0)]
    missing = [key for key in required if key not in present]
    category_labels = {
        "news": "新闻全文",
        "filings": "公告/监管文件正文",
        "financial_statements": "财报三表",
        "peer_data": "同业数据覆盖",
        "valuation_history": "历史估值分位",
    }
    for key, label in category_labels.items():
        if not external_sources.get(key):
            missing.append(label)
    freshness = quote_freshness_score(raw_quote, snap)
    avg_confidence = sum(item["confidence"] for item in evidence_store) / max(1, len(evidence_store))
    completeness = len(present) / len(required)
    external_coverage = sum(1 for key in category_labels if external_sources.get(key)) / len(category_labels)
    score = round((avg_confidence * 0.35 + freshness * 0.2 + completeness * 0.2 + external_coverage * 0.25) * 100, 1)
    gate = build_data_quality_gate(company, snap, raw_quote, evidence_store, external_sources, freshness)
    critical_missing = [item for item in missing if item in {"财报三表", "公告/监管文件正文", "新闻全文"}]
    return {
        "overall_score": score,
        "usable_for_decision": gate["passed"],
        "dqs_mode": "gate",
        "dqs_version": DATA_QUALITY_GATE_VERSION,
        "dqs_passed": gate["passed"],
        "dqs_status": "passed" if gate["passed"] else "failed",
        "dqs_requirements": gate["requirements"],
        "dqs_blocking_items": gate["blocking_items"],
        "dqs_summary": gate["summary"],
        "legacy_overall_score": score,
        "missing_data": sorted(set(missing)),
        "data_conflicts": [],
        "freshness_score": round(freshness, 2),
        "evidence_count": len(evidence_store),
        "source_mix": sorted({item["source_provider"] for item in evidence_store}),
        "document_store": external_sources.get("documents_dir"),
        "collection_gaps": external_sources.get("gaps", []),
        "notes": [
            "真实采集适配器会保存新闻、社媒、公告、财报和研报的原始文本/JSON/PDF；未采集到的来源必须作为缺口进入后续分析。",
            f"本次报价市场为 {company.get('market')} / {company.get('exchange')}，多地上市公司请优先选择目标交易所对应证券。",
        ],
    }


def build_data_quality_gate(company: dict, snap: dict, raw_quote: dict, evidence_store: list[dict], external_sources: dict, freshness: float) -> dict:
    collection = {
        "news": external_sources.get("news") or [],
        "social": external_sources.get("social") or [],
        "filings": external_sources.get("filings") or [],
        "financial_statements": external_sources.get("financial_statements") or [],
        "research_reports": external_sources.get("research_reports") or [],
        "peer_data": external_sources.get("peer_data") or [],
        "technical_history": external_sources.get("technical_history") or [],
    }
    source_mix = sorted({str(item.get("source_provider") or "") for item in evidence_store if item.get("source_provider")})
    authoritative_financials = official_financial_source_items(collection["financial_statements"])
    filing_or_official_financials = collection["filings"] + authoritative_financials
    requirements = [
        data_requirement(
            "quote_reliability",
            "价格行情可信度",
            bool(snap.get("price")) and bool(raw_quote.get("quote_source") or raw_quote.get("quote_fetched_at")),
            "必须有可追溯报价来源、价格和采集时间。",
            pick_gate_evidence(evidence_store, ["ev_quote_latest"]),
            raw_quote.get("quote_source") or "本地快照",
            {"price": snap.get("price"), "fetched_at": raw_quote.get("quote_fetched_at")},
        ),
        data_requirement(
            "financial_completeness",
            "财务数据完整度",
            len(collection["financial_statements"]) >= 1,
            "必须采集到财报三表或结构化财务原文，才能进入公司质量评分。",
            evidence_ids_by_category(evidence_store, "financial_statement")[:4],
            providers_for(collection["financial_statements"]),
            {"count": len(collection["financial_statements"])},
        ),
        data_requirement(
            "filing_coverage",
            "财报/公告原文覆盖",
            len(filing_or_official_financials) >= 1,
            "必须有公告、年报、监管文件、交易所披露正文，或官方结构化财报原文。",
            (evidence_ids_by_category(evidence_store, "filing") + [str(item.get("evidence_id")) for item in authoritative_financials])[:4],
            providers_for(filing_or_official_financials),
            {"count": len(collection["filings"]), "official_financial_count": len(authoritative_financials)},
        ),
        data_requirement(
            "event_coverage",
            "新闻/事件覆盖",
            len(collection["news"]) >= 1 or (len(collection["filings"]) >= 1 and len(collection["social"]) >= 1),
            "必须至少有一条可回溯新闻；若新闻源临时不可用，公告/交易所披露叠加社媒事件也可作为事件覆盖。",
            (evidence_ids_by_category(evidence_store, "news") + evidence_ids_by_category(evidence_store, "filing") + evidence_ids_by_category(evidence_store, "social"))[:4],
            providers_for(collection["news"] + collection["filings"] + collection["social"]),
            {"news_count": len(collection["news"]), "filing_count": len(collection["filings"]), "social_count": len(collection["social"])},
        ),
        data_requirement(
            "peer_coverage",
            "同业数据覆盖",
            len(collection["peer_data"]) >= 3,
            "必须形成至少 3 家同市场同业样本，包含估值、盈利能力和市值快照；研报只作为增强来源。",
            (evidence_ids_by_category(evidence_store, "peer") + evidence_ids_by_category(evidence_store, "research"))[:4],
            providers_for(collection["peer_data"]) or ["本地公司库 + 行情估值快照"],
            {"peer_count": len(collection["peer_data"]), "research_report_count": len(collection["research_reports"]), "adapter": "peer_comparable_snapshot"},
        ),
        data_requirement(
            "freshness",
            "数据新鲜度",
            freshness >= 0.45,
            "报价或关键资料必须足够新，避免用陈旧快照做动作判断。",
            pick_gate_evidence(evidence_store, ["ev_quote_latest", "ev_technical_history"]),
            raw_quote.get("quote_fetched_at") or snap.get("snapshot_date") or "",
            {"freshness_score": round(freshness, 2)},
        ),
        data_requirement(
            "cross_validation",
            "交叉验证一致性",
            len(source_mix) >= 3 and len(evidence_store) >= 6,
            "必须至少有三类来源和六条证据，降低单源错误。",
            [item.get("evidence_id") for item in evidence_store[:6] if item.get("evidence_id")],
            source_mix,
            {"source_count": len(source_mix), "evidence_count": len(evidence_store)},
        ),
    ]
    blocking = [item["label"] for item in requirements if not item["passed"]]
    return {
        "passed": not blocking,
        "requirements": requirements,
        "blocking_items": blocking,
        "summary": "DQS 已完成，允许进入投委会分析。" if not blocking else f"DQS 未完成，需补齐：{'、'.join(blocking)}。",
        "version": DATA_QUALITY_GATE_VERSION,
    }


def data_requirement(key: str, label: str, passed: bool, requirement: str, evidence_ids: list[str], source: Any, details: dict) -> dict:
    return {
        "key": key,
        "label": label,
        "passed": bool(passed),
        "required": True,
        "requirement": requirement,
        "evidence_ids": [item for item in evidence_ids if item],
        "source": source,
        "details": details,
    }


def providers_for(items: list[dict]) -> list[str]:
    return sorted({str(item.get("source_provider") or "unknown") for item in items})


def official_financial_source_items(items: list[dict]) -> list[dict]:
    official_providers = ("sec companyfacts", "sec edgar", "cninfo", "巨潮", "hkex")
    output = []
    for item in items:
        provider = str(item.get("source_provider") or "").lower()
        if any(provider_name in provider for provider_name in official_providers):
            output.append(item)
    return output


def pick_gate_evidence(evidence_store: list[dict], ids: list[str]) -> list[str]:
    valid = {str(item.get("evidence_id")) for item in evidence_store if item.get("evidence_id")}
    return [item for item in ids if item in valid]


def data_quality_gate_status(data_pack: dict) -> dict:
    quality = data_pack.get("data_quality", {}) if isinstance(data_pack, dict) else {}
    requirements = quality.get("dqs_requirements") or []
    blocking = quality.get("dqs_blocking_items")
    if blocking is None:
        blocking = [item.get("label") for item in requirements if isinstance(item, dict) and not item.get("passed")]
    passed = bool(quality.get("dqs_passed") if "dqs_passed" in quality else quality.get("usable_for_decision"))
    return {
        "passed": passed,
        "status": "passed" if passed else "failed",
        "version": quality.get("dqs_version", DATA_QUALITY_GATE_VERSION),
        "requirements": requirements,
        "blocking_items": [str(item) for item in (blocking or []) if item],
        "summary": quality.get("dqs_summary") or ("DQS 已完成，允许进入投委会分析。" if passed else "DQS 未完成，后续分析暂停。"),
    }


def build_valuation_summary(company: dict, snap: dict, currency: str, external_sources: dict | None = None) -> dict:
    external_sources = external_sources or {}
    price = snap.get("price") or 0
    pe = snap.get("pe_ratio") or 0
    valuation_history = first_normalized_payload(external_sources.get("valuation_history") or [])
    if not price:
        low, base, high = 0, 0, 0
    elif pe > 45:
        low, base, high = price * 0.62, price * 0.82, price * 1.05
    elif pe > 30:
        low, base, high = price * 0.72, price * 0.98, price * 1.22
    elif pe < 15:
        low, base, high = price * 0.86, price * 1.14, price * 1.42
    else:
        low, base, high = price * 0.78, price * 1.05, price * 1.32
    return {
        "method": "相对估值加反向现金流折现初版",
        "fair_value_range": {"bear": round(low, 2), "base": round(base, 2), "bull": round(high, 2), "currency": currency},
        "current_price": price,
        "upside_downside_base": round((base / price - 1), 3) if price else None,
        "historical_percentile": valuation_history,
        "key_sensitivity": ["收入增速", "利润率", "估值倍数", "折现率/风险溢价"],
        "assumptions": [
            f"市盈率 {snap.get('pe_ratio', 'N/A')} 倍作为当前市场隐含预期参考。",
            "DCF 详细三表模型待接入正式财务适配器后计算，当前区间仅作为投委会讨论锚点。",
            "历史估值分位仅在 AKShare 或正式估值历史适配器返回可解析序列时参与评分；缺失时不使用占位分。",
        ],
        "source_evidence_ids": ["ev_quote_latest", "ev_valuation_pe_pb", "ev_financial_margin_roe", "ev_valuation_history_akshare"],
    }


def first_normalized_payload(items: list[dict]) -> dict:
    if not items:
        return {}
    value = (items[0] or {}).get("normalized_value")
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_data_plan(company: dict, raw_quote: dict, evidence_store: list[dict], quality: dict, external_sources: dict | None = None) -> list[dict]:
    external_sources = external_sources or {}
    source = raw_quote.get("quote_source") or "本地快照"
    financial_source = raw_quote.get("financial_source") or "本地财务快照"
    financial_done = financial_source != "本地财务快照" or bool(external_sources.get("financial_metrics"))
    plan = [
        {"step": "识别证券", "status": "done", "source": "本地公司库 / 东方财富搜索 / Yahoo 搜索", "confidence": 0.88, "evidence_ids": ["ev_profile_identity"]},
        {"step": "获取公司资料", "status": "done", "source": "本地公司库", "confidence": 0.78, "evidence_ids": ["ev_business_description"]},
        {"step": "获取实时行情", "status": "done" if source != "本地快照" else "partial", "source": source, "confidence": evidence_lookup(evidence_store, "ev_quote_latest", "confidence", 0.6), "evidence_ids": ["ev_quote_latest"]},
        {"step": "获取财务指标", "status": "done" if financial_done else "partial", "source": financial_source, "confidence": evidence_lookup(evidence_store, "ev_financial_margin_roe", "confidence", 0.6), "evidence_ids": ["ev_financial_margin_roe"]},
        {"step": "获取估值指标", "status": "done" if source != "本地快照" else "partial", "source": source, "confidence": evidence_lookup(evidence_store, "ev_valuation_pe_pb", "confidence", 0.6), "evidence_ids": ["ev_valuation_pe_pb"]},
        {"step": "采集新闻全文", "status": source_status(external_sources, "news"), "source": source_label(external_sources, "news"), "confidence": category_confidence(evidence_store, "news"), "evidence_ids": evidence_ids_by_category(evidence_store, "news")[:4]},
        {"step": "采集社媒内容", "status": source_status(external_sources, "social"), "source": source_label(external_sources, "social"), "confidence": category_confidence(evidence_store, "social"), "evidence_ids": evidence_ids_by_category(evidence_store, "social")[:4]},
        {"step": "采集公告正文", "status": source_status(external_sources, "filings"), "source": source_label(external_sources, "filings"), "confidence": category_confidence(evidence_store, "filing"), "evidence_ids": evidence_ids_by_category(evidence_store, "filing")[:4]},
        {"step": "采集财报三表", "status": source_status(external_sources, "financial_statements"), "source": source_label(external_sources, "financial_statements"), "confidence": category_confidence(evidence_store, "financial_statement"), "evidence_ids": evidence_ids_by_category(evidence_store, "financial_statement")[:4]},
        {"step": "采集同业样本", "status": source_status(external_sources, "peer_data"), "source": source_label(external_sources, "peer_data") or "本地公司库 + 行情估值快照", "confidence": category_confidence(evidence_store, "peer"), "evidence_ids": evidence_ids_by_category(evidence_store, "peer")[:4]},
        {"step": "采集研报", "status": source_status(external_sources, "research_reports"), "source": source_label(external_sources, "research_reports"), "confidence": category_confidence(evidence_store, "research"), "evidence_ids": evidence_ids_by_category(evidence_store, "research")[:4]},
        {"step": "采集历史估值分位", "status": source_status(external_sources, "valuation_history"), "source": source_label(external_sources, "valuation_history"), "confidence": category_confidence(evidence_store, "valuation_history"), "evidence_ids": evidence_ids_by_category(evidence_store, "valuation_history")[:2]},
        {"step": "采集历史行情", "status": source_status(external_sources, "technical_history"), "source": source_label(external_sources, "technical_history"), "confidence": category_confidence(evidence_store, "technical"), "evidence_ids": evidence_ids_by_category(evidence_store, "technical")[:2]},
        {"step": "生成证据库", "status": "done", "source": f"{len(evidence_store)} 条 evidence", "confidence": round(quality["overall_score"] / 100, 2), "evidence_ids": [item["evidence_id"] for item in evidence_store[:4]]},
    ]
    return plan


def evidence_lookup(evidence_store: list[dict], evidence_id: str, field: str, fallback: Any = None) -> Any:
    item = next((entry for entry in evidence_store if entry.get("evidence_id") == evidence_id), None)
    if not item:
        return fallback
    return item.get(field, fallback)


def collect_company_sources(company: dict, security_id: str, run_id: str) -> dict:
    try:
        return collect_real_world_sources(company, security_id, run_id)
    except Exception as exc:
        return {
            "news": [],
            "social": [],
            "filings": [],
            "financial_statements": [],
            "research_reports": [],
            "peer_data": [],
            "technical_history": [],
            "valuation_history": [],
            "evidence": [],
            "gaps": [f"真实采集适配器异常：{exc}"],
            "documents_dir": "",
        }


def dedupe_evidence(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    output = []
    for item in items:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        output.append(item)
    return output


def evidence_ids_by_category(evidence_store: list[dict], category: str) -> list[str]:
    return [str(item.get("evidence_id")) for item in evidence_store if item.get("category") == category and item.get("evidence_id")]


def evidence_titles(evidence_store: list[dict], ids: list[str], limit: int = 3) -> list[str]:
    titles = []
    wanted = set(ids)
    for item in evidence_store:
        if item.get("evidence_id") in wanted and item.get("title"):
            titles.append(str(item["title"]))
        if len(titles) >= limit:
            break
    return titles


def source_status(external_sources: dict, key: str) -> str:
    return "done" if external_sources.get(key) else "missing"


def source_label(external_sources: dict, key: str) -> str:
    items = external_sources.get(key) or []
    if not items:
        return "未采集到"
    providers = sorted({str(item.get("source_provider") or "unknown") for item in items})
    return " / ".join(providers)


def category_confidence(evidence_store: list[dict], category: str) -> float:
    values = [float(item.get("confidence") or 0) for item in evidence_store if item.get("category") == category]
    if not values:
        return 0
    return round(sum(values) / len(values), 2)


def real_news_summary(news_titles: list[str], social_titles: list[str]) -> str:
    if not news_titles and not social_titles:
        return "未采集到新闻全文或社媒内容；情绪模块只能提出待验证问题，禁止作高置信市场情绪判断。"
    parts = []
    if news_titles:
        parts.append(f"新闻全文来源：{'；'.join(news_titles)}")
    if social_titles:
        parts.append(f"社媒来源：{'；'.join(social_titles)}")
    return "已采集真实文本证据。" + " ".join(parts)


def real_debate_points(news_titles: list[str], social_titles: list[str], risk_tags: list[str]) -> list[str]:
    debates = []
    if news_titles:
        debates.append("新闻事件是否改变盈利预期或估值锚")
    if social_titles:
        debates.append("社媒短线情绪是否与基本面证据背离")
    debates.extend([f"{tag} 是否已有足够风险补偿" for tag in risk_tags[:2]])
    return debates or ["真实新闻/社媒证据不足，需先补采再讨论市场分歧"]


def build_collection_summary(external_sources: dict) -> dict:
    return {
        "documents_dir": external_sources.get("documents_dir", ""),
        "news_count": len(external_sources.get("news") or []),
        "social_count": len(external_sources.get("social") or []),
        "filing_count": len(external_sources.get("filings") or []),
        "financial_statement_count": len(external_sources.get("financial_statements") or []),
        "financial_series_count": len(external_sources.get("financial_series") or []),
        "research_report_count": len(external_sources.get("research_reports") or []),
        "peer_count": len(external_sources.get("peer_data") or []),
        "technical_history_count": len(external_sources.get("technical_history") or []),
        "valuation_history_count": len(external_sources.get("valuation_history") or []),
        "gaps": external_sources.get("gaps", []),
        "source_attempts": external_sources.get("source_attempts", []),
    }


def first_evidence_by_category(evidence_store: list[dict], category: str) -> dict | None:
    return next((item for item in evidence_store if item.get("category") == category), None)


def parsed_evidence_json(item: dict | None, field: str) -> dict:
    if not item:
        return {}
    value = item.get(field)
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_fundamental_brief(company: dict, snap: dict, financial_ids: list[str], filing_ids: list[str], research_titles: list[str], valuation_tone: str) -> str:
    pe = snap.get("pe_ratio", "N/A")
    pb = snap.get("pb_ratio", "N/A")
    evidence_line = f"已采集财报三表 {len(financial_ids)} 条、公告正文 {len(filing_ids)} 条"
    research_line = f"、研报参考：{'；'.join(research_titles[:2])}" if research_titles else "、暂无研报正文参考"
    return (
        f"{company['name']} 属于 {localize_research_term(company.get('industry'))} / {localize_research_term(company.get('sector'))}，"
        f"{evidence_line}{research_line}。"
        f"当前市盈率 {pe} 倍、市净率 {pb} 倍，{valuation_tone}；后续基本面判断必须引用财报、公告或研报证据。"
    )


def build_macro_brief(company: dict, news_titles: list[str], filing_ids: list[str]) -> str:
    if news_titles or filing_ids:
        news_part = f"相关新闻 {len(news_titles)} 条" if news_titles else "暂无新闻"
        filing_part = f"公告/监管文件 {len(filing_ids)} 条" if filing_ids else "暂无公告"
        return f"{localize_research_term(company.get('industry'))} 行业判断基于{news_part}、{filing_part}，重点验证需求、政策、竞争格局和客户资本开支变化。"
    return f"{localize_research_term(company.get('industry'))} 当前缺少足够宏观行业原文证据，只能先列出需要补采的政策、需求和竞争格局问题。"


def localize_research_term(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "CXO": "医药研发生产外包",
        "AI算力": "人工智能算力",
        "GPU": "图形处理器",
        "ETF": "交易型开放式指数基金",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def macro_events_from_sources(news_titles: list[str], filing_ids: list[str]) -> list[str]:
    events = []
    if news_titles:
        events.extend(news_titles[:2])
    if filing_ids:
        events.append(f"公告/监管文件证据：{', '.join(filing_ids[:2])}")
    return events or ["利率路径影响估值折现率", "行业需求周期影响订单和利润率", "监管和产业政策影响风险溢价"]


def build_technical_agent(company: dict, price: float, pe: float, technical_history: dict, technical_ids: list[str]) -> dict:
    if not technical_history:
        return {}
    ma = technical_history.get("均线", {}) or {}
    volume = technical_history.get("量能", {}) or {}
    macd_data = technical_history.get("MACD", {}) or {}
    rsi_data = technical_history.get("RSI", {}) or {}
    support = technical_history.get("支撑位") or []
    resistance = technical_history.get("压力位") or []
    score = 58
    trend = str(technical_history.get("均线状态") or "")
    if "多头" in trend:
        score += 12
    if "空头" in trend:
        score -= 12
    rsi6 = rsi_data.get("6日")
    if isinstance(rsi6, (int, float)) and 45 <= rsi6 <= 65:
        score += 4
    if isinstance(rsi6, (int, float)) and (rsi6 > 75 or rsi6 < 25):
        score -= 5
    if pe > 40:
        score -= 5
    return {
        "agent": "技术面与交易结构研究员",
        "status": "done",
        "role_type": "analyst",
        "evidence_ids": ["ev_quote_latest"] + technical_ids[:2],
        "price_trend": f"{technical_history.get('信号摘要', '已完成真实历史行情技术分析')}。",
        "moving_averages": ma,
        "volume_analysis": f"量能状态：{volume.get('状态', 'N/A')}；当日量比5日均量：{volume.get('当日量比5日均量', 'N/A')}。",
        "rsi": f"6日 {rsi_data.get('6日', 'N/A')}，12日 {rsi_data.get('12日', 'N/A')}，状态 {rsi_data.get('状态', 'N/A')}",
        "macd": f"{macd_data.get('状态', 'N/A')}；DIF {macd_data.get('DIF', 'N/A')}，DEA {macd_data.get('DEA', 'N/A')}，柱 {macd_data.get('柱', 'N/A')}",
        "support_levels": support,
        "resistance_levels": resistance,
        "volatility": f"技术样本 {technical_history.get('样本天数', 'N/A')} 个交易日，最新交易日 {technical_history.get('最新交易日', 'N/A')}。",
        "technical_score": max(30, min(90, int(score))),
    }


def build_chart_specs(company: dict, analyst_pack: dict, technical: dict) -> list[dict]:
    snapshot = analyst_pack["market_snapshot"]
    financial = analyst_pack["financial_summary"]
    ma = technical.get("moving_averages", {})
    return [
        {
            "chart_id": "chart_price_ma_mvp",
            "title": f"{company['name']} 股价与均线快照",
            "chart_type": "line",
            "data": {
                "price": snapshot["price"],
                "20日均线": ma.get("20日") or ma.get("20日均线") or ma.get("MA20"),
                "60日均线": ma.get("60日") or ma.get("60日均线") or ma.get("MA60"),
                "120日均线": ma.get("120日") or ma.get("120日均线") or ma.get("MA120"),
            },
            "source_evidence_ids": technical.get("evidence_ids", ["ev_quote_latest", "ev_technical_levels"]),
            "caption": "优先使用真实历史行情计算均线；若历史行情缺失，才使用当前价格估算观察位。",
        },
        {
            "chart_id": "chart_quality_metrics_mvp",
            "title": "盈利能力指标快照",
            "chart_type": "bar",
            "data": {
                "gross_margin": financial["gross_margin"],
                "net_margin": financial["net_margin"],
                "roe": financial["roe"],
            },
            "source_evidence_ids": ["ev_financial_margin_roe"],
            "caption": "用于辅助专家判断盈利质量，需结合正式财报进一步验证趋势。",
        },
    ]


def persist_evidence_items(conn, report_id: str, company: dict, data_pack: dict) -> None:
    conn.execute("DELETE FROM evidence_items WHERE report_id = ?", (report_id,))
    for item in data_pack.get("evidence_store", []):
        conn.execute(
            """
            INSERT INTO evidence_items (
                id, report_id, company_id, security_id, category, title, summary,
                raw_value, normalized_value, unit, period, date, source_provider,
                source_url, source_document_id, extracted_quote, confidence, freshness_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{report_id}_{item['evidence_id']}",
                report_id,
                company["id"],
                item.get("security_id"),
                item.get("category"),
                item.get("title"),
                item.get("summary"),
                serialize_scalar(item.get("raw_value")),
                serialize_scalar(item.get("normalized_value")),
                item.get("unit"),
                item.get("period"),
                item.get("date"),
                item.get("source_provider"),
                item.get("source_url"),
                item.get("source_document_id"),
                item.get("extracted_quote"),
                item.get("confidence"),
                item.get("freshness_score"),
            ),
        )


def serialize_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def action_from_score(score: int) -> str:
    if score >= 88:
        return "强烈买入"
    if score >= 80:
        return "买入"
    if score >= 72:
        return "小仓位关注"
    if score >= 64:
        return "重点观察"
    if score >= 56:
        return "等待更好价格"
    if score >= 48:
        return "减仓"
    if score >= 40:
        return "卖出"
    return "回避"


def score_company(company: dict, data_pack: dict, expert: dict | None = None) -> int:
    scorecard = scorecard_from_data_pack(company, data_pack)
    snap = company.get("snapshot", {})
    pe = snap.get("pe_ratio") or 20
    base_value = first_numeric(
        scorecard.get("investment_action_score"),
        scorecard.get("company_quality_score"),
        scorecard.get("data_quality_score"),
    )
    base = float(base_value if base_value is not None else 40)
    if expert:
        styles = expert.get("profile", {}).get("style_tags", [])
        if any(tag in styles for tag in ["估值", "安全边际"]) and pe > 35:
            base -= 4
        if "成长" in styles and "高增长" in company.get("tags", []):
            base += 3
        if "护城河" in styles and ("品牌" in company.get("tags", []) or "平台" in company.get("tags", [])):
            base += 3
        if "风险" in styles and scorecard.get("red_flags"):
            base -= min(5, len(scorecard.get("red_flags") or []) * 2)
    return max(20, min(95, int(round(base))))


def scorecard_from_data_pack(company: dict, data_pack: dict) -> dict:
    scorecard = data_pack.get("scorecard")
    if (
        isinstance(scorecard, dict)
        and scorecard.get("scoring_version") == SCORING_VERSION
        and not scorecard_needs_refresh(scorecard)
    ):
        return scorecard
    try:
        scorecard = build_company_scorecard(company, data_pack)
        data_pack["scorecard"] = scorecard
        return scorecard
    except Exception as exc:
        quality = data_pack.get("data_quality", {}) if isinstance(data_pack, dict) else {}
        return {
            "scoring_version": SCORING_VERSION,
            "data_quality_score": quality.get("overall_score"),
            "company_quality_score": None,
            "valuation_attractiveness_score": None,
            "investment_action_score": None,
            "final_action": "数据不足暂不评级",
            "confidence": 0.0,
            "bucket_scores": {},
            "valuation_bucket_scores": {},
            "data_quality_bucket_scores": {},
            "buckets": [],
            "valuation_buckets": [],
            "data_quality_buckets": [],
            "red_flags": [],
            "missing_metrics": (quality.get("missing_data") or []) + [f"评分引擎异常：{exc}"],
            "action_rules": ["评分引擎未能生成可追溯评分，禁止使用兜底分。"],
            "summary": {
                "text": "评分引擎未能生成可追溯评分，已阻断最终评级。",
                "data_quality_grade": "未评分",
                "company_quality_grade": "未评分",
                "valuation_grade": "未评分",
            },
        }


def scorecard_needs_refresh(scorecard: dict) -> bool:
    missing_text = "\n".join(str(item) for item in (scorecard.get("missing_metrics") or []))
    if any(key in missing_text for key in ["pending_series", "收入/利润/现金流", "增长稳定性", "多年财务序列", "原始值缺失", "适配器尚未接入"]):
        return True
    for bucket_item in scorecard.get("buckets") or []:
        if bucket_item.get("key") != "growth_quality" and bucket_item.get("name") != "成长质量":
            continue
        for metric_item in bucket_item.get("metrics") or []:
            name = str(metric_item.get("name") or "")
            if any(key in name for key in ["收入/利润/现金流", "增长稳定性"]) and metric_item.get("status") != "scored":
                return True
    return False


def first_numeric(*values: Any) -> float | None:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            return number
    return None


def committee_adjusted_score(score_name: str, baseline: Any, *candidates: Any, max_delta: float = 5) -> float | None:
    base = first_numeric(baseline)
    candidate = first_numeric(*candidates)
    if candidate is None:
        return base
    candidate = clamp_score(candidate)
    if base is None:
        return candidate
    return clamp_score(max(base - max_delta, min(base + max_delta, candidate)))


def committee_investment_action_score(cqs: float | None, vas: float | None, scorecard: dict, parsed: dict) -> float | None:
    baseline = first_numeric(scorecard.get("investment_action_score"))
    candidate = first_numeric(parsed.get("investment_action_score"), parsed.get("overall_score"))
    if candidate is not None:
        return committee_adjusted_score("IAS", baseline, candidate)
    catalyst = first_numeric(scorecard.get("catalyst_score"))
    timing = first_numeric(scorecard.get("timing_score"))
    items: list[tuple[float, float]] = []
    if cqs is not None:
        items.append((cqs, 0.50))
    if vas is not None:
        items.append((vas, 0.30))
    if catalyst is not None:
        items.append((catalyst, 0.10))
    if timing is not None:
        items.append((timing, 0.10))
    if not items or sum(weight for _, weight in items) < 0.70:
        return baseline
    derived = sum(score * weight for score, weight in items) / sum(weight for _, weight in items)
    return committee_adjusted_score("IAS", baseline, derived)


def committee_scorecard(scorecard: dict, cqs: float | None, vas: float | None, ias: float | None, dqs: float | None, final_action: str, adjustment_log: list) -> dict:
    updated = dict(scorecard)
    updated.update(
        {
            "company_quality_score": round(cqs, 1) if cqs is not None else None,
            "valuation_attractiveness_score": round(vas, 1) if vas is not None else None,
            "investment_action_score": round(ias, 1) if ias is not None else None,
            "data_quality_score": round(dqs, 1) if dqs is not None else None,
            "final_action": final_action,
            "score_adjustment_log": adjustment_log,
            "baseline_scorecard": {
                "company_quality_score": scorecard.get("company_quality_score"),
                "valuation_attractiveness_score": scorecard.get("valuation_attractiveness_score"),
                "investment_action_score": scorecard.get("investment_action_score"),
                "data_quality_score": scorecard.get("data_quality_score"),
                "final_action": scorecard.get("final_action"),
            },
        }
    )
    cqs_grade = grade_company_quality(cqs) if cqs is not None else "未评分"
    vas_grade = grade_valuation(vas) if vas is not None else "未评分"
    updated["grade"] = f"{cqs_grade} / {vas_grade}"
    summary = dict(updated.get("summary") or {})
    summary.update(
        {
            "company_quality_grade": cqs_grade,
            "valuation_grade": vas_grade,
            "text": f"投委会修正后：公司质量{cqs_grade}，估值{vas_grade}，最终动作锚定为“{final_action}”。",
        }
    )
    updated["summary"] = summary
    return updated


def compact_json(value: Any, max_chars: int = 12000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:max_chars]


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def llm_token_limit(default: int, specific_env: str | None = None) -> int:
    value = env_int("AI_COMMITTEE_LLM_MAX_TOKENS", default, minimum=512)
    if specific_env:
        value = env_int(specific_env, value, minimum=512)
    return value


def json_output_rules() -> str:
    return """

输出压缩规则：
- 只输出一个完整 JSON object，不要 Markdown，不要代码块，不要解释文字。
- 每个数组最多 2 项；每个文本字段尽量控制在 60 个汉字以内。
- 必须使用 schema 中的字段名，必须闭合所有字符串、数组和对象。
- evidence_ids 只能使用输入中存在的 evidence_id 或 allowed_evidence_ids。"""


def parse_llm_json(text: str | None, expected: dict[str, Any]) -> dict:
    if not text:
        raise RuntimeError(llm_config_message())
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        json_text = extract_first_json_object(raw)
        if not json_text:
            if raw.lstrip().startswith("{"):
                raise RuntimeError(f"模型返回的 JSON 不完整，可能被 max_tokens 截断：{text[:240]}")
            raise RuntimeError(f"模型没有返回 JSON：{text[:240]}")
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"模型返回的 JSON 不完整或格式错误：{text[:240]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("模型返回结构不是 JSON object")
    merged = {**expected, **parsed}
    return normalize_round_json(merged)


def extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


async def parse_llm_json_with_repair(text: str | None, expected: dict[str, Any], context: str) -> dict:
    try:
        return parse_llm_json(text, expected)
    except RuntimeError as first_error:
        if not text:
            raise
        repaired = await call_openai_compatible(
            "你是 JSON 修复器。只把用户给出的内容修复成合法 JSON object，不新增分析观点，不输出 Markdown。",
            f"""下面是模型为 {context} 返回的内容，但不是合法 JSON。请只保留原意，修复成符合 schema 的合法 JSON object。

schema:
{compact_json(expected, 3000)}

原始内容：
{text[:env_int('AI_COMMITTEE_LLM_REPAIR_INPUT_CHARS', 12000, minimum=2000)]}
""",
            temperature=0.0,
            max_tokens=llm_token_limit(6000, "AI_COMMITTEE_LLM_REPAIR_MAX_TOKENS"),
        )
        try:
            return parse_llm_json(repaired, expected)
        except RuntimeError as second_error:
            raise RuntimeError(f"{first_error}；JSON 修复失败：{second_error}") from second_error


async def call_json_llm(
    system_prompt: str,
    user_prompt: str,
    output_schema: dict[str, Any],
    context: str,
    temperature: float,
    max_tokens: int,
) -> dict:
    content = await call_openai_compatible(
        system_prompt,
        user_prompt + json_output_rules(),
        temperature=temperature,
        max_tokens=max_tokens,
    )
    try:
        return await parse_llm_json_with_repair(content, output_schema, context)
    except RuntimeError as first_error:
        if not content:
            raise
        retry_prompt = f"""{user_prompt}

上一次输出没有形成可解析的完整 JSON：{str(first_error)[:500]}

请基于同一份输入重新生成，必须极度精简，并且只输出完整 JSON object。
强制限制：
- 每个数组最多 1-2 项。
- 每个文本字段最多一句话。
- 不要复述 schema，不要输出任何 JSON 外文字。
- 输出结束前必须闭合所有字符串、数组和对象。
{json_output_rules()}
"""
        retry_content = await call_openai_compatible(
            system_prompt,
            retry_prompt,
            temperature=min(temperature, 0.2),
            max_tokens=llm_token_limit(max(max_tokens, 9000), "AI_COMMITTEE_LLM_JSON_RETRY_MAX_TOKENS"),
        )
        try:
            return await parse_llm_json_with_repair(retry_content, output_schema, f"{context} 重试")
        except RuntimeError as second_error:
            raise RuntimeError(f"{context} 输出不是可解析 JSON；已自动修复和重试仍失败：{second_error}") from second_error


def normalize_round_json(item: dict) -> dict:
    for key in [
        "key_facts",
        "main_concerns",
        "agree_with",
        "disagree_with",
        "ignored_issues",
        "dangerous_assumptions",
        "questions_to_committee",
        "changed_because",
        "still_believe",
        "consensus",
        "disagreements",
        "top_bull_points",
        "top_bear_points",
        "buy_conditions",
        "sell_conditions",
        "tracking_metrics",
    ]:
        if key in item and isinstance(item[key], str):
            item[key] = [part.strip() for part in re.split(r"[；;\n]", item[key]) if part.strip()]
    for key in [
        "initial_score",
        "new_score",
        "overall_score",
        "business_quality_score",
        "financial_quality_score",
        "growth_score",
        "valuation_score",
        "risk_score",
        "sentiment_score",
        "technical_score",
    ]:
        if key in item:
            try:
                item[key] = max(0, min(100, int(round(float(item[key])))))
            except (TypeError, ValueError):
                item[key] = 0
    if "confidence" in item:
        try:
            item["confidence"] = round(max(0, min(1, float(item["confidence"]))), 2)
        except (TypeError, ValueError):
            item["confidence"] = 0.5
    return item


def llm_config_message() -> str:
    return (
        "LLM 未配置或未启用。为避免生成预设假会议，五轮投委会需要真实模型调用。"
        "请在 .env 设置 MINIMAX_API_KEY、MINIMAX_BASE_URL、MINIMAX_MODEL，并将 AI_COMMITTEE_USE_LLM=true。"
    )


def expert_v2_framework(expert: dict) -> dict:
    profile = expert.get("profile", {})
    styles = set(profile.get("style_tags", []) or [])
    expert_id = expert.get("id", "")
    name = expert.get("name", "")
    base_checks = [
        "业务是否可理解且可持续",
        "财务指标是否支持叙事",
        "当前价格隐含预期是否合理",
        "哪些事实会推翻当前判断",
    ]
    data_preferences = ["market_snapshot", "financial_summary", "valuation_summary", "risk_summary"]
    veto_conditions = ["关键证据缺失且无法形成足够置信度", "估值假设必须依赖无法验证的高增长", "风险暴露与用户期限不匹配"]
    valuation_model = "相对估值 + 反向 DCF 假设检查"
    typical_disagreements = ["质量与价格权重不同", "增长容忍度不同", "尾部风险权重不同"]
    if any(tag in styles for tag in ["护城河", "现金流", "质量"]) or expert_id in {"warren_buffett", "charlie_munger", "terry_smith", "li_lu", "duan_yongping"}:
        data_preferences = ["financial_summary", "business_description", "risk_summary", "valuation_summary"]
        veto_conditions = ["现金流长期弱于利润", "高杠杆或资本开支吞噬增长", "业务不可理解或护城河无法被证据支持"]
        valuation_model = "所有者收益 / FCF 质量 + 合理价格"
        typical_disagreements = ["愿意为质量付费，但反对无安全边际追高"]
    if any(tag in styles for tag in ["估值", "逆向", "安全边际"]) or expert_id in {"benjamin_graham", "seth_klarman", "joel_greenblatt", "michael_burry"}:
        data_preferences = ["valuation_summary", "financial_summary", "market_snapshot", "risk_summary"]
        veto_conditions = ["缺乏安全边际", "资产负债表不能保护本金", "估值完全依赖乐观远期假设"]
        valuation_model = "安全边际 / 低估值 / 收益率"
        typical_disagreements = ["好公司也可能是坏股票", "高估值成长股必须证明隐含预期现实"]
    if any(tag in styles for tag in ["成长", "创新", "科技"]) or expert_id in {"philip_fisher", "peter_lynch", "cathie_wood"}:
        data_preferences = ["financial_summary", "macro_summary", "news_summary", "valuation_summary"]
        veto_conditions = ["增长放缓却仍按高成长定价", "研发或产品竞争力没有证据", "高增长不转化为利润率或现金流"]
        valuation_model = "PEG / TAM 场景 / 增长质量"
        typical_disagreements = ["愿意容忍短期估值压力，但要求增长曲线有证据"]
    if any(tag in styles for tag in ["宏观", "周期", "风险"]) or expert_id in {"stanley_druckenmiller", "ray_dalio", "howard_marks", "nassim_taleb", "george_soros"}:
        data_preferences = ["macro_summary", "market_snapshot", "technical", "risk_summary"]
        veto_conditions = ["宏观逆风与高估值同向叠加", "下行尾部风险不可承受", "趋势和基本面同时恶化"]
        valuation_model = "周期位置 / 风险补偿 / 非对称赔率"
        typical_disagreements = ["不执着于静态便宜，更重视赔率、仓位和退出条件"]
    return {
        "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
        "investment_philosophy": profile.get("investment_philosophy") or expert.get("bio") or f"{name} 的公开投资框架",
        "required_checks": list(dict.fromkeys(base_checks + split_profile_text(profile.get("question_template"))[:5])),
        "data_preferences": data_preferences,
        "valuation_model": valuation_model,
        "hard_veto_conditions": veto_conditions,
        "risk_lens": profile.get("risk_preference") or "中等",
        "time_horizon": profile.get("time_horizon") or "中长期",
        "typical_disagreements": typical_disagreements,
        "output_contract": "所有关键判断必须附 evidence_ids；只能使用 Analyst Pack 内存在的 evidence_id。",
    }


def split_profile_text(text: str | None) -> list[str]:
    if not text:
        return []
    return [item.strip(" -\t") for item in re.split(r"[；;\n]", text) if item.strip(" -\t")]


def make_expert_analyst_pack(data_pack: dict, expert: dict) -> dict:
    framework = expert_v2_framework(expert)
    preferred = set(framework["data_preferences"])
    pack = data_pack.get("analyst_pack", {})
    module_keys = ["market_snapshot", "financial_summary", "valuation_summary", "risk_summary", "news_summary", "macro_summary", "peer_summary"]
    selected_modules = {key: pack.get(key) for key in module_keys if key in pack and (key in preferred or key in {"market_snapshot", "valuation_summary", "risk_summary"})}
    return {
        "schema_version": data_pack.get("schema_version", V2_DATA_SCHEMA_VERSION),
        "run_id": data_pack.get("run_id"),
        "company": pack.get("company"),
        "security": pack.get("security"),
        "selected_modules": selected_modules,
        "data_quality": data_pack.get("data_quality"),
        "evidence_index": data_pack.get("evidence_index", []),
        "allowed_evidence_ids": [item.get("evidence_id") for item in data_pack.get("evidence_store", [])],
        "framework": framework,
        "user_focus": pack.get("user_focus", []),
    }


def collect_expert_web_research(expert: dict, max_sources: int | None = None) -> dict:
    max_sources = max(1, min(max_sources or env_int("AI_COMMITTEE_EXPERT_WEB_MAX_SOURCES", 4), 8))
    max_chars = env_int("AI_COMMITTEE_EXPERT_WEB_SOURCE_CHARS", 12000, minimum=2000)
    base_dir = ROOT / "data" / "expert_research" / safe_slug(expert.get("id") or expert.get("name"))
    base_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    materials: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True) as client:
        for query in expert_research_queries(expert):
            for provider in [search_duckduckgo, search_bing]:
                provider_name = provider.__name__
                records = provider(client, query, base_dir)
                attempts.append({"query": query, "provider": provider_name, "results": len(records)})
                for record in records:
                    if len(materials) >= max_sources:
                        break
                    url = clean_search_url(record.get("url") or "")
                    title = normalize_text(record.get("title") or url)
                    if not url or url in seen_urls or not useful_expert_research_url(url, title):
                        continue
                    seen_urls.add(url)
                    try:
                        text = fetch_readable_text(client, url)
                    except Exception as exc:
                        attempts.append({"query": query, "provider": "fetch", "url": url, "error": str(exc)[:180]})
                        continue
                    if not is_relevant_expert_material(expert, title, text):
                        continue
                    saved = save_text(
                        base_dir / "sources",
                        f"source_{len(materials) + 1}_{safe_slug(title)[:80]}.txt",
                        text[:max_chars],
                    )
                    materials.append(
                        {
                            "title": title,
                            "source_url": url,
                            "source_domain": domain_of(url),
                            "query": query,
                            "raw_text": text[:max_chars],
                            "text_path": saved.text_path,
                            "char_count": min(len(text), max_chars),
                        }
                    )
                if len(materials) >= max_sources:
                    break
            if len(materials) >= max_sources:
                break
    return {"expert_id": expert.get("id"), "expert_name": expert.get("name"), "materials": materials, "attempts": attempts}


def expert_research_queries(expert: dict) -> list[str]:
    name = str(expert.get("name") or "").strip()
    name_en = str(expert.get("name_en") or "").strip()
    identity = str(expert.get("role_title") or expert.get("bio") or "").strip()
    base = [item for item in [name_en, name] if item]
    queries: list[str] = []
    for person in base:
        queries.extend(
            [
                f"{person} investment philosophy interview",
                f"{person} investing framework shareholder letter speech",
                f"{person} biography investment process risk management",
            ]
        )
    if name:
        queries.append(f"{name} 投资框架 访谈 自述 对话 传记")
    if identity:
        queries.append(f"{name_en or name} {identity} interview investment process")
    return list(dict.fromkeys(query for query in queries if query.strip()))


def useful_expert_research_url(url: str, title: str) -> bool:
    text = f"{url} {title}".lower()
    blocked = [
        "facebook.com",
        "instagram.com",
        "pinterest.",
        "tiktok.com",
        "x.com/",
        "twitter.com",
        "amazon.",
        "goodreads.com",
        "open.spotify.com",
    ]
    if any(domain in text for domain in blocked):
        return False
    useful = [
        "interview",
        "letter",
        "speech",
        "transcript",
        "biography",
        "profile",
        "manual",
        "shareholder",
        "memo",
        "访谈",
        "采访",
        "演讲",
        "信",
        "备忘录",
        "传记",
        "对话",
        "自述",
        "投资",
    ]
    return any(keyword in text for keyword in useful)


def is_relevant_expert_material(expert: dict, title: str, text: str) -> bool:
    clean = normalize_text(text or "")
    if len(clean) < 500:
        return False
    haystack = f"{title} {clean[:5000]}".lower()
    name_en = str(expert.get("name_en") or "").lower()
    name = str(expert.get("name") or "").lower()
    tokens = [token for token in re.split(r"\s+", name_en) if len(token) >= 3]
    if name and name in haystack:
        return True
    if name_en and name_en in haystack:
        return True
    return bool(tokens and sum(1 for token in tokens if token in haystack) >= min(2, len(tokens)))


def domain_of(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", httpx.URL(url).host or "")
    except Exception:
        return ""


async def distill_expert_web_research(expert: dict, materials: list[dict]) -> dict:
    schema = {
        "investment_philosophy": "核心投资哲学",
        "core_framework": "结构化投资框架",
        "decision_process": "从研究到下注的决策流程",
        "question_template": "投委会中会反复追问的问题；用分号分隔",
        "speaking_style": "发言风格",
        "strengths": "能力圈与擅长判断",
        "weaknesses": "盲区、不适用场景与容易出错处",
        "preferred_industries": ["擅长行业或公司类型"],
        "avoided_industries": ["回避领域或不擅长场景"],
        "market_tags": ["适用市场"],
        "style_tags": ["风格标签"],
        "risk_preference": "风险偏好",
        "time_horizon": "典型持有周期",
        "source_summary": "材料来源与蒸馏摘要",
        "decision_rules": ["可执行判断规则"],
        "source_notes": [{"title": "材料标题", "url": "来源链接", "usefulness": "如何校准画像"}],
    }
    source_pack = [
        {
            "title": item.get("title"),
            "url": item.get("source_url"),
            "domain": item.get("source_domain"),
            "excerpt": normalize_text(item.get("raw_text", ""))[:5000],
        }
        for item in materials[:8]
    ]
    prompt = f"""请基于公开材料为投资专家建立可执行画像，不要做口吻模仿，要蒸馏其真实投资算法。

专家：
{compact_json({k: expert.get(k) for k in ['id', 'name', 'name_en', 'role_title', 'bio', 'profile']}, 5000)}

公开材料：
{compact_json(source_pack, 22000)}

要求：
1. 区分投资哲学、核心框架、决策流程、能力圈、盲区、风险偏好和持有周期。
2. 把可操作的检查清单写进 question_template 和 decision_rules。
3. 如果材料不足以支持某项判断，保守沿用现有画像并在 source_summary 写明。
4. 输出必须中文，且必须是合法 JSON object。
"""
    return await call_json_llm(
        "你是投资大师公开材料研究员，负责把传记、访谈、股东信、演讲和对话蒸馏成可执行专家画像。",
        prompt,
        schema,
        f"{expert.get('name')} 联网资料蒸馏",
        temperature=0.2,
        max_tokens=llm_token_limit(7000, "AI_COMMITTEE_EXPERT_DISTILL_MAX_TOKENS"),
    )


def evidence_id_set(data_pack: dict) -> set[str]:
    return {str(item.get("evidence_id")) for item in data_pack.get("evidence_store", []) if item.get("evidence_id")}


def fallback_evidence_ids(data_pack: dict, limit: int = 3) -> list[str]:
    preferred = ["ev_quote_latest", "ev_valuation_pe_pb", "ev_financial_margin_roe", "ev_risk_tags"]
    valid = evidence_id_set(data_pack)
    selected = [item for item in preferred if item in valid]
    if len(selected) < limit:
        selected.extend([item for item in valid if item not in selected][: limit - len(selected)])
    return selected[:limit]


def normalize_agent_evidence(parsed: dict, data_pack: dict) -> dict:
    valid = evidence_id_set(data_pack)
    fallback = fallback_evidence_ids(data_pack)
    invalid: list[str] = []

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        cleaned = {key: walk(item) for key, item in value.items()}
        if "evidence_ids" in cleaned:
            ids = cleaned.get("evidence_ids")
            if not isinstance(ids, list):
                ids = [ids] if ids else []
            kept = []
            for evidence_id in ids:
                evidence_id = str(evidence_id)
                if evidence_id in valid:
                    kept.append(evidence_id)
                elif evidence_id:
                    invalid.append(evidence_id)
            cleaned["evidence_ids"] = kept or fallback[:2]
        elif any(key in cleaned for key in ["point", "claim", "flag", "risk", "assumption", "reason", "thesis"]):
            cleaned["evidence_ids"] = fallback[:2]
        return cleaned

    result = walk(parsed)
    if isinstance(result, dict):
        result.setdefault("evidence_ids", fallback)
        if invalid:
            result["evidence_validation_notes"] = {
                "invalid_evidence_ids_removed": sorted(set(invalid)),
                "allowed_evidence_ids": sorted(valid),
            }
    return result


def apply_scorecard_to_final(parsed: dict, company: dict, data_pack: dict) -> dict:
    scorecard = scorecard_from_data_pack(company, data_pack)
    adjustment_log = parsed.get("score_adjustment_log") or []
    cqs = committee_adjusted_score("CQS", scorecard.get("company_quality_score"), parsed.get("company_quality_score"), parsed.get("business_quality_score"))
    vas = committee_adjusted_score("VAS", scorecard.get("valuation_attractiveness_score"), parsed.get("valuation_attractiveness_score"), parsed.get("valuation_score"))
    dqs = committee_adjusted_score("DQS", scorecard.get("data_quality_score"), parsed.get("data_quality_score"))
    ias = committee_investment_action_score(cqs, vas, scorecard, parsed)
    bucket_scores = scorecard.get("bucket_scores") or {}
    if cqs is not None and vas is not None and dqs is not None:
        final_action, action_rules = action_from_scorecard(dqs, cqs, vas, scorecard.get("red_flags", []))
    else:
        final_action = "数据不足暂不评级"
        action_rules = ["公司质量、估值吸引力或数据可信度缺少足够可追溯评分项。"]
    if dqs is not None and dqs < 60 and final_action in {"强烈买入", "买入"}:
        final_action = "重点观察"
    if any(flag.get("severity") == "major" for flag in scorecard.get("red_flags", [])):
        final_action = "回避"
    if cqs is None or vas is None or ias is None:
        final_action = "数据不足暂不评级"
    final_scorecard = committee_scorecard(scorecard, cqs, vas, ias, dqs, final_action, adjustment_log)
    final_scorecard["action_rules"] = action_rules
    parsed.update(
        {
            "final_action": final_action,
            "committee_rating": parsed.get("committee_rating") or final_action,
            "overall_score": int(round(ias)) if ias is not None else None,
            "investment_action_score": round(ias, 1) if ias is not None else None,
            "company_quality_score": round(cqs, 1) if cqs is not None else None,
            "valuation_attractiveness_score": round(vas, 1) if vas is not None else None,
            "data_quality_score": round(dqs, 1) if dqs is not None else None,
            "confidence": round(float(scorecard.get("confidence") or parsed.get("confidence") or 0), 2),
            "business_quality_score": round(cqs, 1) if cqs is not None else None,
            "financial_quality_score": round(first_numeric(bucket_scores.get("financial_quality"), parsed.get("financial_quality_score")) or 0, 1) if first_numeric(bucket_scores.get("financial_quality"), parsed.get("financial_quality_score")) is not None else None,
            "growth_score": round(first_numeric(bucket_scores.get("growth_quality"), parsed.get("growth_score")) or 0, 1) if first_numeric(bucket_scores.get("growth_quality"), parsed.get("growth_score")) is not None else None,
            "valuation_score": round(vas, 1) if vas is not None else None,
            "risk_score": round(first_numeric(bucket_scores.get("risk_governance"), parsed.get("risk_score")) or 0, 1) if first_numeric(bucket_scores.get("risk_governance"), parsed.get("risk_score")) is not None else None,
            "scorecard": final_scorecard,
            "baseline_scorecard": final_scorecard.get("baseline_scorecard"),
            "score_adjustment_log": adjustment_log,
        }
    )
    parsed.setdefault(
        "one_sentence_conclusion",
        f"当前建议：{final_action}；核心判断是先区分公司质量 {format_score(cqs)} 和估值吸引力 {format_score(vas)}，再决定是否行动。",
    )
    parsed["decision_visualization"] = build_decision_visualization(parsed, data_pack)
    return parsed


def clamp_score(value: Any, low: float = 0, high: float = 100) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0
    return round(max(low, min(high, number)), 1)


def nullable_score(value: Any, low: float = 0, high: float = 100) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return round(max(low, min(high, number)), 1)


def spectrum_label(score: float) -> str:
    if score >= 90:
        return "强烈买入"
    if score >= 80:
        return "买入"
    if score >= 70:
        return "小仓关注"
    if score >= 60:
        return "等待更好价格"
    if score >= 50:
        return "谨慎观察"
    if score >= 35:
        return "回避"
    return "强烈回避"


def build_decision_visualization(final: dict, data_pack: dict) -> dict:
    scorecard = final.get("scorecard") if isinstance(final.get("scorecard"), dict) else data_pack.get("scorecard", {})
    gate = data_quality_gate_status(data_pack)
    cqs = nullable_score(first_numeric(scorecard.get("company_quality_score"), final.get("company_quality_score"), final.get("business_quality_score")))
    vas = nullable_score(first_numeric(scorecard.get("valuation_attractiveness_score"), final.get("valuation_attractiveness_score"), final.get("valuation_score")))
    ias = nullable_score(first_numeric(scorecard.get("investment_action_score"), final.get("investment_action_score"), final.get("overall_score")))
    dqs_score = nullable_score(first_numeric(scorecard.get("data_quality_score"), data_pack.get("data_quality", {}).get("overall_score"), 100 if gate.get("passed") else 0)) or 0
    if not gate.get("passed"):
        dqs_score = min(dqs_score, 49)
    quality_high = 75
    valuation_attractive = 65
    if not gate.get("passed"):
        quadrant_code = "data_insufficient"
        quadrant_title = "数据不足区"
        primary_action = "数据不足暂不评级"
        description = gate.get("summary") or "当前证据不足以支撑明确投资结论，应先补齐 DQS 必需资料。"
        spectrum = "数据不足暂不评级"
        ias = min(ias, 50) if ias is not None else None
    elif cqs is None or vas is None or ias is None:
        quadrant_code = "data_insufficient"
        quadrant_title = "证据不足区"
        primary_action = "数据不足暂不评级"
        description = "公司质量、估值吸引力或投资行动分缺少足够可追溯评分项，系统已阻断评级。"
        spectrum = "数据不足暂不评级"
    elif cqs >= quality_high and vas >= valuation_attractive:
        quadrant_code = "quality_value"
        quadrant_title = "优质买入区"
        primary_action = "买入 / 积极关注"
        description = "公司质量较高，同时估值具备吸引力，属于优先研究和可执行买入条件较强的区域。"
        spectrum = spectrum_label(ias)
    elif cqs >= quality_high and vas < valuation_attractive:
        quadrant_code = "quality_wait"
        quadrant_title = "优质等待区"
        primary_action = "等待更好价格"
        description = "公司质量较高，但当前估值吸引力不足，适合继续跟踪，等待估值回落或业绩继续兑现。"
        spectrum = "等待更好价格"
    elif cqs < quality_high and vas >= valuation_attractive:
        quadrant_code = "cheap_trap"
        quadrant_title = "便宜陷阱区"
        primary_action = "小仓研究 / 警惕价值陷阱"
        description = "当前价格看似有吸引力，但公司质量不足，需要重点验证是否存在基本面陷阱。"
        spectrum = "便宜但需警惕"
    else:
        quadrant_code = "avoid"
        quadrant_title = "回避区"
        primary_action = "回避 / 暂不关注"
        description = "公司质量和估值吸引力均不足，当前不适合作为优先投资标的。"
        spectrum = spectrum_label(ias)
    buy_conditions = []
    for item in final.get("buy_conditions", []) or []:
        buy_conditions.append(item.get("condition", str(item)) if isinstance(item, dict) else str(item))
    return {
        "company_quality_score": cqs,
        "valuation_attractiveness_score": vas,
        "investment_action_score": ias,
        "data_quality_score": dqs_score,
        "data_quality_passed": bool(gate.get("passed")),
        "data_quality_status": gate.get("status"),
        "data_quality_gate": gate,
        "quadrant_code": quadrant_code,
        "quadrant_title": quadrant_title,
        "quadrant_description": description,
        "spectrum_label": spectrum,
        "spectrum_position": ias if ias is not None else 0,
        "x_axis_label": "估值吸引力",
        "y_axis_label": "公司质量",
        "position_hint": safe_get(final, ["risk_controls", "position_size_hint"], "") or ("先补齐 DQS 门禁资料，再推进后续分析。" if not gate.get("passed") else ""),
        "primary_action": primary_action,
        "secondary_action": "补齐资料" if not gate.get("passed") else ("加入观察池" if quadrant_code in {"quality_wait", "cheap_trap"} else ""),
        "risk_level": safe_get(final, ["risk_controls", "risk_level"], ""),
        "thresholds": {"quality_high": quality_high, "valuation_attractive": valuation_attractive},
        "buy_conditions": buy_conditions[:5],
    }


def expert_system_prompt(expert: dict) -> str:
    profile = expert.get("profile", {})
    framework = expert_v2_framework(expert)
    return f"""你正在扮演一位基于真实公开材料蒸馏出的投资专家智能体：{expert['name']}。
你不是普通股票分析助手，也不是口吻模仿器；必须用该专家的投资算法、能力圈、盲区和风险偏好分析。

专家画像：
{compact_json(profile, 6000)}

专家 v2 分析框架：
{compact_json(framework, 5000)}

要求：
1. 只能基于输入 Analyst Pack 和 allowed_evidence_ids 发表判断。
2. 每个关键判断、风险、估值假设、反驳都必须附 evidence_ids。
3. 不允许编造财务数据、新闻、估值倍数、来源或不存在的 evidence_id。
4. 如果数据不足，必须明确写“数据不足”，并列入 required_followups。
5. 必须承认能力圈边界和可能出错的地方。
6. 必须输出中文。
7. 只输出一个合法 JSON object，不要 Markdown，不要代码块。"""


async def run_expert_llm(
    expert: dict,
    company: dict,
    data_pack: dict,
    round_number: int,
    previous_rounds: dict[int, Any],
    output_schema: dict[str, Any],
) -> dict:
    stage_instructions = {
        1: "第 1 轮：独立分析。你只能看到 Analyst Pack 和自己的专家框架。不能参考其他专家观点。",
        2: "第 2 轮：相互质疑。你可以看到其他委员第一轮观点，请用 evidence_ids 指出同意、反对、忽略事实、危险假设和追问。",
        3: "第 3 轮：修正观点。你可以看到第一轮和第二轮，请判断是否修正、哪些质疑影响你、仍坚持什么，并给最新评分和最终倾向。",
    }
    analyst_pack = make_expert_analyst_pack(data_pack, expert)
    user_prompt = f"""{stage_instructions[round_number]}

公司资料：
{compact_json(company, 5000)}

Analyst Pack（只能使用其中 allowed_evidence_ids 指向的证据）：
{compact_json(analyst_pack, 11000)}

前序轮次：
{compact_json(previous_rounds, 12000)}

请特别注意：
- 所有 evidence_ids 必须来自 allowed_evidence_ids。
- 如果某个观点没有证据，必须放入 required_followups，不要写成结论。
- 输出必须能映射到 AgentOpinion v2 结构。

请严格按以下 JSON schema 输出，字段名不可改：
{compact_json(output_schema, 4500)}
"""
    parsed = await call_json_llm(
        expert_system_prompt(expert),
        user_prompt,
        output_schema,
        f"{expert['name']} 第 {round_number} 轮发言",
        temperature=0.45 if round_number == 1 else 0.55,
        max_tokens=llm_token_limit(8000, "AI_COMMITTEE_LLM_AGENT_MAX_TOKENS"),
    )
    parsed = normalize_agent_evidence(parsed, data_pack)
    parsed["expert"] = expert["name"]
    parsed["expert_id"] = expert["id"]
    parsed["schema_version"] = AGENT_OUTPUT_SCHEMA_VERSION
    parsed["fit_score"] = expert_fit(company, expert)["fit_score"]
    parsed["generation_mode"] = "llm"
    return parsed


async def summarize_round_llm(company: dict, selected_experts: list[dict], data_pack: dict, round_output: dict, instruction: str) -> str:
    return summarize_round_locally(company, round_output, instruction)


def summarize_round_locally(company: dict, round_output: dict, instruction: str) -> str:
    items = round_output.get("speeches") or round_output.get("challenges") or round_output.get("revisions") or []
    if not isinstance(items, list):
        items = []
    experts = [str(item.get("expert", "")).strip() for item in items if isinstance(item, dict) and item.get("expert")]
    judgments = [
        str(item.get("core_judgment") or item.get("final_action") or item.get("price_attitude") or "").strip()
        for item in items
        if isinstance(item, dict)
    ]
    concerns: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ["main_concerns", "dangerous_assumptions", "ignored_issues", "questions_to_committee", "changed_because"]:
            value = item.get(key)
            if isinstance(value, list):
                concerns.extend(format_inline_value(v) for v in value[:2] if v)
            elif value:
                concerns.append(format_inline_value(value))
    expert_text = "、".join(experts[:5]) or "五位委员"
    judgment_text = "；".join(j for j in judgments[:3] if j) or "本轮已形成结构化观点"
    concern_text = "；".join(dict.fromkeys(concerns[:5])) or "估值、增长兑现和风险补偿仍需继续交叉验证"
    return sanitize_report_text(f"{company['name']} 本轮由 {expert_text} 完成讨论。主要观点：{judgment_text}。下一轮重点：{concern_text}。")


async def run_chairman_llm(company: dict, selected_experts: list[dict], chairman: dict, data_pack: dict, previous_rounds: dict[int, Any]) -> dict:
    schema = {
        "chairman": chairman["name"],
        "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
        "consensus": [{"point": "已形成的共识", "evidence_ids": ["ev_quote_latest"], "importance": "high"}],
        "disagreements": [{"topic": "仍然存在的分歧", "bull_view": "多头视角", "bear_view": "空头视角", "evidence_ids": ["ev_quote_latest"], "what_to_monitor": ["跟踪指标"]}],
        "key_disagreement": "最关键的分歧",
        "highest_weight_expert": "权重最高的专家",
        "core_investment_question": "本次判断的核心问题",
        "score_matrix": [{"expert": "专家", "stance": "bullish/neutral/bearish", "score": 0, "confidence": 0.0, "fit_score": 0, "one_line_reason": "一句话理由", "evidence_ids": ["ev_quote_latest"]}],
        "risk_manager_view": {"risk_level": "low/medium/high", "main_risks": [{"risk": "风险", "severity": "medium", "evidence_ids": ["ev_risk_tags"]}], "kill_switches": ["触发条件"], "position_size_hint": "仓位提示"},
        "fair_value_range": {"bear": 0, "base": 0, "bull": 0, "currency": ""},
        "evidence_gaps": ["仍需补充的数据"],
        "chairman_preliminary_conclusion": "主席临时结论",
        "summary": "本轮摘要",
    }
    analyst_pack = data_pack.get("analyst_pack", {})
    prompt = f"""现在你是本次 AI投委会主席：{chairman['name']}。
你必须综合五位委员前三轮观点，不要简单平均，要判断谁的框架更适合这家公司。

公司资料：
{compact_json(company, 5000)}

Analyst Pack 与 Evidence Store：
{compact_json({'analyst_pack': analyst_pack, 'data_quality': data_pack.get('data_quality'), 'evidence_index': data_pack.get('evidence_index')}, 12000)}

委员名单与画像摘要：
{compact_json([{k: e.get(k) for k in ['id','name','profile']} for e in selected_experts], 12000)}

前三轮：
{compact_json(previous_rounds, 16000)}

要求：
1. 不抹平分歧，必须输出 score_matrix、disagreements 和 risk_manager_view。
2. 所有关键判断必须使用 evidence_ids，且只能来自 evidence_index。
3. 如果数据不足，写入 evidence_gaps。

请只输出 JSON object，schema：
{compact_json(schema, 5000)}
"""
    parsed = await call_json_llm(
        expert_system_prompt(chairman),
        prompt,
        schema,
        "主席总结",
        temperature=0.35,
        max_tokens=llm_token_limit(8000, "AI_COMMITTEE_LLM_CHAIRMAN_MAX_TOKENS"),
    )
    parsed = normalize_agent_evidence(parsed, data_pack)
    parsed["chairman"] = chairman["name"]
    parsed["schema_version"] = AGENT_OUTPUT_SCHEMA_VERSION
    parsed["generation_mode"] = "llm"
    return parsed


async def run_final_decision_llm(company: dict, selected_experts: list[dict], chairman: dict, data_pack: dict, previous_rounds: dict[int, Any]) -> dict:
    scorecard = scorecard_from_data_pack(company, data_pack)
    schema = {
        "final_action": "强烈买入/买入/小仓位关注/重点观察/等待更好价格/减仓/卖出/回避",
        "committee_rating": "强烈看多/谨慎看多/中性观察/谨慎看空/强烈回避/数据不足暂不评级",
        "overall_score": 0,
        "confidence": 0.0,
        "data_quality_score": 0,
        "company_quality_score": 0,
        "valuation_attractiveness_score": 0,
        "investment_action_score": 0,
        "business_quality_score": 0,
        "financial_quality_score": 0,
        "growth_score": 0,
        "valuation_score": 0,
        "risk_score": 0,
        "sentiment_score": 0,
        "technical_score": 0,
        "committee_vote": {"buy": 0, "watch": 0, "avoid": 0},
        "fair_value_range": {"bear": 0, "base": 0, "bull": 0, "currency": ""},
        "suitable_investor": ["适合的投资者"],
        "unsuitable_investor": ["不适合的投资者"],
        "top_bull_points": [{"point": "看多理由", "evidence_ids": ["ev_quote_latest"], "importance": "high"}],
        "top_bear_points": [{"point": "看空理由", "evidence_ids": ["ev_risk_tags"], "importance": "high"}],
        "core_assumptions": [{"assumption": "核心假设", "evidence_ids": ["ev_valuation_pe_pb"], "validation_metric": "验证指标"}],
        "bear_case": {"summary": "最强空头情景", "drivers": [{"risk": "风险", "evidence_ids": ["ev_risk_tags"], "severity": "high"}]},
        "risk_controls": {"risk_level": "low/medium/high", "position_size_hint": "仓位提示", "kill_switches": ["触发条件"]},
        "buy_conditions": [{"condition": "买入条件", "evidence_ids": ["ev_quote_latest"]}],
        "sell_conditions": [{"condition": "卖出条件", "evidence_ids": ["ev_risk_tags"]}],
        "tracking_metrics": [{"metric": "跟踪指标", "why": "为什么重要", "evidence_ids": ["ev_financial_margin_roe"]}],
        "disagreement_map": [{"topic": "分歧点", "bull_view": "多头观点", "bear_view": "空头观点", "what_to_monitor": ["跟踪指标"], "evidence_ids": ["ev_quote_latest"]}],
        "decision_log": {"review_date": "YYYY-MM-DD", "main_thesis": "主论点", "monitoring_indicators": ["指标"]},
        "evidence_coverage": {"used_evidence_ids": ["ev_quote_latest"], "missing_evidence": ["缺失数据"]},
        "score_adjustment_log": [{"score": "CQS/VAS/IAS/DQS", "before": 0, "after": 0, "reason": "调整理由", "evidence_ids": ["ev_quote_latest"]}],
        "one_sentence_conclusion": "一句话结论",
    }
    prompt = f"""现在进入最终结论阶段。
你是 AI投委会报告主笔兼秘书长，只能基于 Evidence Store、AgentOpinion 和主席总结生成最终投资结论。

公司资料：
{compact_json(company, 5000)}

Evidence Store 与 Analyst Pack：
{compact_json({'analyst_pack': data_pack.get('analyst_pack'), 'scorecard': scorecard, 'data_quality': data_pack.get('data_quality'), 'evidence_index': data_pack.get('evidence_index'), 'valuation_summary': data_pack.get('valuation_summary')}, 16000)}

委员与主席：
{compact_json({'experts': [e['name'] for e in selected_experts], 'chairman': chairman['name']}, 2000)}

完整前序轮次：
{compact_json(previous_rounds, 22000)}

要求：
1. 必须使用系统提供的 scorecard 作为最终评分基础，不得重新发明评分体系。
2. 如果调整 DQS/CQS/VAS/IAS，单项调整幅度不得超过 ±5 分，且必须写入 score_adjustment_log 和 evidence_ids。
3. 如果 DQS < 60，不得输出“买入”或“强烈买入”；如果存在 major red flag，不得输出买入类动作。
4. 最终建议必须使用 schema 中 final_action 的枚举之一。
5. 评分为 0-100，confidence 为 0-1。
6. 买入条件、卖出条件和跟踪指标必须具体可执行，并附 evidence_ids。
7. 必须输出 fair_value_range、bear_case、core_assumptions、disagreement_map 和 decision_log。
8. 不允许在报告阶段新增未被 Evidence Store 支持的事实。
9. 只输出 JSON object，不要 Markdown，不要代码块。

schema：
{compact_json(schema, 6500)}
"""
    parsed = await call_json_llm(
        "你是严谨的中文投资委员会秘书长，负责把多专家讨论压缩成最终可执行投资结论。必须输出合法 JSON。",
        prompt,
        schema,
        "最终结论",
        temperature=0.3,
        max_tokens=llm_token_limit(9000, "AI_COMMITTEE_LLM_FINAL_MAX_TOKENS"),
    )
    parsed = normalize_agent_evidence(parsed, data_pack)
    parsed["generation_mode"] = "llm"
    parsed["schema_version"] = AGENT_OUTPUT_SCHEMA_VERSION
    votes = parsed.get("committee_vote")
    if not isinstance(votes, dict):
        parsed["committee_vote"] = {"buy": 0, "watch": 0, "avoid": 0}
    if not isinstance(parsed.get("fair_value_range"), dict):
        parsed["fair_value_range"] = data_pack.get("valuation_summary", {}).get("fair_value_range", {})
    return apply_scorecard_to_final(parsed, company, data_pack)


async def run_round(company: dict, selected_experts: list[dict], chairman: dict, data_pack: dict, previous_rounds: dict[int, Any], round_number: int) -> dict:
    if os.getenv("AI_COMMITTEE_ALLOW_FALLBACK", "false").lower() in ["1", "true", "yes"]:
        return run_round_fallback(company, selected_experts, chairman, data_pack, previous_rounds, round_number)
    if round_number == 1:
        speeches = await asyncio.gather(
            *[
                run_expert_llm(
                    expert,
                    company,
                    data_pack,
                    round_number=1,
                    previous_rounds={},
                    output_schema={
                        "expert": expert["name"],
                        "expert_id": expert["id"],
                        "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
                        "role_type": "master",
                        "stance": "strong_bullish/bullish/neutral/bearish/strong_bearish/not_applicable",
                        "score": 0,
                        "confidence": 0.0,
                        "time_horizon": "short/medium/long",
                        "thesis": "一句话投资论点",
                        "core_judgment": "中文核心判断",
                        "key_points": [{"point": "关键观点", "evidence_ids": ["ev_quote_latest"], "importance": "high/medium/low"}],
                        "bullish_points": [{"point": "看多理由", "evidence_ids": ["ev_quote_latest"], "confidence": 0.0}],
                        "bearish_points": [{"point": "看空理由", "evidence_ids": ["ev_risk_tags"], "severity": "high/medium/low"}],
                        "red_flags": [{"flag": "风险信号", "evidence_ids": ["ev_risk_tags"], "severity": "high/medium/low"}],
                        "required_followups": ["需要继续验证的数据"],
                        "valuation_view": {
                            "fair_value_low": 0,
                            "fair_value_base": 0,
                            "fair_value_high": 0,
                            "currency": "",
                            "method": "估值方法",
                            "assumptions": ["核心假设"],
                            "evidence_ids": ["ev_valuation_pe_pb"],
                        },
                        "action_view": {
                            "suggestion": "buy/hold/sell/watch/avoid",
                            "position_size_hint": "仓位提示",
                            "invalidation_conditions": ["失效条件"],
                        },
                        "key_facts": ["3-5个事实"],
                        "main_concerns": ["最大担忧"],
                        "price_attitude": "当前价格下的态度",
                        "initial_score": 0,
                        "initial_action": "买入/关注/等待/减仓/卖出/回避",
                    },
                )
                for expert in selected_experts
            ]
        )
        return {
            "generation_mode": "llm",
            "speeches": speeches,
            "summary": await summarize_round_llm(company, selected_experts, data_pack, {"speeches": speeches}, "总结第一轮独立分析的主要共识、分歧和下一轮应质疑的问题。"),
        }
    if round_number == 2:
        challenges = await asyncio.gather(
            *[
                run_expert_llm(
                    expert,
                    company,
                    data_pack,
                    round_number=2,
                    previous_rounds={1: previous_rounds.get(1, {})},
                    output_schema={
                        "expert": expert["name"],
                        "expert_id": expert["id"],
                        "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
                        "role_type": "master",
                        "stance": "strong_bullish/bullish/neutral/bearish/strong_bearish/not_applicable",
                        "score": 0,
                        "confidence": 0.0,
                        "agree_with": [{"expert": "专家", "reason": "同意理由", "evidence_ids": ["ev_quote_latest"]}],
                        "disagree_with": [{"expert": "专家", "reason": "反对理由", "evidence_ids": ["ev_risk_tags"]}],
                        "ignored_issues": [{"issue": "谁忽略了什么关键问题", "evidence_ids": ["ev_risk_tags"]}],
                        "dangerous_assumptions": [{"assumption": "最危险假设", "evidence_ids": ["ev_valuation_pe_pb"], "why_dangerous": "为什么危险"}],
                        "questions_to_committee": [{"question": "给投委会的问题", "evidence_ids": ["ev_quote_latest"]}],
                        "controversies": [{"topic": "分歧主题", "bull_view": "多头观点", "bear_view": "空头观点", "key_evidence_bull": ["ev_quote_latest"], "key_evidence_bear": ["ev_risk_tags"], "what_to_monitor": ["跟踪指标"]}],
                        "required_followups": ["需要继续验证的数据"],
                    },
                )
                for expert in selected_experts
            ]
        )
        return {
            "generation_mode": "llm",
            "challenges": challenges,
            "summary": await summarize_round_llm(company, selected_experts, data_pack, {"challenges": challenges}, "总结第二轮相互质疑的有效问题、最大争议和需要修正的假设。"),
        }
    if round_number == 3:
        revisions = await asyncio.gather(
            *[
                run_expert_llm(
                    expert,
                    company,
                    data_pack,
                    round_number=3,
                    previous_rounds={1: previous_rounds.get(1, {}), 2: previous_rounds.get(2, {})},
                    output_schema={
                        "expert": expert["name"],
                        "expert_id": expert["id"],
                        "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
                        "role_type": "master",
                        "stance": "strong_bullish/bullish/neutral/bearish/strong_bearish/not_applicable",
                        "score": 0,
                        "confidence": 0.0,
                        "revised": True,
                        "changed_because": [{"reason": "哪些质疑影响了我", "evidence_ids": ["ev_risk_tags"]}],
                        "still_believe": [{"point": "仍然坚持什么", "evidence_ids": ["ev_quote_latest"]}],
                        "remaining_disagreements": [{"topic": "未解决分歧", "evidence_ids": ["ev_valuation_pe_pb"], "what_to_monitor": ["跟踪指标"]}],
                        "final_thesis": "修正后的最终论点",
                        "required_followups": ["需要继续验证的数据"],
                        "new_score": 0,
                        "final_action": "最终倾向",
                    },
                )
                for expert in selected_experts
            ]
        )
        return {
            "generation_mode": "llm",
            "revisions": revisions,
            "summary": await summarize_round_llm(company, selected_experts, data_pack, {"revisions": revisions}, "总结第三轮修正后评分、倾向和剩余分歧。"),
        }
    if round_number == 4:
        return await run_chairman_llm(company, selected_experts, chairman, data_pack, previous_rounds)
    if round_number == 5:
        return await run_final_decision_llm(company, selected_experts, chairman, data_pack, previous_rounds)
    raise ValueError("unsupported round")


def run_round_fallback(company: dict, selected_experts: list[dict], chairman: dict, data_pack: dict, previous_rounds: dict[int, Any], round_number: int) -> dict:
    ev = fallback_evidence_ids(data_pack)
    valuation_range = data_pack.get("valuation_summary", {}).get("fair_value_range", {})
    if round_number == 1:
        outputs = []
        for expert in selected_experts:
            score = score_company(company, data_pack, expert)
            risk_items = [{"flag": risk, "severity": "medium", "evidence_ids": ["ev_risk_tags"]} for risk in company_tags(company)["risk_tags"][:3]]
            outputs.append(
                {
                    "expert": expert["name"],
                    "expert_id": expert["id"],
                    "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
                    "role_type": "master",
                    "stance": "bullish" if score >= 72 else "neutral" if score >= 56 else "bearish",
                    "score": score,
                    "confidence": 0.62,
                    "time_horizon": "long",
                    "thesis": f"{company['name']} 需要用生意质量、估值和风险补偿一起判断。",
                    "core_judgment": f"我把 {company['name']} 看作一个需要同时验证生意质量、估值和关键风险的案例。核心优势在于{company['description']}",
                    "key_points": [
                        {"point": data_pack["fundamental"]["fundamental_summary"], "evidence_ids": ["ev_business_description", "ev_financial_margin_roe"], "importance": "high"},
                        {"point": f"当前估值口径 PE 约 {company.get('snapshot', {}).get('pe_ratio', 'N/A')} 倍。", "evidence_ids": ["ev_valuation_pe_pb"], "importance": "high"},
                    ],
                    "bullish_points": [{"point": "若盈利质量与行业地位兑现，估值回落后风险收益比改善。", "evidence_ids": ["ev_financial_margin_roe", "ev_macro_industry"], "confidence": 0.58}],
                    "bearish_points": [{"point": risk["flag"], "evidence_ids": risk["evidence_ids"], "severity": risk["severity"]} for risk in risk_items],
                    "red_flags": risk_items,
                    "required_followups": ["正式财报三表", "公告/新闻全文", "同业估值对比"],
                    "valuation_view": {
                        "fair_value_low": valuation_range.get("bear"),
                        "fair_value_base": valuation_range.get("base"),
                        "fair_value_high": valuation_range.get("bull"),
                        "currency": valuation_range.get("currency", ""),
                        "method": data_pack.get("valuation_summary", {}).get("method", ""),
                        "assumptions": data_pack.get("valuation_summary", {}).get("assumptions", []),
                        "evidence_ids": ["ev_quote_latest", "ev_valuation_pe_pb"],
                    },
                    "action_view": {
                        "suggestion": "watch" if score >= 56 else "avoid",
                        "position_size_hint": "先观察或小仓位，等待证据补强。",
                        "invalidation_conditions": ["估值继续扩张但盈利证据不足", "风险标签中的关键事项恶化"],
                    },
                    "key_facts": [
                        data_pack["fundamental"]["fundamental_summary"],
                        f"当前估值口径 PE 约 {company.get('snapshot', {}).get('pe_ratio', 'N/A')} 倍。",
                        f"关键风险包括：{'、'.join(company_tags(company)['risk_tags'][:3])}。",
                    ],
                    "main_concerns": company_tags(company)["risk_tags"][:3],
                    "price_attitude": "有研究价值，但必须用买入条件约束价格和仓位。" if score >= 62 else "当前价格下吸引力不足，先等待更好赔率。",
                    "initial_score": score,
                    "initial_action": action_from_score(score),
                    "fit_score": expert_fit(company, expert)["fit_score"],
                    "evidence_ids": ev,
                }
            )
        return {"generation_mode": "fallback", "speeches": outputs, "summary": "五位专家完成独立判断，分歧主要集中在估值、周期和长期护城河强度。"}
    if round_number == 2:
        r1 = previous_rounds.get(1, {}).get("speeches", [])
        outputs = []
        for expert in selected_experts:
            higher = [item["expert"] for item in r1 if item["initial_score"] >= 72 and item["expert"] != expert["name"]]
            lower = [item["expert"] for item in r1 if item["initial_score"] < 62 and item["expert"] != expert["name"]]
            outputs.append(
                {
                    "expert": expert["name"],
                    "expert_id": expert["id"],
                    "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
                    "role_type": "master",
                    "stance": "neutral",
                    "score": score_company(company, data_pack, expert),
                    "confidence": 0.6,
                    "agree_with": [{"expert": name, "reason": "认可其对质量或估值的提醒", "evidence_ids": ev} for name in (higher[:2] or [r1[0]["expert"]])],
                    "disagree_with": [{"expert": name, "reason": "对估值和风险补偿权重不同", "evidence_ids": ["ev_valuation_pe_pb", "ev_risk_tags"]} for name in (lower[:2] or [r1[-1]["expert"]])],
                    "ignored_issues": [{"issue": "买入价格触发条件不够明确", "evidence_ids": ["ev_quote_latest", "ev_valuation_pe_pb"]}, {"issue": "需要拆分基本面恶化与估值回落两类风险", "evidence_ids": ["ev_risk_tags"]}],
                    "dangerous_assumptions": [{"assumption": "把历史高 ROE 简单外推", "evidence_ids": ["ev_financial_margin_roe"], "why_dangerous": "单期快照不足以证明长期资本回报稳定"}, {"assumption": "默认行业竞争不会继续恶化", "evidence_ids": ["ev_macro_industry", "ev_risk_tags"], "why_dangerous": "行业竞争是主要风险源"}],
                    "questions_to_committee": [
                        {"question": "如果未来两个季度核心指标低于预期，是否应立即下调评分？", "evidence_ids": ["ev_financial_margin_roe"]},
                        {"question": "当前价格隐含的增长预期是否过高？", "evidence_ids": ["ev_valuation_pe_pb"]},
                    ],
                    "controversies": [{"topic": "估值是否补偿风险", "bull_view": "质量和行业地位可支撑部分估值溢价", "bear_view": "若增长低于预期，估值回落会放大下行", "key_evidence_bull": ["ev_business_description", "ev_financial_margin_roe"], "key_evidence_bear": ["ev_valuation_pe_pb", "ev_risk_tags"], "what_to_monitor": ["收入增速", "毛利率", "市盈率/市净率"]}],
                    "required_followups": ["同业估值对比", "正式财报趋势"],
                }
            )
        return {"generation_mode": "fallback", "challenges": outputs, "summary": "第二轮把讨论从“是否好公司”推进到“好公司是否有好价格、什么事实会推翻观点”。"}
    if round_number == 3:
        r1 = previous_rounds.get(1, {}).get("speeches", [])
        outputs = []
        for first in r1:
            expert = next(item for item in selected_experts if item["id"] == first["expert_id"])
            anchored_score = score_company(company, data_pack, expert)
            new_score = max(20, min(95, round(first["initial_score"] * 0.75 + anchored_score * 0.25)))
            revised = abs(new_score - first["initial_score"]) >= 2
            outputs.append(
                {
                    "expert": expert["name"],
                    "expert_id": expert["id"],
                    "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
                    "role_type": "master",
                    "stance": "bullish" if new_score >= 72 else "neutral" if new_score >= 56 else "bearish",
                    "score": new_score,
                    "confidence": 0.64,
                    "revised": revised,
                    "changed_because": [{"reason": "第二轮对估值隐含预期和风险触发条件的质疑有影响", "evidence_ids": ["ev_valuation_pe_pb", "ev_risk_tags"]}],
                    "still_believe": [{"point": "公司质量需要和买入价格分开评价", "evidence_ids": ["ev_business_description", "ev_valuation_pe_pb"]}, {"point": "必须持续跟踪核心经营指标", "evidence_ids": ["ev_financial_margin_roe"]}],
                    "remaining_disagreements": [{"topic": "估值隐含预期是否过高", "evidence_ids": ["ev_valuation_pe_pb"], "what_to_monitor": ["收入增速", "利润率"]}],
                    "final_thesis": "修正后保留条件化判断，等待更多财报和公告证据。",
                    "required_followups": ["正式财报三表", "公告/新闻全文"],
                    "new_score": new_score,
                    "final_action": action_from_score(new_score),
                    "evidence_ids": ev,
                }
            )
        return {"generation_mode": "fallback", "revisions": outputs, "summary": "第三轮分数更收敛，委员们普遍把操作建议从抽象看好转为条件化执行。"}
    if round_number == 4:
        revisions = previous_rounds.get(3, {}).get("revisions", [])
        avg = round(sum(item["new_score"] for item in revisions) / max(1, len(revisions)))
        high = max(revisions, key=lambda item: item["new_score"])
        low = min(revisions, key=lambda item: item["new_score"])
        return {
            "generation_mode": "fallback",
            "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
            "chairman": chairman["name"],
            "consensus": [{"point": "公司具有研究价值", "evidence_ids": ev, "importance": "high"}, {"point": "最终动作必须受估值和触发条件约束", "evidence_ids": ["ev_quote_latest", "ev_valuation_pe_pb"], "importance": "high"}, {"point": "风险跟踪不能只看股价", "evidence_ids": ["ev_risk_tags"], "importance": "medium"}],
            "disagreements": [{"topic": "质量与价格权重", "bull_view": f"{high['expert']} 更重视质量和长期空间", "bear_view": f"{low['expert']} 更担心估值或周期风险", "evidence_ids": ["ev_valuation_pe_pb", "ev_risk_tags"], "what_to_monitor": ["收入增速", "毛利率", "估值倍数"]}],
            "key_disagreement": "当前价格是否已经充分反映中长期增长与质量优势。",
            "highest_weight_expert": high["expert"],
            "core_investment_question": f"{company['name']} 的商业质量能否抵消当前估值和行业风险。",
            "score_matrix": [
                {
                    "expert": item["expert"],
                    "stance": "bullish" if item["new_score"] >= 72 else "neutral" if item["new_score"] >= 56 else "bearish",
                    "score": item["new_score"],
                    "confidence": item.get("confidence", 0.62),
                    "fit_score": item.get("fit_score", ""),
                    "one_line_reason": item.get("final_thesis", "条件化判断"),
                    "evidence_ids": item.get("evidence_ids", ev),
                }
                for item in revisions
            ],
            "risk_manager_view": {"risk_level": "high" if avg < 58 else "medium", "main_risks": [{"risk": risk, "severity": "medium", "evidence_ids": ["ev_risk_tags"]} for risk in company_tags(company)["risk_tags"][:3]], "kill_switches": ["毛利率或收入增速连续两个季度低于假设", "估值继续扩张但盈利证据不足"], "position_size_hint": "单一个股建议小仓位或观察，待证据补强后再提高权重。"},
            "fair_value_range": valuation_range,
            "evidence_gaps": ["正式财报三表", "同业估值", "新闻/公告全文"],
            "chairman_preliminary_conclusion": f"主席 {chairman['name']} 暂定综合评分 {avg}，建议进入最终结论时使用条件化建议，而不是简单买入或回避。",
            "summary": "主席完成对前三轮内容的权重重排，明确了关键分歧和最终结论的约束条件。",
        }
    if round_number == 5:
        scorecard = scorecard_from_data_pack(company, data_pack)
        revisions = previous_rounds.get(3, {}).get("revisions", [])
        revision_scores = [first_numeric(item.get("new_score"), item.get("score")) for item in revisions]
        revision_scores = [item for item in revision_scores if item is not None]
        avg = round(sum(revision_scores) / len(revision_scores)) if revision_scores else None
        technical = first_numeric(data_pack.get("technical", {}).get("technical_score"))
        sentiment = first_numeric(data_pack.get("sentiment", {}).get("sentiment_score"))
        system_ias = first_numeric(scorecard.get("investment_action_score"), avg)
        expert_delta = max(-5, min(5, avg - system_ias)) if avg is not None and system_ias is not None else 0
        final_score = max(20, min(95, round(system_ias + expert_delta * 0.25))) if system_ias is not None else None
        final_action = scorecard.get("final_action") or (action_from_score(final_score) if final_score is not None else "数据不足暂不评级")
        votes = {"buy": 0, "watch": 0, "avoid": 0}
        for item in revisions:
            item_score = item.get("new_score", item.get("score", 0))
            if item_score >= 76:
                votes["buy"] += 1
            elif item_score >= 56:
                votes["watch"] += 1
            else:
                votes["avoid"] += 1
        final = {
            "generation_mode": "fallback",
            "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
            "final_action": final_action,
            "committee_rating": scorecard.get("grade") or ("未评分" if final_score is None else "谨慎看多" if final_score >= 72 else "中性观察" if final_score >= 56 else "谨慎看空"),
            "overall_score": final_score,
            "confidence": scorecard.get("confidence") if scorecard.get("confidence") is not None else (round(min(0.91, max(0.52, 0.55 + final_score / 220)), 2) if final_score is not None else 0),
            "data_quality_score": scorecard.get("data_quality_score"),
            "company_quality_score": scorecard.get("company_quality_score"),
            "valuation_attractiveness_score": scorecard.get("valuation_attractiveness_score"),
            "investment_action_score": scorecard.get("investment_action_score"),
            "business_quality_score": scorecard.get("company_quality_score"),
            "financial_quality_score": scorecard.get("bucket_scores", {}).get("financial_quality", scorecard.get("company_quality_score")),
            "growth_score": scorecard.get("bucket_scores", {}).get("growth_quality", scorecard.get("company_quality_score")),
            "valuation_score": scorecard.get("valuation_attractiveness_score"),
            "risk_score": scorecard.get("bucket_scores", {}).get("risk_governance", scorecard.get("company_quality_score")),
            "sentiment_score": sentiment,
            "technical_score": technical,
            "committee_vote": votes,
            "fair_value_range": valuation_range,
            "suitable_investor": ["能承受波动的长期研究型投资者"],
            "unsuitable_investor": ["低波动保守型", "短线追涨型"],
            "top_bull_points": [{"point": "公司具备明确的行业地位或品牌/平台/规模优势", "evidence_ids": ["ev_business_description", "ev_macro_industry"], "importance": "high"}, {"point": "现金流或成长质量为长期研究提供基础", "evidence_ids": ["ev_financial_margin_roe"], "importance": "medium"}, {"point": "若估值回落或业绩超预期，风险收益比改善", "evidence_ids": ["ev_quote_latest", "ev_valuation_pe_pb"], "importance": "medium"}],
            "top_bear_points": [{"point": risk, "evidence_ids": ["ev_risk_tags"], "importance": "high"} for risk in company_tags(company)["risk_tags"][:3]],
            "core_assumptions": [{"assumption": "盈利质量能够维持，不因竞争而快速恶化", "evidence_ids": ["ev_financial_margin_roe", "ev_macro_industry"], "validation_metric": "毛利率/ROE"}, {"assumption": "当前估值隐含预期可以被未来业绩部分兑现", "evidence_ids": ["ev_valuation_pe_pb"], "validation_metric": "收入增速/PE"}],
            "bear_case": {"summary": "若行业竞争加剧且估值仍处高位，股价可能出现明显回撤。", "drivers": [{"risk": risk, "evidence_ids": ["ev_risk_tags"], "severity": "medium"} for risk in company_tags(company)["risk_tags"][:3]]},
            "risk_controls": {"risk_level": "high" if final_score is None or final_score < 56 else "medium", "position_size_hint": "小仓位观察，等待正式财报和公告证据补强。", "kill_switches": ["核心指标连续两个季度恶化", "估值扩张但盈利预期未同步上修"]},
            "buy_conditions": [{"condition": "估值回到历史中性偏低区间", "evidence_ids": ["ev_valuation_pe_pb"]}, {"condition": "未来两个季度核心经营指标不低于预期", "evidence_ids": ["ev_financial_margin_roe"]}, {"condition": "行业竞争或监管风险没有进一步恶化", "evidence_ids": ["ev_risk_tags"]}],
            "sell_conditions": [{"condition": "毛利率或核心业务增速连续两个季度恶化", "evidence_ids": ["ev_financial_margin_roe"]}, {"condition": "管理层资本配置明显破坏价值", "evidence_ids": ["ev_business_description"]}, {"condition": "估值继续上升但盈利预期未同步上修", "evidence_ids": ["ev_valuation_pe_pb"]}],
            "tracking_metrics": [{"metric": "收入增速", "why": "验证增长叙事", "evidence_ids": ["ev_financial_margin_roe"]}, {"metric": "毛利率/净利率", "why": "验证竞争与定价权", "evidence_ids": ["ev_financial_margin_roe"]}, {"metric": "估值分位与成交量", "why": "验证价格隐含预期和交易拥挤", "evidence_ids": ["ev_quote_latest", "ev_valuation_pe_pb"]}],
            "disagreement_map": [{"topic": "估值是否已反映增长", "bull_view": "质量与行业地位可支撑部分溢价", "bear_view": "若增长低于预期则估值压缩风险较大", "what_to_monitor": ["收入增速", "利润率", "市盈率/市净率"], "evidence_ids": ["ev_valuation_pe_pb", "ev_financial_margin_roe"]}],
            "decision_log": data_pack.get("decision_log", {}),
            "evidence_coverage": {"used_evidence_ids": ev, "missing_evidence": data_pack.get("data_quality", {}).get("missing_data", [])},
            "scorecard": scorecard,
            "score_adjustment_log": [{"score": "IAS", "before": round(system_ias, 1) if system_ias is not None else None, "after": final_score, "reason": "专家第三轮均分只允许在系统投资行动分基础上小幅校准；系统分缺失时不做兜底。", "evidence_ids": ev}],
            "one_sentence_conclusion": f"当前建议：{final_action}；公司质量 {format_score(scorecard.get('company_quality_score'))}，估值吸引力 {format_score(scorecard.get('valuation_attractiveness_score'))}，必须把好公司和好价格分开判断。",
        }
        return apply_scorecard_to_final(final, company, data_pack)
    raise ValueError("unsupported round")


def final_report_markdown(company: dict, selected_experts: list[dict], chairman: dict, data_pack: dict, rounds: dict[int, Any], final: dict) -> str:
    snap = company.get("snapshot", {})
    raw_quote = snap.get("raw_data", {}) or {}
    expert_names = "、".join(expert["name"] for expert in selected_experts)
    vote = final.get("committee_vote") or {"buy": 0, "watch": 0, "avoid": 0}
    data_quality = data_pack.get("data_quality", {})
    scorecard = final.get("scorecard") if isinstance(final.get("scorecard"), dict) else (data_pack.get("scorecard") or scorecard_from_data_pack(company, data_pack))
    final = {**final, "scorecard": scorecard}
    scorecard_summary = scorecard.get("summary") if isinstance(scorecard.get("summary"), dict) else {}
    valuation = final.get("fair_value_range") or data_pack.get("valuation_summary", {}).get("fair_value_range", {})
    decision_log = final.get("decision_log") or data_pack.get("decision_log", {})
    decision_visualization = build_decision_visualization(final, data_pack)
    chair_round = rounds.get(4, {})
    markdown = f"""# AI投委会深度报告

## 1. 一页纸决策结论
{final.get('one_sentence_conclusion', '')}

- 当前象限：{decision_visualization.get('quadrant_title', '待判断')}
- 最终操作建议：{final.get('final_action', scorecard.get('final_action', ''))}
- 公司质量分 CQS：{format_score(decision_visualization.get('company_quality_score'))}（{scorecard.get('grade', '')}）
- 估值吸引力 VAS：{format_score(decision_visualization.get('valuation_attractiveness_score'))}（{scorecard_summary.get('valuation_grade', '')}）
- 投资行动分 IAS：{format_score(decision_visualization.get('investment_action_score'))}
- 数据可信度 DQS：{format_dqs_status(decision_visualization)}
- 当前态度：{decision_visualization.get('spectrum_label', final.get('final_action', ''))}
- 一句话结论：{decision_visualization.get('quadrant_description', '')}
- 置信度：{final.get('confidence', scorecard.get('confidence', ''))}
- 好公司 vs 好价格：{decision_visualization.get('quadrant_title', '待判断')}，{decision_visualization.get('quadrant_description', '')}
- 投票结果：买入 {vote.get('buy', 0)} / 观察 {vote.get('watch', 0)} / 回避 {vote.get('avoid', 0)}

## 2. 好公司还是好股票
- 这是不是一家好公司：{company.get('name', '')} 当前公司质量分为 {format_score(scorecard.get('company_quality_score'))}，核心判断来自商业模式、财务质量、成长质量、管理层、行业结构和风险治理六个模块。
- 当前价格是否值得买：估值吸引力分为 {format_score(scorecard.get('valuation_attractiveness_score'))}，系统不再用单一市盈率直接给结论，而是结合相对估值、历史分位、反向现金流假设、赔率和安全边际。
- 最终是否行动：投资行动分为 {format_score(scorecard.get('investment_action_score'))}，并受数据可信度和红旗风险硬约束。
- 如果不行动：需要等待估值、催化剂、行业数据或财报证据补齐后再复核。

## 3. 四维评分拆解
{format_scorecard_overview(scorecard)}

### 公司质量分拆解
{format_scorecard_buckets(scorecard.get('buckets', []))}

### 估值吸引力分拆解
{format_scorecard_buckets(scorecard.get('valuation_buckets', []))}

### 数据可信度分拆解
{format_scorecard_buckets(scorecard.get('data_quality_buckets', []))}

## 4. 风险红旗与数据缺口
### 风险红旗
{format_red_flags(scorecard.get('red_flags', []))}

### 缺失资料
{format_items(scorecard.get('missing_metrics') or data_quality.get('missing_data', []))}

### 本次评分约束
{format_items(scorecard.get('action_rules', []))}

## 5. 关键证据链
### 最强多头理由
{format_items(final.get('top_bull_points', []))}

### 最强空头理由
{format_items(final.get('top_bear_points', []))}

### 数据来源与采集结果
- Evidence 数量：{data_quality.get('evidence_count', len(data_pack.get('evidence_store', [])))}
- 数据质量总分：{data_quality.get('overall_score', 'N/A')}
- 数据源组合：{join_items(data_quality.get('source_mix', []))}
- 数据链条说明：主证据优先使用公告、财报原文、交易报价和可回溯新闻；缺失来源会进入资料缺口，不会被当作高置信证据。
- 原始资料目录：{safe_get(data_pack, ['collection_summary', 'documents_dir'], '未保存原文目录')}
- 新闻数量：{safe_get(data_pack, ['collection_summary', 'news_count'], 0)}
- 社媒数量：{safe_get(data_pack, ['collection_summary', 'social_count'], 0)}
- 公告数量：{safe_get(data_pack, ['collection_summary', 'filing_count'], 0)}
- 财报三表数量：{safe_get(data_pack, ['collection_summary', 'financial_statement_count'], 0)}
- 研报数量：{safe_get(data_pack, ['collection_summary', 'research_report_count'], 0)}
{format_data_chain_status(data_pack)}

## 6. 报告基本信息
- 公司名称：{company['name']}
- 股票代码：{company['ticker']}
- 市场：{company['market']} / {company['exchange']}
- 报告日期：{date.today().isoformat()}
- 当前股价：{snap.get('price', 'N/A')} {raw_quote.get('quote_currency', '')}
- 市值：{snap.get('market_cap', 'N/A')} {raw_quote.get('market_cap_unit', '')}
- 报价证券：{raw_quote.get('quote_symbol', company['ticker'])}
- 报价来源：{raw_quote.get('quote_source', '本地快照')} / {raw_quote.get('quote_fetched_at', 'N/A')}
- 所属行业：{company['industry']} / {company['sector']}
- 数据版本：{data_pack.get('schema_version', V2_DATA_SCHEMA_VERSION)}
- 模型输出版本：{final.get('schema_version', AGENT_OUTPUT_SCHEMA_VERSION)}
- 评分版本：{scorecard.get('scoring_version', '四维评分-v2.0')}
- 复盘编号：{data_pack.get('run_id', '')}

## 7. 估值与情景分析
- 估值区间：悲观 {valuation.get('bear', 'N/A')} / 基准 {valuation.get('base', 'N/A')} / 乐观 {valuation.get('bull', 'N/A')} {valuation.get('currency', '')}
- 当前价格：{snap.get('price', 'N/A')} {raw_quote.get('quote_currency', '')}
- 基准情景潜在涨跌幅：{data_pack.get('valuation_summary', {}).get('upside_downside_base', 'N/A')}
- 估值方法：{format_inline_value(data_pack.get('valuation_summary', {}).get('method', 'N/A'))}
- 核心敏感变量：{join_items(data_pack.get('valuation_summary', {}).get('key_sensitivity', []))}
{format_items(final.get('core_assumptions', data_pack.get('valuation_summary', {}).get('assumptions', [])))}

## 8. 公司与业务概览
{data_pack.get('fundamental', {}).get('fundamental_summary', '')}

## 9. 财务质量分析
{data_pack.get('fundamental', {}).get('profit_trend', '')}

## 10. 商业模式与护城河
{data_pack.get('fundamental', {}).get('management_guidance', '')}

## 11. 行业与竞争格局
{data_pack.get('macro', {}).get('industry_cycle', '')}

## 12. 技术面与市场情绪
- 技术面：{data_pack.get('technical', {}).get('price_trend', '')}
- 市场情绪：{data_pack.get('sentiment', {}).get('news_summary', '')}

## 13. 投委会成员与核心分歧
- 委员：{expert_names}
- 主席：{chairman['name']}
- 主席理由：{chairman.get('chair_reason', '其框架最适合统筹本次关键分歧。')}
- 核心投资问题：{safe_get(chair_round, ['core_investment_question'], f"{company['name']} 的公司质量能否抵消当前估值和行业风险。")}

### 投资大师观点矩阵
{format_score_matrix(chair_round.get('score_matrix') or rounds_score_matrix(rounds))}

### 多空辩论与评分分歧
{format_items(final.get('disagreement_map') or chair_round.get('disagreements', []))}

## 14. 风险清单与空头情景
- 风险等级：{safe_get(final, ['risk_controls', 'risk_level'], safe_get(chair_round, ['risk_manager_view', 'risk_level'], '未评级'))}
- 适合投资者：{join_items(final.get('suitable_investor', []))}
- 不适合投资者：{join_items(final.get('unsuitable_investor', []))}

### 悲观情景
{format_bear_case(final.get('bear_case', {}))}

### 风控约束
{format_risk_controls(final.get('risk_controls') or chair_round.get('risk_manager_view') or {})}

## 15. 买入条件
{format_items(final.get('buy_conditions', []))}

## 16. 卖出条件
{format_items(final.get('sell_conditions', []))}

## 17. 未来跟踪指标
{format_items(final.get('tracking_metrics', []))}

## 18. 什么情况证明我们错了
{format_items(final.get('kill_switches') or safe_get(final, ['risk_controls', 'kill_switches'], []))}

## 19. 决策复盘记录
- 主论点：{decision_log.get('main_thesis', '')}
- 复盘日期：{decision_log.get('review_date', '')}
- 监控指标：{join_items(decision_log.get('monitoring_indicators', []))}

## 20. 附录：第一轮独立分析
{format_round_one(rounds.get(1, {}))}

## 21. 附录：第二轮相互质疑
{format_round_list(rounds.get(2, {}).get('challenges', []), ['agree_with', 'disagree_with', 'dangerous_assumptions', 'questions_to_committee'])}

## 22. 附录：第三轮修正观点
{format_round_list(rounds.get(3, {}).get('revisions', []), ['changed_because', 'still_believe', 'remaining_disagreements', 'new_score', 'final_action'])}

## 23. 附录：第四轮主席总结
{rounds.get(4, {}).get('chairman_preliminary_conclusion', '')}

## 24. 附录：第五轮最终结论
投票结果：买入 {vote.get('buy', 0)} / 观察 {vote.get('watch', 0)} / 回避 {vote.get('avoid', 0)}。

## 25. 附录：数据来源与证据链
{format_evidence_appendix(data_pack.get('evidence_store', []))}
"""
    return sanitize_report_markdown(markdown)


SCORECARD_LABELS = {
    "business_moat": "商业模式与护城河",
    "financial_quality": "财务质量",
    "growth_quality": "成长质量",
    "management_capital_allocation": "管理层与资本配置",
    "industry_structure": "行业结构与周期位置",
    "risk_governance": "风险与治理",
    "relative_valuation": "相对估值",
    "historical_percentile": "历史估值分位",
    "reverse_dcf": "现金流与隐含预期",
    "risk_reward": "风险收益比",
    "margin_of_safety": "安全边际",
    "market_quote": "价格行情可信度",
    "financial_completeness": "财务数据完整度",
    "filing_coverage": "财报/公告原文覆盖",
    "news_coverage": "新闻/事件覆盖",
    "peer_coverage": "同业数据覆盖",
    "freshness": "数据新鲜度",
    "cross_validation": "交叉验证一致性",
}

STRUCTURED_LITERAL_RE = re.compile(
    r"\{[^{}\n]*(?:'|\")(?:point|reason|risk|condition|metric|assumption|question|topic|summary|title)(?:'|\")[^{}\n]*\}",
    re.DOTALL,
)

TEXT_REPLACEMENTS = [
    ("relative_valuation_plus_reverse_dcf_mvp", "相对估值 + 反向现金流假设（MVP）"),
    ("Base Case 上下行", "基准情景潜在涨跌幅"),
    ("Base Case", "基准情景"),
    ("Bear Case", "悲观情景"),
    ("Bull Case", "乐观情景"),
    ("base估值", "基准估值"),
    ("bear估值", "悲观估值"),
    ("bull估值", "乐观估值"),
    ("base ", "基准 "),
    ("bear ", "悲观 "),
    ("bull ", "乐观 "),
    ("Run ID", "复盘编号"),
    ("Evidence ID", "证据编号"),
    ("Income Statement", "利润表"),
    ("Balance Sheet", "资产负债表"),
    ("Cash Flow Statement", "现金流量表"),
    ("Cash Flow", "现金流量表"),
    ("Data Quality", "数据可信度"),
    ("Kill Switch", "止损/重新评估条件"),
    ("DCF / 反向 DCF", "现金流折现 / 反向现金流假设"),
    ("DCF", "现金流折现"),
    ("AICS 评分体系", "四维评分体系"),
    ("AICS 评分拆解", "四维评分拆解"),
    ("AICS-v2.0", "四维评分-v2.0"),
    ("Markus", "马克斯"),
    ("Damodaran", "达摩达兰"),
    ("MVP", "初版"),
    ("price in", "计入"),
    ("pending_adapter", "待接入采集器"),
    ("low/medium/high", "低/中/高"),
    ("bullish/neutral/bearish", "看多/中性/看空"),
    ("agree_with", "赞同点"),
    ("disagree_with", "不同意见"),
    ("dangerous_assumptions", "危险假设"),
    ("questions_to_committee", "追问委员会"),
    ("changed_because", "修正原因"),
    ("still_believe", "仍然坚持"),
    ("remaining_disagreements", "剩余分歧"),
    ("new_score", "修正后评分"),
    ("final_action", "最终倾向"),
    ("bull_view", "多头视角"),
    ("bear_view", "空头视角"),
    ("SEC XBRL", "美国证监会结构化财报"),
    ("SEC EDGAR Search Fallback", "美国证监会公告搜索补齐"),
    ("SEC EDGAR", "美国证监会公告系统"),
    ("SEC Companyfacts", "美国证监会结构化财报"),
    ("Yahoo Finance RSS", "雅虎财经新闻"),
    ("Yahoo Finance Chart", "雅虎财经历史行情"),
    ("Yahoo Finance 新闻", "雅虎财经新闻"),
    ("Internet Search Fallback", "互联网搜索补齐"),
    ("Search Fallback", "搜索补齐"),
    ("search fallback", "搜索补齐"),
    ("StockTwits", "美股社区"),
    ("Reddit public JSON", "海外论坛公开数据"),
    ("Eastmoney Research", "东方财富研报"),
    ("Eastmoney Guba", "东方财富股吧"),
    ("Eastmoney HK F10", "东方财富港股F10"),
    ("HKEXnews", "港交所披露易"),
    ("CNInfo", "巨潮资讯"),
    ("Tencent Finance", "腾讯财经"),
    ("Income Statement", "利润表"),
    ("Balance Sheet", "资产负债表"),
    ("Cash Flow Statement", "现金流量表"),
    ("Consolidated Statements of Income", "合并利润表"),
    ("Consolidated Statements of Operations", "合并经营报表"),
    ("Consolidated Balance Sheets", "合并资产负债表"),
    ("Consolidated Statements of Cash Flows", "合并现金流量表"),
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "客户合同收入"),
    ("OperatingIncomeLoss", "营业利润"),
    ("NetIncomeLoss", "净利润"),
    ("GrossProfit", "毛利润"),
    ("AssetsCurrent", "流动资产"),
    ("LiabilitiesCurrent", "流动负债"),
    ("Assets", "资产总额"),
    ("Liabilities", "负债总额"),
    ("StockholdersEquity", "股东权益"),
    ("CashAndCashEquivalentsAtCarryingValue", "现金及现金等价物"),
    ("NetCashProvidedByUsedInOperatingActivities", "经营活动现金流量净额"),
    ("NetCashProvidedByUsedInInvestingActivities", "投资活动现金流量净额"),
    ("NetCashProvidedByUsedInFinancingActivities", "融资活动现金流量净额"),
    ("PaymentsToAcquirePropertyPlantAndEquipment", "购建固定资产支出"),
    ("FreeCashFlow", "自由现金流"),
    ("Form 20-F", "20-F 年报"),
    ("annual report", "年报"),
    ("Annual Report", "年报"),
    ("earnings call", "业绩电话会"),
    ("latest earnings news", "最新业绩新闻"),
    ("current price", "当前价格"),
    ("fair value", "公允价值"),
    ("target price", "目标价"),
    ("market cap", "市值"),
    ("confidence", "置信度"),
    ("fit_score", "适配度"),
    ("source_provider", "来源"),
    ("source_url", "来源链接"),
    ("financial_statement", "财报三表"),
    ("filing", "公告原文"),
    ("price", "价格"),
    ("news", "新闻"),
    ("peer", "同业"),
    ("technical", "技术面"),
    ("industry", "行业"),
    ("macro", "宏观"),
    ("sentiment", "情绪"),
    ("calculation", "系统计算"),
    ("metric", "指标"),
    ("TAM", "潜在市场空间"),
    ("WACC", "加权平均资本成本"),
    ("CAGR", "复合增速"),
    ("PE", "市盈率"),
    ("PB", "市净率"),
    ("ROE", "净资产收益率"),
    ("ROIC", "投入资本回报率"),
]


def localize_report_text(text: Any) -> str:
    value = str(text)
    for old, new in TEXT_REPLACEMENTS:
        value = value.replace(old, new)
    token_map = {
        "strong_bullish": "强烈看多",
        "bullish": "看多",
        "neutral": "中性",
        "bearish": "看空",
        "strong_bearish": "强烈看空",
        "major": "重大",
        "medium": "中",
        "high": "高",
        "low": "低",
        "True": "是",
        "False": "否",
        "stance": "立场",
        "risk": "风险",
        "source": "来源",
        "category": "类别",
        "summary": "摘要",
        "title": "标题",
        "metric": "指标",
        "condition": "条件",
        "assumption": "假设",
        "question": "问题",
        "topic": "主题",
        "reason": "理由",
        "watch": "观察",
        "Watch": "观察",
        "hold": "持有",
        "Hold": "持有",
        "buy": "买入",
        "Buy": "买入",
        "avoid": "回避",
        "Avoid": "回避",
    }
    for old, new in token_map.items():
        value = re.sub(rf"(?<![A-Za-z_]){re.escape(old)}(?![A-Za-z_])", new, value)
    value = re.sub(
        r"证据：([^）\n]+)",
        lambda match: f"证据：{match.group(1).replace(', ', '、').replace(',', '、')}",
        value,
    )
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"([。；，、])\s+([。；，、])", r"\1", value)
    value = re.sub(r"([。；])([。；])+", r"\1", value)
    return value.strip()


def _replace_structured_literal(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw
    if isinstance(parsed, dict):
        return format_inline_value(parsed)
    return raw


def sanitize_report_text(text: Any) -> str:
    if text is None:
        return ""
    value = str(text)
    value = STRUCTURED_LITERAL_RE.sub(_replace_structured_literal, value)
    return localize_report_text(value)


def sanitize_report_markdown(markdown: Any) -> str:
    return sanitize_report_text(markdown)


def format_score(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    try:
        return f"{float(value):.0f}"
    except (TypeError, ValueError):
        return str(value)


def format_dqs_status(decision_visualization: dict) -> str:
    passed = bool(decision_visualization.get("data_quality_passed"))
    score = decision_visualization.get("data_quality_score")
    label = "已通过" if passed else "未通过"
    if score in (None, ""):
        return label
    return f"{label}（{format_score(score)}）"


def score_label(key: str, fallback: str = "") -> str:
    return SCORECARD_LABELS.get(str(key), sanitize_report_text(fallback or key))


def format_scorecard_overview(scorecard: dict) -> str:
    if not scorecard:
        return "- 暂无评分卡"
    summary = scorecard.get("summary") if isinstance(scorecard.get("summary"), dict) else {}
    rows = [
        "| 维度 | 分数 | 含义 |",
        "|---|---:|---|",
        f"| 数据可信度 | {format_score(scorecard.get('data_quality_score'))} | {sanitize_report_text(summary.get('data_quality_grade', ''))} |",
        f"| 公司质量 | {format_score(scorecard.get('company_quality_score'))} | {sanitize_report_text(summary.get('company_quality_grade', scorecard.get('grade', '')))} |",
        f"| 估值吸引力 | {format_score(scorecard.get('valuation_attractiveness_score'))} | {sanitize_report_text(summary.get('valuation_grade', ''))} |",
        f"| 投资行动 | {format_score(scorecard.get('investment_action_score'))} | {sanitize_report_text(scorecard.get('final_action', ''))} |",
    ]
    return "\n".join(rows)


def format_scorecard_buckets(buckets: list[dict]) -> str:
    if not buckets:
        return "- 暂无评分拆解"
    rows = ["| 模块 | 分数 | 权重 | 关键依据 |", "|---|---:|---:|---|"]
    for bucket in buckets:
        metrics = bucket.get("metrics") or []
        reason = ""
        if metrics and isinstance(metrics[0], dict):
            reason = metrics[0].get("reason", "")
        rows.append(
            f"| {score_label(bucket.get('name', ''), bucket.get('name', ''))} | {format_score(bucket.get('score'))} | {bucket.get('weight', '')} | {sanitize_report_text(reason)[:90]} |"
        )
    return "\n".join(rows)


def format_red_flags(flags: list[dict]) -> str:
    if not flags:
        return "- 暂无重大红旗，但仍需持续跟踪现金流、债务、监管和治理变化。"
    lines = []
    for flag in flags:
        if isinstance(flag, dict):
            severity = RISK_LEVEL_LABELS.get(str(flag.get("severity", "medium")), sanitize_report_text(flag.get("severity", "中")))
            title = sanitize_report_text(flag.get("title") or flag.get("metric") or "风险")
            reason = sanitize_report_text(flag.get("reason", ""))
            evidence_ids = flag.get("evidence_ids") or []
            suffix = f"（证据：{'、'.join(str(item) for item in evidence_ids)}）" if evidence_ids else ""
            lines.append(f"- {severity}：{title}。{reason}{suffix}")
        else:
            lines.append(f"- {sanitize_report_text(flag)}")
    return "\n".join(lines)


def safe_get(value: dict, path: list[str], fallback: Any = "") -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return fallback
        current = current[key]
    return current


def join_items(items: Any) -> str:
    if not items:
        return ""
    if isinstance(items, str):
        return sanitize_report_text(items)
    if isinstance(items, list):
        return "、".join(format_inline_value(item) for item in items)
    return format_inline_value(items)


def format_data_chain_status(data_pack: dict) -> str:
    collection = data_pack.get("collection_summary", {}) if isinstance(data_pack, dict) else {}
    quality = data_pack.get("data_quality", {}) if isinstance(data_pack, dict) else {}
    gaps = collection.get("gaps") or quality.get("collection_gaps") or quality.get("missing_data") or []
    lines = []
    def count(name: str) -> int:
        try:
            return int(collection.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    if collection:
        if count("filing_count") > 0:
            lines.append("- 公告/财报原文：已接入，可作为核心财务和管理层表述证据。")
        if count("financial_statement_count") > 0:
            lines.append("- 财报三表：已抽取利润表、资产负债表和现金流量表片段，用于交叉验证财务质量。")
        if count("research_report_count") <= 0:
            lines.append("- 研报全文：本次未采到，已作为资料缺口；系统会用公告、财报、新闻和市场数据替代，并降低相关结论置信度。")
        if count("social_count") > 0:
            lines.append("- 社媒/股吧：只作为情绪和分歧线索，不作为高置信基本面证据。")
    if gaps:
        gap_items = gaps[:8] if isinstance(gaps, list) else [gaps]
        lines.append(f"- 待补证据：{join_items(gap_items)}")
    return "\n".join(lines)


def format_items(items: Any) -> str:
    if not items:
        return "- 暂无"
    if isinstance(items, str):
        return f"- {items}"
    if isinstance(items, dict):
        items = [items]
    lines = []
    for item in items:
        if isinstance(item, dict):
            text = sanitize_report_text(item.get("point") or item.get("flag") or item.get("risk") or item.get("condition") or item.get("metric") or item.get("assumption") or item.get("question") or item.get("topic") or item.get("reason") or item.get("summary") or json.dumps(item, ensure_ascii=False))
            evidence_ids = item.get("evidence_ids") or item.get("key_evidence_bull") or []
            suffix = f"（证据：{'、'.join(str(evidence_id) for evidence_id in evidence_ids)}）" if evidence_ids else ""
            extra = ""
            if item.get("why"):
                extra = f"：{sanitize_report_text(item['why'])}"
            elif item.get("why_dangerous"):
                extra = f"：{sanitize_report_text(item['why_dangerous'])}"
            elif item.get("bull_view") or item.get("bear_view"):
                extra = f"：多头 {sanitize_report_text(item.get('bull_view', ''))}；空头 {sanitize_report_text(item.get('bear_view', ''))}"
            lines.append(f"- {text}{extra}{suffix}")
        else:
            lines.append(f"- {sanitize_report_text(item)}")
    return "\n".join(lines)


def format_expert_score(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return sanitize_report_text(value)
    if 0 < score <= 10:
        score *= 10
    return f"{score:.0f}"


def format_score_matrix(items: list[dict]) -> str:
    if not items:
        return "- 暂无观点矩阵"
    rows = ["| 专家 | 立场 | 分数 | 置信度 | 适配度 | 一句话理由 |", "|---|---:|---:|---:|---:|---|"]
    for item in items:
        stance = item.get("stance") or item.get("final_action") or ""
        stance_text = STANCE_LABELS.get(str(stance), sanitize_report_text(stance))
        rows.append(
            "| {expert} | {stance} | {score} | {confidence} | {fit_score} | {reason} |".format(
                expert=sanitize_report_text(item.get("expert", "")),
                stance=stance_text,
                score=format_expert_score(item.get("score", item.get("revised_score", item.get("initial_score", "")))),
                confidence=item.get("confidence", ""),
                fit_score=item.get("fit_score", ""),
                reason=sanitize_report_text(item.get("one_line_reason") or item.get("thesis") or "")[:80],
            )
        )
    return "\n".join(rows)


def rounds_score_matrix(rounds: dict[int, Any]) -> list[dict]:
    items = []
    for item in rounds.get(1, {}).get("speeches", []):
        if isinstance(item, dict):
            items.append(
                {
                    "expert": item.get("expert"),
                    "stance": item.get("stance") or item.get("initial_action"),
                    "score": item.get("score", item.get("initial_score")),
                    "confidence": item.get("confidence", ""),
                    "fit_score": item.get("fit_score", ""),
                    "one_line_reason": item.get("thesis") or item.get("core_judgment", ""),
                }
            )
    return items


def format_bear_case(bear_case: dict) -> str:
    if not bear_case:
        return "- 暂无"
    lines = [f"- 摘要：{sanitize_report_text(bear_case.get('summary', ''))}"]
    lines.append(format_items(bear_case.get("drivers", [])))
    return "\n".join(line for line in lines if line)


def format_risk_controls(risk: dict) -> str:
    if not risk:
        return "- 暂无"
    risk_level = RISK_LEVEL_LABELS.get(str(risk.get("risk_level", "")), sanitize_report_text(risk.get("risk_level", "")))
    lines = [
        f"- 风险等级：{risk_level}",
        f"- 仓位提示：{sanitize_report_text(risk.get('position_size_hint', ''))}",
        f"- 止损/重新评估条件：{join_items(risk.get('kill_switches', []))}",
    ]
    if risk.get("main_risks"):
        lines.append(format_items(risk["main_risks"]))
    return "\n".join(line for line in lines if line)


def format_evidence_appendix(evidence_store: list[dict]) -> str:
    if not evidence_store:
        return "- 暂无证据记录"
    rows = ["| 证据编号 | 类别 | 摘要 | 来源 | 置信度 |", "|---|---|---|---|---:|"]
    for item in evidence_store:
        rows.append(
            f"| {item.get('evidence_id')} | {sanitize_report_text(item.get('category', ''))} | {sanitize_report_text(item.get('summary', ''))[:120]} | {sanitize_report_text(item.get('source_provider', ''))} | {item.get('confidence', '')} |"
        )
    return "\n".join(rows)


def bullets(items: list[str]) -> str:
    return format_items(items)


def format_round_one(round_output: dict) -> str:
    blocks = []
    for item in round_output.get("speeches", []):
        concerns = item.get("main_concerns") or item.get("red_flags") or []
        if isinstance(concerns, list):
            concerns_text = "；".join(format_inline_value(value) for value in concerns)
        else:
            concerns_text = str(concerns)
        key_points = item.get("key_points") or item.get("bullish_points") or item.get("key_facts") or []
        blocks.append(
            f"### {sanitize_report_text(item['expert'])}\n"
            f"- 核心判断：{sanitize_report_text(item.get('core_judgment') or item.get('thesis', ''))}\n"
            f"- 关键证据观点：{format_inline_value(key_points)}\n"
            f"- 最担心的问题：{concerns_text}\n"
            f"- 初始评分：{format_expert_score(item.get('initial_score', item.get('score', '')))}\n"
            f"- 初始建议：{sanitize_report_text(item.get('initial_action') or safe_get(item, ['action_view', 'suggestion'], ''))}"
        )
    return "\n\n".join(blocks)


def format_round_list(items: list[dict], fields: list[str]) -> str:
    blocks = []
    for item in items:
        lines = [f"### {sanitize_report_text(item['expert'])}"]
        for field in fields:
            value = item.get(field)
            lines.append(f"- {ROUND_FIELD_LABELS.get(field, sanitize_report_text(field))}：{format_inline_value(value)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_inline_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return sanitize_report_text(value)
    if isinstance(value, list):
        return "；".join(format_inline_value(item) for item in value if item is not None)
    if isinstance(value, dict):
        text = value.get("point") or value.get("flag") or value.get("risk") or value.get("condition") or value.get("metric") or value.get("assumption") or value.get("question") or value.get("topic") or value.get("reason") or value.get("summary") or value.get("title") or value.get("one_line_reason") or json.dumps(value, ensure_ascii=False)
        evidence = value.get("evidence_ids") or []
        text = sanitize_report_text(text)
        return f"{text}（证据：{'、'.join(str(item) for item in evidence)}）" if evidence else str(text)
    return sanitize_report_text(value)


async def call_openai_compatible(system_prompt: str, user_prompt: str, temperature: float = 0.4, max_tokens: int = 4000) -> str | None:
    if os.getenv("AI_COMMITTEE_USE_LLM", "false").lower() not in ["1", "true", "yes"]:
        return None
    api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
    model = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_split": True,
    }
    timeout_seconds = float(os.getenv("AI_COMMITTEE_LLM_TIMEOUT_SECONDS", "180"))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=20)) as client:
            response = await client.post(f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
            content = message.get("content")
            if content:
                return content
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            return reasoning if isinstance(reasoning, str) else None
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"LLM 调用超时，请稍后重试或降低并发轮次。当前超时：{timeout_seconds:.0f}s") from exc
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise RuntimeError(f"LLM 服务返回错误：{detail}") from exc
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f"LLM 调用失败：{exc}") from exc


def distill_material(expert: dict, raw_text: str) -> dict:
    clean = " ".join((raw_text or "").split())
    excerpt = clean[:420] or "用户尚未提供长文本材料。"
    tags = expert.get("profile", {}).get("style_tags", [])
    points = [
        f"保留 {expert['name']} 的核心框架：{expert.get('profile', {}).get('investment_philosophy', '')}",
        f"材料关键词：{excerpt[:120]}",
        "新增校准：把能力圈、不擅长领域和可推翻条件写入专家画像。",
    ]
    return {
        "ai_summary": f"已从材料中提取 {expert['name']} 的投资框架、典型问题和表达习惯。材料摘要：{excerpt}",
        "distilled_points": {
            "thinking_models": tags,
            "decision_rules": points,
            "speaking_style_patch": "回答时先列事实，再给分歧、风险和可执行条件。",
            "source_quality": "user_uploaded_material",
        },
        "profile_patch": {
            "source_summary": f"{expert.get('profile', {}).get('source_summary', '')}\n新增材料蒸馏：{excerpt[:220]}",
            "question_template": "这家公司哪一点最可能被市场误判？什么证据会迫使我改变结论？",
        },
    }


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"
