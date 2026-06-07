import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BadgeCheck, Database, Download, FileText, Scale, ShieldAlert, Target, TrendingUp } from "lucide-react";
import { useState, type CSSProperties } from "react";
import { api } from "../api";
import type { CompanyScorecard, DataPack, DecisionVisualization, ReportState, ScoreBucket } from "../types";
import { DecisionDashboard } from "./DecisionDashboard";

interface ReportViewProps {
  state: ReportState;
  onRefresh?: () => Promise<void> | void;
}

export function ReportView({ state, onRefresh }: ReportViewProps) {
  const [exportingPdf, setExportingPdf] = useState(false);
  const [exportError, setExportError] = useState("");
  const finalRound = state.rounds.find((round) => round.round_number === 5)?.round_output;
  const canExport = Boolean(state.final_report_markdown);
  const rawScorecard = state.scorecard || state.data_pack.scorecard || finalRound?.scorecard;
  const scorecard = hasUsableScorecard(rawScorecard) ? rawScorecard : undefined;
  const rawDecision = state.decision_visualization || finalRound?.decision_visualization;
  const decision = hasUsableDecision(rawDecision) ? rawDecision : undefined;

  async function exportPdf() {
    setExportError("");
    setExportingPdf(true);
    const pdfWindow = window.open("", "_blank");
    try {
      const result = await api.exportPdf(state.report_id);
      await onRefresh?.();
      if (pdfWindow) {
        pdfWindow.location.href = result.pdf_url;
      } else {
        window.location.href = result.pdf_url;
      }
    } catch (error) {
      pdfWindow?.close();
      setExportError(error instanceof Error ? error.message : "PDF 导出失败，请稍后重试");
    } finally {
      setExportingPdf(false);
    }
  }

  if (!state.final_report_markdown) {
    return (
      <section className="empty-state">
        <FileText size={32} />
        <h2>最终报告会在第五轮完成后生成</h2>
        <p>每一轮的输入和输出都会被保存，报告页会自动汇总四类资料包、五轮纪要、投票、评分、风险和跟踪指标。</p>
      </section>
    );
  }

  return (
    <section className="report-view">
      <div className="report-toolbar">
        <div>
          <p className="eyebrow">最终报告</p>
          <h2>{formatDisplayValue(state.company.name)} AI投委会深度报告</h2>
        </div>
        <button className="primary-action" onClick={exportPdf} disabled={!canExport || exportingPdf}>
          <Download size={18} />
          {exportingPdf ? "导出中..." : "导出 PDF"}
        </button>
      </div>
      {exportError && <p className="inline-error">{exportError}</p>}

      <div className="score-strip">
        <Metric icon={<BadgeCheck size={18} />} label="公司质量分" value={scoreValue(scorecard?.company_quality_score)} />
        <Metric icon={<Scale size={18} />} label="估值吸引力" value={scoreValue(scorecard?.valuation_attractiveness_score)} />
        <Metric icon={<Target size={18} />} label="投资行动分" value={scoreValue(scorecard?.investment_action_score ?? state.overall_score)} />
        <Metric icon={<Database size={18} />} label="数据可信度" value={scoreValue(scorecard?.data_quality_score)} />
        <Metric icon={<TrendingUp size={18} />} label="最终建议" value={scorecard?.final_action || state.final_action || "-"} />
        <Metric icon={<ShieldAlert size={18} />} label="投票" value={formatVotes(finalRound?.committee_vote)} />
      </div>

      <DecisionDashboard decision={decision} />
      <ReportVisualSummary state={state} scorecard={scorecard} />
      <ScorecardPanel scorecard={scorecard} dataPack={state.data_pack} />

      <div className="markdown-card">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdown(state.final_report_markdown)}</ReactMarkdown>
      </div>
    </section>
  );
}

