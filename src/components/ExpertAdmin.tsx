import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { BrainCircuit, Database, Globe2, Plus, RefreshCw, Save, Search, Upload } from "lucide-react";
import { api } from "../api";
import type { Company, CompanyUniverseSummary, Expert, Market } from "../types";

const blankProfile = {
  investment_philosophy: "",
  core_framework: "",
  decision_process: "",
  question_template: "",
  speaking_style: "",
  strengths: "",
  weaknesses: "",
  preferred_industries: [],
  avoided_industries: [],
  market_tags: [],
  style_tags: [],
  risk_preference: "中等",
  time_horizon: "3-5年",
  source_summary: ""
};

const blankExpert: Expert = {
  id: "",
  name: "",
  name_en: "",
  category: "投资大师",
  nationality: "",
  role_title: "",
  bio: "",
  avatar_url: "",
  is_active: true,
  profile: blankProfile
};

export function ExpertAdmin() {
  const [experts, setExperts] = useState<Expert[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<Expert>(blankExpert);
  const [query, setQuery] = useState("");
  const [rawText, setRawText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [researching, setResearching] = useState(false);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companySummary, setCompanySummary] = useState<CompanyUniverseSummary | null>(null);
  const [companyQuery, setCompanyQuery] = useState("");
  const [companyMarket, setCompanyMarket] = useState<Market>("AUTO");
  const [companyTotal, setCompanyTotal] = useState(0);
  const [companiesLoading, setCompaniesLoading] = useState(false);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return experts;
    return experts.filter((expert) => `${expert.name} ${expert.name_en} ${expert.category} ${expert.role_title}`.toLowerCase().includes(needle));
  }, [experts, query]);

  async function loadExperts(nextSelectedId?: string) {
    setLoading(true);
    const result = await api.experts();
    setExperts(result.experts);
    const next = nextSelectedId || selectedId || result.experts[0]?.id;
    const selected = result.experts.find((expert) => expert.id === next) || result.experts[0];
    if (selected) {
      setSelectedId(selected.id);
      setDraft(cloneExpert(selected));
    }
    setLoading(false);
  }

  useEffect(() => {
    loadExperts();
    loadCompanies();
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => loadCompanies(), 250);
    return () => window.clearTimeout(timer);
  }, [companyQuery, companyMarket]);

  function selectExpert(expert: Expert) {
    setSelectedId(expert.id);
    setDraft(cloneExpert(expert));
    setPreview(null);
    setMessage("");
  }

  function createNew() {
    setSelectedId("");
    setDraft({ ...blankExpert, profile: { ...blankProfile } });
    setPreview(null);
    setMessage("");
  }

  async function save() {
    setMessage("");
    const payload = normalizeDraft(draft);
    const result = draft.id ? await api.updateExpert(payload as Expert) : await api.createExpert(payload);
    setMessage("专家画像已保存");
    await loadExperts(result.expert.id);
  }

  async function uploadMaterial() {
    if (!draft.id) {
      setMessage("请先保存专家，再上传材料");
      return;
    }
    const form = new FormData();
    form.set("title", `${draft.name} 蒸馏材料`);
    form.set("material_type", file ? "file" : "manual_text");
    form.set("language", "zh");
    form.set("raw_text", rawText);
    if (file) form.set("file", file);
    const result = await api.uploadMaterial(draft.id, form);
    setPreview(result.distillation_preview);
    setMessage("材料已上传，AI 蒸馏预览已生成");
  }

  async function distill() {
    if (!draft.id) return;
    const result = await api.distillExpert(draft.id);
    setPreview(result.distillation);
    setDraft(cloneExpert(result.expert));
    setMessage("已将蒸馏结果写入专家画像，可继续手动校准后保存");
  }

  async function bulkDistill() {
    setMessage("正在批量蒸馏专家库...");
    const result = await api.bulkDistillExperts();
    setMessage(`已完成 ${result.distilled_count} 位专家的批量 AI 蒸馏`);
    await loadExperts(selectedId);
  }

  async function researchSelected() {
    if (!draft.id) return;
    setResearching(true);
    setMessage(`正在联网检索 ${draft.name} 的传记、访谈、对话和投资框架资料...`);
    try {
      const result = await api.researchExpert(draft.id, 4);
      setPreview(result);
      setDraft(cloneExpert(result.expert));
      setMessage(`已抓取并蒸馏 ${result.research.source_count} 条公开材料，画像已写回专家库`);
      await loadExperts(draft.id);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setResearching(false);
    }
  }

  async function bulkResearch() {
    setResearching(true);
    setMessage("正在批量联网补强专家库，这会逐位搜索公开材料并蒸馏画像...");
    try {
      const result = await api.bulkResearchExperts(3);
      const sourced = result.results.filter((item) => item.source_count > 0).length;
      setPreview(result);
      setMessage(`已完成 ${result.researched_count} 位专家的联网补强，其中 ${sourced} 位抓取到可蒸馏材料`);
      await loadExperts(selectedId);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setResearching(false);
    }
  }

  async function loadCompanies() {
    setCompaniesLoading(true);
    try {
      const result = await api.companies(companyQuery, companyMarket, 80, 0);
      setCompanies(result.companies);
      setCompanyTotal(result.total);
      setCompanySummary(result.summary);
    } finally {
      setCompaniesLoading(false);
    }
  }

  async function syncCompanies() {
    setMessage("正在同步 A股 / 港股 / 美股公司库...");
    const result = await api.syncCompanies(["A", "HK", "US"]);
    setCompanySummary(result.summary);
    setMessage(`公司库同步完成：共 ${result.summary.total.toLocaleString()} 条证券主数据`);
    await loadCompanies();
  }

  return (
    <main className="admin-layout">
      <section className="admin-list">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">专家库后台</p>
            <h1>40 人真实人物蒸馏库</h1>
          </div>
          <button className="icon-action" onClick={createNew} title="新增专家">
            <Plus size={18} />
          </button>
        </div>
        <div className="admin-action-stack">
          <button className="secondary-action full-width-action" onClick={bulkResearch} disabled={researching}>
            <Globe2 size={16} />
            批量联网补强
          </button>
          <button className="secondary-action full-width-action" onClick={bulkDistill} disabled={researching}>
            <BrainCircuit size={16} />
            批量 AI 蒸馏专家库
          </button>
        </div>
        <div className="search-line">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索专家、标签或身份" />
          <button onClick={() => loadExperts()} title="刷新">
            <RefreshCw size={16} />
          </button>
        </div>
        <div className="expert-list">
          {loading && <div className="loading-row">正在读取专家库...</div>}
          {filtered.map((expert) => (
            <button className={`expert-list-item ${selectedId === expert.id ? "active" : ""}`} key={expert.id} onClick={() => selectExpert(expert)}>
              <span className="avatar">{expert.name.slice(0, 1)}</span>
              <span>
                <strong>{expert.name}</strong>
                <small>{expert.category} · {expert.role_title}</small>
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="admin-editor">
        <div className="editor-toolbar">
          <div>
            <p className="eyebrow">画像编辑</p>
            <h2>{draft.id ? draft.name : "新增专家"}</h2>
          </div>
          <button className="primary-action" onClick={save}>
            <Save size={17} />
            保存专家画像
          </button>
        </div>
        {message && <div className="success-banner">{message}</div>}

        <div className="form-grid">
          <TextField label="姓名" value={draft.name} onChange={(value) => setDraft({ ...draft, name: value })} />
          <TextField label="英文名" value={draft.name_en} onChange={(value) => setDraft({ ...draft, name_en: value })} />
          <TextField label="分类" value={draft.category} onChange={(value) => setDraft({ ...draft, category: value })} />
          <TextField label="国籍" value={draft.nationality} onChange={(value) => setDraft({ ...draft, nationality: value })} />
          <TextField label="身份标签" value={draft.role_title} onChange={(value) => setDraft({ ...draft, role_title: value })} wide />
          <TextArea label="简介" value={draft.bio} onChange={(value) => setDraft({ ...draft, bio: value })} />
          <TextArea label="投资哲学" value={draft.profile.investment_philosophy} onChange={(value) => updateProfile("investment_philosophy", value)} />
          <TextArea label="核心框架" value={draft.profile.core_framework} onChange={(value) => updateProfile("core_framework", value)} />
          <TextArea label="决策流程" value={draft.profile.decision_process} onChange={(value) => updateProfile("decision_process", value)} />
          <TextArea label="经典问题" value={draft.profile.question_template} onChange={(value) => updateProfile("question_template", value)} />
          <TextField label="擅长领域" value={draft.profile.preferred_industries.join(", ")} onChange={(value) => updateProfile("preferred_industries", splitTags(value))} />
          <TextField label="不擅长领域" value={draft.profile.avoided_industries.join(", ")} onChange={(value) => updateProfile("avoided_industries", splitTags(value))} />
          <TextField label="市场标签" value={draft.profile.market_tags.join(", ")} onChange={(value) => updateProfile("market_tags", splitTags(value))} />
          <TextField label="风格标签" value={draft.profile.style_tags.join(", ")} onChange={(value) => updateProfile("style_tags", splitTags(value))} />
          <TextArea label="发言风格" value={draft.profile.speaking_style} onChange={(value) => updateProfile("speaking_style", value)} />
          <TextArea label="权重规则/来源摘要" value={draft.profile.source_summary} onChange={(value) => updateProfile("source_summary", value)} />
        </div>

        <div className="distill-panel">
          <div className="panel-heading compact-heading">
            <div>
              <p className="eyebrow">材料蒸馏</p>
              <h3>上传公开材料或手动补齐</h3>
            </div>
            <button className="ghost-action" onClick={distill} disabled={!draft.id}>
              <BrainCircuit size={16} />
              AI 蒸馏
            </button>
            <button className="ghost-action" onClick={researchSelected} disabled={!draft.id || researching}>
              <Globe2 size={16} />
              联网补强
            </button>
          </div>
          <textarea className="material-text" value={rawText} onChange={(event) => setRawText(event.target.value)} placeholder="粘贴访谈、股东信、演讲、书籍摘录、案例复盘或研究摘要..." />
          <div className="upload-line">
            <label className="file-control">
              <Upload size={16} />
              <input type="file" onChange={(event) => setFile(event.target.files?.[0] || null)} />
              {file ? file.name : "选择材料文件"}
            </label>
            <button className="secondary-action" onClick={uploadMaterial} disabled={!draft.id}>
              上传材料并预览蒸馏
            </button>
          </div>
          {preview && (
            <pre className="distill-preview">{JSON.stringify(preview, null, 2)}</pre>
          )}
        </div>

        <div className="company-universe-panel">
          <div className="panel-heading compact-heading">
            <div>
              <p className="eyebrow">公司库后台</p>
              <h3>全市场证券主数据</h3>
            </div>
            <button className="secondary-action" onClick={syncCompanies} disabled={companiesLoading}>
              <Database size={16} />
              同步公司库
            </button>
          </div>
          <div className="company-universe-stats">
            <StatPill label="全部" value={companySummary?.total || companyTotal} />
            <StatPill label="A股" value={companySummary?.by_market?.A || 0} />
            <StatPill label="港股" value={companySummary?.by_market?.HK || 0} />
            <StatPill label="美股" value={companySummary?.by_market?.US || 0} />
            <span className="sync-time">{companySummary?.sync?.completed_at ? `最近同步 ${formatDateTime(companySummary.sync.completed_at)}` : "尚未执行全量同步"}</span>
          </div>
          <div className="company-tools">
            <label className="company-search">
              <Search size={16} />
              <input value={companyQuery} onChange={(event) => setCompanyQuery(event.target.value)} placeholder="搜索公司、代码、行业或别名" />
            </label>
            <select value={companyMarket} onChange={(event) => setCompanyMarket(event.target.value as Market)}>
              <option value="AUTO">全部市场</option>
              <option value="A">A股</option>
              <option value="HK">港股</option>
              <option value="US">美股</option>
            </select>
            <button onClick={loadCompanies} title="刷新公司库">
              <RefreshCw size={16} />
            </button>
          </div>
          <div className="company-table">
            <div className="company-table-head">
              <span>代码</span>
              <span>公司</span>
              <span>市场</span>
              <span>行业</span>
            </div>
            {companiesLoading && <div className="loading-row">正在读取公司库...</div>}
            {!companiesLoading && companies.map((company) => (
              <div className="company-table-row" key={company.id}>
                <strong>{company.ticker}</strong>
                <span>{company.name}<small>{company.name_en}</small></span>
                <span>{marketName(company.market)} · {company.exchange}</span>
                <span>{company.industry || "待补充行业"}</span>
              </div>
            ))}
            {!companiesLoading && !companies.length && <div className="loading-row">暂无匹配公司。请先同步公司库，或换一个关键词。</div>}
          </div>
          <p className="company-footnote">当前展示前 80 条，搜索会直接查询后端公司库；无法匹配到证券主数据的输入不会再自动生成占位公司。</p>
        </div>
      </section>
    </main>
  );

  function updateProfile(key: keyof Expert["profile"], value: any) {
    setDraft({ ...draft, profile: { ...draft.profile, [key]: value } });
  }
}

function StatPill({ label, value }: { label: string; value: number }) {
  return (
    <span className="stat-pill">
      {label}
      <strong>{Number(value || 0).toLocaleString()}</strong>
    </span>
  );
}

function marketName(market: string) {
  return { A: "A股", HK: "港股", US: "美股" }[market] || market;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function TextField({ label, value, onChange, wide }: { label: string; value: string; onChange: (value: string) => void; wide?: boolean }) {
  return (
    <label className={wide ? "wide-field" : ""}>
      <span>{label}</span>
      <input value={value || ""} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function TextArea({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="wide-field">
      <span>{label}</span>
      <textarea value={value || ""} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function splitTags(value: string) {
  return value
    .split(/[,，/、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function cloneExpert(expert: Expert): Expert {
  return JSON.parse(JSON.stringify(expert));
}

function normalizeDraft(expert: Expert) {
  return {
    ...expert,
    profile: {
      ...blankProfile,
      ...expert.profile
    }
  };
}
