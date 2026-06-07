import { CSSProperties, useEffect, useMemo, useState } from "react";
import { Search, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { api } from "../api";
import type { Market, V2Rating, V2RatingsResponse } from "../types";

const QUALITY_THRESHOLD = 82;
const VALUATION_THRESHOLD = 65;
const AXIS_MIN = 55;
const AXIS_MAX = 95;
type SortKey = "quality_score" | "valuation_score" | "action_score" | "final_rating";
type SortDirection = "asc" | "desc";
const RATING_ORDER: Record<string, number> = { S: 5, A: 4, B: 3, C: 2, D: 1 };

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

  const ratings = data?.ratings || [];
  const actionRows = useMemo(() => Object.entries(data?.summary.by_action || {}).sort((a, b) => b[1] - a[1]), [data]);
  const [focused, setFocused] = useState<V2Rating | null>(null);
  const [selected, setSelected] = useState<V2Rating | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection } | null>(null);
  const sortedRatings = useMemo(() => sortRatings(ratings, sort), [ratings, sort]);

  function toggleSort(key: SortKey) {
    setSort((current) => {
      if (current?.key !== key) return { key, direction: "desc" };
      return { key, direction: current.direction === "desc" ? "asc" : "desc" };
    });
  }

  return (
    <main className="content-column">
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
        <Stat label="清单覆盖" value={`${data?.total || 0}/${data?.expected_total || 300}`} />
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
                <button className="text-link strong" onClick={() => setSelected(findRating(ratings, item))}>{item.name}</button>
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
          <SortButton label="质量" column="quality_score" sort={sort} onSort={toggleSort} />
          <SortButton label="估值" column="valuation_score" sort={sort} onSort={toggleSort} />
          <SortButton label="行动" column="action_score" sort={sort} onSort={toggleSort} />
          <SortButton label="评级" column="final_rating" sort={sort} onSort={toggleSort} />
        </div>
        {loading && <div className="loading-row">正在读取 2.0 评级...</div>}
        {!loading && sortedRatings.map((rating) => <RatingRow key={`${rating.list_rank}-${rating.ticker}`} rating={rating} onOpen={setSelected} />)}
      </section>
      {selected && <RatingDetail rating={selected} onClose={() => setSelected(null)} />}
    </main>
  );
}

