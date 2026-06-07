import { useEffect, useState } from "react";
import { Eye, FileDown, History as HistoryIcon, RefreshCw } from "lucide-react";
import { api } from "../api";
import type { ReportState, ReportSummary } from "../types";
import { ReportView } from "./ReportView";

export function History() {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [active, setActive] = useState<ReportState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await api.reports();
      setReports(result.reports);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function openReport(reportId: string) {
    const result = await api.report(reportId);
    setActive(result);
  }

  useEffect(() => {
    load();
  }, []);

  if (active) {
    return (
      <main className="content-column">
        <button className="ghost-action fit" onClick={() => setActive(null)}>
          返回历史
        </button>
        <ReportView state={active} onRefresh={() => openReport(active.report_id)} />
      </main>
    );
  }

  return (
    <main className="content-column">
      <section className="section-heading">
        <div>
          <p className="eyebrow">历史报告</p>
          <h1>所有投委会记录</h1>
          <p>每次分析都会沉淀下来，方便复盘公司跟踪、观点变化、评分变化和 PDF 归档。</p>
        </div>
        <button className="ghost-action" onClick={load}>
          <RefreshCw size={16} />
          刷新
        </button>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="history-table">
        <div className="table-row table-head">
          <span>公司</span>
          <span>日期</span>
          <span>成员</span>
          <span>主席</span>
          <span>评分</span>
          <span>建议</span>
          <span>操作</span>
        </div>
        {loading && <div className="loading-row">正在读取历史报告...</div>}
        {!loading && reports.length === 0 && (
          <div className="empty-state compact">
            <HistoryIcon size={28} />
            <p>还没有报告。先发起一次投委会，历史库就会开始积累。</p>
          </div>
        )}
        {reports.map((report) => (
          <div className="table-row" key={report.report_id}>
            <span>
              <strong>{formatDisplayValue(report.company.name)}</strong>
              <small>{formatDisplayValue(report.company.ticker)}</small>
            </span>
            <span>{formatDisplayValue(report.report_date)}</span>
            <span>{report.selected_experts?.map((expert) => formatDisplayValue(expert.name)).join("、") || "-"}</span>
            <span>{formatDisplayValue(report.chairman?.name || "-")}</span>
            <span>{formatDisplayValue(report.overall_score || "-")}</span>
            <span>{formatDisplayValue(report.final_action || reportStatusText(report))}</span>
            <span className="row-actions">
              <button onClick={() => openReport(report.report_id)} title="查看详情">
                <Eye size={16} />
              </button>
              {report.pdf_url && (
                <button onClick={() => window.open(report.pdf_url, "_blank")} title="下载 PDF">
                  <FileDown size={16} />
                </button>
              )}
            </span>
          </div>
        ))}
      </section>
    </main>
  );
}

function formatDisplayValue(value: unknown): string {
  if (typeof value === "string") return localizeUiText(value);
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "-";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(formatDisplayValue).filter(Boolean).join("、") || "-";
  if (!value) return "-";
  const record = value as Record<string, unknown>;
  return formatDisplayValue(
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
      JSON.stringify(record)
  );
}

function reportStatusText(report: ReportSummary) {
  const status = String(report.status || "");
  if (status === "FINAL_REPORT_DONE") return "已完成";
  if (status.includes("FAILED")) return "生成失败";
  if (status.includes("RUNNING") || status.startsWith("AUTO_RUN")) return `后台生成中 ${report.current_round || 0}/5`;
  if ((report.current_round || 0) > 0 && (report.current_round || 0) < 5) return `已完成 ${report.current_round}/5`;
  return status;
}

function localizeUiText(value: string) {
  return value
    .replace(/SEC XBRL/g, "美国证监会结构化财报")
    .replace(/SEC EDGAR Search Fallback/g, "美国证监会公告搜索补齐")
    .replace(/SEC EDGAR/g, "美国证监会公告系统")
    .replace(/SEC Companyfacts/g, "美国证监会结构化财报")
    .replace(/Yahoo Finance RSS/g, "雅虎财经新闻")
    .replace(/Yahoo Finance Chart/g, "雅虎财经历史行情")
    .replace(/Yahoo Finance/g, "雅虎财经")
    .replace(/Eastmoney HK F10/g, "东方财富港股F10")
    .replace(/Internet Search Fallback/g, "互联网搜索补齐")
    .replace(/Search Fallback/g, "搜索补齐")
    .replace(/Income Statement/g, "利润表")
    .replace(/Balance Sheet/g, "资产负债表")
    .replace(/Cash Flow Statement/g, "现金流量表")
    .replace(/financial_statement/g, "财报三表")
    .replace(/source_provider/g, "来源")
    .replace(/source_url/g, "来源链接")
    .replace(/confidence/g, "置信度")
    .replace(/fit_score/g, "适配度");
}
