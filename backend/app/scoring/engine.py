from __future__ import annotations

import json
from typing import Any

from .red_flags import detect_red_flags
from .rules import (
    action_from_scorecard,
    clamp,
    evidence_ids_by_category,
    grade_company_quality,
    grade_data_quality,
    grade_valuation,
    industry_pe_range,
    pick_evidence,
    scale_between,
    weighted_average,
)
from .schemas import SCORING_VERSION, CompanyScorecard, MetricScore, ScoreBucket


NO_EVIDENCE_REASON = "缺少可回溯证据，指标不评分。"


def build_company_scorecard(company: dict, data_pack: dict) -> dict:
    evidence_store = data_pack.get("evidence_store", []) or []
    red_flags = detect_red_flags(company, data_pack, evidence_store)
    missing_metrics = build_missing_metrics(data_pack)

    data_quality_buckets = score_data_quality(data_pack, evidence_store)
    dqs_gate = data_quality_gate(data_pack)
    dqs = score_data_quality_total(data_pack, data_quality_buckets, dqs_gate)

    cqs_buckets = score_company_quality(company, data_pack, evidence_store, missing_metrics, red_flags)
    cqs = weighted_bucket_average(cqs_buckets)

    valuation_buckets = score_valuation(company, data_pack, evidence_store, missing_metrics)
    vas = weighted_bucket_average(valuation_buckets)

    catalyst = score_catalyst(data_pack, evidence_store)
    timing = score_timing(data_pack, evidence_store)
    ias_raw = investment_action_score(cqs, vas, catalyst, timing)
    action, action_rules = scorecard_action(dqs, cqs, vas, red_flags, dqs_gate)
    if ias_raw is not None and dqs < 60:
        ias_raw = min(ias_raw, 58)
    if ias_raw is not None and any(flag.get("severity") == "major" for flag in red_flags):
        ias_raw = min(ias_raw, 45)

    matrix = decision_matrix(cqs, vas)
    cqs_grade = grade_company_quality(cqs) if cqs is not None else "未评分"
    vas_grade = grade_valuation(vas) if vas is not None else "未评分"
    summary = {
        "text": (
            f"{company.get('name')}：公司质量{cqs_grade}，估值{vas_grade}，"
            f"数据可信度为{grade_data_quality(dqs)}；系统动作锚定为“{action}”。"
        ),
        "data_quality_grade": grade_data_quality(dqs),
        "company_quality_grade": cqs_grade,
        "valuation_grade": vas_grade,
    }
    scorecard = CompanyScorecard(
        scoring_version=SCORING_VERSION,
        data_quality_score=dqs,
        company_quality_score=cqs,
        valuation_attractiveness_score=vas,
        investment_action_score=ias_raw,
        catalyst_score=catalyst,
        timing_score=timing,
        grade=f"{cqs_grade} / {vas_grade}",
        final_action=action,
        confidence=score_confidence(dqs, red_flags, cqs_buckets, valuation_buckets),
        buckets=cqs_buckets,
        valuation_buckets=valuation_buckets,
        data_quality_buckets=data_quality_buckets,
        data_quality_gate=dqs_gate,
        red_flags=red_flags,
        missing_metrics=missing_metrics + score_missing_items(cqs_buckets, valuation_buckets),
        action_rules=action_rules,
        matrix=matrix,
        summary=summary,
        score_coverage={
            "company_quality": bucket_group_coverage(cqs_buckets),
            "valuation": bucket_group_coverage(valuation_buckets),
            "data_quality": bucket_group_coverage(data_quality_buckets),
        },
    )
    return scorecard.to_dict()


def score_data_quality(data_pack: dict, evidence_store: list[dict]) -> list[ScoreBucket]:
    quality = data_pack.get("data_quality", {}) or {}
    collection = data_pack.get("collection_summary", {}) or {}
    gate = data_quality_gate(data_pack)
    requirements = {item.get("key"): item for item in gate.get("requirements", []) if isinstance(item, dict)}
    evidence_count = quality.get("evidence_count") or len(evidence_store)

    def requirement_metric(key: str, label: str, raw_value, reason: str, evidence_ids: list[str] | None = None, score: float | None = None) -> MetricScore:
        item = requirements.get(key) or {}
        metric_score = score if item.get("passed") and score is not None else (100 if item.get("passed") else 0)
        source = item.get("source")
        metric_raw = raw_value if raw_value not in (None, "") else item.get("details") or source
        return metric(
            label,
            metric_raw,
            metric_score,
            1,
            item.get("requirement") or reason,
            item.get("evidence_ids") or evidence_ids or [],
            formula="DQS 门禁项通过则按覆盖质量给分，未通过为 0 分。",
            calculation=json.dumps(item.get("details") or {}, ensure_ascii=False),
            evidence_required=[item.get("requirement") or reason],
            data_source=format_source(source),
            require_evidence=False,
        )

    source_count = len({str(item.get("source_provider") or "") for item in evidence_store if item.get("source_provider")})
    filing_sources = " ".join(str(item.get("source_provider") or "") for item in evidence_store if item.get("category") == "filing")
    news_sources = " ".join(str(item.get("source_provider") or "") for item in evidence_store if item.get("category") == "news")
    gaps = set((collection.get("gaps") or []) + (quality.get("collection_gaps") or []))

    return [
        bucket("quote_reliability", "价格行情可信度", 0.10, [
            requirement_metric("quote_reliability", "行情来源与报价完整", collection.get("quote_source"), "报价越新，交易判断越可靠", pick_evidence(evidence_store, ["ev_quote_latest"]), score=clamp(float(quality.get("freshness_score") or 0.55) * 100)),
        ]),
        bucket("financial_completeness", "财务数据完整度", 0.25, [
            requirement_metric("financial_completeness", "财报三表覆盖", collection.get("financial_statement_count", 0), "必须有财报三表才能支撑财务质量评分", evidence_ids_by_category(evidence_store, "financial_statement"), score=coverage_score(collection.get("financial_statement_count", 0), [(3, 95), (2, 88), (1, 78)])),
        ]),
        bucket("filing_coverage", "财报/公告原文覆盖", 0.20, [
            requirement_metric("filing_coverage", "公告正文覆盖", collection.get("filing_count", 0), "公告和监管文件是管理层与治理判断的底层证据", evidence_ids_by_category(evidence_store, "filing"), score=filing_quality_score(collection.get("filing_count", 0), filing_sources)),
        ]),
        bucket("event_coverage", "新闻/事件覆盖", 0.10, [
            requirement_metric("event_coverage", "新闻与事件文本覆盖", collection.get("news_count", 0), "事件和情绪只作为辅助，不替代财报和公告", evidence_ids_by_category(evidence_store, "news") + evidence_ids_by_category(evidence_store, "social"), score=event_quality_score(collection.get("news_count", 0), collection.get("social_count", 0), news_sources)),
        ]),
        bucket("peer_coverage", "同业数据覆盖", 0.15, [
            requirement_metric("peer_coverage", "同业样本覆盖", collection.get("peer_count", 0), "同业适配器必须形成可回溯同行估值与质量样本", evidence_ids_by_category(evidence_store, "peer") + evidence_ids_by_category(evidence_store, "research"), score=peer_quality_score(collection.get("peer_count", 0), gaps)),
        ]),
        bucket("freshness", "数据新鲜度", 0.10, [
            requirement_metric("freshness", "综合新鲜度达标", quality.get("freshness_score"), "实时行情和近期公告越新，结论置信度越高", pick_evidence(evidence_store, ["ev_quote_latest"]), score=clamp(float(quality.get("freshness_score") or 0.55) * 100)),
        ]),
        bucket("cross_validation", "交叉验证一致性", 0.10, [
            requirement_metric("cross_validation", "多源交叉验证", evidence_count, "多源证据能降低单一来源误差", [item.get("evidence_id") for item in evidence_store[:4] if item.get("evidence_id")], score=cross_validation_score(source_count, evidence_count, gaps)),
        ]),
    ]


