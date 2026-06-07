from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .database import ROOT, from_json, get_conn, init_db, to_json
from .company_universe import company_universe_summary, list_company_universe, sync_company_universe
from .scoring.persistence import load_latest_scorecard, persist_scorecard
from .v2_universe import get_v2_ratings, rebuild_v2_ratings, refresh_v2_quotes_and_rebuild
from .services import (
    ROUND_NAMES,
    call_openai_compatible,
    build_decision_visualization,
    collect_expert_web_research,
    company_by_id,
    company_tags,
    data_quality_gate_status,
    distill_expert_web_research,
    distill_material,
    expert_by_id,
    final_report_markdown,
    list_experts,
    make_data_pack,
    new_id,
    persist_evidence_items,
    recommend_chairman,
    recommend_experts,
    row_to_company,
    row_to_expert,
    run_round,
    sanitize_report_markdown,
    scorecard_needs_refresh,
    search_companies,
    scorecard_from_data_pack,
)


load_dotenv(ROOT / ".env")

app = FastAPI(title="AI投委会 API", version="1.0.0")
ROUND_JOBS: set[str] = set()
REPORT_AUTORUN_JOBS: set[str] = set()
V2_DAILY_REFRESH_META_KEY = "v2_daily_quote_refresh"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("APP_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def basic_auth_guard(request, call_next):
    if os.getenv("APP_BASIC_AUTH_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return await call_next(request)
    username = os.getenv("APP_BASIC_AUTH_USERNAME")
    password = os.getenv("APP_BASIC_AUTH_PASSWORD")
    if not username or not password or request.method == "OPTIONS" or request.url.path == "/api/health":
        return await call_next(request)
    if is_valid_basic_auth(request.headers.get("authorization", ""), username, password):
        return await call_next(request)
    return Response(
        "需要登录后访问 AI投委会。",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="AI Investment Committee", charset="UTF-8"'},
    )


@app.middleware("http")
async def frontend_cache_guard(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/") or (request.method == "GET" and not path.startswith("/api/")):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


class CommitteeCreate(BaseModel):
    ticker: str
    market: str = "AUTO"
    analysis_mode: str = "deep"


class SelectExperts(BaseModel):
    expert_ids: list[str] = Field(min_length=5, max_length=5)


class SelectChairman(BaseModel):
    chairman_id: str | None = None
    auto_selected: bool = True


class ExpertPayload(BaseModel):
    name: str
    name_en: str | None = ""
    category: str = "投资大师"
    nationality: str | None = ""
    role_title: str | None = ""
    bio: str | None = ""
    avatar_url: str | None = ""
    is_active: bool = True
    profile: dict[str, Any] = Field(default_factory=dict)


class CompanySyncPayload(BaseModel):
    markets: list[str] = Field(default_factory=lambda: ["A", "HK", "US"])


class ExpertWebResearchPayload(BaseModel):
    max_sources: int = Field(default=4, ge=1, le=8)
    limit: int | None = Field(default=None, ge=1, le=80)


class V2RatingsQuery(BaseModel):
    market: str = "AUTO"
    q: str = ""


@app.on_event("startup")
async def startup() -> None:
    init_db()
    ensure_v2_ratings_ready()
    resume_interrupted_autoruns()
    if v2_daily_refresh_enabled():
        asyncio.create_task(v2_daily_refresh_loop())


def v2_daily_refresh_enabled() -> bool:
    return os.getenv("AI_COMMITTEE_V2_DAILY_REFRESH", "true").lower() in {"1", "true", "yes", "on"}


def ensure_v2_ratings_ready() -> None:
    try:
        with get_conn() as conn:
            rebuild_v2_ratings(conn)
    except Exception as exc:
        print(f"v2 ratings startup rebuild failed: {exc}")


def v2_refresh_interval_seconds() -> int:
    try:
        return max(3600, int(os.getenv("AI_COMMITTEE_V2_REFRESH_INTERVAL_SECONDS", "86400")))
    except ValueError:
        return 86400


async def v2_daily_refresh_loop() -> None:
    await asyncio.sleep(float(os.getenv("AI_COMMITTEE_V2_REFRESH_STARTUP_DELAY_SECONDS", "8")))
    while True:
        try:
            await maybe_refresh_v2_quotes()
        except Exception as exc:
            print(f"v2 daily refresh failed: {exc}")
        await asyncio.sleep(float(os.getenv("AI_COMMITTEE_V2_REFRESH_CHECK_SECONDS", "3600")))


async def maybe_refresh_v2_quotes() -> None:
    if not v2_refresh_due():
        return
    summary = await asyncio.to_thread(run_v2_quote_refresh)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO app_metadata (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (V2_DAILY_REFRESH_META_KEY, to_json(summary)),
        )


def v2_refresh_due() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT updated_at FROM app_metadata WHERE key = ?", (V2_DAILY_REFRESH_META_KEY,)).fetchone()
    if not row:
        return True
    try:
        updated_at = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
    except ValueError:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated_at).total_seconds() >= v2_refresh_interval_seconds()


def run_v2_quote_refresh() -> dict:
    with get_conn() as conn:
        return refresh_v2_quotes_and_rebuild(conn)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "date": date.today().isoformat(),
        "llm": {
            "enabled": os.getenv("AI_COMMITTEE_USE_LLM", "false").lower() in ["1", "true", "yes"],
            "base_url": os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
            "model": os.getenv("MINIMAX_MODEL", "MiniMax-M2.7"),
            "has_key": bool(os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")),
            "allow_fallback": os.getenv("AI_COMMITTEE_ALLOW_FALLBACK", "false").lower() in ["1", "true", "yes"],
        },
    }


@app.get("/api/companies/search")
def companies_search(q: str = "", market: str = "AUTO") -> dict:
    with get_conn() as conn:
        return {"results": search_companies(conn, q, market)}


@app.get("/api/companies")
def companies(q: str = "", market: str = "AUTO", limit: int = 80, offset: int = 0) -> dict:
    with get_conn() as conn:
        return list_company_universe(conn, q, market, limit, offset)


@app.get("/api/companies/summary")
def companies_summary() -> dict:
    with get_conn() as conn:
        return company_universe_summary(conn)


@app.post("/api/companies/sync")
def companies_sync(payload: CompanySyncPayload) -> dict:
    markets = [market.upper() for market in payload.markets if market.upper() in {"A", "HK", "US"}]
    if not markets:
        raise HTTPException(400, "请至少选择 A / HK / US 中的一个市场")
    with get_conn() as conn:
        return sync_company_universe(conn, markets)


@app.get("/api/v2/ratings")
def v2_ratings(market: str = "AUTO", q: str = "") -> dict:
    with get_conn() as conn:
        return get_v2_ratings(conn, market, q)


@app.post("/api/v2/ratings/rebuild")
def v2_ratings_rebuild(payload: V2RatingsQuery | None = None) -> dict:
    with get_conn() as conn:
        rebuild_v2_ratings(conn)
        market = payload.market if payload else "AUTO"
        q = payload.q if payload else ""
        return get_v2_ratings(conn, market, q)


@app.get("/api/experts")
def experts() -> dict:
    with get_conn() as conn:
        return {"experts": list_experts(conn, active_only=False)}


@app.post("/api/experts")
def create_expert(payload: ExpertPayload) -> dict:
    expert_id = new_id("expert")
    profile_id = new_id("profile")
    profile = normalize_profile(payload.profile)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO experts (id, name, name_en, category, nationality, role_title, bio, avatar_url, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expert_id,
                payload.name,
                payload.name_en,
                payload.category,
                payload.nationality,
                payload.role_title,
                payload.bio,
                payload.avatar_url,
                int(payload.is_active),
            ),
        )
        upsert_profile(conn, profile_id, expert_id, profile)
        return {"expert": expert_by_id(conn, expert_id)}


@app.put("/api/experts/{expert_id}")
def update_expert(expert_id: str, payload: ExpertPayload) -> dict:
    profile = normalize_profile(payload.profile)
    with get_conn() as conn:
        if not conn.execute("SELECT id FROM experts WHERE id = ?", (expert_id,)).fetchone():
            raise HTTPException(404, "专家不存在")
        conn.execute(
            """
            UPDATE experts
            SET name = ?, name_en = ?, category = ?, nationality = ?, role_title = ?,
                bio = ?, avatar_url = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload.name,
                payload.name_en,
                payload.category,
                payload.nationality,
                payload.role_title,
                payload.bio,
                payload.avatar_url,
                int(payload.is_active),
                expert_id,
            ),
        )
        current = conn.execute("SELECT id FROM expert_profiles WHERE expert_id = ?", (expert_id,)).fetchone()
        upsert_profile(conn, current["id"] if current else new_id("profile"), expert_id, profile)
        return {"expert": expert_by_id(conn, expert_id)}


@app.post("/api/experts/{expert_id}/materials")
async def upload_material(
    expert_id: str,
    title: str = Form("未命名材料"),
    material_type: str = Form("article"),
    language: str = Form("zh"),
    source_url: str = Form(""),
    raw_text: str = Form(""),
    file: UploadFile | None = File(None),
) -> dict:
    with get_conn() as conn:
        expert = expert_by_id(conn, expert_id)
        upload_path = ""
        text = raw_text
        if file and file.filename:
            safe_name = f"{expert_id}_{new_id('material')}_{file.filename}".replace("/", "_")
            upload_dir = ROOT / "data" / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            dest = upload_dir / safe_name
            with dest.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            upload_path = str(dest)
            if not text:
                try:
                    text = dest.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    text = f"已上传文件：{file.filename}"
        material_id = new_id("material")
        distill = distill_material(expert, text)
        llm_summary = await call_openai_compatible(
            "你是投资专家画像蒸馏助手。请基于公开材料提取人物投资框架、能力圈、盲区、经典问题和发言风格，输出中文。",
            f"专家：{expert['name']}\n材料：{text[:6000]}",
            temperature=0.2,
        )
        if llm_summary:
            distill["ai_summary"] = llm_summary
        conn.execute(
            """
            INSERT INTO expert_materials (
                id, expert_id, title, material_type, language, source_url,
                uploaded_file_path, raw_text, ai_summary, distilled_points
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                material_id,
                expert_id,
                title,
                material_type,
                language,
                source_url,
                upload_path,
                text,
                distill["ai_summary"],
                to_json(distill["distilled_points"]),
            ),
        )
        return {"material_id": material_id, "distillation_preview": distill}


@app.post("/api/experts/bulk-distill")
async def bulk_distill_experts() -> dict:
    with get_conn() as conn:
        experts = list_experts(conn, active_only=False)
        results = []
        for expert in experts:
            raw_text = (
                f"专家：{expert['name']} / {expert.get('name_en', '')}\n"
                f"身份：{expert.get('role_title', '')}\n"
                f"简介：{expert.get('bio', '')}\n"
                f"投资哲学：{expert.get('profile', {}).get('investment_philosophy', '')}\n"
                f"核心框架：{expert.get('profile', {}).get('core_framework', '')}\n"
                f"能力圈：{'、'.join(expert.get('profile', {}).get('preferred_industries', []))}\n"
                f"盲区：{expert.get('profile', {}).get('weaknesses', '')}\n"
            )
            result = distill_material(expert, raw_text)
            llm_summary = await call_openai_compatible(
                "你是投资专家画像蒸馏助手。请基于公开材料提取人物投资框架、能力圈、盲区、经典问题、权重规则和发言风格，输出中文摘要。",
                raw_text[:6000],
                temperature=0.2,
            )
            if not llm_summary and os.getenv("AI_COMMITTEE_ALLOW_FALLBACK", "false").lower() not in ["1", "true", "yes"]:
                raise HTTPException(400, "LLM 未配置或未启用，无法执行真实 AI 蒸馏。请配置 MINIMAX_API_KEY 并设置 AI_COMMITTEE_USE_LLM=true。")
            if llm_summary:
                result["ai_summary"] = llm_summary
            material_id = f"bulk_distill_{expert['id']}"
            conn.execute(
                """
                INSERT INTO expert_materials (
                    id, expert_id, title, material_type, language, source_url,
                    uploaded_file_path, raw_text, ai_summary, distilled_points
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    raw_text = excluded.raw_text,
                    ai_summary = excluded.ai_summary,
                    distilled_points = excluded.distilled_points
                """,
                (
                    material_id,
                    expert["id"],
                    f"{expert['name']} 批量 AI 蒸馏结果",
                    "bulk_ai_distillation",
                    "zh",
                    "",
                    "",
                    raw_text,
                    result["ai_summary"],
                    to_json(result["distilled_points"]),
                ),
            )
            conn.execute(
                """
                UPDATE expert_profiles
                SET source_summary =
                    CASE
                      WHEN source_summary LIKE '%批量 AI 蒸馏补强%' THEN source_summary
                      ELSE source_summary || CHAR(10) || ?
                    END,
                    question_template = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE expert_id = ?
                """,
                (
                    "批量 AI 蒸馏补强：已把公开材料摘要、能力圈、盲区、发言风格和权重规则写入专家画像。",
                    result["profile_patch"]["question_template"],
                    expert["id"],
                ),
            )
            results.append({"expert_id": expert["id"], "name": expert["name"], "material_id": material_id})
        return {"distilled_count": len(results), "results": results}


@app.post("/api/experts/{expert_id}/distill")
async def distill_expert(expert_id: str) -> dict:
    with get_conn() as conn:
        expert = expert_by_id(conn, expert_id)
        rows = conn.execute(
            "SELECT raw_text FROM expert_materials WHERE expert_id = ? ORDER BY created_at DESC LIMIT 5",
            (expert_id,),
        ).fetchall()
        raw_text = "\n".join(row["raw_text"] or "" for row in rows)
        result = distill_material(expert, raw_text)
        llm_summary = await call_openai_compatible(
            "你是投资专家画像蒸馏助手。请将材料压缩为可执行专家画像补丁，必须中文输出。",
            f"专家：{expert['name']}\n现有画像：{expert.get('profile', {})}\n材料：{raw_text[:8000]}",
            temperature=0.2,
        )
        if not llm_summary and os.getenv("AI_COMMITTEE_ALLOW_FALLBACK", "false").lower() not in ["1", "true", "yes"]:
            raise HTTPException(400, "LLM 未配置或未启用，无法执行真实 AI 蒸馏。请配置 MINIMAX_API_KEY 并设置 AI_COMMITTEE_USE_LLM=true。")
        if llm_summary:
            result["ai_summary"] = llm_summary
        patch = result["profile_patch"]
        conn.execute(
            """
            UPDATE expert_profiles
            SET source_summary = ?, question_template = ?, updated_at = CURRENT_TIMESTAMP
            WHERE expert_id = ?
            """,
            (
                patch["source_summary"],
                patch["question_template"],
                expert_id,
            ),
        )
        return {"distillation": result, "expert": expert_by_id(conn, expert_id)}


@app.post("/api/experts/{expert_id}/web-research")
async def web_research_expert(expert_id: str, payload: ExpertWebResearchPayload | None = None) -> dict:
    payload = payload or ExpertWebResearchPayload()
    with get_conn() as conn:
        expert = expert_by_id(conn, expert_id)
    research = await asyncio.to_thread(collect_expert_web_research, expert, payload.max_sources)
    materials = research.get("materials") or []
    if not materials:
        raise HTTPException(404, "未抓取到可用于蒸馏的公开材料，请稍后重试或手动上传访谈/股东信/传记材料。")
    try:
        patch = await distill_expert_web_research(expert, materials)
    except RuntimeError as exc:
        if os.getenv("AI_COMMITTEE_ALLOW_FALLBACK", "false").lower() not in ["1", "true", "yes"]:
            raise HTTPException(400, f"联网资料已抓取，但 AI 蒸馏需要配置 LLM：{exc}") from exc
        patch = fallback_expert_research_patch(expert, materials)
    with get_conn() as conn:
        persist_expert_research_materials(conn, expert, research, patch)
        apply_expert_profile_patch(conn, expert["id"], patch)
        return {
            "expert": expert_by_id(conn, expert["id"]),
            "research": {
                "source_count": len(materials),
                "sources": [{"title": item.get("title"), "source_url": item.get("source_url"), "domain": item.get("source_domain")} for item in materials],
                "attempts": research.get("attempts") or [],
            },
            "profile_patch": patch,
        }


@app.post("/api/experts/bulk-web-research")
async def bulk_web_research_experts(payload: ExpertWebResearchPayload | None = None) -> dict:
    payload = payload or ExpertWebResearchPayload(max_sources=3)
    with get_conn() as conn:
        experts = list_experts(conn, active_only=False)
    if payload.limit:
        experts = experts[: payload.limit]
    results = []
    for expert in experts:
        research = await asyncio.to_thread(collect_expert_web_research, expert, payload.max_sources)
        materials = research.get("materials") or []
        if not materials:
            results.append({"expert_id": expert["id"], "name": expert["name"], "source_count": 0, "status": "empty"})
            continue
        try:
            patch = await distill_expert_web_research(expert, materials)
        except RuntimeError as exc:
            if os.getenv("AI_COMMITTEE_ALLOW_FALLBACK", "false").lower() not in ["1", "true", "yes"]:
                raise HTTPException(400, f"{expert['name']} 联网资料已抓取，但 AI 蒸馏需要配置 LLM：{exc}") from exc
            patch = fallback_expert_research_patch(expert, materials)
        with get_conn() as conn:
            persist_expert_research_materials(conn, expert, research, patch)
            apply_expert_profile_patch(conn, expert["id"], patch)
        results.append({"expert_id": expert["id"], "name": expert["name"], "source_count": len(materials), "status": "distilled"})
    return {"researched_count": len(results), "results": results}


@app.post("/api/committee/create")
def committee_create(payload: CommitteeCreate) -> dict:
    with get_conn() as conn:
        matches = search_companies(conn, payload.ticker, payload.market)
        if not matches:
            raise HTTPException(404, "未找到匹配公司")
        company = matches[0]
        recommendations = recommend_experts(conn, company, limit=10)
        report_id = new_id("report")
        conn.execute(
            """
            INSERT INTO committee_reports (
                id, company_id, report_date, report_title, recommended_experts,
                selected_experts, chairman, data_pack, status, current_round
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                company["id"],
                date.today().isoformat(),
                f"{company['name']} AI投委会深度报告",
                to_json(recommendations),
                to_json([]),
                to_json({}),
                to_json({}),
                "EXPERTS_RECOMMENDED",
                0,
            ),
        )
        return {
            "report_id": report_id,
            "company": company,
            "company_tags": company_tags(company),
            "recommended_experts": recommendations,
            "status": "EXPERTS_RECOMMENDED",
        }


@app.post("/api/committee/{report_id}/select-experts")
def committee_select_experts(report_id: str, payload: SelectExperts) -> dict:
    with get_conn() as conn:
        report = get_report_row(conn, report_id)
        selected = [expert_by_id(conn, expert_id) for expert_id in payload.expert_ids]
        conn.execute(
            """
            UPDATE committee_reports
            SET selected_experts = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (to_json(selected), "EXPERTS_SELECTED", report["id"]),
        )
        return {"selected_experts": selected, "status": "EXPERTS_SELECTED"}


@app.post("/api/committee/{report_id}/select-chairman")
def committee_select_chairman(report_id: str, payload: SelectChairman) -> dict:
    with get_conn() as conn:
        report = get_report_row(conn, report_id)
        company = company_by_id(conn, report["company_id"])
        selected = from_json(report["selected_experts"], [])
        if len(selected) != 5:
            raise HTTPException(400, "请先选择 5 位专家")
        if payload.auto_selected or not payload.chairman_id:
            chair_result = recommend_chairman(company, selected)
            chairman = chair_result["expert"]
            chairman["chair_reason"] = chair_result["reason"]
            chairman["chair_score"] = chair_result["score"]
        else:
            chairman = next((item for item in selected if item["id"] == payload.chairman_id), None)
            if not chairman:
                raise HTTPException(400, "主席必须来自已选 5 位专家")
            chairman["chair_reason"] = "用户手动指定主席。"
            chairman["chair_score"] = 0
        conn.execute(
            """
            UPDATE committee_reports
            SET chairman = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (to_json(chairman), "CHAIRMAN_SELECTED", report_id),
        )
        return {"chairman": chairman, "status": "CHAIRMAN_SELECTED"}


@app.post("/api/committee/{report_id}/collect-data")
def committee_collect_data(report_id: str) -> dict:
    with get_conn() as conn:
        report = get_report_row(conn, report_id)
        company = company_by_id(conn, report["company_id"])
        data_pack = make_data_pack(company)
        persist_evidence_items(conn, report_id, company, data_pack)
        persist_scorecard(conn, report_id, company, data_pack.get("scorecard", {}))
        conn.execute(
            """
            UPDATE committee_reports
            SET data_pack = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (to_json(data_pack), "DATA_COLLECTION_DONE", report_id),
        )
        gate = data_quality_gate_status(data_pack)
        return {"data_pack": data_pack, "status": "DATA_COLLECTION_DONE", "data_quality_gate": gate}


@app.post("/api/committee/{report_id}/autorun")
async def committee_autorun(report_id: str) -> dict:
    with get_conn() as conn:
        report = get_report_row(conn, report_id)
        ensure_report_ready_for_rounds(report)
        if int(report["current_round"] or 0) >= 5:
            return {"report_id": report_id, "status": report["status"], "running": False, "current_round": report["current_round"]}
        if not report_is_running(report["status"]):
            conn.execute(
                """
                UPDATE committee_reports
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (f"ROUND_{int(report['current_round'] or 0) + 1}_RUNNING", report_id),
            )
    start_report_autorun(report_id)
    return {"report_id": report_id, "status": "AUTO_RUN_STARTED", "running": True}


@app.get("/api/research/runs/{report_id}/evidence")
def research_run_evidence(report_id: str) -> dict:
    with get_conn() as conn:
        state = build_report_state(conn, report_id)
        rows = conn.execute(
            """
            SELECT * FROM evidence_items
            WHERE report_id = ?
            ORDER BY created_at, id
            """,
            (report_id,),
        ).fetchall()
        db_items = [dict(row) for row in rows]
        return {
            "run_id": state["data_pack"].get("run_id") or report_id,
            "report_id": report_id,
            "data_quality": state["data_pack"].get("data_quality", {}),
            "evidence": state["data_pack"].get("evidence_store") or db_items,
            "db_evidence_count": len(db_items),
        }


@app.get("/api/research/runs/{report_id}/agents")
def research_run_agents(report_id: str) -> dict:
    with get_conn() as conn:
        state = build_report_state(conn, report_id)
        return {
            "run_id": state["data_pack"].get("run_id") or report_id,
            "report_id": report_id,
            "selected_experts": state["selected_experts"],
            "chairman": state["chairman"],
            "rounds": state["rounds"],
            "latest_score_matrix": latest_score_matrix(state["rounds"]),
        }


@app.post("/api/committee/{report_id}/round/{round_number}/run")
async def committee_run_round(report_id: str, round_number: int, wait: bool = False) -> dict:
    if round_number not in ROUND_NAMES:
        raise HTTPException(400, "轮次必须在 1-5 之间")
    if wait:
        return await execute_round(report_id, round_number)
    with get_conn() as conn:
        report = get_report_row(conn, report_id)
        selected = from_json(report["selected_experts"], [])
        chairman = from_json(report["chairman"], {})
        data_pack = from_json(report["data_pack"], {})
        if len(selected) != 5:
            raise HTTPException(400, "请先选择 5 位专家")
        if not chairman:
            raise HTTPException(400, "请先选择主席")
        if not data_pack:
            raise HTTPException(400, "请先完成四个 Agent 数据采集")
        gate = data_quality_gate_status(data_pack)
        if not gate.get("passed"):
            missing = "、".join(gate.get("blocking_items", [])[:6])
            raise HTTPException(400, f"DQS 未完成，后续分析暂停。请先补齐：{missing}")
        if round_number > 1 and int(report["current_round"] or 0) < round_number - 1:
            raise HTTPException(400, "每一轮必须在上一轮完成后才能启动")
        existing = conn.execute(
            "SELECT * FROM committee_rounds WHERE report_id = ? AND round_number = ?",
            (report_id, round_number),
        ).fetchone()
        if existing:
            output = from_json(existing["round_output"], {})
            status = f"ROUND_{round_number}_DONE" if round_number < 5 else "FINAL_REPORT_DONE"
            return {"round_number": round_number, "round_name": ROUND_NAMES[round_number], "round_output": output, "status": status, "running": False}
        status = f"ROUND_{round_number}_RUNNING"
        conn.execute(
            """
            UPDATE committee_reports
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, report_id),
        )
    if round_number == 1:
        start_report_autorun(report_id)
        return {"round_number": round_number, "round_name": ROUND_NAMES[round_number], "round_output": None, "status": status, "running": True, "auto_run": True}
    key = f"{report_id}:{round_number}"
    if key not in ROUND_JOBS:
        ROUND_JOBS.add(key)
        asyncio.create_task(run_round_background(report_id, round_number, key))
    return {"round_number": round_number, "round_name": ROUND_NAMES[round_number], "round_output": None, "status": status, "running": True}


