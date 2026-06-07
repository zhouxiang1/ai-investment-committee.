import { CSSProperties, useEffect, useMemo, useState } from "react";
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
  const [focused, setFocused] = useState<V2Rating | null>(null);

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

      <QuadrantMap ratings={ratings} focused={focused} onFocus={setFocused} />

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

function QuadrantMap({ ratings, focused, onFocus }: { ratings: V2Rating[]; focused: V2Rating | null; onFocus: (rating: V2Rating) => void }) {
  const featured = focused || ratings[0];
  return (
    <section className="quadrant-section">
      <div className="quadrant-heading">
        <div>
          <p className="eyebrow">Quality × Valuation</p>
          <h2>公司质量与估值吸引力四象限</h2>
        </div>
        {featured && (
          <div className="quadrant-focus">
            <span>{marketName(featured.market)} · {codeLabel(featured)}</span>
            <strong>{featured.name}</strong>
            <small>质量 {Math.round(featured.quality_score)} · 估值 {Math.round(featured.valuation_score)} · {featured.final_action}</small>
          </div>
        )}
      </div>

      <div className="quadrant-map" aria-label="公司质量与估值吸引力四象限图">
        <div className="quadrant-label top-left">
          <strong>好公司，等价格</strong>
          <span>质量高 / 估值吸引力低</span>
        </div>
        <div className="quadrant-label top-right">
          <strong>优先研究池</strong>
          <span>质量高 / 估值有吸引力</span>
        </div>
        <div className="quadrant-label bottom-left">
          <strong>谨慎回避</strong>
          <span>质量低 / 估值也不便宜</span>
        </div>
        <div className="quadrant-label bottom-right">
          <strong>赔率观察</strong>
          <span>质量一般 / 估值有赔率</span>
        </div>
        <div className="quadrant-axis x-axis">估值吸引力 VAS</div>
        <div className="quadrant-axis y-axis">公司质量 CQS</div>
        <div className="quadrant-midline vertical" />
        <div className="quadrant-midline horizontal" />

        {ratings.map((rating) => {
          const x = clampPct(rating.valuation_score);
          const y = 100 - clampPct(rating.quality_score);
          const active = focused?.list_rank === rating.list_rank;
          return (
            <button
              className={`company-dot market-${rating.market.toLowerCase()} ${active ? "active" : ""}`}
              key={`${rating.list_rank}-${rating.ticker}`}
              onClick={() => onFocus(rating)}
              onMouseEnter={() => onFocus(rating)}
              style={{ "--x": `${x}%`, "--y": `${y}%` } as CSSProperties}
              title={`${rating.name}｜质量 ${Math.round(rating.quality_score)}｜估值 ${Math.round(rating.valuation_score)}｜${rating.final_action}`}
            >
              <span>{rating.list_rank}</span>
            </button>
          );
        })}
      </div>

      <div className="quadrant-legend">
        <span><i className="legend-dot market-us" />美股</span>
        <span><i className="legend-dot market-a" />A股</span>
        <span><i className="legend-dot market-hk" />港股</span>
      </div>
    </section>
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

function clampPct(value: number) {
  return Math.max(4, Math.min(96, Number.isFinite(value) ? value : 50));
}
