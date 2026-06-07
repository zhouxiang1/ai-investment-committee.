from __future__ import annotations

import json
from typing import Any


def persist_scorecard(conn, report_id: str, company: dict, scorecard: dict) -> None:
    if not scorecard:
        return
    scorecard_id = f"scorecard_{report_id}"
    conn.execute("DELETE FROM scoring_items WHERE scorecard_id = ?", (scorecard_id,))
    conn.execute("DELETE FROM scorecards WHERE id = ?", (scorecard_id,))
    conn.execute(
        """
        INSERT INTO scorecards (
          id, report_id, company_id, scoring_version, data_quality_score,
          company_quality_score, valuation_attractiveness_score,
          investment_action_score, final_action, confidence, scorecard_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scorecard_id,
            report_id,
            company["id"],
            scorecard.get("scoring_version"),
            scorecard.get("data_quality_score"),
            scorecard.get("company_quality_score"),
            scorecard.get("valuation_attractiveness_score"),
            scorecard.get("investment_action_score"),
            scorecard.get("final_action"),
            scorecard.get("confidence"),
            json.dumps(scorecard, ensure_ascii=False),
        ),
    )
    index = 0
    for bucket_group in ["data_quality_buckets", "buckets", "valuation_buckets"]:
        for bucket in scorecard.get(bucket_group, []) or []:
            for metric in bucket.get("metrics", []) or []:
                index += 1
                score = metric.get("score")
                weight = metric.get("weight")
                weighted = None
                try:
                    weighted = float(score) * float(weight) if score is not None and weight is not None else None
                except (TypeError, ValueError):
                    weighted = None
                conn.execute(
                    """
                    INSERT INTO scoring_items (
                      id, scorecard_id, bucket, metric_name, raw_value,
                      normalized_score, weight, weighted_score, reason, evidence_ids
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{scorecard_id}_{index}",
                        scorecard_id,
                        bucket.get("key") or bucket.get("name"),
                        metric.get("name"),
                        serialize(metric.get("raw_value")),
                        metric.get("score"),
                        metric.get("weight"),
                        weighted,
                        metric.get("reason"),
                        json.dumps(metric.get("evidence_ids") or [], ensure_ascii=False),
                    ),
                )


def load_latest_scorecard(conn, report_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT scorecard_json FROM scorecards WHERE report_id = ? ORDER BY created_at DESC LIMIT 1",
        (report_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["scorecard_json"])
    except (TypeError, json.JSONDecodeError):
        return None


def serialize(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