async def run_round_background(report_id: str, round_number: int, key: str) -> None:
    try:
        await execute_round(report_id, round_number)
    except Exception as exc:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE committee_reports
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (f"ROUND_{round_number}_FAILED: {str(exc)[:180]}", report_id),
            )
    finally:
        ROUND_JOBS.discard(key)


def start_report_autorun(report_id: str) -> None:
    if report_id in REPORT_AUTORUN_JOBS:
        return
    REPORT_AUTORUN_JOBS.add(report_id)
    asyncio.create_task(run_report_autorun_background(report_id))


async def run_report_autorun_background(report_id: str) -> None:
    try:
        while True:
            with get_conn() as conn:
                report = get_report_row(conn, report_id)
                ensure_report_ready_for_rounds(report)
                current_round = int(report["current_round"] or 0)
                if current_round >= 5:
                    return
                next_round = current_round + 1
                conn.execute(
                    """
                    UPDATE committee_reports
                    SET status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (f"ROUND_{next_round}_RUNNING", report_id),
                )
            round_key = f"{report_id}:{next_round}"
            if round_key in ROUND_JOBS:
                await wait_for_round_job(report_id, next_round)
                continue
            ROUND_JOBS.add(round_key)
            try:
                await execute_round(report_id, next_round)
            finally:
                ROUND_JOBS.discard(round_key)
    except Exception as exc:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE committee_reports
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (f"AUTO_RUN_FAILED: {str(exc)[:180]}", report_id),
            )
    finally:
        REPORT_AUTORUN_JOBS.discard(report_id)


async def wait_for_round_job(report_id: str, round_number: int) -> None:
    for _ in range(240):
        await asyncio.sleep(2)
        with get_conn() as conn:
            report = get_report_row(conn, report_id)
            if "FAILED" in str(report["status"] or ""):
                raise RuntimeError(str(report["status"]))
            if int(report["current_round"] or 0) >= round_number:
                return
    raise RuntimeError(f"第 {round_number} 轮后台任务超时")


async def execute_round(report_id: str, round_number: int) -> dict:
    with get_conn() as conn:
        report = get_report_row(conn, report_id)
        company = company_by_id(conn, report["company_id"])
        selected = from_json(report["selected_experts"], [])
        chairman = from_json(report["chairman"], {})
        data_pack = from_json(report["data_pack"], {})
        if len(selected) != 5:
            raise HTTPException(400, "请先选择 5 位专家")
        if not chairman:
            raise HTTPException(400, "请先选择主席")
        if not data_pack:
            raise HTTPException(400, "请先完成四个 Agent 数据采集")
        gate = data_quality_gate_status(data_pack)
        if not gate.get("passed"):
            missing = "、".join(gate.get("blocking_items", [])[:6])
            raise HTTPException(400, f"DQS 未完成，后续分析暂停。请先补齐：{missing}")
        if round_number > 1 and int(report["current_round"] or 0) < round_number - 1:
            raise HTTPException(400, "每一轮必须在上一轮完成后才能启动")
        existing = conn.execute(
            "SELECT * FROM committee_rounds WHERE report_id = ? AND round_number = ?",
            (report_id, round_number),
        ).fetchone()
        if existing:
            output = from_json(existing["round_output"], {})
            status = f"ROUND_{round_number}_DONE" if round_number < 5 else "FINAL_REPORT_DONE"
            conn.execute(
                """
                UPDATE committee_reports
                SET status = ?, current_round = MAX(current_round, ?), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, round_number, report_id),
            )
            return {"round_number": round_number, "round_name": ROUND_NAMES[round_number], "round_output": output, "status": status, "running": False}
        previous = load_round_outputs(conn, report_id)

    try:
        output = await run_round(company, selected, chairman, data_pack, previous, round_number)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc

    with get_conn() as conn:
        report = get_report_row(conn, report_id)
        existing = conn.execute(
            "SELECT * FROM committee_rounds WHERE report_id = ? AND round_number = ?",
            (report_id, round_number),
        ).fetchone()
        if existing:
            output = from_json(existing["round_output"], output)
        else:
            conn.execute(
                """
                INSERT INTO committee_rounds (
                    id, report_id, round_number, round_name, round_status,
                    round_input, round_output, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    new_id("round"),
                    report_id,
                    round_number,
                    ROUND_NAMES[round_number],
                    "done",
                    to_json({"company": company, "selected_experts": selected, "chairman": chairman, "data_pack": data_pack}),
                    to_json(output),
                ),
            )
        status = f"ROUND_{round_number}_DONE" if round_number < 5 else "FINAL_REPORT_DONE"
        final_markdown = report["final_report_markdown"]
        final_action = report["final_action"]
        final_score = report["overall_score"]
        confidence = report["confidence"]
        if round_number == 5:
            previous = load_round_outputs(conn, report_id)
            previous[5] = output
            final_markdown = final_report_markdown(company, selected, chairman, data_pack, previous, output)
            final_action = output["final_action"]
            final_score = output["overall_score"]
            confidence = output["confidence"]
        conn.execute(
            """
            UPDATE committee_reports
            SET status = ?, current_round = MAX(current_round, ?), final_report_markdown = ?,
                final_action = ?, overall_score = ?, confidence = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, round_number, final_markdown, final_action, final_score, confidence, report_id),
        )
    return {"round_number": round_number, "round_name": ROUND_NAMES[round_number], "round_output": output, "status": status, "running": False}