function ReportVisualSummary({ state, scorecard }: { state: ReportState; scorecard?: CompanyScorecard }) {
  const finalRound = state.rounds.find((round) => round.round_number === 5)?.round_output;
  const vote = finalRound?.committee_vote;
  const dataQuality = state.data_pack.data_quality;
  const valuationRange = state.data_pack.valuation_summary?.fair_value_range;
  const currentPrice = state.data_pack.valuation_summary?.current_price;
  const bars = [
    ["公司质量", scorecard?.company_quality_score],
    ["估值吸引力", scorecard?.valuation_attractiveness_score],
    ["投资行动", scorecard?.investment_action_score ?? state.overall_score],
    ["数据可信度", scorecard?.data_quality_score]
  ] as const;
  return (
    <section className="report-visual-summary">
      <div className="visual-card score-bars-card">
        <div className="visual-card-head">
          <span>核心评分</span>
          <strong>{formatDisplayValue(scorecard?.final_action || state.final_action || "等待结论")}</strong>
        </div>
        <div className="score-bars">
          {bars.map(([label, value]) => (
            <ScoreBar key={label} label={label} value={numericScore(value)} />
          ))}
        </div>
      </div>
      <div className="visual-card vote-card">
        <div className="visual-card-head">
          <span>委员会投票</span>
          <strong>{formatVotes(vote)}</strong>
        </div>
        <VoteBars vote={vote} />
      </div>
      <div className="visual-card valuation-card">
        <div className="visual-card-head">
          <span>估值区间</span>
          <strong>{valuationRange?.currency || "-"}</strong>
        </div>
        <div className="valuation-ladder">
          <ValuationPoint label="悲观" value={valuationRange?.bear} tone="bear" />
          <ValuationPoint label="基准" value={valuationRange?.base} tone="base" />
          <ValuationPoint label="乐观" value={valuationRange?.bull} tone="bull" />
        </div>
        <p>当前价格：{formatDisplayValue(currentPrice ?? "-")}</p>
      </div>
      <div className="visual-card data-quality-card">
        <div className="visual-card-head">
          <span>资料质量</span>
          <strong>{formatDisplayValue(dataQuality?.overall_score ?? "-")}</strong>
        </div>
        <ScoreBar label="新鲜度" value={numericScore(dataQuality?.freshness_score)} />
        <ScoreBar label="证据覆盖" value={evidenceCoverageScore(dataQuality?.evidence_count)} />
        <p>{formatDisplayValue(dataQuality?.usable_for_decision ? "可用于本次决策" : "仍需补充关键资料")}</p>
      </div>
    </section>
  );
}

function ScoreBar({ label, value }: { label: string; value?: number }) {
  const pct = clampScore(value);
  return (
    <div className="score-bar-row">
      <div>
        <span>{formatDisplayValue(label)}</span>
        <strong>{value === undefined ? "-" : Math.round(pct)}</strong>
      </div>
      <div className="score-bar-track">
        <span style={{ width: `${pct}%`, background: scoreColor(pct) }} />
      </div>
    </div>
  );
}