def score_data_quality_total(data_pack: dict, buckets: list[ScoreBucket], gate: dict) -> float:
    bucket_score = weighted_bucket_average(buckets) or 0
    quality = data_pack.get("data_quality", {}) or {}
    legacy_score = quality.get("overall_score")
    try:
        legacy_score = float(legacy_score)
    except (TypeError, ValueError):
        legacy_score = bucket_score
    score = min(bucket_score, legacy_score)
    if not gate.get("passed"):
        return clamp(min(score, 49))
    return clamp(score)


def coverage_score(count, thresholds: list[tuple[int, float]]) -> float:
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    for threshold, score in thresholds:
        if count >= threshold:
            return score
    return 0


def filing_quality_score(count, source_text: str) -> float:
    score = coverage_score(count, [(3, 94), (2, 88), (1, 78)])
    source = source_text.lower()
    if "search fallback" in source:
        score = min(score, 82)
    if any(provider in source for provider in ["sec edgar", "cninfo", "巨潮", "hkex"]):
        score = max(score, 82)
    return score


def event_quality_score(news_count, social_count, source_text: str) -> float:
    score = coverage_score(news_count, [(5, 86), (3, 80), (1, 68)])
    try:
        social_count = int(social_count or 0)
    except (TypeError, ValueError):
        social_count = 0
    if social_count:
        score = min(92, score + 5)
    if "search fallback" in source_text.lower():
        score = min(score, 76)
    return score


def peer_quality_score(peer_count, gaps: set[str]) -> float:
    score = coverage_score(peer_count, [(8, 84), (5, 78), (3, 70)])
    if any("正式同业适配器" in gap or "同业估值分位" in gap for gap in gaps):
        score = min(score, 78)
    return score


def cross_validation_score(source_count: int, evidence_count: int, gaps: set[str]) -> float:
    score = min(94, 45 + min(source_count, 8) * 5 + min(evidence_count, 20) * 0.8)
    non_blocking_gaps = [gap for gap in gaps if any(key in gap for key in ["社媒", "研报", "历史估值", "同业估值", "正式同业"])]
    score -= min(16, len(non_blocking_gaps) * 3)
    return clamp(score)


def data_quality_gate(data_pack: dict) -> dict:
    quality = data_pack.get("data_quality", {}) or {}
    requirements = quality.get("dqs_requirements") or []
    blocking = quality.get("dqs_blocking_items")
    if blocking is None:
        blocking = [item.get("label") for item in requirements if isinstance(item, dict) and not item.get("passed")]
    passed = bool(quality.get("dqs_passed") if "dqs_passed" in quality else quality.get("usable_for_decision"))
    return {
        "passed": passed,
        "status": "passed" if passed else "failed",
        "requirements": requirements,
        "blocking_items": [str(item) for item in (blocking or []) if item],
        "summary": quality.get("dqs_summary") or ("DQS 已完成，允许进入投委会分析。" if passed else "DQS 未完成，后续分析暂停。"),
    }