@app.get("/api/committee/{report_id}/status")
def committee_status(report_id: str) -> dict:
    with get_conn() as conn:
        return build_report_state(conn, report_id)


@app.get("/api/reports")
def reports() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM committee_reports ORDER BY created_at DESC").fetchall()
        return {"reports": [report_summary(conn, row) for row in rows]}


@app.get("/api/reports/{report_id}")
def report_detail(report_id: str) -> dict:
    with get_conn() as conn:
        return build_report_state(conn, report_id)


@app.post("/api/committee/{report_id}/export-pdf")
def export_pdf(report_id: str) -> dict:
    with get_conn() as conn:
        state = build_report_state(conn, report_id)
        if not state.get("final_report_markdown"):
            raise HTTPException(400, "第五轮完成后才能导出 PDF")
        output_dir = Path(os.getenv("REPORT_OUTPUT_DIR", ROOT / "output" / "reports"))
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = output_dir / f"{report_id}.pdf"
        render_pdf(state, pdf_path)
        conn.execute("UPDATE committee_reports SET pdf_path = ? WHERE id = ?", (str(pdf_path), report_id))
        return {"pdf_url": f"/api/reports/{report_id}/pdf", "pdf_path": str(pdf_path)}


@app.get("/api/reports/{report_id}/pdf")
def download_pdf(report_id: str) -> FileResponse:
    with get_conn() as conn:
        row = get_report_row(conn, report_id)
        pdf_path = row["pdf_path"]
        if not pdf_path or not Path(pdf_path).exists():
            raise HTTPException(404, "PDF 尚未生成")
        return FileResponse(pdf_path, media_type="application/pdf", filename=f"{row['report_title']}.pdf")


