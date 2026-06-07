from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from .database import from_json, to_json


V2_VERSION = "ai-committee-v2.0-100"


V2_COMPANIES: list[dict[str, Any]] = [
    {"rank": 1, "market": "US", "ticker": "AAPL", "name": "苹果", "name_en": "Apple", "theme": "消费/品牌垄断", "industry": "消费电子", "exchange": "NASDAQ"},
    {"rank": 2, "market": "US", "ticker": "KO", "name": "可口可乐", "name_en": "Coca-Cola", "theme": "消费/品牌垄断", "industry": "饮料", "exchange": "NYSE"},
    {"rank": 3, "market": "US", "ticker": "PEP", "name": "百事可乐", "name_en": "PepsiCo", "theme": "消费/品牌垄断", "industry": "饮料/食品", "exchange": "NASDAQ"},
    {"rank": 4, "market": "US", "ticker": "PG", "name": "宝洁", "name_en": "Procter & Gamble", "theme": "消费/品牌垄断", "industry": "日化消费品", "exchange": "NYSE"},
    {"rank": 5, "market": "US", "ticker": "JNJ", "name": "强生", "name_en": "Johnson & Johnson", "theme": "消费/品牌垄断", "industry": "医疗健康", "exchange": "NYSE"},
    {"rank": 6, "market": "US", "ticker": "PM", "name": "菲利普莫里斯", "name_en": "Philip Morris International", "theme": "消费/品牌垄断", "industry": "烟草", "exchange": "NYSE"},
    {"rank": 7, "market": "US", "ticker": "KHC", "name": "卡夫亨氏", "name_en": "Kraft Heinz", "theme": "消费/品牌垄断", "industry": "包装食品", "exchange": "NASDAQ"},
    {"rank": 8, "market": "US", "ticker": "MCD", "name": "麦当劳", "name_en": "McDonald's", "theme": "消费/品牌垄断", "industry": "餐饮连锁", "exchange": "NYSE"},
    {"rank": 9, "market": "US", "ticker": "SBUX", "name": "星巴克", "name_en": "Starbucks", "theme": "消费/品牌垄断", "industry": "咖啡连锁", "exchange": "NASDAQ"},
    {"rank": 10, "market": "US", "ticker": "TMO", "name": "赛默飞", "name_en": "Thermo Fisher Scientific", "theme": "消费/品牌垄断", "industry": "科研服务", "exchange": "NYSE"},
    {"rank": 11, "market": "US", "ticker": "V", "name": "Visa", "name_en": "Visa", "theme": "金融/支付", "industry": "支付网络", "exchange": "NYSE"},
    {"rank": 12, "market": "US", "ticker": "MA", "name": "万事达", "name_en": "Mastercard", "theme": "金融/支付", "industry": "支付网络", "exchange": "NYSE"},
    {"rank": 13, "market": "US", "ticker": "AXP", "name": "美国运通", "name_en": "American Express", "theme": "金融/支付", "industry": "信用卡/支付", "exchange": "NYSE"},
    {"rank": 14, "market": "US", "ticker": "JPM", "name": "摩根大通", "name_en": "JPMorgan Chase", "theme": "金融/支付", "industry": "银行", "exchange": "NYSE"},
    {"rank": 15, "market": "US", "ticker": "BAC", "name": "美国银行", "name_en": "Bank of America", "theme": "金融/支付", "industry": "银行", "exchange": "NYSE"},
    {"rank": 16, "market": "US", "ticker": "WFC", "name": "富国银行", "name_en": "Wells Fargo", "theme": "金融/支付", "industry": "银行", "exchange": "NYSE"},
    {"rank": 17, "market": "US", "ticker": "BRK.B", "name": "伯克希尔哈撒韦", "name_en": "Berkshire Hathaway", "theme": "金融/支付", "industry": "保险/多元控股", "exchange": "NYSE"},
    {"rank": 18, "market": "US", "ticker": "MS", "name": "摩根士丹利", "name_en": "Morgan Stanley", "theme": "金融/支付", "industry": "投行/财富管理", "exchange": "NYSE"},
    {"rank": 19, "market": "US", "ticker": "MSFT", "name": "微软", "name_en": "Microsoft", "theme": "科技/硬护城河", "industry": "软件/云计算", "exchange": "NASDAQ"},
    {"rank": 20, "market": "US", "ticker": "GOOGL", "name": "谷歌", "name_en": "Alphabet", "theme": "科技/硬护城河", "industry": "互联网/云计算", "exchange": "NASDAQ"},
    {"rank": 21, "market": "US", "ticker": "AMZN", "name": "亚马逊", "name_en": "Amazon", "theme": "科技/硬护城河", "industry": "电商/云计算", "exchange": "NASDAQ"},
    {"rank": 22, "market": "US", "ticker": "NVDA", "name": "英伟达", "name_en": "NVIDIA", "theme": "科技/硬护城河", "industry": "AI算力/半导体", "exchange": "NASDAQ"},
    {"rank": 23, "market": "US", "ticker": "ORCL", "name": "甲骨文", "name_en": "Oracle", "theme": "科技/硬护城河", "industry": "企业软件/云", "exchange": "NYSE"},
    {"rank": 24, "market": "US", "ticker": "IBM", "name": "IBM", "name_en": "IBM", "theme": "科技/硬护城河", "industry": "企业 IT/AI", "exchange": "NYSE"},
    {"rank": 25, "market": "US", "ticker": "INTC", "name": "英特尔", "name_en": "Intel", "theme": "科技/硬护城河", "industry": "半导体", "exchange": "NASDAQ"},
    {"rank": 26, "market": "US", "ticker": "QCOM", "name": "高通", "name_en": "Qualcomm", "theme": "科技/硬护城河", "industry": "通信芯片", "exchange": "NASDAQ"},
    {"rank": 27, "market": "US", "ticker": "ADBE", "name": "Adobe", "name_en": "Adobe", "theme": "科技/硬护城河", "industry": "创意软件", "exchange": "NASDAQ"},
    {"rank": 28, "market": "US", "ticker": "CRM", "name": "Salesforce", "name_en": "Salesforce", "theme": "科技/硬护城河", "industry": "SaaS", "exchange": "NYSE"},
    {"rank": 29, "market": "US", "ticker": "XOM", "name": "埃克森美孚", "name_en": "Exxon Mobil", "theme": "能源/资源/公用事业", "industry": "综合能源", "exchange": "NYSE"},
    {"rank": 30, "market": "US", "ticker": "CVX", "name": "雪佛龙", "name_en": "Chevron", "theme": "能源/资源/公用事业", "industry": "综合能源", "exchange": "NYSE"},
    {"rank": 31, "market": "US", "ticker": "OXY", "name": "西方石油", "name_en": "Occidental Petroleum", "theme": "能源/资源/公用事业", "industry": "油气", "exchange": "NYSE"},
    {"rank": 32, "market": "US", "ticker": "COP", "name": "康菲石油", "name_en": "ConocoPhillips", "theme": "能源/资源/公用事业", "industry": "油气", "exchange": "NYSE"},
    {"rank": 33, "market": "US", "ticker": "DUK", "name": "杜克能源", "name_en": "Duke Energy", "theme": "能源/资源/公用事业", "industry": "公用事业", "exchange": "NYSE"},
    {"rank": 34, "market": "US", "ticker": "SO", "name": "南方电力", "name_en": "Southern Company", "theme": "能源/资源/公用事业", "industry": "公用事业", "exchange": "NYSE"},
    {"rank": 35, "market": "US", "ticker": "RIO", "name": "力拓", "name_en": "Rio Tinto", "theme": "能源/资源/公用事业", "industry": "矿业资源", "exchange": "NYSE"},
    {"rank": 36, "market": "US", "ticker": "PFE", "name": "辉瑞", "name_en": "Pfizer", "theme": "医药/生物", "industry": "制药", "exchange": "NYSE"},
    {"rank": 37, "market": "US", "ticker": "ABT", "name": "雅培", "name_en": "Abbott Laboratories", "theme": "医药/生物", "industry": "医疗器械/诊断", "exchange": "NYSE"},
    {"rank": 38, "market": "US", "ticker": "AMGN", "name": "安进", "name_en": "Amgen", "theme": "医药/生物", "industry": "生物制药", "exchange": "NASDAQ"},
    {"rank": 39, "market": "US", "ticker": "GILD", "name": "吉利德", "name_en": "Gilead Sciences", "theme": "医药/生物", "industry": "生物制药", "exchange": "NASDAQ"},
    {"rank": 40, "market": "US", "ticker": "REGN", "name": "再生元", "name_en": "Regeneron", "theme": "医药/生物", "industry": "生物制药", "exchange": "NASDAQ"},
    {"rank": 41, "market": "A", "ticker": "600519", "name": "贵州茅台", "name_en": "Kweichow Moutai", "theme": "白酒/消费", "industry": "白酒", "exchange": "SSE"},
    {"rank": 42, "market": "A", "ticker": "000858", "name": "五粮液", "name_en": "Wuliangye", "theme": "白酒/消费", "industry": "白酒", "exchange": "SZSE"},
    {"rank": 43, "market": "A", "ticker": "002304", "name": "洋河股份", "name_en": "Yanghe Brewery", "theme": "白酒/消费", "industry": "白酒", "exchange": "SZSE"},
    {"rank": 44, "market": "A", "ticker": "600809", "name": "山西汾酒", "name_en": "Shanxi Fen Wine", "theme": "白酒/消费", "industry": "白酒", "exchange": "SSE"},
    {"rank": 45, "market": "A", "ticker": "603369", "name": "今世缘", "name_en": "King's Luck Brewery", "theme": "白酒/消费", "industry": "白酒", "exchange": "SSE"},
    {"rank": 46, "market": "A", "ticker": "600276", "name": "恒瑞医药", "name_en": "Hengrui Pharmaceuticals", "theme": "白酒/消费", "industry": "创新药", "exchange": "SSE"},
    {"rank": 47, "market": "A", "ticker": "600887", "name": "伊利股份", "name_en": "Yili", "theme": "白酒/消费", "industry": "乳制品", "exchange": "SSE"},
    {"rank": 48, "market": "A", "ticker": "603288", "name": "海天味业", "name_en": "Haitian Flavouring", "theme": "白酒/消费", "industry": "调味品", "exchange": "SSE"},
    {"rank": 49, "market": "A", "ticker": "600036", "name": "招商银行", "name_en": "China Merchants Bank", "theme": "金融", "industry": "银行", "exchange": "SSE"},
    {"rank": 50, "market": "A", "ticker": "601318", "name": "中国平安", "name_en": "Ping An Insurance", "theme": "金融", "industry": "保险/金融", "exchange": "SSE"},
    {"rank": 51, "market": "A", "ticker": "601166", "name": "兴业银行", "name_en": "Industrial Bank", "theme": "金融", "industry": "银行", "exchange": "SSE"},
    {"rank": 52, "market": "A", "ticker": "600000", "name": "浦发银行", "name_en": "SPDB", "theme": "金融", "industry": "银行", "exchange": "SSE"},
    {"rank": 53, "market": "A", "ticker": "601688", "name": "华泰证券", "name_en": "Huatai Securities", "theme": "金融", "industry": "证券", "exchange": "SSE"},
    {"rank": 54, "market": "A", "ticker": "000776", "name": "广发证券", "name_en": "GF Securities", "theme": "金融", "industry": "证券", "exchange": "SZSE"},
    {"rank": 55, "market": "A", "ticker": "600999", "name": "招商证券", "name_en": "China Merchants Securities", "theme": "金融", "industry": "证券", "exchange": "SSE"},
    {"rank": 56, "market": "A", "ticker": "600900", "name": "长江电力", "name_en": "China Yangtze Power", "theme": "电力/能源/资源", "industry": "水电", "exchange": "SSE"},
    {"rank": 57, "market": "A", "ticker": "601088", "name": "中国神华", "name_en": "China Shenhua", "theme": "电力/能源/资源", "industry": "煤炭/电力", "exchange": "SSE"},
    {"rank": 58, "market": "A", "ticker": "600938", "name": "中国海油", "name_en": "CNOOC", "theme": "电力/能源/资源", "industry": "油气", "exchange": "SSE"},
    {"rank": 59, "market": "A", "ticker": "601899", "name": "紫金矿业", "name_en": "Zijin Mining", "theme": "电力/能源/资源", "industry": "有色金属", "exchange": "SSE"},
    {"rank": 60, "market": "A", "ticker": "600585", "name": "海螺水泥", "name_en": "Anhui Conch Cement", "theme": "电力/能源/资源", "industry": "水泥", "exchange": "SSE"},
    {"rank": 61, "market": "A", "ticker": "600019", "name": "宝钢股份", "name_en": "Baoshan Iron & Steel", "theme": "电力/能源/资源", "industry": "钢铁", "exchange": "SSE"},
    {"rank": 62, "market": "A", "ticker": "300750", "name": "宁德时代", "name_en": "CATL", "theme": "高端制造/科技", "industry": "动力电池", "exchange": "SZSE"},
    {"rank": 63, "market": "A", "ticker": "002594", "name": "比亚迪", "name_en": "BYD", "theme": "高端制造/科技", "industry": "新能源汽车", "exchange": "SZSE"},
    {"rank": 64, "market": "A", "ticker": "600660", "name": "福耀玻璃", "name_en": "Fuyao Glass", "theme": "高端制造/科技", "industry": "汽车玻璃", "exchange": "SSE"},
    {"rank": 65, "market": "A", "ticker": "300124", "name": "汇川技术", "name_en": "Inovance", "theme": "高端制造/科技", "industry": "工业自动化", "exchange": "SZSE"},
    {"rank": 66, "market": "A", "ticker": "603501", "name": "韦尔股份", "name_en": "Will Semiconductor", "theme": "高端制造/科技", "industry": "半导体", "exchange": "SSE"},
    {"rank": 67, "market": "A", "ticker": "002415", "name": "海康威视", "name_en": "Hikvision", "theme": "高端制造/科技", "industry": "安防/AIoT", "exchange": "SZSE"},
    {"rank": 68, "market": "A", "ticker": "002230", "name": "科大讯飞", "name_en": "iFlytek", "theme": "高端制造/科技", "industry": "AI软件", "exchange": "SZSE"},
    {"rank": 69, "market": "A", "ticker": "688012", "name": "中微公司", "name_en": "AMEC", "theme": "高端制造/科技", "industry": "半导体设备", "exchange": "SSE STAR"},
    {"rank": 70, "market": "A", "ticker": "300015", "name": "爱尔眼科", "name_en": "Aier Eye Hospital", "theme": "医药/医疗服务", "industry": "医疗服务", "exchange": "SZSE"},
    {"rank": 71, "market": "A", "ticker": "600436", "name": "片仔癀", "name_en": "Pien Tze Huang", "theme": "医药/医疗服务", "industry": "中药", "exchange": "SSE"},
    {"rank": 72, "market": "A", "ticker": "600196", "name": "复星医药", "name_en": "Fosun Pharma", "theme": "医药/医疗服务", "industry": "医药", "exchange": "SSE"},
    {"rank": 73, "market": "A", "ticker": "002223", "name": "鱼跃医疗", "name_en": "Yuwell", "theme": "医药/医疗服务", "industry": "医疗器械", "exchange": "SZSE"},
    {"rank": 74, "market": "A", "ticker": "002027", "name": "分众传媒", "name_en": "Focus Media", "theme": "互联网/平台", "industry": "广告传媒", "exchange": "SZSE"},
    {"rank": 75, "market": "A", "ticker": "300413", "name": "芒果超媒", "name_en": "Mango Excellent Media", "theme": "互联网/平台", "industry": "内容平台", "exchange": "SZSE"},
    {"rank": 76, "market": "HK", "ticker": "0700.HK", "name": "腾讯控股", "name_en": "Tencent", "theme": "互联网平台", "industry": "互联网平台", "exchange": "HKEX"},
    {"rank": 77, "market": "HK", "ticker": "9988.HK", "name": "阿里巴巴-W", "name_en": "Alibaba", "theme": "互联网平台", "industry": "电商/云计算", "exchange": "HKEX"},
    {"rank": 78, "market": "HK", "ticker": "3690.HK", "name": "美团-W", "name_en": "Meituan", "theme": "互联网平台", "industry": "本地生活", "exchange": "HKEX"},
    {"rank": 79, "market": "HK", "ticker": "9961.HK", "name": "拼多多", "name_en": "PDD Holdings", "theme": "互联网平台", "industry": "电商平台", "exchange": "HKEX"},
    {"rank": 80, "market": "HK", "ticker": "1810.HK", "name": "小米集团", "name_en": "Xiaomi", "theme": "互联网平台", "industry": "消费电子/汽车", "exchange": "HKEX"},
    {"rank": 81, "market": "HK", "ticker": "9888.HK", "name": "知乎-W", "name_en": "Zhihu", "theme": "互联网平台", "industry": "内容社区", "exchange": "HKEX"},
    {"rank": 82, "market": "HK", "ticker": "2318.HK", "name": "中国平安", "name_en": "Ping An Insurance", "theme": "金融/交易所", "industry": "保险/金融", "exchange": "HKEX"},
    {"rank": 83, "market": "HK", "ticker": "3968.HK", "name": "招商银行", "name_en": "China Merchants Bank", "theme": "金融/交易所", "industry": "银行", "exchange": "HKEX"},
    {"rank": 84, "market": "HK", "ticker": "0388.HK", "name": "香港交易所", "name_en": "Hong Kong Exchanges and Clearing", "theme": "金融/交易所", "industry": "交易所", "exchange": "HKEX"},
    {"rank": 85, "market": "HK", "ticker": "1299.HK", "name": "友邦保险", "name_en": "AIA", "theme": "金融/交易所", "industry": "保险", "exchange": "HKEX"},
    {"rank": 86, "market": "HK", "ticker": "0005.HK", "name": "汇丰控股", "name_en": "HSBC", "theme": "金融/交易所", "industry": "银行", "exchange": "HKEX"},
    {"rank": 87, "market": "HK", "ticker": "2020.HK", "name": "安踏体育", "name_en": "ANTA Sports", "theme": "消费/品牌", "industry": "运动品牌", "exchange": "HKEX"},
    {"rank": 88, "market": "HK", "ticker": "2331.HK", "name": "李宁", "name_en": "Li Ning", "theme": "消费/品牌", "industry": "运动品牌", "exchange": "HKEX"},
    {"rank": 89, "market": "HK", "ticker": "0291.HK", "name": "华润啤酒", "name_en": "China Resources Beer", "theme": "消费/品牌", "industry": "啤酒", "exchange": "HKEX"},
    {"rank": 90, "market": "HK", "ticker": "6862.HK", "name": "海底捞", "name_en": "Haidilao", "theme": "消费/品牌", "industry": "餐饮连锁", "exchange": "HKEX"},
    {"rank": 91, "market": "HK", "ticker": "1579.HK", "name": "颐海国际", "name_en": "Yihai International", "theme": "消费/品牌", "industry": "复合调味料", "exchange": "HKEX"},
    {"rank": 92, "market": "HK", "ticker": "0883.HK", "name": "中国海油", "name_en": "CNOOC", "theme": "能源/公用事业/电信", "industry": "油气", "exchange": "HKEX"},
    {"rank": 93, "market": "HK", "ticker": "0941.HK", "name": "中国移动", "name_en": "China Mobile", "theme": "能源/公用事业/电信", "industry": "通信运营商", "exchange": "HKEX"},
    {"rank": 94, "market": "HK", "ticker": "0002.HK", "name": "中电控股", "name_en": "CLP Holdings", "theme": "能源/公用事业/电信", "industry": "公用事业", "exchange": "HKEX"},
    {"rank": 95, "market": "HK", "ticker": "0836.HK", "name": "华润电力", "name_en": "China Resources Power", "theme": "能源/公用事业/电信", "industry": "电力", "exchange": "HKEX"},
    {"rank": 96, "market": "HK", "ticker": "1816.HK", "name": "中广核电力", "name_en": "CGN Power", "theme": "能源/公用事业/电信", "industry": "核电", "exchange": "HKEX"},
    {"rank": 97, "market": "HK", "ticker": "2269.HK", "name": "药明生物", "name_en": "WuXi Biologics", "theme": "医药/先进制造", "industry": "CXO/生物药", "exchange": "HKEX"},
    {"rank": 98, "market": "HK", "ticker": "6160.HK", "name": "百济神州", "name_en": "BeiGene", "theme": "医药/先进制造", "industry": "创新药", "exchange": "HKEX"},
    {"rank": 99, "market": "HK", "ticker": "0960.HK", "name": "龙湖集团", "name_en": "Longfor Group", "theme": "医药/先进制造", "industry": "地产现金流", "exchange": "HKEX"},
    {"rank": 100, "market": "HK", "ticker": "0656.HK", "name": "复星国际", "name_en": "Fosun International", "theme": "医药/先进制造", "industry": "综合产业", "exchange": "HKEX"},
]