def score_company_quality(company: dict, data_pack: dict, evidence_store: list[dict], missing_metrics: list[str], red_flags: list[dict]) -> list[ScoreBucket]:
    snap = company.get("snapshot", {}) or {}
    raw = snap.get("raw_data", {}) or {}
    financial_summary = financial_summary_from_pack(data_pack)
    market_snapshot = data_pack.get("market_snapshot") if isinstance(data_pack.get("market_snapshot"), dict) else {}
    tags = company.get("tags", []) or []
    industry = f"{company.get('industry', '')} {company.get('sector', '')}"
    roe = first_valid_number(snap.get("roe"), raw.get("roe"), financial_summary.get("roe"))
    gross = first_valid_number(snap.get("gross_margin"), raw.get("gross_margin"), financial_summary.get("gross_margin"))
    net = first_valid_number(snap.get("net_margin"), raw.get("net_margin"), financial_summary.get("net_margin"))
    market_cap = first_valid_number(snap.get("market_cap"), raw.get("market_cap"), market_snapshot.get("market_cap"))
    financial_coverage = data_pack.get("collection_summary", {}).get("financial_statement_count", 0)
    filing_coverage = data_pack.get("collection_summary", {}).get("filing_count", 0)
    total_assets = first_valid_number(
        snap.get("total_assets"),
        raw.get("total_assets"),
        financial_summary.get("total_assets"),
        financial_evidence_value(evidence_store, ["total_assets", "TOTAL_ASSETS", "总资产", "资产总额", "资产合计"]),
    )
    total_liabilities = first_valid_number(
        snap.get("total_liabilities"),
        raw.get("total_liabilities"),
        financial_summary.get("total_liabilities"),
        financial_evidence_value(evidence_store, ["total_liabilities", "TOTAL_LIABILITIES", "总负债", "负债总额", "负债合计"]),
    )
    debt_to_asset = first_valid_number(snap.get("debt_to_asset_ratio"), raw.get("debt_to_asset_ratio"), financial_summary.get("debt_to_asset_ratio"))
    if debt_to_asset is None and total_assets and total_liabilities is not None:
        debt_to_asset = total_liabilities / total_assets * 100
    operating_cash_flow = first_valid_number(
        snap.get("operating_cash_flow"),
        raw.get("operating_cash_flow"),
        financial_summary.get("operating_cash_flow"),
        financial_evidence_value(evidence_store, ["operating_cash_flow", "NETCASH_OPERATE", "经营业务现金净额", "经营活动现金流量净额", "经营产生现金"]),
    )
    net_income = first_valid_number(
        snap.get("net_income"),
        raw.get("net_income"),
        financial_summary.get("net_income"),
        financial_evidence_value(evidence_store, ["net_income", "HOLDER_PROFIT", "股东应占溢利", "归母利润", "净利润", "年内溢利"]),
    )
    diluted_shares = first_valid_number(snap.get("diluted_shares"), raw.get("diluted_shares"), financial_summary.get("diluted_shares"))
    financial_series = financial_series_from_pack(data_pack)
    growth_cagr_metric = growth_cagr_from_series(financial_series)
    growth_stability_metric = growth_stability_from_series(financial_series)
    dilution_metric = dilution_risk_from_series(financial_series, diluted_shares)
    growth_evidence_ids = evidence_ids_by_category(evidence_store, "financial_statement")
    filing_evidence_ids = evidence_ids_by_category(evidence_store, "filing")
    technical_evidence_ids = evidence_ids_by_category(evidence_store, "technical")

    moat = bucket("business_moat", "商业模式与护城河", 0.25, [
        metric("品牌/心智/定价权", gross, score_if_number(gross, lambda v: 42 + v * 0.55 + (12 if "品牌" in tags or "白酒" in industry else 0)), 5, "毛利率和品牌/行业标签共同验证定价权。", pick_evidence(evidence_store, ["ev_business_description", "ev_financial_margin_roe"], ["filing"]), formula="42 + 毛利率×0.55 + 品牌/白酒标签加 12。", evidence_required=["毛利率", "业务描述或公告证据"], data_source="财务指标 + 公司/公告描述"),
        metric("用户粘性/转换成本", company.get("description"), 72 if any(key in industry for key in ["互联网", "SaaS", "白酒", "AI"]) else 58, 5, "该项是基于业务描述和行业属性的定性推断，若没有业务描述证据则不评分。", pick_evidence(evidence_store, ["ev_business_description"], ["filing", "research"]), formula="行业/业务描述规则：平台、SaaS、白酒、AI 场景给更高转换成本初评。", evidence_required=["业务描述", "公告/研报增强证据"], data_source="公司资料 + 可回溯文本"),
        metric("规模效应/成本优势", market_cap, score_if_number(market_cap, lambda v: 70 if v > 1000 else 56), 5, "市值只能初步表示规模，后续需同业市占率。", pick_evidence(evidence_store, ["ev_ownership_market_cap", "ev_business_description"]), formula="市值大于 1000 亿元/亿美元口径时给 70，否则 56。", evidence_required=["市值口径"], data_source="实时行情/市值证据"),
        metric("网络效应/生态位", industry, 75 if any(key in industry for key in ["互联网", "AI", "半导体"]) else 55, 4, "生态位评分必须结合行业属性和公告证据。", pick_evidence(evidence_store, ["ev_macro_industry"], ["news", "research"]), formula="行业结构规则：互联网/AI/半导体生态位更高，其余保守初评。", evidence_required=["行业分类", "宏观/研报/新闻证据"], data_source="行业框架 + 外部文本"),
        metric("渠道/牌照/专利/资源壁垒", tags, 72 if any(key in " ".join(tags) for key in ["牌照", "品牌", "龙头", "平台"]) else 55, 3, "资源壁垒以标签和业务描述初筛，缺少文本证据则不评分。", pick_evidence(evidence_store, ["ev_business_description"], ["filing"]), formula="标签包含牌照/品牌/龙头/平台时给 72，否则 55。", evidence_required=["业务描述", "壁垒相关标签或公告文本"], data_source="公司标签 + 业务描述"),
        metric("护城河持续年限判断", {"roe": roe, "gross_margin": gross}, score_if_numbers([roe, gross], lambda values: 48 + values[0] * 0.8 + values[1] * 0.25), 3, "长期护城河应体现为资本回报和利润率稳定。", pick_evidence(evidence_store, ["ev_financial_margin_roe"], ["financial_statement"]), formula="48 + ROE×0.8 + 毛利率×0.25。", evidence_required=["ROE", "毛利率", "财务指标证据"], data_source="财报三表/结构化财务指标"),
    ])

    financial = bucket("financial_quality", "财务质量", 0.25, [
        metric("ROIC/ROE 水平与稳定性", roe, score_if_number(roe, lambda v: 35 + v * 1.8), 7, "ROE 是资本回报初步锚点；缺少多年序列时只评当前水平，不评价稳定性。", pick_evidence(evidence_store, ["ev_financial_margin_roe"], ["financial_statement"]), formula="35 + ROE×1.8。", evidence_required=["ROE", "财报/结构化财务证据"], data_source="财务指标"),
        metric("利润率水平", {"gross_margin": gross, "net_margin": net}, score_if_numbers([gross, net], lambda values: 35 + values[0] * 0.45 + values[1] * 0.8), 5, "毛利率、净利率共同判断赚钱质量。", pick_evidence(evidence_store, ["ev_financial_margin_roe"], ["financial_statement"]), formula="35 + 毛利率×0.45 + 净利率×0.8。", evidence_required=["毛利率", "净利率"], data_source="财务指标"),
        metric("经营现金流质量", operating_cash_flow, score_cash_flow(operating_cash_flow, net_income), 5, "现金流质量必须由现金流量表或结构化财务指标支撑。", evidence_ids_by_category(evidence_store, "financial_statement"), formula="经营现金流/净利润越高，现金含量越好。", evidence_required=["经营现金流", "净利润", "现金流量表证据"], data_source="财报三表"),
        metric("资产负债表安全性", debt_to_asset, score_if_number(debt_to_asset, lambda v: scale_between(v, 0, 80, inverse=True)), 4, "债务和短债压力是财务质量的下限约束。", pick_evidence(evidence_store, ["ev_financial_margin_roe"], ["financial_statement", "filing"]), formula="资产负债率 0%-80% 反向线性映射。", evidence_required=["资产负债率或可计算债务指标"], data_source="资产负债表"),
        metric("会计质量与利润含金量", {"operating_cash_flow": operating_cash_flow, "net_income": net_income}, score_accounting_quality(operating_cash_flow, net_income), 4, "经营现金流、应收和存货趋势待正式财报序列补强；当前仅评现金流含金量。", evidence_ids_by_category(evidence_store, "financial_statement"), formula="经营现金流/净利润作为利润含金量代理。", evidence_required=["经营现金流", "净利润", "财报三表"], data_source="财报三表"),
    ])

    growth = bucket("growth_quality", "成长质量", 0.15, [
        metric("收入/利润/现金流 复合增速", growth_cagr_metric.get("raw_value"), growth_cagr_metric.get("score"), 4, "使用已采集的多年财务序列计算收入、利润和现金流复合增速；不再用估值隐含预期替代。", growth_evidence_ids, formula="按最新年度/最早年度计算复合增速，并按收入、利润、经营现金流可用项加权评分。", calculation=growth_cagr_metric.get("calculation", ""), evidence_required=["多年收入/利润/现金流序列"], data_source="结构化财务指标/财报三表") if growth_cagr_metric else missing_metric("收入/利润/现金流 复合增速", "pending_series", 4, "缺少多年收入、利润和现金流序列，不能用估值隐含预期替代真实复合增速。", ["5年收入/利润/现金流序列"]),
        metric("增长稳定性", growth_stability_metric.get("raw_value"), growth_stability_metric.get("score"), 3, "使用多年收入和利润增速的均值、波动和负增长次数评估增长稳定性。", growth_evidence_ids, formula="62 + 平均增速×0.45 - 增速波动×0.65 - 负增长次数×12。", calculation=growth_stability_metric.get("calculation", ""), evidence_required=["多年财务序列"], data_source="结构化财务指标/财报三表") if growth_stability_metric else missing_metric("增长稳定性", "pending_series", 3, "缺少多年序列，增长稳定性不评分。", ["多年财务序列"]),
        metric("增长是否提升 ROE", roe, score_if_number(roe, lambda v: 42 + v * 1.1), 3, "高质量增长应提升或维持资本回报；当前仅使用最新 ROE 作为代理。", pick_evidence(evidence_store, ["ev_financial_margin_roe"], ["financial_statement"]), formula="42 + ROE×1.1。", evidence_required=["ROE"], data_source="财务指标"),
        metric("行业空间/TAM", industry, 74 if any(key in industry for key in ["AI", "半导体", "互联网", "医药"]) else 60, 3, "行业空间要结合真实新闻、研报和公告继续验证。", pick_evidence(evidence_store, ["ev_macro_industry"], ["news", "research"]), formula="行业规则初评，高景气行业 74，其余 60。", evidence_required=["行业分类", "新闻/研报/公告证据"], data_source="行业/新闻/研报"),
        metric("每股价值增长/稀释风险", dilution_metric.get("raw_value"), dilution_metric.get("score"), 2, "使用 SEC 披露的摊薄股数序列或最新摊薄股数识别稀释风险；回购/分红作为后续增强。", growth_evidence_ids, formula="股数增速越高，稀释扣分越多；缺少序列时用最新摊薄股数给中性偏保守分。", calculation=dilution_metric.get("calculation", ""), evidence_required=["股本变动", "回购/增发/分红公告"], data_source="SEC/XBRL 股数披露") if dilution_metric else missing_metric("每股价值增长/稀释风险", "pending_adapter", 2, "股本稀释、回购和分红数据未接入，不能评分。", ["股本变动", "回购/增发/分红公告"]),
    ])

    management = bucket("management_capital_allocation", "管理层与资本配置", 0.15, [
        metric("历史承诺兑现度", filing_coverage, disclosure_score(filing_coverage, 58), 3, "在缺少逐条指引兑现表前，先用正式公告覆盖度作为透明度代理，后续继续补电话会/指引对照。", filing_evidence_ids, formula="公告覆盖 1/2/3 份约 58/63/68 分。", evidence_required=["管理层指引原文", "兑现结果"], data_source="公告/SEC 文件"),
        metric("资本开支回报", roe, score_if_number(roe, lambda v: 42 + v * 1.2), 3, "资本开支回报先由资本回报近似，后续需 Capex/ROIC 序列。", pick_evidence(evidence_store, ["ev_financial_margin_roe"], ["financial_statement"]), formula="42 + ROE×1.2。", evidence_required=["ROE", "Capex/ROIC 序列待增强"], data_source="财务指标"),
        metric("并购纪律", filing_coverage, disclosure_score(filing_coverage, 56), 2, "未发现重大并购/减值红旗时，以公告覆盖和红旗数量给保守初评；后续接并购与商誉明细。", filing_evidence_ids, formula="公告覆盖分 - 红旗扣分。", calculation=json.dumps({"filing_count": filing_coverage, "red_flags": len(red_flags)}, ensure_ascii=False), evidence_required=["并购公告", "商誉/减值数据"], data_source="公告/风险规则"),
        metric("分红/回购是否理性", filing_coverage, disclosure_score(filing_coverage, 54), 2, "未接入完整分红回购表前，以公告覆盖给中性偏保守分，避免编造资本回报政策。", filing_evidence_ids, formula="公告覆盖 1/2/3 份约 54/59/64 分。", evidence_required=["分红", "回购", "估值对照"], data_source="公告/SEC 文件"),
        metric("股权激励与股东一致性", dilution_metric.get("raw_value") if dilution_metric else filing_coverage, (dilution_metric.get("score") if dilution_metric else disclosure_score(filing_coverage, 55)), 2, "股权激励和稀释先用摊薄股数披露与公告覆盖保守评分；后续接股权激励公告细项。", growth_evidence_ids or filing_evidence_ids, formula="优先股数稀释评分，否则公告覆盖中性分。", calculation=dilution_metric.get("calculation", "") if dilution_metric else "", evidence_required=["股权激励公告", "稀释数据"], data_source="SEC/XBRL + 公告"),
        metric("治理透明度与诚信记录", red_flags, max(35, 72 - len(red_flags) * 8), 3, "红旗越多，治理评分越受压制。", pick_evidence(evidence_store, ["ev_risk_tags"], ["filing"]), formula="72 - 红旗数量×8，下限 35。", evidence_required=["风险标签", "公告/新闻红旗证据"], data_source="风险规则 + 文本证据"),
    ])

    industry_bucket = bucket("industry_structure", "行业结构与周期位置", 0.10, [
        metric("行业长期空间", industry, 75 if any(key in industry for key in ["AI", "高端消费", "半导体", "医药"]) else 60, 3, "行业空间按行业适配器初判，需新闻/研报增强。", pick_evidence(evidence_store, ["ev_macro_industry"], ["news", "research"]), formula="行业规则：AI/高端消费/半导体/医药为 75，其余 60。", evidence_required=["行业分类", "行业文本证据"], data_source="行业框架"),
        metric("竞争格局", company.get("description"), 72 if "龙头" in company.get("description", "") else 58, 3, "龙头地位需要市占率和同业数据继续验证。", pick_evidence(evidence_store, ["ev_business_description"], ["research", "news"]), formula="业务描述包含“龙头”为 72，否则 58。", evidence_required=["业务描述", "同业/研报增强证据"], data_source="公司资料 + 同业增强"),
        metric("周期位置", data_pack.get("macro", {}).get("industry_cycle"), cycle_position_score(data_pack), 2, "用新闻/公告/技术面证据给行业周期与交易位置的保守初评；后续接正式行业周期数据。", technical_evidence_ids or evidence_ids_by_category(evidence_store, "news") or filing_evidence_ids, formula="技术面和事件覆盖决定 56-68 分区间。", evidence_required=["行业周期数据", "新闻/研报证据"], data_source="新闻/公告/技术面"),
        metric("政策/监管环境", red_flags, max(35, 72 - len(red_flags) * 6), 2, "监管和制裁等变量直接压低行业结构评分。", pick_evidence(evidence_store, ["ev_risk_tags"], ["filing", "news"]), formula="72 - 红旗数量×6，下限 35。", evidence_required=["风险标签", "监管/公告/新闻证据"], data_source="风险规则 + 文本证据"),
    ])

    risk_governance = bucket("risk_governance", "风险与治理", 0.10, [
        metric("重大红旗风险", red_flags, 35 if any(flag.get("severity") == "major" for flag in red_flags) else max(45, 82 - len(red_flags) * 7), 5, "风险治理是公司质量的下限项。", pick_evidence(evidence_store, ["ev_risk_tags"], ["filing", "news"]), formula="无重大红旗时 82 - 红旗数量×7，下限 45；重大红旗为 35。", evidence_required=["风险标签", "公告/新闻证据"], data_source="风险规则 + 文本证据"),
        metric("客户/供应商/政策集中风险", data_pack.get("fundamental", {}).get("key_risks"), max(45, 76 - len(data_pack.get("fundamental", {}).get("key_risks") or []) * 4), 3, "集中风险越高，安全边际要求越高。", pick_evidence(evidence_store, ["ev_risk_tags"], ["filing"]), formula="76 - 风险标签数量×4，下限 45。", evidence_required=["风险标签或公告风险段落"], data_source="风险标签 + 公告增强"),
        metric("财务困境辅助判断", debt_to_asset, score_if_number(debt_to_asset, lambda v: scale_between(v, 0, 85, inverse=True)), 2, "Altman Z-Score 尚未完整接入；仅在有负债率证据时评分。", pick_evidence(evidence_store, ["ev_financial_margin_roe"], ["financial_statement"]), formula="资产负债率 0%-85% 反向线性映射。", evidence_required=["资产负债率"], data_source="资产负债表"),
    ])

    return [moat, financial, growth, management, industry_bucket, risk_governance]