DIST_DIR = ROOT / "dist"
if DIST_DIR.exists():
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        candidate = (DIST_DIR / full_path).resolve()
        try:
            candidate.relative_to(DIST_DIR.resolve())
        except ValueError:
            raise HTTPException(404, "资源不存在")
        headers = {"Cache-Control": "no-store, max-age=0"}
        if full_path and candidate.is_file():
            return FileResponse(candidate, headers=headers)
        if full_path.startswith("assets/"):
            raise HTTPException(404, "静态资源不存在，请重新构建并部署前端资源")
        return FileResponse(DIST_DIR / "index.html", headers=headers)


def is_valid_basic_auth(header: str, username: str, password: str) -> bool:
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return False
    supplied_username, sep, supplied_password = decoded.partition(":")
    if not sep:
        return False
    return secrets.compare_digest(supplied_username, username) and secrets.compare_digest(supplied_password, password)


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "investment_philosophy": "",
        "core_framework": "",
        "decision_process": "",
        "question_template": "",
        "speaking_style": "",
        "strengths": "",
        "weaknesses": "",
        "preferred_industries": [],
        "avoided_industries": [],
        "market_tags": [],
        "style_tags": [],
        "risk_preference": "中等",
        "time_horizon": "3-5年",
        "source_summary": "",
    }
    merged = {**defaults, **(profile or {})}
    for key in ["preferred_industries", "avoided_industries", "market_tags", "style_tags"]:
        if isinstance(merged[key], str):
            merged[key] = [item.strip() for item in merged[key].split(",") if item.strip()]
    return merged


