from __future__ import annotations


def detect_red_flags(company: dict, data_pack: dict, evidence_store: list[dict]) -> list[dict]:
    snap = company.get("snapshot", {}) or {}
    tags = company.get("tags", []) or []
    risks = data_pack.get("risk_summary", {}).get("risk_tags") or data_pack.get("fundamental", {}).get("key_risks") or []
    flags: list[dict] = []

    if any("造假" in str(risk) or "审计" in str(risk) for risk in risks):
        flags.append({"flag": "存在财务造假、审计或财务重述相关风险标签", "severity": "major", "evidence_ids": ["ev_risk_tags"]})
    if snap.get("debt_to_equity") and snap.get("debt_to_equity") > 180:
        flags.append({"flag": "资产负债表杠杆过高，需要优先验证短债和现金流", "severity": "high", "evidence_ids": ["ev_financial_margin_roe"]})
    if "高估值" in tags and (snap.get("pe_ratio") or 0) > 60:
        flags.append({"flag": "高估值与高预期叠加，容错率低", "severity": "high", "evidence_ids": ["ev_valuation_pe_pb"]})
    if "监管风险" in " ".join(str(risk) for risk in risks):
        flags.append({"flag": "监管变量可能直接影响商业模式或估值中枢", "severity": "medium", "evidence_ids": ["ev_risk_tags"]})
    if not data_pack.get("collection_summary", {}).get("financial_statement_count"):
        flags.append({"flag": "财报三表覆盖不足，财务质量评分存在上限", "severity": "medium", "evidence_ids": []})
    if not data_pack.get("collection_summary", {}).get("filing_count"):
        flags.append({"flag": "公告/监管文件正文覆盖不足，管理层与治理判断存在上限", "severity": "medium", "evidence_ids": []})
    return flags