def score_valuation(company: dict, data_pack: dict, evidence_store: list[dict], missing_metrics: list[str]) -> list[ScoreBucket]:
    snap = company.get("snapshot", {}) or {}
    raw = snap.get("raw_data", {}) or {}
    financial_summary = financial_summary_from_pack(data_pack)
    market_snapshot = data_pack.get("market_snapshot") if isinstance(data_pack.get("market_snapshot"), dict) else {}
    pe = first_valid_number(snap.get("pe_ratio"), raw.get("pe_ratio"), financial_summary.get("pe_ratio"))
    pb = first_valid_number(snap.get("pb_ratio"), raw.get("pb_ratio"), financial_summary.get("pb_ratio"))
    price = first_valid_number(snap.get("price"), raw.get("price"), market_snapshot.get("price"))
    valuation = data_pack.get("valuation_summary", {}) or {}
    fair = valuation.get("fair_value_range", {}) or {}
    base = valid_number(fair.get("base"))
    pe = pe if pe and pe > 0 else None
    pb = pb if pb and pb > 0 else None
    price = price if price and price > 0 else None
    base = base if base and base > 0 else None
    low, mid, high = industry_pe_range(company)
    relative_pe = score_relative_pe(pe, low, mid, high)
    upside = ((base / price) - 1) if price and base else None
    historical = valuation.get("historical_percentile") or data_pack.get("valuation_history") or {}
    pe_percentile = valid_number(historical.get("pe_percentile") or historical.get("pe_ttm_percentile"))
    pb_percentile = valid_number(historical.get("pb_percentile"))
    historical_ids = pick_evidence(evidence_store, ["ev_valuation_history_akshare", "ev_valuation_history"], ["valuation_history"])
    return [
        bucket("relative_valuation", "相对估值", 0.25, [
            metric("行业适配市盈率", pe, relative_pe, 3, f"行业参考区间约 {low}/{mid}/{high} 倍，不能跨行业硬比。", pick_evidence(evidence_store, ["ev_valuation_pe_pb"]), formula="PE <= 低位区间给 85；<= 中位 70；<= 高位 52；高于高位 32。", evidence_required=["当前 PE", "行情/估值来源"], data_source="实时行情估值"),
            metric("市净率辅助", pb, score_if_number(pb, lambda v: 76 if v < 2 else 62 if v < 5 else 45), 1, "市净率只作辅助，资产轻重行业权重不同。", pick_evidence(evidence_store, ["ev_valuation_pe_pb"]), formula="PB < 2 为 76；2-5 为 62；>=5 为 45。", evidence_required=["当前 PB"], data_source="实时行情估值"),
        ]),
        bucket("historical_percentile", "历史估值分位", 0.20, [
            metric("历史 PE 分位", pe_percentile, score_historical_percentile(pe_percentile), 2, "历史分位越低，当前估值吸引力越高；没有历史序列则不评分。", historical_ids, formula="100 - 历史 PE 分位。", evidence_required=["历史 PE/PB 序列"], data_source="AKShare/估值历史适配器"),
            metric("历史 PB 分位", pb_percentile, score_historical_percentile(pb_percentile), 1, "PB 分位作为资产估值辅助；没有历史序列则不评分。", historical_ids, formula="100 - 历史 PB 分位。", evidence_required=["历史 PB 序列"], data_source="AKShare/估值历史适配器"),
        ]),
        bucket("reverse_dcf", "现金流折现 / 反向现金流假设", 0.25, [
            metric("当前价格隐含增长预期", upside, score_if_number(upside, lambda v: 55 + v * 120), 1, "用基准情景潜在涨跌幅近似反向现金流假设，后续接三表模型。", pick_evidence(evidence_store, ["ev_quote_latest", "ev_valuation_pe_pb"]), formula="55 + 基准情景潜在涨跌幅×120。", evidence_required=["当前价", "基准估值区间"], data_source="行情 + 估值计算"),
        ]),
        bucket("risk_reward", "风险收益比", 0.20, [
            metric("基准情景潜在涨跌幅", upside, score_if_number(upside, lambda v: 50 + v * 150), 1, "上涨空间和下跌风险必须同时看。", pick_evidence(evidence_store, ["ev_quote_latest", "ev_valuation_pe_pb"]), formula="50 + 基准情景潜在涨跌幅×150。", evidence_required=["当前价", "估值区间"], data_source="行情 + 估值计算"),
        ]),
        bucket("margin_of_safety", "安全边际", 0.10, [
            metric("安全边际", upside, score_if_number(upside, lambda v: 45 + v * 170), 1, "好公司也必须有足够价格补偿。", pick_evidence(evidence_store, ["ev_quote_latest", "ev_valuation_pe_pb"]), formula="45 + 基准情景潜在涨跌幅×170。", evidence_required=["当前价", "估值区间"], data_source="行情 + 估值计算"),
        ]),
    ]