def upsert_profile(conn, profile_id: str, expert_id: str, profile: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO expert_profiles (
            id, expert_id, investment_philosophy, core_framework, decision_process,
            question_template, speaking_style, strengths, weaknesses, preferred_industries,
            avoided_industries, market_tags, style_tags, risk_preference, time_horizon, source_summary
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            investment_philosophy = excluded.investment_philosophy,
            core_framework = excluded.core_framework,
            decision_process = excluded.decision_process,
            question_template = excluded.question_template,
            speaking_style = excluded.speaking_style,
            strengths = excluded.strengths,
            weaknesses = excluded.weaknesses,
            preferred_industries = excluded.preferred_industries,
            avoided_industries = excluded.avoided_industries,
            market_tags = excluded.market_tags,
            style_tags = excluded.style_tags,
            risk_preference = excluded.risk_preference,
            time_horizon = excluded.time_horizon,
            source_summary = excluded.source_summary,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            profile_id,
            expert_id,
            profile["investment_philosophy"],
            profile["core_framework"],
            profile["decision_process"],
            profile["question_template"],
            profile["speaking_style"],
            profile["strengths"],
            profile["weaknesses"],
            to_json(profile["preferred_industries"]),
            to_json(profile["avoided_industries"]),
            to_json(profile["market_tags"]),
            to_json(profile["style_tags"]),
            profile["risk_preference"],
            profile["time_horizon"],
            profile["source_summary"],
        ),
    )