QUALITY_PRESETS = {
    "支付网络": 91,
    "软件/云计算": 90,
    "互联网/云计算": 88,
    "AI算力/半导体": 88,
    "白酒": 87,
    "饮料": 86,
    "交易所": 86,
    "保险/多元控股": 85,
    "公用事业": 78,
    "银行": 76,
    "油气": 74,
    "房地产": 55,
}


def ensure_v2_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS v2_company_ratings (
          list_rank INTEGER PRIMARY KEY,
          company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
          market TEXT NOT NULL,
          ticker TEXT NOT NULL,
          name TEXT NOT NULL,
          theme TEXT NOT NULL,
          moat_score REAL,
          quality_score REAL,
          valuation_score REAL,
          action_score REAL,
          final_rating TEXT,
          final_action TEXT,
          rating_json TEXT NOT NULL,
          rating_version TEXT NOT NULL,
          rated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_v2_company_ratings_market ON v2_company_ratings(market);
        CREATE INDEX IF NOT EXISTS idx_v2_company_ratings_company ON v2_company_ratings(company_id);
        """
    )


def rebuild_v2_ratings(conn) -> dict[str, Any]:
    ensure_v2_schema(conn)
    rows = []
    for item in V2_COMPANIES:
        company_id = upsert_v2_company(conn, item)
        rating = build_v2_rating(conn, item, company_id)
        conn.execute(
            """
            INSERT INTO v2_company_ratings (
              list_rank, company_id, market, ticker, name, theme,
              moat_score, quality_score, valuation_score, action_score,
              final_rating, final_action, rating_json, rating_version, rated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(list_rank) DO UPDATE SET
              company_id = excluded.company_id,
              market = excluded.market,
              ticker = excluded.ticker,
              name = excluded.name,
              theme = excluded.theme,
              moat_score = excluded.moat_score,
              quality_score = excluded.quality_score,
              valuation_score = excluded.valuation_score,
              action_score = excluded.action_score,
              final_rating = excluded.final_rating,
              final_action = excluded.final_action,
              rating_json = excluded.rating_json,
              rating_version = excluded.rating_version,
              rated_at = CURRENT_TIMESTAMP
            """,
            (
                item["rank"],
                company_id,
                item["market"],
                item["ticker"],
                item["name"],
                item["theme"],
                rating["moat_score"],
                rating["quality_score"],
                rating["valuation_score"],
                rating["action_score"],
                rating["final_rating"],
                rating["final_action"],
                to_json(rating),
                V2_VERSION,
            ),
        )
        rows.append(rating)
    return v2_summary(rows)


def get_v2_ratings(conn, market: str = "AUTO", q: str = "") -> dict[str, Any]:
    ensure_v2_schema(conn)
    count = conn.execute("SELECT COUNT(*) AS count FROM v2_company_ratings").fetchone()["count"]
    if count != len(V2_COMPANIES):
        rebuild_v2_ratings(conn)
    where = []
    params: list[Any] = []
    if market and market != "AUTO":
        where.append("market = ?")
        params.append(market.upper())
    query = (q or "").strip().lower()
    if query:
        where.append("(LOWER(ticker) LIKE ? OR LOWER(name) LIKE ? OR LOWER(theme) LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"SELECT * FROM v2_company_ratings {where_sql} ORDER BY list_rank",
        params,
    ).fetchall()
    ratings = [v2_rating_row(row) for row in rows]
    return {
        "version": V2_VERSION,
        "as_of": date.today().isoformat(),
        "total": len(ratings),
        "expected_total": len(V2_COMPANIES),
        "summary": v2_summary(ratings),
        "ratings": ratings,
    }


def upsert_v2_company(conn, item: dict[str, Any]) -> str:
    existing = conn.execute(
        "SELECT id FROM companies WHERE market = ? AND UPPER(ticker) = UPPER(?) LIMIT 1",
        (item["market"], item["ticker"]),
    ).fetchone()
    company_id = existing["id"] if existing else company_id_for(item)
    aliases = aliases_for(item)
    tags = [market_label(item["market"]), item["theme"], item["industry"], "2.0重点100"]
    conn.execute(
        """
        INSERT INTO companies (id, name, name_en, ticker, market, exchange, industry, sector, description, tags, aliases)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          name_en = excluded.name_en,
          ticker = excluded.ticker,
          market = excluded.market,
          exchange = excluded.exchange,
          industry = excluded.industry,
          sector = excluded.sector,
          description = excluded.description,
          tags = excluded.tags,
          aliases = excluded.aliases,
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            company_id,
            item["name"],
            item["name_en"],
            item["ticker"],
            item["market"],
            item["exchange"],
            item["industry"],
            item["theme"],
            f"{item['name']} 是 AI投委会 2.0 重点100公司第 {item['rank']} 位，归属 {item['theme']}。",
            to_json(tags),
            to_json(aliases),
        ),
    )
    return company_id