def score_relative_pe(pe: float | None, low: float, mid: float, high: float) -> float | None:
    if pe is None:
        return None
    if pe <= low:
        return 85
    if pe <= mid:
        return 70
    if pe <= high:
        return 52
    return 32


def score_historical_percentile(percentile: float | None) -> float | None:
    if percentile is None:
        return None
    return clamp(100 - percentile)


def score_catalyst(data_pack: dict, evidence_store: list[dict]) -> float | None:
    event_ids = evidence_ids_by_category(evidence_store, "news") + evidence_ids_by_category(evidence_store, "filing")
    if not event_ids:
        return None
    sentiment = valid_number(data_pack.get("sentiment", {}).get("sentiment_score"))
    macro = valid_number(data_pack.get("macro", {}).get("macro_impact_score"))
    if sentiment is None and macro is None:
        return None
    items = []
    if sentiment is not None:
        items.append((sentiment, 0.55))
    if macro is not None:
        items.append((macro, 0.45))
    return clamp(weighted_average(items))


def score_timing(data_pack: dict, evidence_store: list[dict]) -> float | None:
    if not evidence_ids_by_category(evidence_store, "technical"):
        return None
    return valid_number(data_pack.get("technical", {}).get("technical_score"))


def investment_action_score(cqs: float | None, vas: float | None, catalyst: float | None, timing: float | None) -> float | None:
    items = []
    if cqs is not None:
        items.append((cqs, 0.50))
    if vas is not None:
        items.append((vas, 0.30))
    if catalyst is not None:
        items.append((catalyst, 0.10))
    if timing is not None:
        items.append((timing, 0.10))
    if not items or sum(weight for _, weight in items) < 0.70:
        return None
    return clamp(weighted_average(items))


