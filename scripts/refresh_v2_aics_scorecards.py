from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.database import get_conn, init_db, to_json  # noqa: E402
from backend.app.scoring.persistence import persist_scorecard  # noqa: E402
from backend.app.services import company_by_id, make_data_pack, new_id, persist_evidence_items  # noqa: E402
from backend.app.v2_universe import V2_COMPANIES, get_v2_ratings, rebuild_v2_ratings, upsert_v2_company  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按第一版 AICS 评分引擎刷新 2.0 重点300公司 scorecard")
    parser.add_argument("--limit", type=int, default=0, help="最多刷新多少家公司；0 表示全部")
    parser.add_argument("--rank", type=int, action="append", default=[], help="只刷新指定排名，可重复传入")
    parser.add_argument("--force", action="store_true", help="已有 AICS scorecard 时也重新采集评分")
    parser.add_argument("--sleep", type=float, default=0.2, help="每家公司之间暂停秒数，降低公开数据源压力")
    parser.add_argument("--timeout", type=int, default=240, help="单家公司最大处理秒数，超时后记录错误并继续")
    parser.add_argument("--progress-file", default="", help="逐家公司追加 JSONL 进度，便于生产环境断点监控")
    parser.add_argument("--source-mode", choices=["full", "snapshot"], default="full", help="full 会联网补采资料；snapshot 只用已有快照和本地证据跑 AICS 引擎")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    init_db()
    selected = [item for item in V2_COMPANIES if not args.rank or item["rank"] in set(args.rank)]
    if args.limit > 0:
        selected = selected[: args.limit]

    results = []
    with get_conn() as conn:
        rebuild_v2_ratings(conn)
        for item in selected:
            company_id = upsert_v2_company(conn, item)
            existing = latest_company_scorecard(conn, company_id)
            if existing and not args.force:
                result = {"rank": item["rank"], "ticker": item["ticker"], "name": item["name"], "status": "reused"}
                results.append(result)
                append_progress(args.progress_file, result)
                continue
            report_id = ensure_aics_report(conn, company_id, item)
            try:
                with company_timeout(args.timeout):
                    company = company_by_id(conn, company_id)
                    data_pack = make_data_pack(company, source_mode=args.source_mode)
                    persist_evidence_items(conn, report_id, company, data_pack)
                    persist_scorecard(conn, report_id, company, data_pack.get("scorecard", {}))
                    conn.execute(
                        """
                        UPDATE committee_reports
                        SET data_pack = ?, status = ?, current_round = ?, final_action = ?,
                            overall_score = ?, confidence = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            to_json(data_pack),
                            "AICS_SCORECARD_DONE",
                            0,
                            data_pack.get("scorecard", {}).get("final_action"),
                            data_pack.get("scorecard", {}).get("investment_action_score"),
                            data_pack.get("scorecard", {}).get("confidence"),
                            report_id,
                        ),
                    )
                result = {"rank": item["rank"], "ticker": item["ticker"], "name": item["name"], "status": "refreshed"}
                results.append(result)
            except Exception as exc:
                result = {"rank": item["rank"], "ticker": item["ticker"], "name": item["name"], "status": "error", "error": str(exc)}
                results.append(result)
            conn.commit()
            append_progress(args.progress_file, result)
            if args.sleep:
                time.sleep(args.sleep)
        summary = rebuild_v2_ratings(conn)
        ratings = get_v2_ratings(conn)

    output = {
        "processed": len(results),
        "refreshed": sum(1 for item in results if item["status"] == "refreshed"),
        "reused": sum(1 for item in results if item["status"] == "reused"),
        "errors": [item for item in results if item["status"] == "error"],
        "results": results,
        "ratings_summary": summary,
        "ratings_total": ratings["total"],
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"processed={output['processed']} refreshed={output['refreshed']} reused={output['reused']} errors={len(output['errors'])}")
        print(f"ratings: {summary['total']} {summary['by_market']}")
        for item in output["errors"][:10]:
            print(f"ERROR rank={item['rank']} {item['ticker']} {item['name']}: {item['error']}")
    return 1 if output["errors"] else 0


class company_timeout:
    def __init__(self, seconds: int) -> None:
        self.seconds = max(0, int(seconds or 0))
        self.previous_handler = None

    def __enter__(self):
        if not self.seconds:
            return self
        self.previous_handler = signal.signal(signal.SIGALRM, self._handle_timeout)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.seconds:
            signal.alarm(0)
            if self.previous_handler is not None:
                signal.signal(signal.SIGALRM, self.previous_handler)
        return False

    def _handle_timeout(self, signum, frame) -> None:
        raise TimeoutError(f"单家公司 AICS 评分超过 {self.seconds} 秒")


def append_progress(path: str, item: dict) -> None:
    if not path:
        return
    progress_path = Path(path)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"ts": time.time(), **item}, ensure_ascii=False) + "\n")


def latest_company_scorecard(conn, company_id: str) -> dict | None:
    row = conn.execute(
        "SELECT scorecard_json FROM scorecards WHERE company_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["scorecard_json"])
    except (TypeError, json.JSONDecodeError):
        return None


def ensure_aics_report(conn, company_id: str, item: dict) -> str:
    row = conn.execute(
        """
        SELECT id FROM committee_reports
        WHERE company_id = ? AND report_title LIKE 'AICS 2.0 批量评分%'
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (company_id,),
    ).fetchone()
    if row:
        return row["id"]
    report_id = new_id("report")
    conn.execute(
        """
        INSERT INTO committee_reports (
          id, company_id, report_date, report_title, selected_experts,
          recommended_experts, chairman, data_pack, status, current_round
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            company_id,
            date.today().isoformat(),
            f"AICS 2.0 批量评分：{item['name']}",
            to_json([]),
            to_json([]),
            to_json({}),
            to_json({}),
            "AICS_SCORECARD_PENDING",
            0,
        ),
    )
    return report_id


if __name__ == "__main__":
    raise SystemExit(main())
