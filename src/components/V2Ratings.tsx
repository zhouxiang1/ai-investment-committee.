import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Search, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { api } from "../api";
import type { Market, V2Rating, V2RatingsResponse } from "../types";

export function V2Ratings() {
  const [data, setData] = useState<V2RatingsResponse | null>(null);
  const [market, setMarket] = useState<Market>("AUTO");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  async function load(nextMarket = market, nextQuery = query) {
    setLoading(true);
    try {
      const result = await api.v2Ratings(nextMarket, nextQuery);
      setData(result);
      setMessage("");
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => load(market, query), 220);
    return () => window.clearTimeout(timer);
  }, [market, query]);

  async function rebuild() {
    setLoading(true);
    setMessage("正在重建 2.0 重点100评级...");
    try {
      const result = await api.rebuildV2Ratings(market, query);
      setData(result);
      setMessage(`已重建 ${result.expected_total} 家公司评级`);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const ratings = data?.ratings || [];
  const actionRows = useMemo(() => Object.entries(data?.summary.by_action || {}).sort((a, b) => b[1] - a[1]), [data]);

  return (
    <main className="content-column">
      <section className="section-heading">
        <div>
          <p className="eyebrow">Version 2.0</p>
          <h1>重点100公司评级</h1>
          <p>覆盖美股 40 家、A股 35 家、港股 25 家；评分会吸收已有 AICS 证据卡，并在缺少实时证据时使用可复现 baseline。</p>
        </div>
        <button className="primary-action" onClick={rebuild} disabled={loading}>
          <RefreshCw size={17} />
          重建评级
        </button>
      </section>

      <section className="v2-rating-toolbar">
        <label className="company-search">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索代码、公司或分组" />
        </label>
        <select value={market} onChange={(event) => setMarket(event.target.value as Market)}>
          <option value="AUTO">全部市场</option>
          <option value="US">美股</option>
          <option value="A">A股</option>
          <option value="HK">港股</option>
        </select>
      </section>

      {message && <div className="inline-message">{message}</div>}

      <section className="v2-rating-summary">
        <Stat label="清单覆盖" value={`${data?.total || 0}/${data?.expected_total || 100}`} />
        <Stat label="美股" value={data?.summary.by_market.US || 0} />
        <Stat label="A股" value={data?.summary.by_market.A || 0} />
        <Stat label="港股" value={data?.summary.by_market.HK || 0} />
        <Stat label="版本" value={data?.version || "-"} compact />
      </section>

      <section className="v2-rating-grid">
        <div className="v2-rating-panel">
          <div className="panel-heading tight">
            <div>
              <p className="eyebrow">Top 10</p>
              <h2>行动分领先</h2>
            </div>
            <ShieldCheck size={20} />
          </div>
          <div className="v2-top-list">
            {(data?.summary.top10 || []).map((item) => (
              <div key={`${item.ticker}-${item.name}`}>
                <span>{item.rank || item.list_rank}</span>
                <strong>{item.name}</strong>
                <small>{item.ticker} · {item.final_action}</small>
                <b>{Math.round(item.action_score)}</b>
              </div>
            ))}
          </div>
        </div>

        <div className="v2-rating-panel">
          <div className="panel-heading tight">
            <div>
              <p className="eyebrow">Actions</p>
              <h2>动作分布</h2>
            </div>
            <SlidersHorizontal size={20} />
          </div>
          <div className="action-distribution">
            {actionRows.map(([action, count]) => (
              <div key={action}>
                <span>{action}</span>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="v2-rating-table">
        <div className="v2-rating-row head">
          <span>排名</span>
          <span>公司</span>
          <span>市场</span>
          <span>分组</span>
          <span>质量</span>
          <span>估值</span>
          <span>行动</span>
          <span>评级</span>
        </div>
        {loading && <div className="loading-row">正在读取 2.0 评级...</div>}
        {!loading && ratings.map((rating) => <RatingRow key={`${rating.list_rank}-${rating.ticker}`} rating={rating} />)}
      </section>
    </main>
  );
}

function RatingRow({ rating }: { rating: V2Rating }) {
  return (
    <div className="v2-rating-row">
      <span>{rating.list_rank}</span>
      <span>
        <strong>{rating.name}</strong>
        <small>{codeLabel(rating)} · {rating.name_en || rating.exchange}</small>
      </span>
      <span>{marketName(rating.market)}</span>
      <span>{rating.theme}</span>
      <ScorePill value={rating.quality_score} />
      <ScorePill value={rating.valuation_score} />
      <ScorePill value={rating.action_score} />
      <span className={`rating-badge rating-${rating.final_rating.toLowerCase()}`}>
        {rating.final_rating} · {rating.final_action}
      </span>
    </div>
  );
}

function ScorePill({ value }: { value: number }) {
  return <span className="score-pill">{Math.round(value)}</span>;
}

function Stat({ label, value, compact = false }: { label: string; value: string | number; compact?: boolean }) {
  return (
    <div className={`settings-card ${compact ? "compact" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function marketName(market: string) {
  if (market === "US") return "美股";
  if (market === "A") return "A股";
  if (market === "HK") return "港股";
  return market;
}

function codeLabel(rating: V2Rating) {
  if (rating.original_code && rating.original_code !== rating.ticker) {
    return `${rating.original_code} / ${rating.ticker}`;
  }
  return rating.ticker;
}