def score_confidence(dqs: float, red_flags: list[dict], cqs_buckets: list[ScoreBucket], valuation_buckets: list[ScoreBucket]) -> float:
    coverage = min(bucket_group_coverage(cqs_buckets).get("coverage_ratio", 0), bucket_group_coverage(valuation_buckets).get("coverage_ratio", 0))
    penalty = 0.08 if any(flag.get("severity") == "major" for flag in red_flags) else len(red_flags) * 0.015
    return clamp(0.35 + dqs / 210 + coverage * 0.16 - penalty, 0.25, 0.92)


def build_missing_metrics(data_pack: dict) -> list[str]:
    missing = set(data_pack.get("data_quality", {}).get("missing_data") or [])
    collection = data_pack.get("collection_summary", {}) or {}
    for gap in data_pack.get("data_quality", {}).get("collection_gaps") or []:
        if is_non_blocking_collection_gap(str(gap), collection):
            continue
        missing.add(gap)
    company = data_pack.get("company", {}) or data_pack.get("analyst_pack", {}).get("company", {}) or {}
    if not collection.get("financial_statement_count"):
        missing.add("财报三表")
        missing.add("5年自由现金流")
    if not collection.get("filing_count"):
        missing.add("管理层承诺兑现材料")
    if not collection.get("research_report_count"):
        missing.add("研报全文/索引")
    if not collection.get("valuation_history_count"):
        missing.add("历史估值分位")
    if "待补充" in str(company.get("industry") or "") or "外部识别" in str(company.get("sector") or ""):
        missing.add("行业分类与主营业务标签")
        if any(key in f"{company.get('industry', '')} {company.get('sector', '')}" for key in ["消费", "零售", "餐饮", "连锁"]):
            missing.add("门店数/同店销售/加盟直营结构")
    if (collection.get("peer_count") or 0) < 3:
        missing.add("正式同业适配器")
    return sorted(str(item) for item in missing if item)


def is_non_blocking_collection_gap(gap: str, collection: dict) -> bool:
    if any(key in gap for key in ["StockTwits", "Reddit", "社媒"]):
        return True
    if "东方财富公开研报接口" in gap and (collection.get("research_report_count") or 0) > 0:
        return True
    return False


def decision_matrix(cqs: float | None, vas: float | None) -> dict[str, str]:
    if cqs is None or vas is None:
        return {
            "quadrant": "证据不足，暂不入象限",
            "implication": "公司质量或估值吸引力缺少足够可评分证据，应先补齐资料。",
            "x_axis": "估值吸引力 VAS",
            "y_axis": "公司质量 CQS",
        }
    if cqs >= 75 and vas >= 70:
        quadrant = "高质量 + 好价格"
        implication = "可进入买入候选，但仍需风控和仓位约束"
    elif cqs >= 75:
        quadrant = "高质量 + 价格不够好"
        implication = "好公司不等于好股票，优先等待更好价格"
    elif vas >= 70:
        quadrant = "质量一般 + 价格便宜"
        implication = "警惕价值陷阱，只能小仓位或继续观察"
    else:
        quadrant = "质量/价格均未达标"
        implication = "不具备主动买入条件"
    return {
        "quadrant": quadrant,
        "implication": implication,
        "x_axis": "估值吸引力 VAS",
        "y_axis": "公司质量 CQS",
    }


def bucket(key: str, name: str, weight: float, metrics: list[MetricScore], cap: float = 100) -> ScoreBucket:
    scored = [(metric.score, metric.weight) for metric in metrics if isinstance(metric.score, (int, float))]
    total_weight = sum(metric.weight for metric in metrics)
    scored_weight = sum(weight for _, weight in scored)
    score = weighted_average(scored) if scored else None
    if score is not None:
        score = min(score, cap)
    coverage_ratio = scored_weight / total_weight if total_weight else 0
    if score is None:
        status = "missing_evidence"
    elif coverage_ratio >= 0.99:
        status = "scored"
    else:
        status = "partial"
    return ScoreBucket(
        key=key,
        name=name,
        score=score,
        weight=weight,
        metrics=metrics,
        summary=summary_for_bucket(name, score, coverage_ratio),
        status=status,
        scored_weight=scored_weight,
        total_weight=total_weight,
        coverage_ratio=coverage_ratio,
    )