def fallback_expert_research_patch(expert: dict, materials: list[dict]) -> dict:
    profile = expert.get("profile", {})
    titles = "；".join(str(item.get("title") or "") for item in materials[:4])
    sources = "；".join(str(item.get("source_domain") or item.get("source_url") or "") for item in materials[:4])
    source_summary = (
        f"{profile.get('source_summary', '')}\n"
        f"联网公开材料补强：抓取 {len(materials)} 条资料（{sources}），标题包括：{titles}。"
        "LLM 未启用时仅写入来源索引；完整框架蒸馏需启用模型。"
    ).strip()
    return {
        "investment_philosophy": profile.get("investment_philosophy", ""),
        "core_framework": profile.get("core_framework", ""),
        "decision_process": profile.get("decision_process", ""),
        "question_template": profile.get("question_template", ""),
        "speaking_style": profile.get("speaking_style", ""),
        "strengths": profile.get("strengths", ""),
        "weaknesses": profile.get("weaknesses", ""),
        "preferred_industries": profile.get("preferred_industries", []),
        "avoided_industries": profile.get("avoided_industries", []),
        "market_tags": profile.get("market_tags", []),
        "style_tags": profile.get("style_tags", []),
        "risk_preference": profile.get("risk_preference", "中等"),
        "time_horizon": profile.get("time_horizon", "3-5年"),
        "source_summary": source_summary,
        "decision_rules": [],
        "source_notes": [{"title": item.get("title"), "url": item.get("source_url"), "usefulness": "待 LLM 蒸馏"} for item in materials[:4]],
    }


def persist_expert_research_materials(conn, expert: dict, research: dict, patch: dict) -> None:
    for item in research.get("materials") or []:
        url = str(item.get("source_url") or "")
        material_id = f"web_research_{expert['id']}_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"
        conn.execute(
            """
            INSERT INTO expert_materials (
                id, expert_id, title, material_type, language, source_url,
                uploaded_file_path, raw_text, ai_summary, distilled_points
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                raw_text = excluded.raw_text,
                ai_summary = excluded.ai_summary,
                distilled_points = excluded.distilled_points,
                source_url = excluded.source_url
            """,
            (
                material_id,
                expert["id"],
                item.get("title") or "联网公开材料",
                "web_research",
                "zh/en",
                url,
                item.get("text_path") or "",
                item.get("raw_text") or "",
                patch.get("source_summary", "")[:2000],
                to_json({"profile_patch": patch, "source": item}),
            ),
        )