function QuadrantMap({ ratings, focused, onFocus }: { ratings: V2Rating[]; focused: V2Rating | null; onFocus: (rating: V2Rating) => void }) {
  const featured = focused || ratings[0];
  const qualityLineY = 100 - scaledPct(QUALITY_THRESHOLD);
  const valueLineX = scaledPct(VALUATION_THRESHOLD);
  return (
    <section className="quadrant-section">
      <div className="quadrant-heading">
        <div>
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

      <div
        className="quadrant-map"
        aria-label="公司质量与估值吸引力四象限图"
        style={{ "--quality-line-y": `${qualityLineY}%`, "--value-line-x": `${valueLineX}%` } as CSSProperties}
      >
        <div className="quadrant-label top-left">
          <strong>好公司，等价格</strong>
          <span>CQS ≥ {QUALITY_THRESHOLD} / VAS &lt; {VALUATION_THRESHOLD}</span>
        </div>
        <div className="quadrant-label top-right">
          <strong>好公司、好价格</strong>
          <span>CQS ≥ {QUALITY_THRESHOLD} / VAS ≥ {VALUATION_THRESHOLD}</span>
        </div>
        <div className="quadrant-label bottom-left">
          <strong>质量和价格都不占优</strong>
          <span>CQS &lt; {QUALITY_THRESHOLD} / VAS &lt; {VALUATION_THRESHOLD}</span>
        </div>
        <div className="quadrant-label bottom-right">
          <strong>价格诱人</strong>
          <span>CQS &lt; {QUALITY_THRESHOLD} / VAS ≥ {VALUATION_THRESHOLD}</span>
        </div>
        <div className="quadrant-axis x-axis">估值吸引力 VAS</div>
        <div className="quadrant-axis y-axis">公司质量 CQS</div>
        <div className="quadrant-midline vertical" />
        <div className="quadrant-midline horizontal" />

        {ratings.map((rating) => {
          const x = scaledPct(rating.valuation_score);
          const y = 100 - scaledPct(rating.quality_score);
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
              <span>{companyInitial(rating.name)}</span>
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

function RatingRow({ rating, onOpen }: { rating: V2Rating; onOpen: (rating: V2Rating) => void }) {
  return (
    <div className="v2-rating-row">
      <span>{rating.list_rank}</span>
      <span>
        <button className="text-link strong" onClick={() => onOpen(rating)}>{rating.name}</button>
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

function SortButton({ label, column, sort, onSort }: { label: string; column: SortKey; sort: { key: SortKey; direction: SortDirection } | null; onSort: (key: SortKey) => void }) {
  const active = sort?.key === column;
  return (
    <button className={`sort-header ${active ? "active" : ""}`} onClick={() => onSort(column)}>
      <span>{label}</span>
      <b>{active ? (sort.direction === "desc" ? "↓" : "↑") : "↕"}</b>
    </button>
  );
}

function RatingDetail({ rating, onClose }: { rating: V2Rating; onClose: () => void }) {
  const detail = rating.scorecard_detail || {};
  return (
    <div className="rating-detail-backdrop" onClick={onClose}>
      <aside className="rating-detail" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={`${rating.name}评级详情`}>
        <button className="detail-close" onClick={onClose} aria-label="关闭">×</button>
        <header>
          <span>{marketName(rating.market)} · {codeLabel(rating)}</span>
          <h2>{rating.name}</h2>
          <p>{rating.theme} · {rating.industry || rating.exchange}</p>
        </header>
        <div className="detail-score-grid">
          <Stat label="质量 CQS" value={Math.round(rating.quality_score)} />
          <Stat label="估值 VAS" value={Math.round(rating.valuation_score)} />
          <Stat label="行动 IAS" value={Math.round(rating.action_score)} />
          <Stat label="数据 DQS" value={Math.round(rating.data_quality_score || 0)} />
        </div>
        <section>
          <h3>{rating.final_rating} · {rating.final_action}</h3>
          <p>{detail.summary_text || "暂无评分摘要。"}</p>
          <small>{detail.scoring_version || rating.rating_version} · 置信度 {formatPercent(detail.confidence)}</small>
        </section>
        <DetailBuckets title="公司质量" buckets={detail.bucket_scores} />
        <DetailBuckets title="估值吸引力" buckets={detail.valuation_bucket_scores} />
        <DetailBuckets title="数据可信度" buckets={detail.data_quality_bucket_scores} />
        <DetailList title="行动规则" items={detail.action_rules || []} />
        <DetailList title="数据缺口" items={detail.missing_metrics || []} />
        <DetailFlags flags={detail.red_flags || []} />
      </aside>
    </div>
  );
}

function DetailBuckets({ title, buckets }: { title: string; buckets?: Record<string, number> }) {
  const rows = Object.entries(buckets || {});
  if (!rows.length) return null;
  return (
    <section>
      <h3>{title}</h3>
      <div className="detail-buckets">
        {rows.map(([key, value]) => (
          <div key={key}>
            <span>{localBucketName(key)}</span>
            <strong>{Math.round(Number(value))}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function DetailList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <section>
      <h3>{title}</h3>
      <ul className="detail-list">
        {items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}
      </ul>
    </section>
  );
}

function DetailFlags({ flags }: { flags: NonNullable<V2Rating["scorecard_detail"]>["red_flags"] }) {
  if (!flags?.length) return null;
  return (
    <section>
      <h3>风险红旗</h3>
      <ul className="detail-list">
        {flags.map((flag, index) => <li key={index}>{flag.title || flag.reason || flag.severity || "风险项"}</li>)}
      </ul>
    </section>
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

function scaledPct(value: number) {
  if (!Number.isFinite(value)) return 50;
  const pct = ((value - AXIS_MIN) / (AXIS_MAX - AXIS_MIN)) * 100;
  return Math.max(4, Math.min(96, pct));
}

function companyInitial(name: string) {
  return Array.from(name.trim())[0] || "";
}

function sortRatings(ratings: V2Rating[], sort: { key: SortKey; direction: SortDirection } | null) {
  if (!sort) return ratings;
  const direction = sort.direction === "desc" ? -1 : 1;
  return [...ratings].sort((a, b) => {
    const aValue = sortValue(a, sort.key);
    const bValue = sortValue(b, sort.key);
    if (aValue === bValue) return a.list_rank - b.list_rank;
    return (aValue > bValue ? 1 : -1) * direction;
  });
}

function sortValue(rating: V2Rating, key: SortKey) {
  if (key === "final_rating") return RATING_ORDER[rating.final_rating] || 0;
  return Number(rating[key] || 0);
}

function findRating(ratings: V2Rating[], item: { rank?: number; list_rank?: number; ticker: string; name: string }) {
  return ratings.find((rating) => rating.list_rank === (item.rank || item.list_rank)) || ratings.find((rating) => rating.ticker === item.ticker && rating.name === item.name) || null;
}

function formatPercent(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${Math.round(number * 100)}%`;
}

function localBucketName(key: string) {
  const labels: Record<string, string> = {
    profitability: "盈利质量",
    growth_quality: "成长质量",
    moat: "护城河",
    management: "管理治理",
    risk: "风险约束",
    absolute_valuation: "绝对估值",
    relative_valuation: "相对估值",
    historical_valuation: "历史估值",
    margin_of_safety: "安全边际",
    quote_reliability: "行情可靠",
    financial_completeness: "财务完整",
    filing_coverage: "公告覆盖",
    event_coverage: "事件覆盖",
    peer_coverage: "同业覆盖",
    freshness: "新鲜度",
    cross_validation: "交叉验证",
  };
  return labels[key] || key;
}