def metric(
    name: str,
    raw_value,
    score: float | None,
    weight: float,
    reason: str,
    evidence_ids: list[str] | None = None,
    *,
    formula: str = "",
    calculation: str = "",
    evidence_required: list[str] | None = None,
    data_source: str = "",
    require_evidence: bool = True,
) -> MetricScore:
    ids = [str(item) for item in (evidence_ids or []) if item]
    missing_reason = ""
    if is_pending(raw_value):
        missing_reason = "原始值缺失或适配器尚未接入。"
    elif require_evidence and not ids:
        missing_reason = NO_EVIDENCE_REASON
    elif score is None:
        missing_reason = "缺少可计算原始值。"
    if missing_reason:
        return MetricScore(
            name=name,
            raw_value=raw_value,
            score=None,
            weight=weight,
            reason=reason,
            evidence_ids=ids,
            status="missing_evidence",
            formula=formula,
            calculation=calculation,
            evidence_required=evidence_required or [],
            data_source=data_source,
            confidence=None,
            missing_reason=missing_reason,
        )
    return MetricScore(
        name=name,
        raw_value=raw_value,
        score=round(clamp(score), 1),
        weight=weight,
        reason=reason,
        evidence_ids=ids,
        status="scored",
        formula=formula,
        calculation=calculation or f"raw={format_source(raw_value)}; score={round(clamp(score), 1)}",
        evidence_required=evidence_required or [],
        data_source=data_source,
        confidence=None,
        missing_reason="",
    )


def missing_metric(name: str, raw_value, weight: float, reason: str, evidence_required: list[str]) -> MetricScore:
    return MetricScore(
        name=name,
        raw_value=raw_value,
        score=None,
        weight=weight,
        reason=reason,
        evidence_ids=[],
        status="missing_evidence",
        formula="不使用替代指标或固定占位分。",
        calculation="未计算",
        evidence_required=evidence_required,
        data_source="未接入",
        confidence=None,
        missing_reason=reason,
    )


def score_if_number(value: float | None, fn) -> float | None:
    if value is None:
        return None
    return clamp(fn(value))


def score_if_numbers(values: list[float | None], fn) -> float | None:
    if any(value is None for value in values):
        return None
    return clamp(fn([float(value) for value in values if value is not None]))


def score_cash_flow(operating_cash_flow: float | None, net_income: float | None) -> float | None:
    if operating_cash_flow is None or net_income in (None, 0):
        return None
    ratio = operating_cash_flow / abs(net_income)
    return clamp(45 + ratio * 35)


def score_accounting_quality(operating_cash_flow: float | None, net_income: float | None) -> float | None:
    if operating_cash_flow is None or net_income in (None, 0):
        return None
    ratio = operating_cash_flow / abs(net_income)
    return clamp(40 + ratio * 40)


