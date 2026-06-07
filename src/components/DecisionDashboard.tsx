import { Activity, AlertTriangle, CheckCircle2, Target, XCircle } from "lucide-react";
import type { DecisionVisualization } from "../types";

interface Props {
  decision?: DecisionVisualization;
}

export function DecisionDashboard({ decision }: Props) {
  if (!decision) return null;
  const x = clamp(decision.valuation_attractiveness_score ?? 0);
  const y = clamp(decision.company_quality_score ?? 0);
  const markerLeft = `${x}%`;
  const markerBottom = `${y}%`;
  const passed = decision.data_quality_passed !== false && decision.quadrant_code !== "data_insufficient";
  const qualityHigh = clamp(decision.thresholds?.quality_high ?? 75);
  const valuationAttractive = clamp(decision.thresholds?.valuation_attractive ?? 65);
  const chartGridStyle = {
    gridTemplateColumns: `${valuationAttractive}% ${100 - valuationAttractive}%`,
    gridTemplateRows: `${100 - qualityHigh}% ${qualityHigh}%`
  };

  return (
    <section className="decision-dashboard">
      <div className="decision-header">
        <div>
          <p className="eyebrow">投资决策仪表盘</p>
          <h2>{decision.primary_action}</h2>
          <p>{decision.quadrant_description}</p>
        </div>
        <div className={`decision-badge ${decision.quadrant_code}`}>{decision.quadrant_title}</div>
      </div>

      <div className="decision-score-grid">
        <DecisionMetric label="公司质量 CQS" value={decision.company_quality_score} />
        <DecisionMetric label="估值吸引力 VAS" value={decision.valuation_attractiveness_score} />
        <DecisionMetric label="投资行动 IAS" value={decision.investment_action_score} />
        <DecisionMetric label="数据可信度 DQS" value={`${passed ? "已通过" : "未通过"} · ${Math.round(decision.data_quality_score)}`} tone={passed ? "pass" : "fail"} />
      </div>

      <div className="decision-body">
        <div className="quadrant-card">
          <div className="quadrant-title">
            <Target size={18} />
            好公司 vs 好价格
          </div>
          <div className="quadrant-chart" style={chartGridStyle}>
            <div className="quadrant-cell top left">
              <strong>优质等待区</strong>
              <span>好公司，但价格不够好</span>
            </div>
            <div className="quadrant-cell top right">
              <strong>优质买入区</strong>
              <span>好公司 + 好价格</span>
            </div>
            <div className="quadrant-cell bottom left">
              <strong>回避区</strong>
              <span>质量和价格都不占优</span>
            </div>
            <div className="quadrant-cell bottom right">
              <strong>便宜陷阱区</strong>
              <span>价格便宜，但质量需验证</span>
            </div>
            <div className={`quadrant-marker ${decision.quadrant_code}`} style={{ left: markerLeft, bottom: markerBottom }} title={`CQS ${y} / VAS ${x}`}>
              <span />
            </div>
            <div className="axis x-axis">{decision.x_axis_label} →</div>
            <div className="axis y-axis">↑ {decision.y_axis_label}</div>
          </div>
        </div>

        <div className="spectrum-card">
          <div className="quadrant-title">
            <Activity size={18} />
            投资态度光谱
          </div>
          <div className="spectrum-label-row">
            <strong>{decision.spectrum_label}</strong>
            <span>{scoreText(decision.investment_action_score)} / 100</span>
          </div>
          <div className="investment-spectrum">
            <div className="spectrum-track">
              <span>强烈回避</span>
              <span>观察</span>
              <span>等待</span>
              <span>买入</span>
              <div className="spectrum-marker" style={{ left: `${clamp(decision.spectrum_position ?? 0)}%` }} />
            </div>
          </div>

          {decision.data_quality_gate?.requirements?.length ? (
            <div className="dqs-checklist">
              <strong>DQS 门禁</strong>
              {decision.data_quality_gate.requirements.map((item) => (
                <span className={item.passed ? "passed" : "failed"} key={item.key}>
                  {item.passed ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                  {item.label}
                </span>
              ))}
            </div>
          ) : null}

          {decision.position_hint ? (
            <div className="position-hint">
              <AlertTriangle size={16} />
              <p>{decision.position_hint}</p>
            </div>
          ) : null}

          {decision.buy_conditions?.length ? (
            <div className="condition-list">
              <strong>触发买入需要满足：</strong>
              {decision.buy_conditions.slice(0, 3).map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function DecisionMetric({ label, value, tone }: { label: string; value?: number | string | null; tone?: "pass" | "fail" }) {
  return (
    <div className={`decision-metric ${tone || ""}`}>
      <span>{label}</span>
      <strong>{typeof value === "number" ? Math.round(value) : value ?? "-"}</strong>
    </div>
  );
}

function scoreText(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? String(Math.round(value)) : "-";
}

function clamp(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}
