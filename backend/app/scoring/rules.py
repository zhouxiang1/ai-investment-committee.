from __future__ import annotations

from typing import Any


INDUSTRY_PE_RANGES = {
    "白酒": (18, 28, 38),
    "高端消费": (18, 28, 38),
    "银行": (5, 8, 12),
    "房地产": (6, 10, 16),
    "半导体": (22, 38, 60),
    "AI算力": (25, 45, 70),
    "互联网": (18, 30, 45),
    "SaaS": (28, 45, 70),
    "医药": (20, 35, 55),
    "CXO": (18, 32, 50),
}


def clamp(value: Any, low: float = 0, high: float = 100) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0
    return max(low, min(high, number))


def weighted_average(items: list[tuple[float, float]]) -> float:
    total_weight = sum(weight for _, weight in items)
    if total_weight <= 0:
        return 0
    return sum(score * weight for score, weight in items) / total_weight


def scale_between(value: Any, low: float, high: float, *, inverse: bool = False) -> float:
    value = clamp(value, low, high)
    ratio = (value - low) / max(1e-9, high - low)
    if inverse:
        ratio = 1 - ratio
    return clamp(ratio * 100)


def evidence_ids_by_category(evidence_store: list[dict], category: str, limit: int = 4) -> list[str]:
    return [
        str(item.get("evidence_id"))
        for item in evidence_store
        if item.get("category") == category and item.get("evidence_id")
    ][:limit]


def pick_evidence(evidence_store: list[dict], preferred: list[str], fallback_categories: list[str] | None = None, limit: int = 3) -> list[str]:
    valid = {str(item.get("evidence_id")) for item in evidence_store if item.get("evidence_id")}
    selected = [item for item in preferred if item in valid]
    for category in fallback_categories or []:
        selected.extend(item for item in evidence_ids_by_category(evidence_store, category, limit) if item not in selected)
    return selected[:limit]


def industry_pe_range(company: dict) -> tuple[float, float, float]:
    text = f"{company.get('industry', '')} {company.get('sector', '')} {' '.join(company.get('tags', []) or [])}"
    for key, value in INDUSTRY_PE_RANGES.items():
        if key in text:
            return value
    return (15, 25, 40)


def grade_company_quality(score: float) -> str:
    if score >= 90:
        return "伟大公司"
    if score >= 80:
        return "优秀公司"
    if score >= 70:
        return "好公司但有明显短板"
    if score >= 60:
        return "普通公司"
    if score >= 50:
        return "问题公司"
    return "原则上回避"


def grade_valuation(score: float) -> str:
    if score >= 85:
        return "明显低估"
    if score >= 70:
        return "有吸引力"
    if score >= 55:
        return "大致合理"
    if score >= 40:
        return "偏贵"
    return "明显高估"


def grade_data_quality(score: float) -> str:
    if score >= 90:
        return "数据充分"
    if score >= 75:
        return "可辅助决策"
    if score >= 50:
        return "只能初步观察"
    return "不评级"


def action_from_scorecard(dqs: float, cqs: float, vas: float, red_flags: list[dict]) -> tuple[str, list[str]]:
    rules: list[str] = []
    if dqs < 50:
        return "数据不足暂不评级", ["数据可信度低于 50，禁止输出买入类动作"]
    if any(flag.get("severity") == "major" for flag in red_flags):
        return "回避", ["触发重大红旗风险，优先保护本金"]
    if dqs < 60:
        rules.append("数据可信度低于 60，最高只能观察")
        return "重点观察", rules
    if cqs < 55:
        return "回避", ["公司质量分低于 55，原则上回避"]
    if cqs >= 85 and vas >= 75:
        return "强烈买入", ["高质量公司叠加高估值吸引力"]
    if cqs >= 75 and vas >= 65:
        return "买入", ["优秀公司且当前价格有吸引力"]
    if cqs >= 75 and vas < 65:
        return "等待更好价格", ["好公司但价格不够好"]
    if cqs >= 65 and vas >= 70:
        return "小仓位关注", ["公司质量尚可，估值提供一定赔率"]
    if cqs >= 55:
        return "重点观察", ["公司质量或价格条件尚未达到行动阈值"]
    return "回避", ["公司质量不足"]
