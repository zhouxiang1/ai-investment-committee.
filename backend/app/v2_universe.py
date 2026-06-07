from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .database import from_json, to_json


V2_VERSION = "ai-committee-v2.1-300"
V2_DATA_PATH = Path(__file__).resolve().parent / "data" / "v2_companies.json"


def load_v2_companies() -> list[dict[str, Any]]:
    with V2_DATA_PATH.open("r", encoding="utf-8") as file:
        rows = json.load(file)
    if not isinstance(rows, list):
        raise ValueError("v2_companies.json must contain a list")
    companies = [normalize_v2_company(row) for row in rows]
    ranks = [item["rank"] for item in companies]
    expected = list(range(1, len(companies) + 1))
    if ranks != expected:
        raise ValueError("v2 company ranks must be contiguous and start at 1")
    return companies


def normalize_v2_company(row: dict[str, Any]) -> dict[str, Any]:
    market = str(row.get("market") or "").upper()
    ticker = str(row.get("ticker") or "").strip().upper()
    if market == "HK" and ticker.endswith(".HK"):
        code = "".join(ch for ch in ticker[:-3] if ch.isdigit())
        ticker = f"{code.zfill(4)}.HK"
    if market == "A":
        ticker = "".join(ch for ch in ticker if ch.isdigit()).zfill(6)
    return {
        "rank": int(row["rank"]),
        "market": market,
        "ticker": ticker,
        "name": str(row.get("name") or ticker).strip(),
        "name_en": str(row.get("name_en") or "").strip(),
        "theme": str(row.get("theme") or "待补充").strip(),
        "industry": str(row.get("industry") or row.get("theme") or "待补充行业").strip(),
        "exchange": str(row.get("exchange") or "").strip(),
        "source_group": str(row.get("source_group") or "").strip(),
        "source_note": str(row.get("source_note") or "").strip(),
    }


V2_COMPANIES: list[dict[str, Any]] = load_v2_companies()


QUALITY_PRESETS = {
    "支付网络": 91,
    "软件/云计算": 90,
    "互联网/云计算": 88,
    "AI算力/半导体": 88,
    "白酒": 87,
    "饮料": 86,
    "交易所": 86,
    "保险/多元控股": 85,
    "公用事业": 78,
    "银行": 76,
    "油气": 74,
    "房地产": 55,
}


