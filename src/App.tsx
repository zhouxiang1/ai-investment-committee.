import { useEffect, useState } from "react";
import { BookOpen, DatabaseZap, History, Library, Settings, Sparkles, Trophy } from "lucide-react";
import { api } from "./api";
import { ExpertAdmin } from "./components/ExpertAdmin";
import { History as HistoryView } from "./components/History";
import { V2Ratings } from "./components/V2Ratings";
import { Workbench } from "./components/Workbench";
import type { Health } from "./types";

type View = "workbench" | "ratings" | "experts" | "history" | "settings";

export default function App() {
  const [view, setView] = useState<View>("workbench");
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Sparkles size={22} />
          </span>
          <div>
            <strong>AI投委会</strong>
            <small>个人深度投研系统</small>
          </div>
        </div>

        <nav className="main-nav">
          <button className={view === "workbench" ? "active" : ""} onClick={() => setView("workbench")}>
            <DatabaseZap size={18} />
            发起会议
          </button>
          <button className={view === "ratings" ? "active" : ""} onClick={() => setView("ratings")}>
            <Trophy size={18} />
            2.0评级
          </button>
          <button className={view === "experts" ? "active" : ""} onClick={() => setView("experts")}>
            <Library size={18} />
            专家库后台
          </button>
          <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>
            <History size={18} />
            报告历史
          </button>
          <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>
            <Settings size={18} />
            Provider 状态
          </button>
        </nav>

        <div className="sidebar-footer">
          <BookOpen size={16} />
          <span>中文报告 · 五轮递进 · PDF 导出</span>
        </div>
      </aside>

      <div className="app-content">
        {view === "workbench" && <Workbench />}
        {view === "ratings" && <V2Ratings />}
        {view === "experts" && <ExpertAdmin />}
        {view === "history" && <HistoryView />}
        {view === "settings" && <SettingsView health={health} />}
      </div>
    </div>
  );
}

function SettingsView({ health }: { health: Health | null }) {
  return (
    <main className="content-column">
      <section className="section-heading">
        <div>
          <p className="eyebrow">LLM Provider</p>
          <h1>统一大模型接入状态</h1>
          <p>后端支持 OpenAI-compatible Provider。五轮会议默认要求真实模型调用，密钥只在后端读取，不进入前端包。</p>
        </div>
      </section>
      <section className="settings-grid">
        <div className="settings-card">
          <span>后端状态</span>
          <strong>{health?.ok ? "运行中" : "未连接"}</strong>
        </div>
        <div className="settings-card">
          <span>模型</span>
          <strong>{health?.llm.model || "MiniMax-M2.7"}</strong>
        </div>
        <div className="settings-card">
          <span>Base URL</span>
          <strong>{health?.llm.base_url || "https://api.minimax.io/v1"}</strong>
        </div>
        <div className="settings-card">
          <span>LLM 调用</span>
          <strong>{health?.llm.enabled ? "已启用" : "未启用，会议轮次会停止"}</strong>
        </div>
        <div className="settings-card">
          <span>API Key</span>
          <strong>{health?.llm.has_key ? "已配置" : "未配置"}</strong>
        </div>
        <div className="settings-card">
          <span>兜底模式</span>
          <strong>{health?.llm.allow_fallback ? "已启用" : "关闭"}</strong>
        </div>
      </section>
      <section className="note-panel">
        <h2>启用 MiniMax M2.7</h2>
        <p>在本机 `.env` 中设置 `MINIMAX_API_KEY`、`MINIMAX_BASE_URL`、`MINIMAX_MODEL`，并把 `AI_COMMITTEE_USE_LLM` 设为 `true`。为了避免预设假会议，五轮投委会没有配置模型时不会继续生成专家发言。</p>
      </section>
    </main>
  );
}
