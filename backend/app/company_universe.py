from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .database import from_json, to_json


SYNC_SOURCE_VERSION = "company-universe-v1"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (AI Investment Committee company universe)",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
EASTMONEY_CLIST_HOSTS = [
    "https://push2.eastmoney.com",
    "https://12.push2.eastmoney.com",
    "https://13.push2.eastmoney.com",
    "https://15.push2.eastmoney.com",
    "https://16.push2.eastmoney.com",
    "https://17.push2.eastmoney.com",
    "https://18.push2.eastmoney.com",
    "https://19.push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://72.push2.eastmoney.com",
]
TICKER_HK_SUPABASE_URL = "https://ojgshtvloxifakqiuxth.supabase.co/rest/v1/tickers"
TICKER_HK_SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9qZ3NodHZsb3hpZmFrcWl1eHRoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjA5NTg3NDksImV4cCI6MjA3NjUzNDc0OX0."
    "papR3VvHup5JRv_g5b6Crh9f5DrEMCPpBhIJKueNYe0"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def company_universe_summary(conn) -> dict:
    rows = conn.execute("SELECT market, COUNT(*) AS count FROM companies GROUP BY market").fetchall()
    by_market = {row["market"] or "UNKNOWN": row["count"] for row in rows}
    meta = get_sync_meta(conn)
    return {
        "total": sum(by_market.values()),
        "by_market": by_market,
        "sync": meta,
    }