def disclosure_score(count: Any, base: float = 56) -> float | None:
    try:
        number = int(count or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return None
    return clamp(base + min(number, 3) * 5)


def cycle_position_score(data_pack: dict) -> float | None:
    evidence = data_pack.get("evidence_store") or []
    has_technical = bool(evidence_ids_by_category(evidence, "technical"))
    has_news = bool(evidence_ids_by_category(evidence, "news"))
    has_filing = bool(evidence_ids_by_category(evidence, "filing"))
    if not (has_technical or has_news or has_filing):
        return None
    score = 56
    if has_news:
        score += 4
    if has_filing:
        score += 3
    if has_technical:
        score += 5
    technical_score = valid_number(data_pack.get("technical", {}).get("technical_score"))
    if technical_score is not None:
        score = score * 0.7 + technical_score * 0.3
    return clamp(score)


def first_valid_number(*values: Any) -> float | None:
    for value in values:
        number = valid_number(value)
        if number is not None:
            return number
    return None


def valid_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(number, float) or number != number:
        return None
    return number


def financial_summary_from_pack(data_pack: dict) -> dict:
    summary = data_pack.get("financial_summary")
    if isinstance(summary, dict):
        return summary
    analyst_summary = (data_pack.get("analyst_pack") or {}).get("financial_summary")
    return analyst_summary if isinstance(analyst_summary, dict) else {}


def financial_series_from_pack(data_pack: dict) -> list[dict]:
    series = data_pack.get("financial_series")
    if not isinstance(series, list):
        series = (data_pack.get("analyst_pack") or {}).get("financial_series")
    return [item for item in (series or []) if isinstance(item, dict)]


def financial_evidence_value(evidence_store: list[dict], aliases: list[str]) -> float | None:
    normalized_aliases = [normalize_metric_alias(alias) for alias in aliases]
    for item in evidence_store:
        if item.get("category") != "financial_statement":
            continue
        for field in ["normalized_value", "raw_value"]:
            value = payload_from_jsonish(item.get(field))
            number = value_from_financial_payload(value, normalized_aliases)
            if number is not None:
                return number
    return None


def payload_from_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def value_from_financial_payload(payload: Any, aliases: list[str]) -> float | None:
    if isinstance(payload, dict):
        label = payload.get("STD_ITEM_NAME") or payload.get("ITEM_NAME") or payload.get("item") or payload.get("name")
        if label and alias_match(label, aliases):
            for value_key in ["AMOUNT", "amount", "value", "val", "latest"]:
                number = valid_number(payload.get(value_key))
                if number is not None:
                    return number
        for key, value in payload.items():
            if alias_match(key, aliases):
                number = valid_number(value)
                if number is not None:
                    return number
            if isinstance(value, (dict, list)):
                nested = value_from_financial_payload(value, aliases)
                if nested is not None:
                    return nested
    elif isinstance(payload, list):
        for item in payload:
            number = value_from_financial_payload(item, aliases)
            if number is not None:
                return number
    return None


def alias_match(value: Any, aliases: list[str]) -> bool:
    normalized = normalize_metric_alias(value)
    return any(alias and alias in normalized for alias in aliases)


def normalize_metric_alias(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def growth_cagr_from_series(series: list[dict]) -> dict:
    annual = annual_financial_series(series)
    if len(annual) < 2:
        return {}
    cagr_items = []
    weights = {"revenue": 0.4, "net_income": 0.4, "operating_cash_flow": 0.2}
    raw: dict[str, Any] = {"periods": [item.get("period") for item in annual]}
    for key, weight in weights.items():
        cagr = metric_cagr(annual, key)
        if cagr is None:
            continue
        cagr_pct = round(cagr * 100, 2)
        raw[f"{key}_cagr"] = cagr_pct
        cagr_items.append((45 + cagr_pct * 1.2, weight))
    if not cagr_items:
        return {}
    score = clamp(weighted_average(cagr_items))
    return {
        "raw_value": raw,
        "score": score,
        "calculation": json.dumps({"annual_series": annual, "score": round(score, 1)}, ensure_ascii=False),
    }


def growth_stability_from_series(series: list[dict]) -> dict:
    annual = annual_financial_series(series)
    rates = []
    raw: dict[str, Any] = {"periods": [item.get("period") for item in annual]}
    for key in ["revenue", "net_income", "operating_cash_flow"]:
        metric_rates = metric_growth_rates(annual, key)
        if metric_rates:
            raw[f"{key}_growth_rates"] = [round(item, 2) for item in metric_rates]
            rates.extend(metric_rates)
    if len(rates) < 2:
        return {}
    avg = sum(rates) / len(rates)
    variance = sum((item - avg) ** 2 for item in rates) / len(rates)
    volatility = variance ** 0.5
    negative_count = sum(1 for item in rates if item < 0)
    score = clamp(62 + avg * 0.45 - volatility * 0.65 - negative_count * 12)
    raw.update({"average_growth": round(avg, 2), "growth_volatility": round(volatility, 2), "negative_count": negative_count})
    return {
        "raw_value": raw,
        "score": score,
        "calculation": json.dumps({"growth_rates": raw, "score": round(score, 1)}, ensure_ascii=False),
    }


def dilution_risk_from_series(series: list[dict], latest_shares: float | None = None) -> dict:
    annual = annual_financial_series(series)
    share_points = [
        (str(item.get("period") or ""), valid_number(item.get("diluted_shares") or item.get("shares")))
        for item in annual
        if valid_number(item.get("diluted_shares") or item.get("shares")) is not None
    ]
    share_points = [(period, value) for period, value in share_points if value is not None and value > 0]
    raw: dict[str, Any] = {"latest_diluted_shares": latest_shares}
    if len(share_points) >= 2:
        share_points = sorted(share_points, key=lambda item: item[0])
        start_period, start_value = share_points[0]
        end_period, end_value = share_points[-1]
        years = max(1, int(end_period[:4]) - int(start_period[:4])) if start_period[:4].isdigit() and end_period[:4].isdigit() else max(1, len(share_points) - 1)
        growth = (end_value / start_value) ** (1 / years) - 1 if start_value > 0 else 0
        growth_pct = round(growth * 100, 2)
        score = clamp(72 - max(0, growth_pct) * 2.2)
        raw.update({"share_points": share_points, "diluted_share_cagr": growth_pct})
        return {
            "raw_value": raw,
            "score": score,
            "calculation": json.dumps({"share_points": share_points, "score": round(score, 1)}, ensure_ascii=False),
        }
    if latest_shares is not None and latest_shares > 0:
        return {
            "raw_value": raw,
            "score": 58,
            "calculation": json.dumps({"latest_diluted_shares": latest_shares, "score": 58, "note": "缺少可比年度股数序列，按中性偏保守评分"}, ensure_ascii=False),
        }
    return {}


def annual_financial_series(series: list[dict]) -> list[dict]:
    by_year: dict[str, dict] = {}
    for item in series:
        period = str(item.get("period") or "")
        if not period:
            continue
        report_type = str(item.get("report_type") or "").lower()
        has_cash_flow_or_shares = (
            valid_number(item.get("operating_cash_flow")) is not None
            or valid_number(item.get("diluted_shares") or item.get("shares")) is not None
        )
        is_calendar_year = period.endswith("12-31")
        is_named_annual = "annual" in report_type or "年报" in report_type or "全年" in report_type or report_type in {"fy", "year"}
        is_sec_annual = report_type in {"10-k", "10k", "10-k/a", "10k/a"} and has_cash_flow_or_shares
        if not (is_calendar_year or is_named_annual or is_sec_annual):
            continue
        year = period[:4]
        normalized = {
            "period": period,
            "revenue": valid_number(item.get("revenue")),
            "net_income": valid_number(item.get("net_income")),
            "operating_cash_flow": valid_number(item.get("operating_cash_flow")),
            "diluted_shares": valid_number(item.get("diluted_shares") or item.get("shares")),
        }
        if year not in by_year or period > str(by_year[year].get("period") or ""):
            by_year[year] = normalized
    return sorted(by_year.values(), key=lambda item: str(item.get("period") or ""))


def metric_cagr(series: list[dict], key: str) -> float | None:
    values = [(int(str(item.get("period"))[:4]), valid_number(item.get(key))) for item in series if str(item.get("period") or "")[:4].isdigit()]
    values = [(year, value) for year, value in values if value is not None and value > 0]
    if len(values) < 2:
        return None
    start_year, start_value = values[0]
    end_year, end_value = values[-1]
    years = max(1, end_year - start_year)
    return (end_value / start_value) ** (1 / years) - 1


def metric_growth_rates(series: list[dict], key: str) -> list[float]:
    values = [valid_number(item.get(key)) for item in series]
    values = [value for value in values if value is not None and value > 0]
    if len(values) < 3:
        return []
    return [((values[index] / values[index - 1]) - 1) * 100 for index in range(1, len(values))]


def is_pending(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str) and value.lower() in {"pending", "pending_adapter", "n/a", "none"}:
        return True
    if isinstance(value, dict):
        return all(is_pending(item) for item in value.values())
    return False


def weighted_bucket_average(buckets: list[ScoreBucket]) -> float | None:
    scored = [(bucket.score, bucket.weight) for bucket in buckets if isinstance(bucket.score, (int, float))]
    if not scored:
        return None
    return clamp(weighted_average(scored))


def bucket_group_coverage(buckets: list[ScoreBucket]) -> dict[str, float]:
    total = sum(bucket.weight for bucket in buckets)
    scored = sum(bucket.weight * bucket.coverage_ratio for bucket in buckets)
    return {
        "coverage_ratio": round(scored / total, 3) if total else 0,
        "scored_weight": round(scored, 3),
        "total_weight": round(total, 3),
    }


def score_missing_items(*bucket_groups: list[ScoreBucket]) -> list[str]:
    missing: set[str] = set()
    for buckets in bucket_groups:
        for item in buckets:
            for metric_item in item.metrics:
                if metric_item.status != "scored":
                    missing.add(f"{item.name} / {metric_item.name}: {metric_item.missing_reason or NO_EVIDENCE_REASON}")
    return sorted(missing)


def scorecard_action(dqs: float, cqs: float | None, vas: float | None, red_flags: list[dict], gate: dict) -> tuple[str, list[str]]:
    if not gate.get("passed"):
        missing = "、".join(gate.get("blocking_items", [])[:6])
        return "数据不足暂不评级", [f"DQS 未通过：{missing or '关键证据不足'}"]
    if cqs is None or vas is None:
        return "数据不足暂不评级", ["公司质量或估值吸引力缺少足够可评分证据"]
    return action_from_scorecard(dqs, cqs, vas, red_flags)


def summary_for_bucket(name: str, score: float | None, coverage_ratio: float) -> dict[str, Any]:
    if score is None:
        return {
            "text": f"{name}缺少足够证据，未评分。",
            "band": "未评分",
            "coverage_ratio": round(coverage_ratio, 3),
        }
    if score >= 85:
        label = "强"
    elif score >= 70:
        label = "较强"
    elif score >= 55:
        label = "中性"
    elif score >= 40:
        label = "偏弱"
    else:
        label = "弱"
    return {
        "text": f"{name}评分为{round(score, 1)}，判断为{label}；可评分权重覆盖 {round(coverage_ratio * 100)}%。",
        "band": label,
        "coverage_ratio": round(coverage_ratio, 3),
    }


def format_source(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)
