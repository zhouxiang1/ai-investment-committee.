import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Check,
  FileText,
  Play,
  RefreshCw,
  Search,
  Shuffle,
  Sparkles,
  Users
} from "lucide-react";
import { api } from "../api";
import type { Company, DataPack, Expert, Market, RecommendedExpert, ReportState } from "../types";
import { DataPackSummary, ReportView } from "./ReportView";

type Step = "input" | "confirm" | "experts" | "chairman" | "collect" | "meeting" | "report";

const stepLabels: Array<[Step, string]> = [
  ["input", "输入"],
  ["confirm", "确认公司"],
  ["experts", "选择专家"],
  ["chairman", "确认主席"],
  ["collect", "资料包"],
  ["meeting", "五轮会议"],
  ["report", "报告"]
];

const examples = ["中芯国际 / 688981 / 00981.HK", "贵州茅台 / 600519", "腾讯 / 0700.HK", "阿里巴巴 / BABA / 9988.HK", "英伟达 / NVDA", "苹果 / AAPL", "比亚迪 / 002594 / 1211.HK"];

export function Workbench() {
  const [step, setStep] = useState<Step>("input");
  const [query, setQuery] = useState("贵州茅台");
  const [market, setMarket] = useState<Market>("AUTO");
  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [state, setState] = useState<ReportState | null>(null);
  const [selectedExpertIds, setSelectedExpertIds] = useState<string[]>([]);
  const [viewRound, setViewRound] = useState(1);
  const [viewRoundPinned, setViewRoundPinned] = useState(false);
  const [runningRound, setRunningRound] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [agentProgress, setAgentProgress] = useState<Record<string, number>>({});

  const currentRound = state?.current_round || 0;
  const nextRound = Math.min(5, currentRound + 1);
  const recommendations = state?.recommended_experts || [];
  const selectedExperts = state?.selected_experts || [];
  const chairman = state?.chairman as Expert | undefined;
  const meetingRunning = Boolean(state && currentRound < 5 && isRunningStatus(state.status));

  useEffect(() => {
    if (!state || state.current_round <= 0 || viewRoundPinned) return;
    setViewRound(state.current_round);
  }, [state?.current_round, viewRoundPinned]);

  useEffect(() => {
    if (!state || state.current_round <= 0 || viewRound <= state.current_round) return;
    setViewRound(state.current_round);
  }, [state?.current_round, viewRound]);

  useEffect(() => {
    if (!state || !["meeting", "report"].includes(step) || state.current_round >= 5) return;
    if (!isRunningStatus(state.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const nextState = await api.status(state.report_id);
        setState(nextState);
      } catch (err) {
        setError((err as Error).message);
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [state?.report_id, state?.current_round, state?.status, step]);

  async function search() {
    setLoading(true);
    setError("");
    try {
      const result = await api.searchCompanies(query, market);
      setCompanies(result.results);
      setSelectedCompany(result.results[0] || null);
      setStep("confirm");
      if (result.results.length === 0) setError("没有找到公司，请换一个代码或名称。");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function createCommittee(company = selectedCompany) {
    if (!company) return;
    setLoading(true);
    setError("");
    try {
      const result = await api.createCommittee(company.ticker, company.market as Market, "deep");
      const nextState = await api.status(result.report_id);
      setState(nextState);
      setSelectedExpertIds([]);
      setStep("experts");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function confirmExperts() {
    if (!state || selectedExpertIds.length !== 5) return;
    setLoading(true);
    try {
      await api.selectExperts(state.report_id, selectedExpertIds);
      const nextState = await api.status(state.report_id);
      setState(nextState);
      setStep("chairman");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function chooseChairman(chairmanId?: string, auto = true) {
    if (!state) return;
    setLoading(true);
    try {
      await api.selectChairman(state.report_id, chairmanId, auto);
      const nextState = await api.status(state.report_id);
      setState(nextState);
      setStep("collect");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function randomChairman() {
    if (!state?.selected_experts.length) return;
    const pick = state.selected_experts[Math.floor(Math.random() * state.selected_experts.length)];
    await chooseChairman(pick.id, false);
  }

  async function collectData() {
    if (!state) return;
    setLoading(true);
    setError("");
    setAgentProgress({ fundamental: 18, sentiment: 8, macro: 12, technical: 16 });
    const timer = window.setInterval(() => {
      setAgentProgress((current) => ({
        fundamental: Math.min(95, (current.fundamental || 0) + 15),
        sentiment: Math.min(95, (current.sentiment || 0) + 18),
        macro: Math.min(95, (current.macro || 0) + 13),
        technical: Math.min(95, (current.technical || 0) + 20)
      }));
    }, 260);
    try {
      await api.collectData(state.report_id);
      const nextState = await api.status(state.report_id);
      setAgentProgress({ fundamental: 100, sentiment: 100, macro: 100, technical: 100 });
      setState(nextState);
      setViewRoundPinned(false);
      setViewRound(Math.max(1, nextState.current_round || 1));
      setStep("meeting");
      await startBackgroundMeeting(state.report_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      window.clearInterval(timer);
      setLoading(false);
    }
  }

  async function startBackgroundMeeting(reportId = state?.report_id) {
    if (!reportId) return;
    setRunningRound(true);
    setError("");
    try {
      await api.autorunCommittee(reportId);
      const nextState = await api.status(reportId);
      setState(nextState);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRunningRound(false);
    }
  }

  async function runRound(roundNumber: number) {
    if (!state || runningRound) return;
    setRunningRound(true);
    setError("");
    try {
      const result = await api.runRound(state.report_id, roundNumber);
      const nextState = result?.running ? await waitForRound(state.report_id, roundNumber) : await api.status(state.report_id);
      setState(nextState);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRunningRound(false);
    }
  }

  async function waitForRound(reportId: string, roundNumber: number) {
    for (let index = 0; index < 90; index += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 3000));
      const nextState = await api.status(reportId);
      setState(nextState);
      if (String(nextState.status || "").includes("FAILED")) {
        throw new Error(nextState.status);
      }
      if ((nextState.current_round || 0) >= roundNumber) {
        return nextState;
      }
    }
    throw new Error("本轮仍在后台运行，请稍后刷新状态。");
  }

  async function refreshState() {
    if (!state) return;
    const nextState = await api.status(state.report_id);
    setState(nextState);
  }

  function toggleExpert(expertId: string) {
    setSelectedExpertIds((current) => {
      if (current.includes(expertId)) return current.filter((id) => id !== expertId);
      if (current.length >= 5) return current;
      return [...current, expertId];
    });
  }

  return (
    <main className="content-column">
      <Progress step={step} />
      {error && <div className="error-banner">{error}</div>}
      {step === "input" && (
        <section className="hero-workbench">
          <div className="hero-copy">
            <h1>AI投委会</h1>
            <p>输入一家上市公司，系统自动组建最懂它的真实人物投委会，完成资料采集、五轮递进会议和中文深度报告。</p>
          </div>
          <div className="start-panel">
            <label>
              <span>公司名称 / 股票代码</span>
              <div className="input-with-icon">
                <Search size={18} />
                <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && search()} placeholder="例如：贵州茅台 / NVDA / BABA" />
              </div>
            </label>
            <div className="control-grid">
              <label>
                <span>市场</span>
                <select value={market} onChange={(event) => setMarket(event.target.value as Market)}>
                  <option value="AUTO">自动识别</option>
                  <option value="US">美股 / 中概股</option>
                  <option value="HK">港股</option>
                  <option value="A">A 股</option>
                </select>
              </label>
              <label>
                <span>分析模式</span>
                <select defaultValue="deep">
                  <option value="deep">深度模式</option>
                </select>
              </label>
            </div>
            <button className="primary-action large" onClick={search} disabled={loading || !query.trim()}>
              <Sparkles size={18} />
              开始组建投委会
            </button>
            <div className="example-grid">
              {examples.map((example) => (
                <button key={example} onClick={() => setQuery(example.split(" / ")[0])}>{example}</button>
              ))}
            </div>
          </div>
        </section>
      )}

      {step === "confirm" && (
        <section className="workflow-panel">
          <SectionTitle eyebrow="公司确认" title="确认公司名称" description="同一家公司可能同时有美股、港股或 A 股证券，确认后会创建一次投委会任务。" />
          {companies.length > 1 && (
            <div className="listing-alert">
              已识别到 {companies.length} 个候选证券；同一经营主体在不同交易所的股价、币种、市值口径和流动性会分开显示。
            </div>
          )}
          <div className="company-grid">
            {companies.map((company) => (
              <button className={`company-card ${selectedCompany?.id === company.id ? "active" : ""}`} key={company.id} onClick={() => setSelectedCompany(company)}>
                <span>{formatDisplayValue(company.market)}</span>
                <strong>{formatDisplayValue(company.name)}</strong>
                <small>{quoteLabel(company)} · {formatDisplayValue(company.exchange)}</small>
                <p>{formatDisplayValue(company.description)}</p>
              </button>
            ))}
          </div>
          {selectedCompany && <CompanySnapshot company={selectedCompany} />}
          <div className="workflow-actions">
            <button className="ghost-action" onClick={() => setStep("input")}>返回修改</button>
            <button className="primary-action" onClick={() => createCommittee()} disabled={loading || !selectedCompany}>
              生成公司标签与专家推荐
              <ArrowRight size={17} />
            </button>
          </div>
        </section>
      )}

      {step === "experts" && state && (
        <section className="workflow-panel">
          <SectionTitle eyebrow="专家推荐" title="从 40 人专家库中选择 5 位委员" description="推荐分综合行业匹配、投资风格、市场经验、风险问题和人物多样性。" />
          <div className="tag-row">
            {Object.entries(state.company_tags).flatMap(([key, tags]) => tags.map((tag) => <span key={`${key}-${formatDisplayValue(tag)}`}>{formatDisplayValue(tag)}</span>))}
          </div>
            <div className="expert-card-grid">
              {recommendations.map((item) => (
                <ExpertCard key={item.expert.id} item={item} selected={selectedExpertIds.includes(item.expert.id)} onToggle={() => toggleExpert(item.expert.id)} />
              ))}
          </div>
          <div className="workflow-actions sticky-actions">
            <span>已选择 {selectedExpertIds.length} / 5</span>
            <button className="primary-action" onClick={confirmExperts} disabled={selectedExpertIds.length !== 5 || loading}>
              确认五位委员
              <ArrowRight size={17} />
            </button>
          </div>
        </section>
      )}

      {step === "chairman" && state && (
        <section className="workflow-panel">
          <SectionTitle eyebrow="主席确认" title="选出本次投委会主席" description="主席逻辑：行业匹配度 40% + 框架适配度 30% + 综合判断能力 20% + 随机扰动 10%。" />
          <div className="chairman-layout">
            <div className="chairman-card">
              <Users size={26} />
              <h2>系统将自动推荐主席</h2>
              <p>推荐会基于公司标签与五位委员画像计算，用户也可以手动指定或随机选择。</p>
              <div className="workflow-actions left">
                <button className="primary-action" onClick={() => chooseChairman(undefined, true)}>
                  接受推荐主席
                </button>
                <button className="ghost-action" onClick={randomChairman}>
                  <Shuffle size={16} />
                  随机主席
                </button>
              </div>
            </div>
            <div className="chairman-list">
              {selectedExperts.map((expert) => (
                <button key={expert.id} onClick={() => chooseChairman(expert.id, false)}>
                  <strong>{formatDisplayValue(expert.name)}</strong>
                  <span>{formatDisplayValue(expert.profile?.investment_philosophy)}</span>
                </button>
              ))}
            </div>
          </div>
        </section>
      )}

      {step === "collect" && state && (
        <section className="workflow-panel">
          <SectionTitle eyebrow="数据采集" title="生成研究资料包与证据库" description="系统会先识别证券、获取行情、公告、新闻、研报和指标，再把关键事实写成可追溯证据，专家只能基于这些证据分析。" />
          {chairman && Object.keys(chairman).length > 0 && (
            <div className="chairman-note">
              <strong>主席：{formatDisplayValue(chairman.name)}</strong>
              <span>{formatDisplayValue(chairman.chair_reason)}</span>
            </div>
          )}
          <div className="agent-progress-grid">
            <AgentProgress title="基本面研究员" desc="读取财报、指标、风险因素与管理层指引" value={agentProgress.fundamental || (state.data_pack.fundamental ? 100 : 0)} />
            <AgentProgress title="市场情绪研究员" desc="整理新闻、社媒、多空争议与催化剂" value={agentProgress.sentiment || (state.data_pack.sentiment ? 100 : 0)} />
            <AgentProgress title="宏观行业研究员" desc="分析政策、利率、周期、产业链与竞争格局" value={agentProgress.macro || (state.data_pack.macro ? 100 : 0)} />
            <AgentProgress title="技术面研究员" desc="计算均线、成交量、相对强弱指标、平滑异同均线和支撑压力" value={agentProgress.technical || (state.data_pack.technical ? 100 : 0)} />
            <AgentProgress title="证据库" desc="生成证据编号、来源、置信度与新鲜度评分" value={state.data_pack.evidence_store?.length ? 100 : 0} />
          </div>
          {state.data_pack.fundamental && <DataPackSummary dataPack={state.data_pack} />}
          <div className="workflow-actions">
            <button className="primary-action" onClick={collectData} disabled={loading}>
              {state.data_pack.fundamental ? "重新采集资料包" : "启动数据采集"}
            </button>
            {state.data_pack.fundamental && (
              <button className="secondary-action" onClick={() => {
                setStep("meeting");
                void startBackgroundMeeting();
              }}>
                进入五轮会议
                <ArrowRight size={17} />
              </button>
            )}
          </div>
        </section>
      )}

      {step === "meeting" && state && (
        <section className="meeting-layout">
          <div className="meeting-main">
            <SectionTitle eyebrow="投委会会议" title={`${state.company.name} · 已完成 ${currentRound} / 5 轮`} description="五轮会议已交给后端托管，离开页面或切到历史记录也不会中断；完成后最终报告会保存在历史库。" />
            <div className="round-timeline">
              {[1, 2, 3, 4, 5].map((round) => (
                <button
                  key={round}
                  className={`${round <= currentRound ? "done" : round === nextRound && currentRound < 5 ? "next" : ""} ${round === viewRound ? "active-view" : ""}`}
                  onClick={() => {
                    if (round <= currentRound) {
                      setViewRound(round);
                      setViewRoundPinned(true);
                    }
                  }}
                  disabled={round > currentRound}
                >
                  {round <= currentRound ? <Check size={15} /> : round}
                  <span>{roundName(round)}</span>
                </button>
              ))}
            </div>
            <RoundOutput state={state} viewingRound={viewRound} runningRound={meetingRunning || runningRound ? nextRound : undefined} />
            <div className="meeting-actions">
              <span className="run-status">{meetingStatusText(state, runningRound)}</span>
              {viewRoundPinned && currentRound > 0 && (
                <button className="ghost-action" onClick={() => {
                  setViewRound(currentRound);
                  setViewRoundPinned(false);
                }}>
                  <RefreshCw size={16} />
                  跟随最新轮
                </button>
              )}
              <button className="ghost-action" onClick={refreshState}>
                <RefreshCw size={16} />
                刷新状态
              </button>
              {currentRound < 5 ? (
                <button className="primary-action" onClick={() => startBackgroundMeeting()} disabled={runningRound || meetingRunning}>
                  <Play size={16} />
                  {runningRound || meetingRunning ? "后台托管中..." : `托管生成第 ${nextRound}-5 轮`}
                </button>
              ) : (
                <button className="primary-action" onClick={() => setStep("report")}>
                  <FileText size={16} />
                  查看最终报告
                </button>
              )}
            </div>
          </div>
          <aside className="meeting-side">
            <CompanyMini company={state.company} />
            <div className="side-panel">
              <h3>委员</h3>
              {state.selected_experts.map((expert) => (
                <span className="member-pill" key={expert.id}>{formatDisplayValue(expert.name)}</span>
              ))}
            </div>
            <div className="side-panel">
              <h3>资料包</h3>
              <DataPackSummary dataPack={state.data_pack} />
            </div>
          </aside>
        </section>
      )}

      {step === "report" && state && <ReportView state={state} onRefresh={refreshState} />}
    </main>
  );
}

function Progress({ step }: { step: Step }) {
  const activeIndex = stepLabels.findIndex(([key]) => key === step);
  return (
    <nav className="stepper">
      {stepLabels.map(([key, label], index) => (
        <span key={key} className={index <= activeIndex ? "active" : ""}>
          {index + 1}. {label}
        </span>
      ))}
    </nav>
  );
}

function SectionTitle({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <div className="section-heading compact-heading">
      <div>
        <p className="eyebrow">{formatDisplayValue(eyebrow)}</p>
        <h1>{formatDisplayValue(title)}</h1>
        <p>{formatDisplayValue(description)}</p>
      </div>
    </div>
  );
}

function CompanySnapshot({ company }: { company: Company }) {
  const snap = company.snapshot || {};
  const raw = snap.raw_data || {};
  const currency = typeof raw.quote_currency === "string" ? raw.quote_currency : "";
  const capUnit = typeof raw.market_cap_unit === "string" ? raw.market_cap_unit : "亿";
  const quoteSource = typeof raw.quote_source === "string" ? raw.quote_source : "本地快照";
  const quoteSymbol = typeof raw.quote_symbol === "string" ? raw.quote_symbol : quoteLabel(company);
  return (
    <div className="snapshot-grid">
      <Metric label="最新股价" value={formatPrice(snap.price, currency)} />
      <Metric label="报价证券" value={quoteSymbol} />
      <Metric label="市值" value={formatMarketCap(snap.market_cap, capUnit)} />
      <Metric label="市盈率" value={snap.pe_ratio ?? "-"} />
      <Metric label="市净率" value={snap.pb_ratio ?? "-"} />
      <Metric label="毛利率" value={snap.gross_margin ? `${snap.gross_margin}%` : "-"} />
      <Metric label="ROE" value={snap.roe ? `${snap.roe}%` : "-"} />
      <Metric label="币种" value={currency || "-"} />
      <Metric label="报价源" value={quoteSource} />
      <Metric label="行业" value={localizeDomainTerm(company.industry)} />
      <Metric label="报价更新时间" value={formatQuoteTime(raw.quote_fetched_at, snap.snapshot_date)} />
    </div>
  );
}

function CompanyMini({ company }: { company: Company }) {
  return (
    <div className="side-panel">
      <h3>{formatDisplayValue(company.name)}</h3>
      <p>{formatDisplayValue(company.ticker)} · {formatDisplayValue(company.exchange)}</p>
      <p>{formatDisplayValue(company.description)}</p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="metric">
      <span>{formatDisplayValue(label)}</span>
      <strong>{formatDisplayValue(value)}</strong>
    </div>
  );
}

function quoteLabel(company: Company) {
  if (company.market === "A") {
    return `${company.ticker}.${company.exchange.startsWith("SSE") ? "SH" : "SZ"}`;
  }
  if (company.market === "HK") {
    const code = company.ticker.replace(".HK", "").replace(/\D/g, "").padStart(5, "0");
    return `${code}.HK`;
  }
  return company.ticker;
}

function formatPrice(value?: number, currency?: string) {
  if (typeof value !== "number") return "-";
  return currency ? `${value} ${currency}` : value;
}

function formatMarketCap(value?: number, unit = "亿") {
  if (typeof value !== "number") return "-";
  return `${value} ${unit}`;
}

function formatQuoteTime(value: unknown, fallback?: string) {
  if (typeof value === "string" && value) {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleString("zh-CN", { hour12: false });
    }
  }
  return fallback || "-";
}

function ExpertCard({ item, selected, onToggle }: { item: RecommendedExpert; selected: boolean; onToggle: () => void }) {
  const expert = item.expert;
  return (
    <button type="button" className={`expert-card ${selected ? "selected" : ""}`} onClick={onToggle} aria-pressed={selected}>
      <div className="expert-card-head">
        <span className="avatar">{formatDisplayValue(expert.name).slice(0, 1)}</span>
        <div>
          <h3>{formatDisplayValue(expert.name)}</h3>
          <p>{formatDisplayValue(expert.role_title)}</p>
        </div>
        <span className={selected ? "select-check active" : "select-check"}>
          {selected ? <Check size={16} /> : "+"}
        </span>
      </div>
      <div className="tag-row compact-tags">
        {expert.profile.style_tags.slice(0, 4).map((tag) => <span key={formatDisplayValue(tag)}>{formatDisplayValue(tag)}</span>)}
      </div>
      <p>{formatDisplayValue(expert.profile.investment_philosophy)}</p>
      <dl>
        <dt>擅长</dt>
        <dd>{expert.profile.preferred_industries.map(formatDisplayValue).join("、")}</dd>
        <dt>不擅长</dt>
        <dd>{formatDisplayValue(expert.profile.weaknesses)}</dd>
      </dl>
      <div className="fit-line">
        <span>匹配度</span>
        <strong>{item.fit_score}%</strong>
      </div>
      <p className="reason">{formatDisplayValue(item.reason)}</p>
    </button>
  );
}

function AgentProgress({ title, desc, value }: { title: string; desc: string; value: number }) {
  return (
    <article className="agent-progress">
      <div>
        <h3>{formatDisplayValue(title)}</h3>
        <p>{formatDisplayValue(desc)}</p>
      </div>
      <strong>{value}%</strong>
      <div className="progress-bar">
        <span style={{ width: `${value}%` }} />
      </div>
    </article>
  );
}

export function RoundOutput({ state, viewingRound, runningRound }: { state: ReportState; viewingRound: number; runningRound?: number }) {
  const selected = state.rounds.find((round) => round.round_number === viewingRound);
  if (!selected) {
    return (
      <div className="empty-state compact">
        <Play size={26} />
        <p>{runningRound ? `第 ${runningRound} 轮正在后台生成，完成后会自动出现在时间线中。` : "五轮会议会在后台按顺序生成，完成的轮次可在上方时间线查看。"}</p>
      </div>
    );
  }
  const output = selected.round_output;
  return (
    <div className="round-output">
      <div className="round-header">
        <span>{formatDisplayValue(selected.round_name)}</span>
        <strong>{formatDisplayValue(output.summary || output.one_sentence_conclusion || "本轮已完成")}</strong>
      </div>
      {renderRound(output, selected.round_number, state.data_pack)}
    </div>
  );
}

function renderRound(output: any, roundNumber: number, dataPack: DataPack) {
  if (roundNumber === 1) {
    return <SpeechGrid items={output.speeches} scoreKey="initial_score" actionKey="initial_action" dataPack={dataPack} />;
  }
  if (roundNumber === 2) {
    return <SpeechGrid items={output.challenges} dataPack={dataPack} />;
  }
  if (roundNumber === 3) {
    return <SpeechGrid items={output.revisions} scoreKey="new_score" actionKey="final_action" dataPack={dataPack} />;
  }
  if (roundNumber === 4) {
    const consensus = normalizeList(output.consensus);
    return (
      <div className="chair-summary">
        <p>{formatDisplayValue(output.chairman_preliminary_conclusion || output.summary || "主席总结已完成。")}</p>
        {consensus.length ? (
          <ul>
            {consensus.map((item, index) => (
              <li key={`consensus-${index}`}>
                {formatOpinionItem(item)}
                <EvidencePills ids={extractEvidenceIds(item)} dataPack={dataPack} />
              </li>
            ))}
          </ul>
        ) : null}
        <EvidenceList title="关键分歧" items={output.disagreements || output.key_disagreement} dataPack={dataPack} />
      </div>
    );
  }
  const scorecard = output.scorecard || dataPack.scorecard;
  return (
    <div className="final-grid">
      <Metric label="最终建议" value={formatDisplayValue(output.final_action)} />
      <Metric label="公司质量分" value={formatDisplayValue(scorecard?.company_quality_score)} />
      <Metric label="估值吸引力" value={formatDisplayValue(scorecard?.valuation_attractiveness_score)} />
      <Metric label="投资行动分" value={formatDisplayValue(scorecard?.investment_action_score ?? output.overall_score)} />
      <Metric label="数据可信度" value={formatDisplayValue(scorecard?.data_quality_score)} />
      <Metric label="置信度" value={formatDisplayValue(output.confidence)} />
      <Metric label="估值区间" value={formatValueRange(output.fair_value_range)} />
      <Metric label="一句话结论" value={formatDisplayValue(output.one_sentence_conclusion)} />
    </div>
  );
}

function SpeechGrid({ items = [], scoreKey, actionKey, dataPack }: { items: any[]; scoreKey?: string; actionKey?: string; dataPack: DataPack }) {
  return (
    <div className="speech-grid">
      {items.map((item) => (
        <article className="speech-card" key={item.expert}>
          <h3>{formatDisplayValue(item.expert || "委员")}</h3>
          {(item.core_judgment || item.thesis || item.final_thesis) && <p>{formatDisplayValue(item.core_judgment || item.thesis || item.final_thesis)}</p>}
          <EvidenceList title="关键观点" items={item.key_points || item.bullish_points || item.key_facts} dataPack={dataPack} />
          <EvidenceList title="风险/质疑" items={item.red_flags || item.bearish_points || item.dangerous_assumptions || item.main_concerns} dataPack={dataPack} />
          <EvidenceList title="问题/修正" items={item.questions_to_committee || item.changed_because || item.still_believe} dataPack={dataPack} />
          {scoreKey && <strong>{formatDisplayValue(item[scoreKey] ?? item.score)} 分 · {formatDisplayValue(item[actionKey || ""])}</strong>}
          {item.confidence !== undefined && <span className="confidence-line">置信度 {formatDisplayValue(item.confidence)} · 适配度 {formatDisplayValue(item.fit_score ?? "-")}</span>}
        </article>
      ))}
    </div>
  );
}

function EvidenceList({ title, items, dataPack }: { title: string; items?: unknown; dataPack: DataPack }) {
  const list = normalizeList(items).slice(0, 3);
  if (!list.length) return null;
  return (
    <div className="evidence-list">
      <span>{formatDisplayValue(title)}</span>
      {list.map((item, index) => (
        <p key={index}>
          {formatOpinionItem(item)}
          <EvidencePills ids={extractEvidenceIds(item)} dataPack={dataPack} />
        </p>
      ))}
    </div>
  );
}

function EvidencePills({ ids, dataPack }: { ids: string[]; dataPack: DataPack }) {
  if (!ids.length) return null;
  return (
    <span className="evidence-pills">
      {ids.slice(0, 4).map((id) => {
        const evidence = dataPack.evidence_store?.find((item) => item.evidence_id === id);
        return <span key={id} title={evidence ? `${id} · ${formatDisplayValue(evidence.title)} · ${formatDisplayValue(evidence.source_provider)} · 置信度 ${formatDisplayValue(evidence.confidence)}` : id}>{formatDisplayValue(evidence?.title || evidenceName(id))}</span>;
      })}
    </span>
  );
}

function evidenceName(id: string) {
  const names: Record<string, string> = {
    ev_profile_identity: "公司与证券识别",
    ev_financial_margin_roe: "盈利能力快照",
    ev_business_description: "商业模式与行业定位",
    ev_risk_tags: "风险标签与待验证事项",
    ev_sentiment_debate: "市场分歧议题",
    ev_macro_industry: "宏观行业变量",
    ev_quote_latest: "最新证券报价",
    ev_technical_levels: "技术面支撑压力估算",
    ev_valuation_pe_pb: "相对估值倍数",
    ev_technical_history: "六个月日K技术指标"
  };
  return names[id] || localizeUiText(
    id
      .replace(/^ev_/, "证据：")
      .replace(/financial_income_statement/g, "财务 利润表")
      .replace(/financial_balance_sheet/g, "财务 资产负债表")
      .replace(/financial_cash_flow/g, "财务 现金流量表")
      .replace(/filing_search/g, "公告 搜索补齐")
      .replace(/news_yahoo/g, "雅虎财经新闻")
      .replace(/peer_comparable_set/g, "同业样本")
      .replace(/_/g, " ")
  );
}

function localizeDomainTerm(value: string) {
  return value
    .replace(/CXO/g, "医药研发生产外包")
    .replace(/AI算力/g, "人工智能算力")
    .replace(/GPU/g, "图形处理器")
    .replace(/ETF/g, "交易型开放式指数基金");
}

function formatOpinionItem(item: unknown): string {
  if (typeof item === "string") return localizeUiText(item);
  if (typeof item === "number") return Number.isFinite(item) ? String(item) : "-";
  if (typeof item === "boolean") return item ? "是" : "否";
  if (!item || typeof item !== "object") return String(item ?? "");
  const record = item as Record<string, unknown>;
  return formatDisplayValue(record.point || record.flag || record.risk || record.condition || record.metric || record.assumption || record.question || record.topic || record.reason || record.summary || record.title || JSON.stringify(record));
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
  return value
    .replace(/SEC XBRL/g, "美国证监会结构化财报")
    .replace(/SEC EDGAR Search Fallback/g, "美国证监会公告搜索补齐")
    .replace(/SEC EDGAR/g, "美国证监会公告系统")
    .replace(/SEC Companyfacts/g, "美国证监会结构化财报")
    .replace(/Yahoo Finance RSS/g, "雅虎财经新闻")
    .replace(/Yahoo Finance Chart/g, "雅虎财经历史行情")
    .replace(/Yahoo Finance 新闻/g, "雅虎财经新闻")
    .replace(/Yahoo Finance/g, "雅虎财经")
    .replace(/Eastmoney HK F10/g, "东方财富港股F10")
    .replace(/Internet Search Fallback/g, "互联网搜索补齐")
    .replace(/Search Fallback/g, "搜索补齐")
    .replace(/Reddit public JSON/g, "海外论坛公开数据")
    .replace(/StockTwits/g, "美股社区")
    .replace(/Income Statement/g, "利润表")
    .replace(/Balance Sheet/g, "资产负债表")
    .replace(/Cash Flow Statement/g, "现金流量表")
    .replace(/current price/g, "当前价格")
    .replace(/target price/g, "目标价")
    .replace(/fair value/g, "公允价值")
    .replace(/market cap/g, "市值")
    .replace(/financial_statement/g, "财报三表")
    .replace(/source_provider/g, "来源")
    .replace(/source_url/g, "来源链接")
    .replace(/confidence/g, "置信度")
    .replace(/fit_score/g, "适配度");
}

function normalizeList(items: unknown) {
  if (Array.isArray(items)) return items.filter((item) => item !== null && item !== undefined);
  return items === null || items === undefined ? [] : [items];
}

function extractEvidenceIds(item: unknown) {
  if (!item || typeof item !== "object") return [];
  const ids = (item as Record<string, unknown>).evidence_ids;
  return Array.isArray(ids) ? ids.map(String) : [];
}

function formatValueRange(range?: { bear?: number; base?: number; bull?: number; currency?: string }) {
  if (!range) return "-";
  return `${range.bear ?? "-"} / ${range.base ?? "-"} / ${range.bull ?? "-"} ${range.currency ?? ""}`;
}

function isRunningStatus(status?: string) {
  const value = status || "";
  return value.includes("RUNNING") || value.startsWith("AUTO_RUN");
}

function meetingStatusText(state: ReportState, starting: boolean) {
  if (state.current_round >= 5) return "五轮已完成，可查看最终报告。";
  if (starting || isRunningStatus(state.status)) {
    return `后台正在托管生成，已完成 ${state.current_round || 0} / 5 轮；可以离开页面，结果会进入历史记录。`;
  }
  if (String(state.status || "").includes("FAILED")) return `后台生成失败：${state.status}`;
  return `研判已保存，可继续托管生成第 ${Math.min(5, (state.current_round || 0) + 1)}-5 轮。`;
}

function roundName(round: number) {
  return ["独立分析", "相互质疑", "修正观点", "主席总结", "最终结论"][round - 1];
}