function VoteBars({ vote }: { vote?: { buy: number; watch: number; avoid: number } }) {
  const rows = [
    ["买入", vote?.buy || 0, "#12805c"],
    ["观察", vote?.watch || 0, "#ca8a04"],
    ["回避", vote?.avoid || 0, "#b42318"]
  ] as const;
  const total = Math.max(1, rows.reduce((sum, [, value]) => sum + value, 0));
  return (
    <div className="vote-bars">
      {rows.map(([label, value, color]) => (
        <div className="vote-bar-row" key={label}>
          <span>{label}</span>
          <div className="vote-track">
            <span style={{ width: `${(value / total) * 100}%`, background: color }} />
          </div>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

function ValuationPoint({ label, value, tone }: { label: string; value?: number; tone: "bear" | "base" | "bull" }) {
  return (
    <div className={`valuation-point ${tone}`}>
      <span>{formatDisplayValue(label)}</span>
      <strong>{formatDisplayValue(value ?? "-")}</strong>
    </div>
  );
}

export function DataPackSummary({ dataPack }: { dataPack: DataPack }) {
  const agents = [
    ["fundamental", "基本面"],
    ["sentiment", "情绪"],
    ["macro", "宏观行业"],
    ["technical", "技术面"]
  ] as const;
  const quality = dataPack.data_quality;
  const valueRange = dataPack.valuation_summary?.fair_value_range;
  const collection = dataPack.collection_summary;
  const gaps = collection?.gaps?.length ? collection.gaps : quality?.collection_gaps;

  return (
    <>
      <div className="data-pack-meta">
        <Metric label="证据数" value={quality?.evidence_count ?? dataPack.evidence_store?.length ?? "-"} />
        <Metric label="数据质量" value={quality?.overall_score ?? "-"} />
        <Metric label="公司质量" value={scoreValue(dataPack.scorecard?.company_quality_score)} />
        <Metric label="估值吸引力" value={scoreValue(dataPack.scorecard?.valuation_attractiveness_score)} />
        <Metric label="新鲜度" value={quality?.freshness_score ?? "-"} />
        <Metric label="估值区间" value={valueRange ? `${valueRange.bear ?? "-"} / ${valueRange.base ?? "-"} / ${valueRange.bull ?? "-"} ${valueRange.currency ?? ""}` : "-"} />
        <Metric label="真实文档" value={collection ? `${collection.news_count ?? 0}新闻 / ${collection.filing_count ?? 0}公告 / ${collection.research_report_count ?? 0}研报` : "-"} />
        <Metric label="同业样本" value={collection?.peer_count ?? "-"} />
        <Metric label="财报三表" value={collection?.financial_statement_count ?? "-"} />
        <Metric label="历史估值" value={collection?.valuation_history_count ?? "-"} />
        <Metric label="历史行情" value={collection?.technical_history_count ?? "-"} />
      </div>
      <div className="data-pack-grid">
        {agents.map(([key, label]) => {
              const agent = dataPack[key];
              return (
                <article className="agent-summary" key={key}>
                  <span>{formatDisplayValue(label)}</span>
                  <strong>{localizeAgentName(formatDisplayValue(agent?.agent || "待采集"))}</strong>
                  <p>{firstSummary(agent)}</p>
                  <EvidenceIds ids={agent?.evidence_ids as string[] | undefined} evidenceStore={dataPack.evidence_store} />
                </article>
              );
            })}
      </div>
      {dataPack.data_plan?.length ? (
        <div className="data-plan-list">
          {dataPack.data_plan.map((step) => (
            <span key={formatDisplayValue(step.step)} title={`${localizeProvider(formatDisplayValue(step.source))} · 置信度 ${formatDisplayValue(step.confidence)}`}>
              {step.status === "done" ? "✓" : "•"} {formatDisplayValue(step.step)}
            </span>
          ))}
        </div>
      ) : null}
      {gaps?.length ? (
        <div className="data-gap-list">
          {gaps.slice(0, 8).map((gap) => (
            <span key={formatDisplayValue(gap)}>{formatDisplayValue(gap)}</span>
          ))}
        </div>
      ) : null}
      {dataPack.evidence_store?.length ? (
        <div className="evidence-store-strip">
          {dataPack.evidence_store.slice(0, 6).map((item) => (
            <span key={formatDisplayValue(item.evidence_id)} title={`${formatDisplayValue(item.evidence_id)} · ${formatDisplayValue(item.summary)} · ${localizeProvider(formatDisplayValue(item.source_provider))}`}>
              {evidenceLabel(item.evidence_id, dataPack.evidence_store)}
            </span>
          ))}
        </div>
      ) : null}
    </>
  );
}

function ScorecardPanel({ scorecard, dataPack }: { scorecard?: CompanyScorecard; dataPack: DataPack }) {
  if (!hasUsableScorecard(scorecard)) return null;
  const cqs = numericScore(scorecard.company_quality_score);
  const vas = numericScore(scorecard.valuation_attractiveness_score);
  const ias = numericScore(scorecard.investment_action_score);
  const dqs = numericScore(scorecard.data_quality_score);
  return (
    <section className="scorecard-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">四维评分体系</p>
          <h3>先看公司，再看价格，最后决定动作</h3>
        </div>
        <span className="scorecard-version">{formatDisplayValue(scorecard.scoring_version || "四维评分-v2.0")}</span>
      </div>

      <div className="scorecard-hero">
        <ScoreGauge label="公司质量" value={cqs} detail={scorecard.grade || scorecard.summary?.company_quality_grade} buckets={scorecard.buckets || []} dataPack={dataPack} />
        <ScoreGauge label="估值吸引力" value={vas} detail={scorecard.summary?.valuation_grade} buckets={scorecard.valuation_buckets || []} dataPack={dataPack} />
        <ScoreGauge label="投资行动" value={ias} detail={scorecard.final_action} buckets={[...(scorecard.buckets || []), ...(scorecard.valuation_buckets || [])]} dataPack={dataPack} />
        <ScoreGauge label="数据可信度" value={dqs} detail={scorecard.summary?.data_quality_grade} buckets={scorecard.data_quality_buckets || []} dataPack={dataPack} />
      </div>

      <div className="decision-matrix">
        <div>
          <span>好公司 vs 好价格</span>
          <strong>{formatDisplayValue(scorecard.matrix?.quadrant || "等待更多数据")}</strong>
          <p>{formatDisplayValue(scorecard.matrix?.interpretation || scorecard.matrix?.implication || "系统会把公司质量和估值吸引力分开判断，避免用一个综合分掩盖关键分歧。")}</p>
        </div>
        <div className="matrix-axis">
          <span>公司质量：{formatDisplayValue(scorecard.matrix?.quality_axis || (cqs === undefined ? "暂无评分" : qualityAxis(cqs)))}</span>
          <span>估值吸引力：{formatDisplayValue(scorecard.matrix?.valuation_axis || (vas === undefined ? "暂无评分" : valuationAxis(vas)))}</span>
        </div>
      </div>

      <BucketGrid title="公司质量分拆解" buckets={scorecard.buckets || []} dataPack={dataPack} />
      <BucketGrid title="估值吸引力分拆解" buckets={scorecard.valuation_buckets || []} dataPack={dataPack} compact />
      <BucketGrid title="数据可信度分拆解" buckets={scorecard.data_quality_buckets || []} dataPack={dataPack} compact />

      <div className="scorecard-alert-grid">
        <div className="scorecard-alert">
          <span>风险红旗</span>
          {scorecard.red_flags?.length ? (
            scorecard.red_flags.slice(0, 4).map((flag, index) => (
              <p key={`${flag.title}-${index}`}>
                <strong>{severityLabel(formatDisplayValue(flag.severity))}</strong>
                {formatDisplayValue(flag.title || "风险事项")}：{formatDisplayValue(flag.reason || "需要继续核验")}
              </p>
            ))
          ) : (
            <p>暂无重大红旗，仍需持续跟踪现金流、债务、监管和治理变化。</p>
          )}
        </div>
        <div className="scorecard-alert">
          <span>资料缺口</span>
          {scorecard.missing_metrics?.length ? (
            <div className="data-gap-list">
              {scorecard.missing_metrics.slice(0, 10).map((item) => <span key={formatDisplayValue(item)}>{formatDisplayValue(item)}</span>)}
            </div>
          ) : (
            <p>本次评分没有识别出关键缺口。</p>
          )}
        </div>
      </div>
    </section>
  );
}

function ScoreGauge({ label, value, detail, buckets = [], dataPack }: { label: string; value?: number; detail?: string; buckets?: ScoreBucket[]; dataPack: DataPack }) {
  const hasValue = typeof value === "number" && Number.isFinite(value);
  const pct = Math.max(0, Math.min(100, hasValue ? value : 0));
  return (
    <details className="score-gauge score-gauge-detail">
      <summary>
        <div className="score-gauge-ring" style={{ "--score": pct } as CSSProperties}>
          <strong>{hasValue ? Math.round(pct) : "-"}</strong>
        </div>
        <div>
          <span>{label}</span>
          <p>{formatDisplayValue(detail || (hasValue ? scoreBand(pct) : "暂无评分"))}</p>
        </div>
      </summary>
      <div className="score-gauge-breakdown">
        <p>{hasValue ? `${label}由下列模块按权重汇总；每个模块和子指标可继续展开查看证据。` : "缺少足够可评分证据，系统没有生成该项分数。"}</p>
        <MiniBucketList buckets={buckets} dataPack={dataPack} />
      </div>
    </details>
  );
}

function BucketGrid({ title, buckets, dataPack, compact = false }: { title: string; buckets: ScoreBucket[]; dataPack: DataPack; compact?: boolean }) {
  if (!buckets.length) return null;
  return (
    <div className={compact ? "bucket-section compact" : "bucket-section"}>
      <h4>{formatDisplayValue(title)}</h4>
      <div className="score-bucket-grid">
        {buckets.map((bucket) => (
          <details className={`score-bucket-card ${bucket.status || ""}`} key={formatDisplayValue(bucket.key || bucket.name)} open={!compact && bucket.status !== "scored"}>
            <summary>
              <div className="bucket-head">
                <span>{bucketLabel(formatDisplayValue(bucket.key || bucket.name))}</span>
                <strong>{scoreValue(bucket.score)}</strong>
              </div>
              <div className="bucket-bar">
                <span style={{ width: `${clampScore(numericScore(bucket.score))}%` }} />
              </div>
              <div className="bucket-meta-row">
                <span>{scoreStatusLabel(bucket.status)}</span>
                <span>覆盖 {formatPercent(bucket.coverage_ratio)}</span>
                <span>权重 {formatDisplayValue(bucket.weight)}</span>
              </div>
            </summary>
            <div className="bucket-detail">
              <p>{bucketSummaryText(bucket.summary)}</p>
              {bucket.metrics?.length ? (
                <div className="bucket-metrics">
                  {bucket.metrics.map((metric) => (
                    <MetricDetail key={formatDisplayValue(metric.name)} metric={metric} dataPack={dataPack} />
                  ))}
                </div>
              ) : (
                <p>暂无子指标。</p>
              )}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

function MiniBucketList({ buckets, dataPack }: { buckets: ScoreBucket[]; dataPack: DataPack }) {
  if (!buckets.length) return <p>暂无模块拆解。</p>;
  return (
    <div className="mini-bucket-list">
      {buckets.slice(0, 8).map((bucket) => (
        <details key={formatDisplayValue(bucket.key || bucket.name)}>
          <summary>
            <span>{bucketLabel(formatDisplayValue(bucket.key || bucket.name))}</span>
            <strong>{scoreValue(bucket.score)}</strong>
          </summary>
          <p>{bucketSummaryText(bucket.summary)}</p>
          {bucket.metrics?.slice(0, 4).map((metric) => (
            <MetricDetail key={formatDisplayValue(metric.name)} metric={metric} dataPack={dataPack} compact />
          ))}
        </details>
      ))}
    </div>
  );
}

function MetricDetail({ metric, dataPack, compact = false }: { metric: NonNullable<ScoreBucket["metrics"]>[number]; dataPack: DataPack; compact?: boolean }) {
  const scored = metric.status === "scored" && typeof metric.score === "number";
  return (
    <details className={`metric-detail ${scored ? "scored" : "missing"}`} open={!compact && !scored}>
      <summary>
        <span>{metricLabel(formatDisplayValue(metric.name))}</span>
        <strong>{scoreValue(metric.score)}</strong>
      </summary>
      <div className="metric-detail-body">
        <dl>
          <DetailRow label="状态" value={scored ? "已评分" : formatDisplayValue(metric.missing_reason || "未评分")} />
          <DetailRow label="权重" value={metric.weight} />
          <DetailRow label="原始值" value={metric.raw_value} variant="code" />
          {metric.formula ? (
            <DetailRow label="公式/规则" value={metric.formula} />
          ) : null}
          {metric.calculation ? (
            <DetailRow label="计算" value={metric.calculation} variant="code" />
          ) : null}
          <DetailRow label="理由" value={metric.reason} />
          {metric.data_source ? (
            <DetailRow label="数据源" value={metric.data_source} />
          ) : null}
          {metric.evidence_required?.length ? (
            <DetailRow label="所需证据" value={metric.evidence_required.map(formatDisplayValue).join("；")} />
          ) : null}
        </dl>
        <EvidenceIds ids={metric.evidence_ids} evidenceStore={dataPack.evidence_store} />
      </div>
    </details>
  );
}

function DetailRow({ label, value, variant = "text" }: { label: string; value: unknown; variant?: "text" | "code" }) {
  const displayValue = variant === "code" ? formatRawValue(value) : formatDisplayValue(value);
  const text = displayValue || "-";
  const isLong = text.length > 96 || text.includes("\n");
  return (
    <div className={isLong ? "detail-row long" : "detail-row"}>
      <dt>{label}</dt>
      <dd className={`detail-value ${variant}${isLong ? " long" : ""}`}>
        {variant === "code" || text.includes("\n") ? <pre>{text}</pre> : text}
      </dd>
    </div>
  );
}

function bucketSummaryText(summary: ScoreBucket["summary"]) {
  if (!summary) return "暂无模块摘要。";
  if (typeof summary === "string") return formatDisplayValue(summary);
  return formatDisplayValue(summary.text || JSON.stringify(summary));
}

function scoreStatusLabel(status?: string) {
  if (status === "scored") return "已完整评分";
  if (status === "partial") return "部分评分";
  if (status === "missing_evidence") return "未评分";
  return formatDisplayValue(status || "已评分");
}

function formatPercent(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

function formatRawValue(value: unknown) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch {
        return formatDisplayValue(value);
      }
    }
    return formatDisplayValue(value);
  }
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return formatDisplayValue(value);
}

function EvidenceIds({ ids = [], evidenceStore = [] }: { ids?: string[]; evidenceStore?: DataPack["evidence_store"] }) {
  if (!ids.length) return null;
  return (
    <div className="evidence-pills">
      {ids.slice(0, 6).map((id) => {
        const item = evidenceStore.find((entry) => entry.evidence_id === id);
        return (
          <details className="evidence-popover" key={formatDisplayValue(id)}>
            <summary title={formatDisplayValue(id)}>{evidenceLabel(formatDisplayValue(id), evidenceStore)}</summary>
            {item ? (
              <dl>
                <div>
                  <dt>编号</dt>
                  <dd>{formatDisplayValue(item.evidence_id)}</dd>
                </div>
                <div>
                  <dt>来源</dt>
                  <dd>{localizeProvider(formatDisplayValue(item.source_provider))}</dd>
                </div>
                <div>
                  <dt>摘要</dt>
                  <dd>{formatDisplayValue(item.summary)}</dd>
                </div>
                <div>
                  <dt>置信度</dt>
                  <dd>{formatDisplayValue(item.confidence)}</dd>
                </div>
                {item.source_url ? (
                  <div>
                    <dt>链接</dt>
                    <dd><a href={item.source_url} target="_blank" rel="noreferrer">打开来源</a></dd>
                  </div>
                ) : null}
                {item.extracted_quote ? (
                  <div>
                    <dt>摘录</dt>
                    <dd>{formatDisplayValue(item.extracted_quote)}</dd>
                  </div>
                ) : null}
              </dl>
            ) : (
              <p>证据库中未找到该编号。</p>
            )}
          </details>
        );
      })}
    </div>
  );
}

function Metric({ label, value, icon }: { label: string; value: unknown; icon?: React.ReactNode }) {
  return (
    <div className="metric">
      <span>
        {icon}
        {formatDisplayValue(label)}
      </span>
      <strong>{formatDisplayValue(value)}</strong>
    </div>
  );
}

function scoreValue(value: unknown) {
  if (typeof value !== "number") return "-";
  if (!Number.isFinite(value)) return "-";
  return Math.round(value);
}

function numericScore(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return value;
}

function hasUsableScorecard(scorecard?: CompanyScorecard): scorecard is CompanyScorecard {
  if (!scorecard) return false;
  return [scorecard.company_quality_score, scorecard.valuation_attractiveness_score, scorecard.investment_action_score, scorecard.data_quality_score].some(
    (value) => typeof value === "number" && Number.isFinite(value)
  );
}

function hasUsableDecision(decision?: DecisionVisualization): decision is DecisionVisualization {
  if (!decision) return false;
  return typeof decision.quadrant_code === "string" && typeof decision.primary_action === "string";
}

function formatVotes(votes?: { buy: number; watch: number; avoid: number }) {
  if (!votes) return "-";
  return `买入 ${votes.buy} / 观察 ${votes.watch} / 回避 ${votes.avoid}`;
}

function firstSummary(agent: Record<string, unknown> | undefined) {
  if (!agent) return "等待研究员输出资料摘要。";
  return formatDisplayValue(
    agent.fundamental_summary ||
      agent.news_summary ||
      agent.industry_cycle ||
      agent.price_trend ||
      "已完成结构化资料采集。"
  );
}

function formatMarkdown(value: unknown) {
  if (typeof value === "string") return value;
  return formatDisplayValue(value);
}

function formatDisplayValue(value: unknown): string {
  if (typeof value === "string") return localizeUiText(value);
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "-";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(formatOpinionItem).filter(Boolean).join("；") || "-";
  if (!value) return "-";
  return formatOpinionItem(value);
}

function localizeUiText(value: string) {
  let output = value;
  const replacements: Array<[RegExp, string]> = [
    [/AICS 评分体系/g, "四维评分体系"],
    [/AICS-v2\.1/g, "四维评分-v2.1"],
    [/AICS-v2\.2/g, "四维评分-v2.2"],
    [/AICS-v2\.3/g, "四维评分-v2.3"],
    [/AICS-v2\.0/g, "四维评分-v2.0"],
    [/Base Case 上下行/g, "基准情景潜在涨跌幅"],
    [/Base Case/g, "基准情景"],
    [/Bear Case/g, "悲观情景"],
    [/Bull Case/g, "乐观情景"],
    [/Kill Switch/g, "止损/重新评估条件"],
    [/SEC XBRL/g, "美国证监会结构化财报"],
    [/SEC EDGAR Search Fallback/g, "美国证监会公告搜索补齐"],
    [/SEC EDGAR/g, "美国证监会公告系统"],
    [/SEC Companyfacts/g, "美国证监会结构化财报"],
    [/Yahoo Finance RSS/g, "雅虎财经新闻"],
    [/Yahoo Finance Chart/g, "雅虎财经历史行情"],
    [/Yahoo Finance 新闻/g, "雅虎财经新闻"],
    [/Internet Search Fallback/g, "互联网搜索补齐"],
    [/Search Fallback/g, "搜索补齐"],
    [/search fallback/g, "搜索补齐"],
    [/StockTwits/g, "美股社区"],
    [/Reddit public JSON/g, "海外论坛公开数据"],
    [/Eastmoney Research/g, "东方财富研报"],
    [/Eastmoney Guba/g, "东方财富股吧"],
    [/Eastmoney HK F10/g, "东方财富港股F10"],
    [/HKEXnews/g, "港交所披露易"],
    [/CNInfo/g, "巨潮资讯"],
    [/Tencent Finance/g, "腾讯财经"],
    [/Income Statement/g, "利润表"],
    [/Balance Sheet/g, "资产负债表"],
    [/Cash Flow Statement/g, "现金流量表"],
    [/Cash Flow/g, "现金流量表"],
    [/Consolidated Statements of Income/g, "合并利润表"],
    [/Consolidated Statements of Operations/g, "合并经营报表"],
    [/Consolidated Balance Sheets/g, "合并资产负债表"],
    [/Consolidated Statements of Cash Flows/g, "合并现金流量表"],
    [/RevenueFromContractWithCustomerExcludingAssessedTax/g, "客户合同收入"],
    [/OperatingIncomeLoss/g, "营业利润"],
    [/NetIncomeLoss/g, "净利润"],
    [/GrossProfit/g, "毛利润"],
    [/AssetsCurrent/g, "流动资产"],
    [/LiabilitiesCurrent/g, "流动负债"],
    [/Assets/g, "资产总额"],
    [/Liabilities/g, "负债总额"],
    [/StockholdersEquity/g, "股东权益"],
    [/CashAndCashEquivalentsAtCarryingValue/g, "现金及现金等价物"],
    [/NetCashProvidedByUsedInOperatingActivities/g, "经营活动现金流量净额"],
    [/NetCashProvidedByUsedInInvestingActivities/g, "投资活动现金流量净额"],
    [/NetCashProvidedByUsedInFinancingActivities/g, "融资活动现金流量净额"],
    [/PaymentsToAcquirePropertyPlantAndEquipment/g, "购建固定资产支出"],
    [/FreeCashFlow/g, "自由现金流"],
    [/Form 20-F/g, "20-F 年报"],
    [/annual report/gi, "年报"],
    [/earnings call/gi, "业绩电话会"],
    [/current price/gi, "当前价格"],
    [/fair value/gi, "公允价值"],
    [/target price/gi, "目标价"],
    [/market cap/gi, "市值"],
    [/\bTAM\b/g, "潜在市场空间"],
    [/\bWACC\b/g, "加权平均资本成本"],
    [/\bCAGR\b/g, "复合增速"],
    [/\bPE\b/g, "市盈率"],
    [/\bPB\b/g, "市净率"],
    [/\bROE\b/g, "净资产收益率"],
    [/\bROIC\b/g, "投入资本回报率"],
    [/\bbullish\b/g, "看多"],
    [/\bneutral\b/g, "中性"],
    [/\bbearish\b/g, "看空"],
    [/\bwatch\b/gi, "观察"],
    [/\bhold\b/gi, "持有"],
    [/\bbuy\b/gi, "买入"],
    [/\bavoid\b/gi, "回避"],
    [/\bconfidence\b/g, "置信度"],
    [/\bfit_score\b/g, "适配度"],
    [/\bsource_provider\b/g, "来源"],
    [/\bsource_url\b/g, "来源链接"],
    [/\bfinancial_statement\b/g, "财报三表"],
    [/\bfiling\b/g, "公告原文"],
    [/\bprice\b/g, "价格"],
    [/\bnews\b/g, "新闻"],
    [/\bpeer\b/g, "同业"],
    [/\btechnical\b/g, "技术面"],
    [/\bindustry\b/g, "行业"],
    [/\bmacro\b/g, "宏观"],
    [/\bsentiment\b/g, "情绪"],
    [/\bcalculation\b/g, "系统计算"],
    [/\bmetric\b/g, "指标"],
    [/\bscored\b/g, "已评分"],
    [/\bmissing_evidence\b/g, "缺少证据"],
    [/\bpartial\b/g, "部分覆盖"],
    [/\bmedium\b/g, "中"],
    [/\bhigh\b/g, "高"],
    [/\blow\b/g, "低"]
  ];
  for (const [pattern, replacement] of replacements) {
    output = output.replace(pattern, replacement);
  }
  return output
    .replace(/AICS 评分体系/g, "四维评分体系")
    .replace(/AICS-v2\.0/g, "四维评分-v2.0");
}

function formatOpinionItem(item: unknown): string {
  if (typeof item === "string") return item;
  if (typeof item === "number" || typeof item === "boolean") return formatDisplayValue(item);
  if (!item || typeof item !== "object") return String(item ?? "");
  const record = item as Record<string, unknown>;
  const primary =
    record.point ||
    record.flag ||
    record.risk ||
    record.condition ||
    record.metric ||
    record.assumption ||
    record.question ||
    record.topic ||
    record.reason ||
    record.summary ||
    record.title ||
    record.one_line_reason;
  if (primary !== undefined && primary !== null) return formatDisplayValue(primary);
  return JSON.stringify(record);
}

function localizeAgentName(value: string) {
  return value.replace(/\s*Agent\b/g, "研究员");
}

function localizeProvider(value?: string) {
  return localizeUiText(String(value || ""))
    .replace(/Yahoo Finance RSS/g, "雅虎财经新闻")
    .replace(/Yahoo Finance Chart/g, "雅虎财经历史行情")
    .replace(/StockTwits/g, "美股社区")
    .replace(/Reddit public JSON/g, "海外论坛公开数据")
    .replace(/Eastmoney Research/g, "东方财富研报")
    .replace(/Eastmoney Guba/g, "东方财富股吧")
    .replace(/Eastmoney HK F10/g, "东方财富港股F10")
    .replace(/CNInfo 巨潮资讯/g, "巨潮资讯")
    .replace(/Tencent Finance/g, "腾讯财经")
    .replace(/SEC Companyfacts/g, "美国证监会结构化财报")
    .replace(/SEC EDGAR/g, "美国证监会公告系统")
    .replace(/Evidence Store/g, "证据库");
}

function evidenceLabel(id: string, evidenceStore: DataPack["evidence_store"] = []) {
  const item = evidenceStore.find((entry) => entry.evidence_id === id);
  if (item?.title) return localizeUiText(item.title);
  const labels: Record<string, string> = {
    ev_profile_identity: "公司与证券识别",
    ev_financial_margin_roe: "盈利能力快照",
    ev_business_description: "商业模式与行业定位",
    ev_risk_tags: "风险标签与待验证事项",
    ev_sentiment_debate: "市场分歧议题",
    ev_macro_industry: "宏观行业变量",
    ev_quote_latest: "最新证券报价",
    ev_technical_levels: "技术面支撑压力估算",
    ev_valuation_pe_pb: "相对估值倍数",
    ev_ownership_market_cap: "市值与流通市值",
    ev_technical_history: "六个月日线技术指标"
  };
  return labels[id] || localizeEvidenceId(id);
}

function bucketLabel(value: string) {
  const labels: Record<string, string> = {
    business_moat: "商业模式与护城河",
    financial_quality: "财务质量",
    growth_quality: "成长质量",
    management_capital_allocation: "管理层与资本配置",
    industry_structure: "行业结构与周期位置",
    risk_governance: "风险与治理",
    relative_valuation: "相对估值",
    historical_percentile: "历史估值分位",
    reverse_dcf: "现金流与隐含预期",
    risk_reward: "风险收益比",
    margin_of_safety: "安全边际",
    market_quote: "价格行情可信度",
    quote_reliability: "价格行情可信度",
    financial_completeness: "财务数据完整度",
    filing_coverage: "财报/公告原文覆盖",
    news_coverage: "新闻/事件覆盖",
    event_coverage: "新闻/事件覆盖",
    peer_coverage: "同业数据覆盖",
    freshness: "数据新鲜度",
    cross_validation: "交叉验证一致性"
  };
  return labels[value] || value;
}

function metricLabel(value: string) {
  return localizeUiText(value)
    .replace(/ROIC/g, "投入资本回报率")
    .replace(/ROE/g, "净资产收益率")
    .replace(/DCF/g, "现金流折现");
}

function localizeEvidenceId(id: string) {
  return localizeUiText(
    id
      .replace(/^ev_/, "证据：")
      .replace(/financial_income_statement/g, "财务 利润表")
      .replace(/financial_balance_sheet/g, "财务 资产负债表")
      .replace(/financial_cash_flow/g, "财务 现金流量表")
      .replace(/financial_margin_roe/g, "财务 盈利能力")
      .replace(/filing_search/g, "公告 搜索补齐")
      .replace(/filing_sec/g, "美国证监会公告")
      .replace(/news_yahoo/g, "雅虎财经新闻")
      .replace(/peer_comparable_set/g, "同业样本")
      .replace(/technical_history/g, "历史行情")
      .replace(/quote_latest/g, "最新报价")
      .replace(/valuation_pe_pb/g, "相对估值")
      .replace(/ownership_market_cap/g, "市值口径")
      .replace(/profile_identity/g, "公司主体识别")
      .replace(/business_description/g, "业务描述")
      .replace(/risk_tags/g, "风险标签")
      .replace(/macro_industry/g, "宏观行业")
      .replace(/sentiment_debate/g, "情绪分歧")
      .replace(/_/g, " ")
  );
}

function severityLabel(value?: string) {
  if (value === "major") return "重大";
  if (value === "high") return "高";
  if (value === "medium") return "中";
  return "提示";
}

function scoreBand(value: number) {
  if (value >= 85) return "强";
  if (value >= 70) return "较强";
  if (value >= 55) return "中性";
  if (value >= 40) return "偏弱";
  return "弱";
}

function clampScore(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function scoreColor(value: number) {
  if (value >= 75) return "linear-gradient(90deg, #12805c, #36a269)";
  if (value >= 55) return "linear-gradient(90deg, #ca8a04, #d9a441)";
  return "linear-gradient(90deg, #b42318, #d1604f)";
}

function evidenceCoverageScore(count?: number) {
  if (typeof count !== "number" || !Number.isFinite(count)) return undefined;
  return Math.max(0, Math.min(100, count * 8));
}

function qualityAxis(value: number) {
  return value >= 75 ? "高质量" : value >= 55 ? "中等质量" : "低质量";
}

function valuationAxis(value: number) {
  return value >= 65 ? "有吸引力" : value >= 45 ? "大致合理" : "偏贵";
}