def apply_expert_profile_patch(conn, expert_id: str, patch: dict) -> None:
    expert = expert_by_id(conn, expert_id)
    current = normalize_profile(expert.get("profile", {}))
    merged = {**current}
    for key in [
        "investment_philosophy",
        "core_framework",
        "decision_process",
        "question_template",
        "speaking_style",
        "strengths",
        "weaknesses",
        "risk_preference",
        "time_horizon",
        "source_summary",
    ]:
        value = patch.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    for key in ["preferred_industries", "avoided_industries", "market_tags", "style_tags"]:
        patch_values = patch.get(key)
        if isinstance(patch_values, str):
            patch_values = [item.strip() for item in patch_values.split(",") if item.strip()]
        if isinstance(patch_values, list) and patch_values:
            merged[key] = list(dict.fromkeys([str(item).strip() for item in current.get(key, []) + patch_values if str(item).strip()]))[:16]
    rules = patch.get("decision_rules")
    if isinstance(rules, list) and rules:
        rule_text = "；".join(str(item).strip() for item in rules[:8] if str(item).strip())
        if rule_text and rule_text not in merged["question_template"]:
            merged["question_template"] = f"{merged['question_template']}\n可执行规则：{rule_text}".strip()
    profile_row = conn.execute("SELECT id FROM expert_profiles WHERE expert_id = ?", (expert_id,)).fetchone()
    upsert_profile(conn, profile_row["id"] if profile_row else new_id("profile"), expert_id, normalize_profile(merged))


def get_report_row(conn, report_id: str):
    row = conn.execute("SELECT * FROM committee_reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, "报告不存在")
    return row


def ensure_report_ready_for_rounds(report: Any) -> None:
    selected = from_json(report["selected_experts"], [])
    chairman = from_json(report["chairman"], {})
    data_pack = from_json(report["data_pack"], {})
    if len(selected) != 5:
        raise HTTPException(400, "请先选择 5 位专家")
    if not chairman:
        raise HTTPException(400, "请先选择主席")
    if not data_pack:
        raise HTTPException(400, "请先完成四个 Agent 数据采集")
    gate = data_quality_gate_status(data_pack)
    if not gate.get("passed"):
        missing = "、".join(gate.get("blocking_items", [])[:6])
        raise HTTPException(400, f"DQS 未完成，后续分析暂停。请先补齐：{missing}")


def report_is_running(status: str | None) -> bool:
    value = str(status or "")
    return "RUNNING" in value or value.startswith("AUTO_RUN")


def resume_interrupted_autoruns() -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id FROM committee_reports
            WHERE current_round < 5
              AND (status LIKE 'ROUND_%_RUNNING' OR status LIKE 'AUTO_RUN%')
            ORDER BY updated_at DESC
            LIMIT 20
            """
        ).fetchall()
    for row in rows:
        start_report_autorun(row["id"])


def load_round_outputs(conn, report_id: str) -> dict[int, Any]:
    rows = conn.execute(
        "SELECT round_number, round_output FROM committee_rounds WHERE report_id = ? ORDER BY round_number",
        (report_id,),
    ).fetchall()
    return {row["round_number"]: from_json(row["round_output"], {}) for row in rows}


def sanitize_display_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_report_markdown(value)
    if isinstance(value, list):
        return [sanitize_display_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_display_value(item) for key, item in value.items()}
    return value


def build_report_state(conn, report_id: str, refresh_missing_data: bool = True) -> dict:
    row = get_report_row(conn, report_id)
    company = company_by_id(conn, row["company_id"], refresh_quote=False)
    data_pack = from_json(row["data_pack"], {})
    if refresh_missing_data and data_pack_needs_financial_series_refresh(data_pack):
        data_pack = make_data_pack(company)
        persist_evidence_items(conn, report_id, company, data_pack)
        persist_scorecard(conn, report_id, company, data_pack.get("scorecard", {}))
        conn.execute(
            """
            UPDATE committee_reports
            SET data_pack = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (to_json(data_pack), report_id),
        )
    scorecard = scorecard_from_data_pack(company, data_pack) if isinstance(data_pack, dict) and data_pack else None
    if not scorecard:
        scorecard = load_latest_scorecard(conn, report_id)
        if scorecard and isinstance(data_pack, dict):
            data_pack["scorecard"] = scorecard
    selected_experts = from_json(row["selected_experts"], [])
    chairman = from_json(row["chairman"], {})
    rounds = conn.execute(
        "SELECT * FROM committee_rounds WHERE report_id = ? ORDER BY round_number",
        (report_id,),
    ).fetchall()
    round_outputs = [
        {
            "id": round_row["id"],
            "round_number": round_row["round_number"],
            "round_name": round_row["round_name"],
            "round_status": round_row["round_status"],
            "round_output": sanitize_display_value(from_json(round_row["round_output"], {})),
            "completed_at": round_row["completed_at"],
        }
        for round_row in rounds
    ]
    final_round = next((item for item in round_outputs if item["round_number"] == 5), None)
    decision_visualization = {}
    if final_round:
        decision_visualization = final_round.get("round_output", {}).get("decision_visualization", {}) or {}
    if scorecard:
        decision_visualization = build_decision_visualization(
            {
                "scorecard": scorecard,
                "final_action": row["final_action"],
                "overall_score": row["overall_score"],
                "confidence": row["confidence"],
            },
            data_pack,
        )
    gate = data_quality_gate_status(data_pack) if isinstance(data_pack, dict) else {"passed": False, "blocking_items": ["未完成数据采集"]}
    display_markdown = sanitize_report_markdown(row["final_report_markdown"]) if row["final_report_markdown"] else ""
    if final_round and isinstance(data_pack, dict) and scorecard:
        previous_rounds = {item["round_number"]: item["round_output"] for item in round_outputs}
        display_markdown = final_report_markdown(company, selected_experts, chairman, data_pack, previous_rounds, final_round.get("round_output", {}))
    return {
        "report_id": row["id"],
        "report_title": row["report_title"],
        "report_date": row["report_date"],
        "company": company,
        "company_tags": company_tags(company),
        "recommended_experts": from_json(row["recommended_experts"], []),
        "selected_experts": selected_experts,
        "chairman": chairman,
        "data_pack": data_pack,
        "scorecard": scorecard or {},
        "status": row["status"],
        "current_round": row["current_round"],
        "rounds": round_outputs,
        "data_quality_gate": gate,
        "decision_visualization": decision_visualization,
        "final_action": row["final_action"],
        "overall_score": row["overall_score"],
        "confidence": row["confidence"],
        "final_report_markdown": display_markdown,
        "pdf_url": f"/api/reports/{row['id']}/pdf" if row["pdf_path"] else "",
        "created_at": row["created_at"],
    }


