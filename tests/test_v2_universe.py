from __future__ import annotations

import sqlite3
import unittest
from collections import Counter
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.v2_universe import V2_COMPANIES, V2_VERSION, get_v2_ratings, rebuild_v2_ratings  # noqa: E402


class V2UniverseTest(unittest.TestCase):
    def test_target_list_has_exactly_100_ranked_companies(self) -> None:
        self.assertEqual(len(V2_COMPANIES), 100)
        self.assertEqual([item["rank"] for item in V2_COMPANIES], list(range(1, 101)))
        by_market = Counter(item["market"] for item in V2_COMPANIES)
        self.assertEqual(by_market, {"US": 40, "A": 35, "HK": 25})

    def test_rebuild_persists_100_ratings(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE companies (
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
            CREATE TABLE company_snapshots (
              id TEXT PRIMARY KEY,
              company_id TEXT,
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
            CREATE TABLE scorecards (
               id TEXT PRIMARY KEY,
               report_id TEXT,
               company_id TEXT,
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
            """
        )
        summary = rebuild_v2_ratings(conn)
        result = get_v2_ratings(conn)
        self.assertEqual(summary["total"], 100)
        self.assertEqual(result["total"], 100)
        self.assertEqual(result["expected_total"], 100)
        self.assertEqual(result["version"], V2_VERSION)
        self.assertEqual(result["summary"]["by_market"], {"US": 40, "A": 35, "HK": 25})
        self.assertTrue(all(item["final_action"] for item in result["ratings"]))
        self.assertTrue(all(0 <= item["action_score"] <= 100 for item in result["ratings"]))


if __name__ == "__main__":
    unittest.main()