def build_v2_rating(conn, item: dict[str, Any], company_id: str) -> dict[str, Any]:
    snapshot = latest_snapshot(conn, company_id)
    scorecard = latest_scorecard(conn, company_id)
    base_quality = preset_quality(item)
    scorecard_quality = numeric(scorecard.get("company_quality_score")) if scorecard else None
    scorecard_valuation = numeric(scorecard.get("valuation_attractiveness_score")) if scorecard else None
    dqs = numeric(scorecard.get("data_quality_score")) if scorecard else None
    pe = numeric(snapshot.get("pe_ratio")) if snapshot else None
    roe = numeric(snapshot.get("roe")) if snapshot else None
    moat = moat_score(item)
    quality = blend(base_quality, scorecard_quality, 0.65)
    if roe is not None:
        quality = clamp(quality + min(8, max(-8, (roe - 12) * 0.35)))
    valuation = scorecard_valuation if scorecard_valuation is not None else valuation_from_pe(item, pe)
    action = clamp(quality * 0.48 + valuation * 0.32 + moat * 0.15 + (dqs if dqs is not None else 68) * 0.05)
    final_action = action_label(action, quality, valuation)
    final_rating = rating_label(action)
    return {
        "list_rank": item["rank"],
        "company_id": company_id,
        "market": item["market"],
        "ticker": item["ticker"],
        "name": item["name"],
        "name_en": item["name_en"],
        "theme": item["theme"],
        "industry": item["industry"],
        "exchange": item["exchange"],
        "moat_score": round(moat, 1),
        "quality_score": round(quality, 1),
        "valuation_score": round(valuation, 1),
        "action_score": round(action, 1),
        "data_quality_score": round(dqs if dqs is not None else 68, 1),
        "final_rating": final_rating,
        "final_action": final_action,
        "rating_version": V2_VERSION,
        "rating_basis": {
            "mode": "baseline_plus_live_evidence",
            "snapshot_used": bool(snapshot),
            "aics_scorecard_used": bool(scorecard),
            "pe_ratio": pe,
            "roe": roe,
        },
    }