def data_pack_needs_financial_series_refresh(data_pack: dict) -> bool:
    if not isinstance(data_pack, dict) or not data_pack:
        return False
    series = data_pack.get("financial_series") or (data_pack.get("analyst_pack") or {}).get("financial_series") or []
    if isinstance(series, list) and len(series) >= 2 and not scorecard_needs_refresh(data_pack.get("scorecard") or {}):
        return False
    scorecard = data_pack.get("scorecard") if isinstance(data_pack.get("scorecard"), dict) else {}
    if scorecard and scorecard_needs_refresh(scorecard):
        return True
    missing_text = "\n".join(str(item) for item in ((scorecard or {}).get("missing_metrics") or data_pack.get("data_quality", {}).get("missing_data") or []))
    if any(key in missing_text for key in ["pending_series", "收入/利润/现金流", "增长稳定性", "多年财务序列"]):
        return True
    if not isinstance(series, list) or len(series) < 2:
        evidence = data_pack.get("evidence_store") or []
        return any(item.get("category") == "financial_statement" for item in evidence if isinstance(item, dict))
    return False


def report_summary(conn, row: Any) -> dict:
    state = build_report_state(conn, row["id"], refresh_missing_data=False)
    return {
        "report_id": state["report_id"],
        "company": state["company"],
        "report_date": state["report_date"],
        "selected_experts": state["selected_experts"],
        "chairman": state["chairman"],
        "overall_score": state["overall_score"],
        "final_action": state["final_action"],
        "status": state["status"],
        "current_round": state["current_round"],
        "pdf_url": state["pdf_url"],
        "created_at": state["created_at"],
    }


def latest_score_matrix(rounds: list[dict]) -> list[dict]:
    first = next((round_item for round_item in rounds if round_item["round_number"] == 1), None)
    third = next((round_item for round_item in rounds if round_item["round_number"] == 3), None)
    by_expert: dict[str, dict[str, Any]] = {}
    for item in (first or {}).get("round_output", {}).get("speeches", []):
        if not isinstance(item, dict):
            continue
        key = item.get("expert_id") or item.get("expert")
        by_expert[key] = {
            "expert": item.get("expert"),
            "expert_id": item.get("expert_id"),
            "stance": item.get("stance") or item.get("initial_action"),
            "initial_score": item.get("score", item.get("initial_score")),
            "confidence": item.get("confidence"),
            "fit_score": item.get("fit_score"),
            "thesis": item.get("thesis") or item.get("core_judgment"),
            "evidence_ids": item.get("evidence_ids", []),
        }
    for item in (third or {}).get("round_output", {}).get("revisions", []):
        if not isinstance(item, dict):
            continue
        key = item.get("expert_id") or item.get("expert")
        row = by_expert.setdefault(key, {"expert": item.get("expert"), "expert_id": item.get("expert_id")})
        row["revised_score"] = item.get("score", item.get("new_score"))
        row["final_action"] = item.get("final_action")
        row["remaining_disagreements"] = item.get("remaining_disagreements", [])
    return list(by_expert.values())


def render_pdf(state: dict, pdf_path: Path) -> None:
    font_name = register_cjk_font()
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="CJKTitle",
            fontName=font_name,
            fontSize=20,
            leading=28,
            textColor=colors.HexColor("#111827"),
            spaceAfter=14,
        )
    )
    styles.add(ParagraphStyle(name="CJKHeading", fontName=font_name, fontSize=13, leading=18, textColor=colors.HexColor("#1f2937"), spaceBefore=10, spaceAfter=8))
    styles.add(ParagraphStyle(name="CJKBody", fontName=font_name, fontSize=9.5, leading=15, textColor=colors.HexColor("#374151")))
    story: list[Any] = []
    story.append(Paragraph("AI投委会深度报告", styles["CJKTitle"]))
    company = state["company"]
    decision = state.get("decision_visualization") or {}
    summary_table = Table(
        [
            ["公司", company["name"], "代码", company["ticker"]],
            ["市场", f"{company['market']} / {company['exchange']}", "日期", state["report_date"]],
            ["建议", decision.get("primary_action") or state.get("final_action") or "-", "所在象限", decision.get("quadrant_title") or "-"],
            ["公司质量 CQS", str(decision.get("company_quality_score") or "-"), "估值吸引力 VAS", str(decision.get("valuation_attractiveness_score") or "-")],
            ["投资行动 IAS", str(decision.get("investment_action_score") or state.get("overall_score") or "-"), "数据可信度 DQS", "已通过" if decision.get("data_quality_passed") else "未通过"],
        ],
        colWidths=[2.2 * cm, 6.2 * cm, 2.2 * cm, 5.6 * cm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.25 * cm))
    markdown = state.get("final_report_markdown") or ""
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 0.12 * cm))
            continue
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            story.append(Paragraph(pdf_paragraph_text(line.replace("## ", "")), styles["CJKHeading"]))
        elif line.startswith("### "):
            story.append(Paragraph(pdf_paragraph_text(line.replace("### ", "")), styles["CJKBody"]))
        elif line.startswith("- "):
            story.append(Paragraph(pdf_paragraph_text("- " + line[2:]), styles["CJKBody"]))
        else:
            story.append(Paragraph(pdf_paragraph_text(line), styles["CJKBody"]))
    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, rightMargin=1.4 * cm, leftMargin=1.4 * cm, topMargin=1.2 * cm, bottomMargin=1.2 * cm)
    doc.build(story)


def pdf_paragraph_text(value: Any) -> str:
    text = sanitize_report_markdown(str(value or ""))
    text = text.replace("**", "")
    return escape(text)


def register_cjk_font() -> str:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont("AppCJK", path))
                return "AppCJK"
            except Exception:
                continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"
