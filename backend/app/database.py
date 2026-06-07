from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("APP_DB_PATH", ROOT / "data" / "ai_committee.sqlite"))


def ensure_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    Path(os.getenv("REPORT_OUTPUT_DIR", ROOT / "output" / "reports")).mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def init_db() -> None:
    ensure_dirs()
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS experts (
               id TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               name_en TEXT,
               category TEXT,
               nationality TEXT,
               role_title TEXT,
               bio TEXT,
               avatar_url TEXT,
               is_active INTEGER DEFAULT 1,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS expert_profiles (
               id TEXT PRIMARY KEY,
               expert_id TEXT REFERENCES experts(id) ON DELETE CASCADE,
               investment_philosophy TEXT,
               core_framework TEXT,
               decision_process TEXT,
               question_template TEXT,
               speaking_style TEXT,
               strengths TEXT,
               weaknesses TEXT,
               preferred_industries TEXT,
               avoided_industries TEXT,
               market_tags TEXT,
               style_tags TEXT,
               risk_preference TEXT,
               time_horizon TEXT,
               source_summary TEXT,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS expert_materials (
               id TEXT PRIMARY KEY,
               expert_id TEXT REFERENCES experts(id) ON DELETE CASCADE,
               title TEXT,
               material_type TEXT,
               language TEXT,
               source_url TEXT,
               uploaded_file_path TEXT,
               raw_text TEXT,
               ai_summary TEXT,
               distilled_points TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS expert_company_fit (
               id TEXT PRIMARY KEY,
               expert_id TEXT REFERENCES experts(id) ON DELETE CASCADE,
               industry_tag TEXT,
               company_tag TEXT,
               fit_score INTEGER,
               reason TEXT
            );

            CREATE TABLE IF NOT EXISTS companies (
               id TEXT PRIMARY KEY,
               name TEXT,
               name_en TEXT,
               ticker TEXT,
               market TEXT,
               exchange TEXT,
               industry TEXT,
               sector TEXT,
               description TEXT,
               tags TEXT,
               aliases TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS company_snapshots (
              id TEXT PRIMARY KEY,
              company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
              snapshot_date TEXT,
              price REAL,
              market_cap REAL,
              pe_ratio REAL,
              pb_ratio REAL,
              ps_ratio REAL,
              revenue REAL,
              net_income REAL,
              gross_margin REAL,
              net_margin REAL,
              roe REAL,
              debt_to_equity REAL,
              free_cash_flow REAL,
              raw_data TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS committee_reports (
               id TEXT PRIMARY KEY,
               company_id TEXT REFERENCES companies(id),
               report_date TEXT,
               report_title TEXT,
               selected_experts TEXT,
               recommended_experts TEXT,
               chairman TEXT,
               data_pack TEXT,
               status TEXT,
               current_round INTEGER DEFAULT 0,
               final_action TEXT,
               overall_score INTEGER,
               confidence REAL,
               final_report_markdown TEXT,
               pdf_path TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS committee_rounds (
               id TEXT PRIMARY KEY,
               report_id TEXT REFERENCES committee_reports(id) ON DELETE CASCADE,
               round_number INTEGER,
               round_name TEXT,
               round_status TEXT,
               round_input TEXT,
               round_output TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS evidence_items (
               id TEXT PRIMARY KEY,
               report_id TEXT REFERENCES committee_reports(id) ON DELETE CASCADE,
               company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
               security_id TEXT,
               category TEXT NOT NULL,
               title TEXT NOT NULL,
               summary TEXT,
               raw_value TEXT,
               normalized_value TEXT,
               unit TEXT,
               period TEXT,
               date TEXT,
               source_provider TEXT,
               source_url TEXT,
               source_document_id TEXT,
               extracted_quote TEXT,
               confidence REAL,
               freshness_score REAL,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_evidence_report_id ON evidence_items(report_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_company_id ON evidence_items(company_id);

            CREATE TABLE IF NOT EXISTS scorecards (
               id TEXT PRIMARY KEY,
               report_id TEXT REFERENCES committee_reports(id) ON DELETE CASCADE,
               company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
               scoring_version TEXT,
               data_quality_score REAL,
               company_quality_score REAL,
               valuation_attractiveness_score REAL,
               investment_action_score REAL,
               final_action TEXT,
               confidence REAL,
               scorecard_json TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scoring_items (
               id TEXT PRIMARY KEY,
               scorecard_id TEXT REFERENCES scorecards(id) ON DELETE CASCADE,
               bucket TEXT,
               metric_name TEXT,
               raw_value TEXT,
               normalized_score REAL,
               weight REAL,
               weighted_score REAL,
               reason TEXT,
               evidence_ids TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_scorecards_report_id ON scorecards(report_id);
            CREATE INDEX IF NOT EXISTS idx_scoring_items_scorecard_id ON scoring_items(scorecard_id);

            CREATE TABLE IF NOT EXISTS app_metadata (
               key TEXT PRIMARY KEY,
               value TEXT,
               updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        from .seed_data import seed_database

        seed_database(conn)