def v2_summary(ratings: list[dict[str, Any]]) -> dict[str, Any]:
    by_market: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for rating in ratings:
        by_market[rating["market"]] = by_market.get(rating["market"], 0) + 1
        by_action[rating["final_action"]] = by_action.get(rating["final_action"], 0) + 1
    top = sorted(ratings, key=lambda item: item.get("action_score") or 0, reverse=True)[:10]
    return {
        "total": len(ratings),
        "by_market": by_market,
        "by_action": by_action,
        "top10": [{"rank": item["list_rank"], "ticker": item["ticker"], "name": item["name"], "action_score": item["action_score"], "final_action": item["final_action"]} for item in top],
    }


def latest_snapshot(conn, company_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM company_snapshots WHERE company_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return dict(row) if row else {}


def latest_scorecard(conn, company_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT scorecard_json FROM scorecards WHERE company_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return from_json(row["scorecard_json"], {}) if row else {}


def v2_rating_row(row: Any) -> dict[str, Any]:
    rating = from_json(row["rating_json"], {}) or {}
    rating.update(
        {
            "list_rank": row["list_rank"],
            "company_id": row["company_id"],
            "market": row["market"],
            "ticker": row["ticker"],
            "name": row["name"],
            "theme": row["theme"],
            "moat_score": row["moat_score"],
            "quality_score": row["quality_score"],
            "valuation_score": row["valuation_score"],
            "action_score": row["action_score"],
            "final_rating": row["final_rating"],
            "final_action": row["final_action"],
            "rating_version": row["rating_version"],
            "rated_at": row["rated_at"],
        }
    )
    return rating


def preset_quality(item: dict[str, Any]) -> float:
    industry = item["industry"]
    for key, score in QUALITY_PRESETS.items():
        if key in industry:
            return float(score)
    if any(key in item["theme"] for key in ["消费", "品牌", "科技", "医药"]):
        return 80.0
    if any(key in item["theme"] for key in ["能源", "资源", "电力"]):
        return 73.0
    return 76.0


def moat_score(item: dict[str, Any]) -> float:
    text = f"{item['theme']} {item['industry']}"
    score = 72.0
    for key, delta in {
        "品牌": 10,
        "支付": 14,
        "软件": 12,
        "云计算": 10,
        "白酒": 12,
        "交易所": 12,
        "公用事业": 8,
        "银行": 4,
        "半导体": 7,
        "互联网": 8,
        "医药": 5,
    }.items():
        if key in text:
            score += delta
    return clamp(score)


def valuation_from_pe(item: dict[str, Any], pe: float | None) -> float:
    if pe is None or pe <= 0:
        if any(key in item["industry"] for key in ["银行", "保险", "油气", "公用事业", "煤炭"]):
            return 70.0
        if any(key in item["industry"] for key in ["AI算力", "半导体", "创新药", "SaaS"]):
            return 58.0
        return 64.0
    if pe <= 8:
        return 84.0
    if pe <= 18:
        return 76.0
    if pe <= 30:
        return 66.0
    if pe <= 45:
        return 55.0
    return 42.0


def action_label(action: float, quality: float, valuation: float) -> str:
    if action >= 82 and quality >= 82 and valuation >= 65:
        return "核心买入"
    if action >= 74:
        return "买入"
    if action >= 66:
        return "重点观察"
    if action >= 56:
        return "等待更好价格"
    return "回避"


def rating_label(action: float) -> str:
    if action >= 85:
        return "S"
    if action >= 78:
        return "A"
    if action >= 68:
        return "B"
    if action >= 58:
        return "C"
    return "D"


def blend(base: float, live: float | None, base_weight: float) -> float:
    if live is None:
        return clamp(base)
    return clamp(base * base_weight + live * (1 - base_weight))


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, float(value)))


def numeric(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def company_id_for(item: dict[str, Any]) -> str:
    digest = hashlib.sha256(f"{item['market']}:{item['ticker']}".encode("utf-8")).hexdigest()[:16]
    return f"co_v2_{digest}"


def aliases_for(item: dict[str, Any]) -> list[str]:
    aliases = [item["ticker"], item["name"], item["name_en"]]
    if item["market"] == "A":
        suffix = ".SH" if item["exchange"].startswith("SSE") else ".SZ"
        aliases.append(f"{item['ticker']}{suffix}")
    if item["market"] == "HK" and item["ticker"].endswith(".HK"):
        code = item["ticker"].replace(".HK", "")
        aliases.extend([code, code.zfill(5), code.zfill(4)])
    return list(dict.fromkeys([alias for alias in aliases if alias]))


def market_label(market: str) -> str:
    return {"US": "美股", "A": "A股", "HK": "港股"}.get(market, market)
