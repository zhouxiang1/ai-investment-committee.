from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.database import get_conn, init_db  # noqa: E402
from backend.app.v2_universe import get_v2_ratings, rebuild_v2_ratings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild AI投委会 2.0 重点100公司评级")
    parser.add_argument("--market", default="AUTO", choices=["AUTO", "US", "A", "HK"])
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    init_db()
    with get_conn() as conn:
        summary = rebuild_v2_ratings(conn)
        result = get_v2_ratings(conn, args.market)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"v2 ratings rebuilt: {summary['total']} companies")
        print(f"by market: {summary['by_market']}")
        print(f"by action: {summary['by_action']}")
        print("top10:")
        for item in summary["top10"]:
            print(f"  {item['rank']:>3}. {item['ticker']:<8} {item['name']:<12} {item['action_score']:>5} {item['final_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
