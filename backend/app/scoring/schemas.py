from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCORING_VERSION = "AICS-v2.4"


@dataclass
class MetricScore:
    name: str
    raw_value: Any
    score: float | None
    weight: float
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    status: str = "scored"
    formula: str = ""
    calculation: str = ""
    evidence_required: list[str] = field(default_factory=list)
    data_source: str = ""
    confidence: float | None = None
    missing_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoreBucket:
    key: str
    name: str
    score: float | None
    weight: float
    metrics: list[MetricScore] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    status: str = "scored"
    scored_weight: float = 0.0
    total_weight: float = 0.0
    coverage_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "score": round(self.score, 1) if isinstance(self.score, (int, float)) else None,
            "weight": self.weight,
            "summary": self.summary,
            "status": self.status,
            "scored_weight": round(self.scored_weight, 2),
            "total_weight": round(self.total_weight, 2),
            "coverage_ratio": round(self.coverage_ratio, 3),
            "metrics": [metric.to_dict() for metric in self.metrics],
        }


@dataclass
class CompanyScorecard:
    scoring_version: str
    data_quality_score: float
    company_quality_score: float | None
    valuation_attractiveness_score: float | None
    investment_action_score: float | None
    catalyst_score: float | None
    timing_score: float | None
    grade: str
    final_action: str
    confidence: float
    buckets: list[ScoreBucket] = field(default_factory=list)
    valuation_buckets: list[ScoreBucket] = field(default_factory=list)
    data_quality_buckets: list[ScoreBucket] = field(default_factory=list)
    data_quality_gate: dict = field(default_factory=dict)
    red_flags: list[dict] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)
    action_rules: list[str] = field(default_factory=list)
    matrix: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    score_coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scoring_version": self.scoring_version,
            "data_quality_score": round(self.data_quality_score, 1),
            "company_quality_score": round(self.company_quality_score, 1) if isinstance(self.company_quality_score, (int, float)) else None,
            "valuation_attractiveness_score": round(self.valuation_attractiveness_score, 1) if isinstance(self.valuation_attractiveness_score, (int, float)) else None,
            "investment_action_score": round(self.investment_action_score, 1) if isinstance(self.investment_action_score, (int, float)) else None,
            "catalyst_score": round(self.catalyst_score, 1) if isinstance(self.catalyst_score, (int, float)) else None,
            "timing_score": round(self.timing_score, 1) if isinstance(self.timing_score, (int, float)) else None,
            "grade": self.grade,
            "final_action": self.final_action,
            "confidence": round(self.confidence, 2),
            "buckets": [bucket.to_dict() for bucket in self.buckets],
            "valuation_buckets": [bucket.to_dict() for bucket in self.valuation_buckets],
            "data_quality_buckets": [bucket.to_dict() for bucket in self.data_quality_buckets],
            "data_quality_gate": self.data_quality_gate,
            "bucket_scores": {bucket.key: round(bucket.score, 1) if isinstance(bucket.score, (int, float)) else None for bucket in self.buckets},
            "valuation_bucket_scores": {bucket.key: round(bucket.score, 1) if isinstance(bucket.score, (int, float)) else None for bucket in self.valuation_buckets},
            "data_quality_bucket_scores": {bucket.key: round(bucket.score, 1) if isinstance(bucket.score, (int, float)) else None for bucket in self.data_quality_buckets},
            "red_flags": self.red_flags,
            "missing_metrics": self.missing_metrics,
            "action_rules": self.action_rules,
            "matrix": self.matrix,
            "summary": self.summary,
            "score_coverage": self.score_coverage,
        }