def list_company_universe(conn, q: str = "", market: str = "AUTO", limit: int = 80, offset: int = 0) -> dict:
    limit = max(1, min(limit, 300))
    offset = max(0, offset)
    where = []
    params: list[Any] = []
    if market and market != "AUTO":
        where.append("market = ?")
        params.append(market)
    tokens = [token for token in re.split(r"[\s,，/／;；|｜]+", q.strip().lower()) if token]
    for token in tokens:
        where.append(
            "(LOWER(name) LIKE ? OR LOWER(name_en) LIKE ? OR LOWER(ticker) LIKE ? OR LOWER(exchange) LIKE ? OR LOWER(industry) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(aliases) LIKE ?)"
        )
        like = f"%{token}%"
        params.extend([like, like, like, like, like, like, like])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    total = conn.execute(f"SELECT COUNT(*) AS count FROM companies{where_sql}", params).fetchone()["count"]
    rows = conn.execute(
        f"""
        SELECT * FROM companies
        {where_sql}
        ORDER BY
          CASE market WHEN 'A' THEN 1 WHEN 'HK' THEN 2 WHEN 'US' THEN 3 ELSE 9 END,
          ticker
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    companies = dedupe_company_rows([company_row(row) for row in rows])
    return {
        "companies": companies,
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": company_universe_summary(conn),
    }


def sync_company_universe(conn, markets: list[str] | None = None) -> dict:
    selected = {market.upper() for market in (markets or ["A", "HK", "US"])}
    started_at = utc_now_iso()
    stats: dict[str, Any] = {
        "source_version": SYNC_SOURCE_VERSION,
        "started_at": started_at,
        "completed_at": None,
        "markets": {},
        "errors": [],
    }
    if "A" in selected:
        sync_market_safe(conn, "A", fetch_eastmoney_a_shares, stats)
    if "HK" in selected:
        sync_market_safe(conn, "HK", fetch_eastmoney_hk_shares, stats)
    if "US" in selected:
        sync_market_safe(conn, "US", fetch_us_listed_companies, stats)
    stats["completed_at"] = utc_now_iso()
    save_sync_meta(conn, stats)
    return {"sync": stats, "summary": company_universe_summary(conn)}


def sync_market_safe(conn, market: str, fetcher, aggregate: dict) -> None:
    try:
        sync_market(conn, market, fetcher(), aggregate)
    except Exception as exc:
        aggregate["errors"].append({"market": market, "error": str(exc)})
        aggregate["markets"][market] = {"source": "error", "fetched": 0, "upserted": 0, "error": str(exc)}


def sync_market(conn, market: str, result: dict, aggregate: dict) -> None:
    stats = result.setdefault("stats", {})
    stats["upserted"] = 0
    for item in result.get("companies", []):
        upsert_company(conn, item)
        stats["upserted"] += 1
    aggregate["markets"][market] = stats


def fetch_eastmoney_a_shares() -> dict:
    fs = "m:1+t:2,m:0+t:6,m:0+t:80"
    try:
        return fetch_eastmoney_clist("A", fs, page_size=100)
    except Exception:
        return fetch_akshare_a_shares()


def fetch_eastmoney_hk_shares() -> dict:
    fs = "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2"
    try:
        return fetch_eastmoney_clist("HK", fs, page_size=100)
    except Exception:
        return fetch_ticker_hk_shares()


def fetch_eastmoney_clist(market: str, fs: str, page_size: int = 5000) -> dict:
    companies: list[dict] = []
    stats = {"source": "Eastmoney clist", "total_remote": 0, "pages": 0}
    with httpx.Client(timeout=18, headers=HTTP_HEADERS) as client:
        page = 1
        while True:
            payload = eastmoney_payload(client, page, page_size, fs)
            data = payload.get("data") or {}
            rows = data.get("diff") or []
            stats["total_remote"] = data.get("total") or len(rows)
            stats["pages"] = page
            for row in rows:
                item = eastmoney_row_to_company(row, market)
                if item:
                    companies.append(item)
            if len(companies) >= int(stats["total_remote"] or 0) or not rows:
                break
            page += 1
            time.sleep(0.08)
    stats["fetched"] = len(companies)
    return {"companies": companies, "stats": stats}


def eastmoney_payload(client: httpx.Client, page: int, page_size: int, fs: str) -> dict:
    last_exc: Exception | None = None
    for attempt in range(3):
        for host in EASTMONEY_CLIST_HOSTS:
            try:
                response = client.get(
                    f"{host}/api/qt/clist/get",
                    params={
                        "pn": page,
                        "pz": page_size,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f3",
                        "fs": fs,
                        "fields": "f12,f13,f14,f100,f102",
                    },
                )
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_exc = exc
                try:
                    return eastmoney_payload_via_curl(host, page, page_size, fs)
                except Exception as curl_exc:
                    last_exc = curl_exc
        time.sleep(0.4 * (attempt + 1))
    raise last_exc or RuntimeError("Eastmoney request failed")


def eastmoney_payload_via_curl(host: str, page: int, page_size: int, fs: str) -> dict:
    query = (
        f"{host}/api/qt/clist/get"
        f"?pn={page}&pz={page_size}&po=1&np=1&fltt=2&invt=2&fid=f3"
        f"&fs={fs}&fields=f12,f13,f14,f100,f102"
    )
    completed = subprocess.run(
        ["curl", "-sS", "--max-time", "12", "-A", HTTP_HEADERS["User-Agent"], query],
        check=True,
        capture_output=True,
        text=True,
    )
    text = completed.stdout.strip()
    if not text:
        raise RuntimeError("Eastmoney curl fallback returned empty response")
    return json.loads(text)


def eastmoney_row_to_company(row: dict, market: str) -> dict | None:
    code = str(row.get("f12") or "").strip().upper()
    name = str(row.get("f14") or "").strip()
    if not code or not name:
        return None
    if market == "A":
        exchange = "SSE" if str(row.get("f13")) == "1" or code.startswith(("6", "9")) else "SZSE"
        ticker = code
        quote_alias = f"{code}.SH" if exchange == "SSE" else f"{code}.SZ"
        aliases = [code, quote_alias, name]
    else:
        exchange = "HKEX"
        ticker = normalize_hk_ticker(code)
        aliases = [code, code.lstrip("0"), ticker, name]
    industry = clean_text(row.get("f100")) or "待补充行业"
    sector = clean_text(row.get("f102")) or market_label(market)
    return {
        "id": company_id(ticker, market),
        "name": name,
        "name_en": name,
        "ticker": ticker,
        "market": market,
        "exchange": exchange,
        "industry": industry,
        "sector": sector,
        "description": f"{name} 已由东方财富证券主数据同步为 {ticker} / {market_label(market)}。",
        "tags": [market_label(market), industry, "证券主数据"],
        "aliases": aliases,
    }


def fetch_us_listed_companies() -> dict:
    companies: list[dict] = []
    stats = {"source": "NASDAQ Trader SymDir", "files": []}
    with httpx.Client(timeout=18, headers=HTTP_HEADERS) as client:
        nasdaq = client.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt")
        nasdaq.raise_for_status()
        rows = parse_pipe_table(nasdaq.text)
        stats["files"].append({"name": "nasdaqlisted", "rows": len(rows)})
        for row in rows:
            item = nasdaq_row_to_company(row)
            if item:
                companies.append(item)

        other = client.get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")
        other.raise_for_status()
        rows = parse_pipe_table(other.text)
        stats["files"].append({"name": "otherlisted", "rows": len(rows)})
        for row in rows:
            item = otherlisted_row_to_company(row)
            if item:
                companies.append(item)
    companies = dedupe(companies)
    stats["fetched"] = len(companies)
    return {"companies": companies, "stats": stats}


def fetch_akshare_a_shares() -> dict:
    try:
        import akshare as ak
    except Exception as exc:
        raise RuntimeError("A股同步失败，且 AkShare 兜底未安装") from exc
    companies: list[dict] = []
    errors = []
    try:
        sh = ak.stock_info_sh_name_code()
        for _, row in sh.iterrows():
            code = str(row.get("证券代码") or "").strip()
            name = str(row.get("证券简称") or row.get("公司简称") or "").strip()
            full_name = str(row.get("公司全称") or name).strip()
            if code and name:
                companies.append(a_share_record(code, name, full_name, "SSE", "上海证券交易所"))
    except Exception as exc:
        errors.append(f"SSE: {exc}")
    try:
        sz = ak.stock_info_sz_name_code()
        for _, row in sz.iterrows():
            code = str(row.get("A股代码") or "").strip()
            name = str(row.get("A股简称") or "").strip()
            industry = str(row.get("所属行业") or "深圳证券交易所").strip()
            if code and name:
                companies.append(a_share_record(code, name, name, "SZSE", industry))
    except Exception as exc:
        errors.append(f"SZSE: {exc}")
    companies = dedupe(companies)
    if not companies:
        raise RuntimeError("AkShare A股兜底未返回公司列表：" + "；".join(errors))
    return {
        "companies": companies,
        "stats": {
            "source": "AkShare SSE/SZSE company list",
            "fetched": len(companies),
            "errors": errors,
        },
    }


def a_share_record(code: str, name: str, full_name: str, exchange: str, industry: str) -> dict:
    return {
        "id": company_id(code, "A"),
        "name": name,
        "name_en": full_name or name,
        "ticker": code,
        "market": "A",
        "exchange": exchange,
        "industry": industry or "待补充行业",
        "sector": "A股上市公司",
        "description": f"{name} 已由交易所/AkShare 公司列表同步为 {code} / A股 / {exchange}。",
        "tags": ["A股", industry or "待补充行业", "证券主数据"],
        "aliases": [code, f"{code}.SH" if exchange == "SSE" else f"{code}.SZ", name, full_name],
    }


def fetch_ticker_hk_shares() -> dict:
    rows = []
    with httpx.Client(timeout=18, headers=HTTP_HEADERS) as client:
        offset = 0
        page_size = 1000
        while True:
            response = client.get(
                TICKER_HK_SUPABASE_URL,
                params={"select": "ticker_symbol,company_name,sector", "limit": str(page_size), "offset": str(offset)},
                headers={
                    **HTTP_HEADERS,
                    "apikey": TICKER_HK_SUPABASE_KEY,
                    "Authorization": "Bearer " + TICKER_HK_SUPABASE_KEY,
                },
            )
            response.raise_for_status()
            page = response.json()
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
    companies = []
    for row in rows:
        code = str(row.get("ticker_symbol") or "").strip()
        name = str(row.get("company_name") or "").strip()
        if not code or not name:
            continue
        ticker = normalize_hk_ticker(code)
        sector = clean_text(row.get("sector")) or "港股上市公司"
        companies.append(
            {
                "id": company_id(ticker, "HK"),
                "name": name,
                "name_en": name,
                "ticker": ticker,
                "market": "HK",
                "exchange": "HKEX",
                "industry": sector,
                "sector": sector,
                "description": f"{name} 已由 ticker.com.hk 公开 HKEX 股票目录同步为 {ticker} / 港股。",
                "tags": ["港股", sector, "证券主数据"],
                "aliases": [code, code.zfill(4), ticker, name],
            }
        )
    companies = dedupe(companies)
    return {
        "companies": companies,
        "stats": {
            "source": "ticker.com.hk Supabase public tickers",
            "fetched": len(companies),
        },
    }


def parse_pipe_table(text: str) -> list[dict]:
    lines = [line for line in text.splitlines() if line and not line.startswith("File Creation Time")]
    if not lines:
        return []
    headers = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        values = line.split("|")
        if len(values) != len(headers):
            continue
        rows.append(dict(zip(headers, values)))
    return rows


def nasdaq_row_to_company(row: dict) -> dict | None:
    if row.get("Test Issue") == "Y" or row.get("ETF") == "Y":
        return None
    ticker = normalize_us_ticker(row.get("Symbol") or "")
    name = clean_security_name(row.get("Security Name") or "")
    if not valid_us_company(ticker, name):
        return None
    exchange = "NASDAQ"
    return us_company_record(ticker, name, exchange)


def otherlisted_row_to_company(row: dict) -> dict | None:
    if row.get("Test Issue") == "Y" or row.get("ETF") == "Y":
        return None
    ticker = normalize_us_ticker(row.get("ACT Symbol") or "")
    name = clean_security_name(row.get("Security Name") or "")
    if not valid_us_company(ticker, name):
        return None
    exchange = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}.get(row.get("Exchange"), row.get("Exchange") or "US")
    return us_company_record(ticker, name, exchange)


def us_company_record(ticker: str, name: str, exchange: str) -> dict:
    industry = "待补充行业"
    return {
        "id": company_id(ticker, "US"),
        "name": name,
        "name_en": name,
        "ticker": ticker,
        "market": "US",
        "exchange": exchange,
        "industry": industry,
        "sector": exchange,
        "description": f"{name} 已由 NASDAQ Trader 证券目录同步为 {ticker} / 美股 / {exchange}。",
        "tags": ["美股", exchange, "证券主数据"],
        "aliases": [ticker, name],
    }


def valid_us_company(ticker: str, name: str) -> bool:
    if not ticker or not name or len(ticker) > 12:
        return False
    lower = name.lower()
    blocked = [
        " warrant",
        " warrants",
        " right",
        " rights",
        " unit",
        " units",
        " note",
        " notes",
        "preferred",
        "depositary shares",
        "closed end fund",
        "etf",
        " etn",
    ]
    return not any(word in lower for word in blocked)


def upsert_company(conn, item: dict) -> None:
    existing = conn.execute(
        """
        SELECT id FROM companies
        WHERE market = ? AND UPPER(ticker) = UPPER(?) AND id NOT LIKE 'co_manual_%'
        ORDER BY CASE WHEN id LIKE 'co_univ_%' THEN 2 ELSE 1 END
        LIMIT 1
        """,
        (item["market"], item["ticker"]),
    ).fetchone()
    company_id_value = existing["id"] if existing else item["id"]
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
            industry = CASE
                WHEN companies.industry IS NULL OR companies.industry = '' OR companies.industry = '待补充行业'
                THEN excluded.industry ELSE companies.industry END,
            sector = CASE
                WHEN companies.sector IS NULL OR companies.sector = '' OR companies.sector IN ('外部识别', '用户输入')
                THEN excluded.sector ELSE companies.sector END,
            description = excluded.description,
            tags = excluded.tags,
            aliases = excluded.aliases,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            company_id_value,
            item["name"],
            item["name_en"],
            item["ticker"],
            item["market"],
            item["exchange"],
            item["industry"],
            item["sector"],
            item["description"],
            to_json(list(dict.fromkeys([tag for tag in item["tags"] if tag]))),
            to_json(list(dict.fromkeys([alias for alias in item["aliases"] if alias]))),
        ),
    )


def get_sync_meta(conn) -> dict:
    row = conn.execute("SELECT value FROM app_metadata WHERE key = ?", ("company_universe_sync",)).fetchone()
    return from_json(row["value"], {}) if row else {}


def save_sync_meta(conn, stats: dict) -> None:
    conn.execute(
        """
        INSERT INTO app_metadata (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        ("company_universe_sync", to_json(stats)),
    )


def company_row(row: Any) -> dict:
    company = dict(row)
    company["tags"] = from_json(company.get("tags"), [])
    company["aliases"] = from_json(company.get("aliases"), [])
    return company


def dedupe_company_rows(companies: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for company in companies:
        if is_non_company_record(company):
            continue
        key = (str(company.get("market") or ""), str(company.get("ticker") or "").upper())
        current = by_key.get(key)
        if current is None or row_preference(company) < row_preference(current):
            by_key[key] = company
    return list(by_key.values())


def row_preference(company: dict) -> tuple[int, str]:
    company_id = str(company.get("id") or "")
    if company_id.startswith("co_manual_"):
        return (3, company_id)
    if company_id.startswith("co_univ_") or company_id.startswith("co_ext_"):
        return (2, company_id)
    return (1, company_id)


def is_non_company_record(company: dict) -> bool:
    ticker = str(company.get("ticker") or "").upper()
    name = str(company.get("name") or "")
    market = str(company.get("market") or "").upper()
    if market == "US" and "." in ticker and ticker not in {"BRK.A", "BRK.B", "BF.A", "BF.B"}:
        return True
    blocked_terms = ["权证", "认购", "认沽", "牛证", "熊证", "法巴", "摩通", "星展", "瑞银", "信证", "汇丰", "麦银", "花旗", "高盛"]
    return any(term in name for term in blocked_terms)


def dedupe(companies: list[dict]) -> list[dict]:
    seen = set()
    output = []
    for company in companies:
        key = (company["market"], company["ticker"])
        if key in seen:
            continue
        seen.add(key)
        output.append(company)
    return output


def company_id(ticker: str, market: str) -> str:
    return "co_univ_" + hashlib.sha256(f"{market}:{ticker}".encode("utf-8")).hexdigest()[:16]


def normalize_hk_ticker(code: str) -> str:
    digits = re.sub(r"\D", "", code)
    return f"{digits.zfill(4)}.HK" if digits else code.upper()


def normalize_us_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace("/", ".")


def clean_security_name(name: str) -> str:
    clean = re.sub(r"\s+", " ", name).strip()
    clean = re.sub(r"\s+-\s+Common Stock$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+Common Stock$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+Ordinary Shares$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+Class [A-Z] Ordinary Shares$", "", clean, flags=re.IGNORECASE)
    return clean.strip(" -")


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"-", "None", "null"} else text


def market_label(market: str) -> str:
    return {"US": "美股", "HK": "港股", "A": "A股"}.get(market, market)