def ensure_v2_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS v2_company_ratings (
          list_rank INTEGER PRIMARY KEY,
          company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
          market TEXT NOT NULL,
          ticker TEXT NOT NULL,
          name TEXT NOT NULL,
          theme TEXT NOT NULL,
          moat_score REAL,
          quality_score REAL,
          valuation_score REAL,
          action_score REAL,
          final_rating TEXT,
          final_action TEXT,
          rating_json TEXT NOT NULL,
          rating_version TEXT NOT NULL,
          rated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_v2_company_ratings_market ON v2_company_ratings(market);
        CREATE INDEX IF NOT EXISTS idx_v2_company_ratings_company ON v2_company_ratings(company_id);
        """
    )


def rebuild_v2_ratings(conn) -> dict[str, Any]:
    ensure_v2_schema(conn)
    rows = []
    for item in V2_COMPANIES:
        company_id = upsert_v2_company(conn, item)
        rating = build_v2_rating(conn, item, company_id)
        conn.execute(
            """
            INSERT INTO v2_company_ratings (
              list_rank, company_id, market, ticker, name, theme,
              moat_score, quality_score, valuation_score, action_score,
              final_rating, final_action, rating_json, rating_version, rated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(list_rank) DO UPDATE SET
              company_id = excluded.company_id,
              market = excluded.market,
              ticker = excluded.ticker,
              name = excluded.name,
              theme = excluded.theme,
              moat_score = excluded.moat_score,
              quality_score = excluded.quality_score,
              valuation_score = excluded.valuation_score,
              action_score = excluded.action_score,
              final_rating = excluded.final_rating,
              final_action = excluded.final_action,
              rating_json = excluded.rating_json,
              rating_version = excluded.rating_version,
              rated_at = CURRENT_TIMESTAMP
            """,
            (
                item["rank"],
                company_id,
                item["market"],
                item["ticker"],
                item["name"],
                item["theme"],
                rating["moat_score"],
                rating["quality_score"],
                rating["valuation_score"],
                rating["action_score"],
                rating["final_rating"],
                rating["final_action"],
                to_json(rating),
                V2_VERSION,
            ),
        )
        rows.append(rating)
    return v2_summary(rows)


def refresh_v2_quotes_and_rebuild(conn, limit: int | None = None) -> dict[str, Any]:
    from .services import refresh_company_quote, row_to_company

    ensure_v2_schema(conn)
    started_at = datetime.now(timezone.utc)
    items = V2_COMPANIES[:limit] if limit else V2_COMPANIES
    stats: dict[str, Any] = {
        "started_at": started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "attempted": 0,
        "refreshed": 0,
        "failed": 0,
        "by_market": {},
    }
    for item in items:
        company_id = upsert_v2_company(conn, item)
        conn.commit()
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not row:
            stats["failed"] += 1
            continue
        snapshot_before = latest_snapshot(conn, company_id).get("created_at")
        stats["attempted"] += 1
        stats["by_market"][item["market"]] = stats["by_market"].get(item["market"], 0) + 1
        try:
            refresh_company_quote(conn, row_to_company(row, latest_snapshot(conn, company_id)))
        except Exception:
            stats["failed"] += 1
            conn.commit()
            continue
        conn.commit()
        snapshot_after = latest_snapshot(conn, company_id).get("created_at")
        if snapshot_after and snapshot_after != snapshot_before:
            stats["refreshed"] += 1
    stats["ratings_summary"] = rebuild_v2_ratings(conn)
    conn.commit()
    stats["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return stats


def get_v2_ratings(conn, market: str = "AUTO", q: str = "") -> dict[str, Any]:
    ensure_v2_schema(conn)
    count = conn.execute("SELECT COUNT(*) AS count FROM v2_company_ratings").fetchone()["count"]
    if count != len(V2_COMPANIES):
        rebuild_v2_ratings(conn)
    where = []
    params: list[Any] = []
    if market and market != "AUTO":
        where.append("market = ?")
        params.append(market.upper())
    query = (q or "").strip().lower()
    if query:
        where.append("(LOWER(ticker) LIKE ? OR LOWER(name) LIKE ? OR LOWER(theme) LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"SELECT * FROM v2_company_ratings {where_sql} ORDER BY list_rank",
        params,
    ).fetchall()
    ratings = [v2_rating_row(row) for row in rows]
    return {
        "version": V2_VERSION,
        "as_of": date.today().isoformat(),
        "total": len(ratings),
        "expected_total": len(V2_COMPANIES),
        "summary": v2_summary(ratings),
        "ratings": ratings,
    }


def upsert_v2_company(conn, item: dict[str, Any]) -> str:
    existing = conn.execute(
        "SELECT id FROM companies WHERE market = ? AND UPPER(ticker) = UPPER(?) LIMIT 1",
        (item["market"], item["ticker"]),
    ).fetchone()
    company_id = existing["id"] if existing else company_id_for(item)
    aliases = aliases_for(item)
    tags = [market_label(item["market"]), item["theme"], item["industry"], "2.0重点300"]
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
            company_id,
            item["name"],
            item["name_en"],
            item["ticker"],
            item["market"],
            item["exchange"],
            item["industry"],
            item["theme"],
            f"{item['name']} 是 AI投委会 2.0 重点300公司第 {item['rank']} 位，归属 {item['theme']}。",
            to_json(tags),
            to_json(aliases),
        ),
    )
    return company_id


def build_v2_rating(conn, item: dict[str, Any], company_id: str) -> dict[str, Any]:
    snapshot = latest_snapshot(conn, company_id)
    scorecard = latest_scorecard(conn, company_id)
    base_quality = preset_quality(item)
    scorecard_quality = numeric(scorecard.get("company_quality_score")) if scorecard else None
    scorecard_valuation = numeric(scorecard.get("valuation_attractiveness_score")) if scorecard else None
    dqs = numeric(scorecard.get("data_quality_score")) if scorecard else None
    pe = numeric(snapshot.get("pe_ratio")) if snapshot else None
    roe = numeric(snapshot.get("roe")) if snapshot else None
    moat = moat_score(item)
    quality = blend(base_quality, scorecard_quality, 0.65)
    if roe is not None:
        quality = clamp(quality + min(8, max(-8, (roe - 12) * 0.35)))
    valuation = scorecard_valuation if scorecard_valuation is not None else valuation_from_pe(item, pe)
    action = clamp(quality * 0.48 + valuation * 0.32 + moat * 0.15 + (dqs if dqs is not None else 68) * 0.05)
    final_action = action_label(action, quality, valuation)
    final_rating = rating_label(action)
    return {
        "list_rank": item["rank"],
        "company_id": company_id,
        "market": item["market"],
        "ticker": item["ticker"],
        "original_code": original_code_for(item),
        "name": item["name"],
        "name_en": item["name_en"],
        "theme": item["theme"],
        "industry": item["industry"],
        "exchange": item["exchange"],
        "moat_score": round(moat, 1),
        "quality_score": round(quality, 1),
        "valuation_score": round(valuation, 1),
        "action_score": round(action, 1),
        "data_quality_score": round(dqs if dqs is not None else 68, 1),
        "final_rating": final_rating,
        "final_action": final_action,
        "rating_version": V2_VERSION,
        "scorecard_detail": scorecard_detail(scorecard),
        "rating_basis": {
            "mode": "aics_first_live_evidence",
            "snapshot_used": bool(snapshot),
            "aics_scorecard_used": bool(scorecard),
            "pe_ratio": pe,
            "roe": roe,
        },
    }


def v2_summary(ratings: list[dict[str, Any]]) -> dict[str, Any]:
    by_market: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for rating in ratings:
        by_market[rating["market"]] = by_market.get(rating["market"], 0) + 1
        by_action[rating["final_action"]] = by_action.get(rating["final_action"], 0) + 1
    top = sorted(ratings, key=lambda item: item.get("action_score") or 0, reverse=True)[:10]
    return {
        "total": len(ratings),
        "by_market": by_market,
        "by_action": by_action,
        "top10": [{"rank": item["list_rank"], "ticker": item["ticker"], "name": item["name"], "action_score": item["action_score"], "final_action": item["final_action"]} for item in top],
    }


def latest_snapshot(conn, company_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM company_snapshots WHERE company_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return dict(row) if row else {}


def latest_scorecard(conn, company_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT scorecard_json FROM scorecards WHERE company_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return from_json(row["scorecard_json"], {}) if row else {}


def v2_rating_row(row: Any) -> dict[str, Any]:
    rating = from_json(row["rating_json"], {}) or {}
    rating.update(
        {
            "list_rank": row["list_rank"],
            "company_id": row["company_id"],
            "market": row["market"],
            "ticker": row["ticker"],
            "original_code": rating.get("original_code") or original_code_for(dict(row)),
            "name": row["name"],
            "theme": row["theme"],
            "moat_score": row["moat_score"],
            "quality_score": row["quality_score"],
            "valuation_score": row["valuation_score"],
            "action_score": row["action_score"],
            "final_rating": row["final_rating"],
            "final_action": row["final_action"],
            "rating_version": row["rating_version"],
            "rated_at": row["rated_at"],
        }
    )
    return rating


def scorecard_detail(scorecard: dict[str, Any]) -> dict[str, Any]:
    if not scorecard:
        return {}
    summary = scorecard.get("summary") if isinstance(scorecard.get("summary"), dict) else {}
    return {
        "scoring_version": scorecard.get("scoring_version"),
        "summary_text": summary.get("text"),
        "data_quality_grade": summary.get("data_quality_grade"),
        "company_quality_grade": summary.get("company_quality_grade"),
        "valuation_grade": summary.get("valuation_grade"),
        "confidence": scorecard.get("confidence"),
        "bucket_scores": scorecard.get("bucket_scores") or {},
        "valuation_bucket_scores": scorecard.get("valuation_bucket_scores") or {},
        "data_quality_bucket_scores": scorecard.get("data_quality_bucket_scores") or {},
        "red_flags": (scorecard.get("red_flags") or [])[:6],
        "missing_metrics": (scorecard.get("missing_metrics") or [])[:8],
        "action_rules": (scorecard.get("action_rules") or [])[:6],
    }


def preset_quality(item: dict[str, Any]) -> float:
    industry = item["industry"]
    for key, score in QUALITY_PRESETS.items():
        if key in industry:
            return float(score)
    if any(key in item["theme"] for key in ["消费", "品牌", "科技", "医药"]):
        return 80.0
    if any(key in item["theme"] for key in ["能源", "资源", "电力"]):
        return 73.0
    return 76.0


def moat_score(item: dict[str, Any]) -> float:
    text = f"{item['theme']} {item['industry']}"
    score = 72.0
    for key, delta in {
        "品牌": 10,
        "支付": 14,
        "软件": 12,
        "云计算": 10,
        "白酒": 12,
        "交易所": 12,
        "公用事业": 8,
        "银行": 4,
        "半导体": 7,
        "互联网": 8,
        "医药": 5,
    }.items():
        if key in text:
            score += delta
    return clamp(score)


def valuation_from_pe(item: dict[str, Any], pe: float | None) -> float:
    if pe is None or pe <= 0:
        if any(key in item["industry"] for key in ["银行", "保险", "油气", "公用事业", "煤炭"]):
            return 70.0
        if any(key in item["industry"] for key in ["AI算力", "半导体", "创新药", "SaaS"]):
            return 58.0
        return 64.0
    if pe <= 8:
        return 84.0
    if pe <= 18:
        return 76.0
    if pe <= 30:
        return 66.0
    if pe <= 45:
        return 55.0
    return 42.0


def action_label(action: float, quality: float, valuation: float) -> str:
    if action >= 82 and quality >= 82 and valuation >= 65:
        return "核心买入"
    if action >= 74:
        return "买入"
    if action >= 66:
        return "重点观察"
    if action >= 56:
        return "等待更好价格"
    return "回避"


def rating_label(action: float) -> str:
    if action >= 85:
        return "S"
    if action >= 78:
        return "A"
    if action >= 68:
        return "B"
    if action >= 58:
        return "C"
    return "D"


def blend(base: float, live: float | None, base_weight: float) -> float:
    if live is None:
        return clamp(base)
    return clamp(base * base_weight + live * (1 - base_weight))


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, float(value)))


def numeric(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def company_id_for(item: dict[str, Any]) -> str:
    digest = hashlib.sha256(f"{item['market']}:{item['ticker']}".encode("utf-8")).hexdigest()[:16]
    return f"co_v2_{digest}"


def aliases_for(item: dict[str, Any]) -> list[str]:
    aliases = [item["ticker"], original_code_for(item), item["name"], item["name_en"]]
    if item["market"] == "A":
        suffix = ".SH" if item["exchange"].startswith("SSE") else ".SZ"
        aliases.append(f"{item['ticker']}{suffix}")
    if item["market"] == "HK" and item["ticker"].endswith(".HK"):
        code = item["ticker"].replace(".HK", "")
        aliases.extend([code, code.zfill(5), code.zfill(4)])
    return list(dict.fromkeys([alias for alias in aliases if alias]))


def original_code_for(item: dict[str, Any]) -> str:
    ticker = str(item.get("ticker") or "")
    if item.get("market") == "HK" and ticker.endswith(".HK"):
        return ticker.replace(".HK", "").zfill(5)
    return ticker


def market_label(market: str) -> str:
    return {"US": "美股", "A": "A股", "HK": "港股"}.get(market, market)
