from __future__ import annotations

import sqlite3
from uuid import uuid4

from .database import to_json


COMPANIES = [
    {
        "id": "co_moutai_a",
        "name": "贵州茅台",
        "name_en": "Kweichow Moutai",
        "ticker": "600519",
        "market": "A",
        "exchange": "SSE",
        "industry": "白酒",
        "sector": "高端消费",
        "description": "中国高端白酒龙头，核心资产是品牌、渠道、定价权与高现金流。",
        "tags": ["白酒", "高端消费", "品牌", "现金流", "高ROE", "A股"],
        "aliases": ["茅台", "600519.SH", "600519", "Kweichow Moutai"],
        "snapshot": {"price": 1688.0, "market_cap": 21200, "pe_ratio": 27.4, "pb_ratio": 9.1, "gross_margin": 91.8, "net_margin": 52.4, "roe": 31.2},
    },
    {
        "id": "co_tencent_hk",
        "name": "腾讯控股",
        "name_en": "Tencent Holdings",
        "ticker": "0700.HK",
        "market": "HK",
        "exchange": "HKEX",
        "industry": "互联网平台",
        "sector": "科技/线上娱乐",
        "description": "中国互联网平台龙头，游戏、社交、广告、金融科技和云业务构成多元利润池。",
        "tags": ["互联网", "游戏", "社交", "平台", "现金流", "港股"],
        "aliases": ["腾讯", "700.HK", "0700", "Tencent"],
        "snapshot": {"price": 392.4, "market_cap": 36500, "pe_ratio": 20.8, "pb_ratio": 3.9, "gross_margin": 49.6, "net_margin": 27.2, "roe": 19.4},
    },
    {
        "id": "co_alibaba_us",
        "name": "阿里巴巴",
        "name_en": "Alibaba Group",
        "ticker": "BABA",
        "market": "US",
        "exchange": "NYSE",
        "industry": "电商/云计算",
        "sector": "中概互联网",
        "description": "电商、云计算、本地生活与国际业务并行的中国平台公司。",
        "tags": ["电商", "云计算", "中概股", "平台", "估值修复", "美股"],
        "aliases": ["阿里", "Alibaba", "BABA", "9988.HK"],
        "snapshot": {"price": 84.2, "market_cap": 2030, "pe_ratio": 15.7, "pb_ratio": 1.8, "gross_margin": 38.1, "net_margin": 12.4, "roe": 10.9},
    },
    {
        "id": "co_alibaba_hk",
        "name": "阿里巴巴-W",
        "name_en": "Alibaba Group",
        "ticker": "9988.HK",
        "market": "HK",
        "exchange": "HKEX",
        "industry": "电商/云计算",
        "sector": "中概互联网",
        "description": "阿里巴巴港股二次上市证券，与美股 BABA 映射同一家公司。",
        "tags": ["电商", "云计算", "中概股", "平台", "港股"],
        "aliases": ["阿里", "阿里巴巴", "9988", "BABA"],
        "snapshot": {"price": 82.6, "market_cap": 15850, "pe_ratio": 15.5, "pb_ratio": 1.8, "gross_margin": 38.1, "net_margin": 12.4, "roe": 10.9},
    },
    {
        "id": "co_apple_us",
        "name": "苹果",
        "name_en": "Apple",
        "ticker": "AAPL",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "消费电子",
        "sector": "科技硬件",
        "description": "全球消费电子与服务生态龙头，核心变量是硬件周期、服务收入和生态粘性。",
        "tags": ["消费电子", "品牌", "生态", "现金流", "美股"],
        "aliases": ["Apple", "AAPL", "苹果公司"],
        "snapshot": {"price": 191.5, "market_cap": 29500, "pe_ratio": 29.8, "pb_ratio": 39.0, "gross_margin": 46.1, "net_margin": 25.3, "roe": 147.2},
    },
    {
        "id": "co_nvidia_us",
        "name": "英伟达",
        "name_en": "NVIDIA",
        "ticker": "NVDA",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "半导体",
        "sector": "AI算力",
        "description": "GPU 与 AI 数据中心平台龙头，受益于算力需求、软件生态和供应链执行。",
        "tags": ["半导体", "AI算力", "GPU", "数据中心", "高增长", "高估值", "美股"],
        "aliases": ["NVIDIA", "NVDA", "英伟达"],
        "snapshot": {"price": 910.2, "market_cap": 22400, "pe_ratio": 64.3, "pb_ratio": 51.2, "gross_margin": 74.9, "net_margin": 48.8, "roe": 93.4},
    },
    {
        "id": "co_tesla_us",
        "name": "特斯拉",
        "name_en": "Tesla",
        "ticker": "TSLA",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "新能源汽车",
        "sector": "汽车/能源",
        "description": "电动车、储能、软件与自动驾驶叙事并存的全球成长股。",
        "tags": ["新能源汽车", "自动驾驶", "高波动", "成长股", "美股"],
        "aliases": ["Tesla", "TSLA", "特斯拉"],
        "snapshot": {"price": 182.4, "market_cap": 5800, "pe_ratio": 58.2, "pb_ratio": 9.7, "gross_margin": 18.2, "net_margin": 8.3, "roe": 18.1},
    },
    {
        "id": "co_byd_a",
        "name": "比亚迪",
        "name_en": "BYD",
        "ticker": "002594",
        "market": "A",
        "exchange": "SZSE",
        "industry": "新能源汽车",
        "sector": "汽车/电池",
        "description": "新能源汽车与动力电池一体化龙头，规模、成本和产品周期是关键变量。",
        "tags": ["新能源汽车", "电池", "垂直整合", "A股", "港股映射"],
        "aliases": ["BYD", "002594.SZ", "1211.HK", "比亚迪股份"],
        "snapshot": {"price": 253.8, "market_cap": 7400, "pe_ratio": 22.5, "pb_ratio": 4.7, "gross_margin": 21.9, "net_margin": 5.2, "roe": 21.7},
    },
    {
        "id": "co_byd_hk",
        "name": "比亚迪股份",
        "name_en": "BYD",
        "ticker": "1211.HK",
        "market": "HK",
        "exchange": "HKEX",
        "industry": "新能源汽车",
        "sector": "汽车/电池",
        "description": "比亚迪港股证券，与 A 股 002594 映射同一家公司。",
        "tags": ["新能源汽车", "电池", "垂直整合", "港股"],
        "aliases": ["BYD", "1211", "002594", "比亚迪"],
        "snapshot": {"price": 242.2, "market_cap": 7050, "pe_ratio": 21.8, "pb_ratio": 4.4, "gross_margin": 21.9, "net_margin": 5.2, "roe": 21.7},
    },
    {
        "id": "co_jd_us",
        "name": "京东",
        "name_en": "JD.com",
        "ticker": "JD",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "电商/物流",
        "sector": "中概互联网",
        "description": "自营电商和供应链物流能力驱动的中概平台公司。",
        "tags": ["电商", "物流", "中概股", "低估值", "美股"],
        "aliases": ["JD", "京东", "9618.HK", "JD.com"],
        "snapshot": {"price": 28.7, "market_cap": 430, "pe_ratio": 10.4, "pb_ratio": 1.3, "gross_margin": 15.9, "net_margin": 3.1, "roe": 12.0},
    },
    {
        "id": "co_pdd_us",
        "name": "拼多多",
        "name_en": "PDD Holdings",
        "ticker": "PDD",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "电商",
        "sector": "中概互联网",
        "description": "国内电商和跨境平台 Temu 驱动的高增长中概公司。",
        "tags": ["电商", "跨境", "高增长", "中概股", "美股"],
        "aliases": ["PDD", "拼多多", "Temu"],
        "snapshot": {"price": 138.6, "market_cap": 1900, "pe_ratio": 19.6, "pb_ratio": 7.4, "gross_margin": 62.5, "net_margin": 26.1, "roe": 43.7},
    },
    {
        "id": "co_meituan_hk",
        "name": "美团",
        "name_en": "Meituan",
        "ticker": "3690.HK",
        "market": "HK",
        "exchange": "HKEX",
        "industry": "本地生活",
        "sector": "互联网平台",
        "description": "本地生活、外卖、到店酒旅和新业务构成的互联网平台。",
        "tags": ["本地生活", "外卖", "互联网", "平台", "港股"],
        "aliases": ["美团", "3690", "Meituan"],
        "snapshot": {"price": 108.3, "market_cap": 6700, "pe_ratio": 24.9, "pb_ratio": 3.6, "gross_margin": 37.0, "net_margin": 8.9, "roe": 13.4},
    },
    {
        "id": "co_catl_a",
        "name": "宁德时代",
        "name_en": "CATL",
        "ticker": "300750",
        "market": "A",
        "exchange": "SZSE",
        "industry": "动力电池",
        "sector": "新能源",
        "description": "全球动力电池龙头，受电动车渗透率、价格周期和储能需求影响。",
        "tags": ["动力电池", "新能源", "制造", "周期", "A股"],
        "aliases": ["CATL", "300750.SZ", "宁德"],
        "snapshot": {"price": 196.0, "market_cap": 8620, "pe_ratio": 18.2, "pb_ratio": 4.2, "gross_margin": 22.9, "net_margin": 11.4, "roe": 24.5},
    },
    {
        "id": "co_google_us",
        "name": "Google",
        "name_en": "Alphabet",
        "ticker": "GOOGL",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "互联网/云计算",
        "sector": "科技平台",
        "description": "搜索广告、YouTube、云和 AI 基础设施驱动的全球科技平台。",
        "tags": ["广告", "云计算", "AI", "平台", "美股"],
        "aliases": ["Alphabet", "Google", "GOOG", "GOOGL"],
        "snapshot": {"price": 166.1, "market_cap": 20500, "pe_ratio": 24.2, "pb_ratio": 6.2, "gross_margin": 57.4, "net_margin": 25.8, "roe": 29.1},
    },
    {
        "id": "co_msft_us",
        "name": "Microsoft",
        "name_en": "Microsoft",
        "ticker": "MSFT",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "软件/云计算",
        "sector": "科技平台",
        "description": "企业软件、Azure 云、Office 和 AI Copilot 组成的高质量复利型公司。",
        "tags": ["软件", "云计算", "AI", "现金流", "美股"],
        "aliases": ["微软", "Microsoft", "MSFT"],
        "snapshot": {"price": 421.8, "market_cap": 31300, "pe_ratio": 35.4, "pb_ratio": 12.8, "gross_margin": 69.8, "net_margin": 36.3, "roe": 38.7},
    },
    {
        "id": "co_amazon_us",
        "name": "Amazon",
        "name_en": "Amazon",
        "ticker": "AMZN",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "电商/云计算",
        "sector": "科技平台",
        "description": "电商、AWS、广告和物流网络共同驱动的全球平台公司。",
        "tags": ["电商", "云计算", "广告", "规模经济", "美股"],
        "aliases": ["亚马逊", "Amazon", "AMZN"],
        "snapshot": {"price": 183.3, "market_cap": 19100, "pe_ratio": 43.0, "pb_ratio": 8.5, "gross_margin": 48.8, "net_margin": 5.9, "roe": 19.9},
    },
    {
        "id": "co_meta_us",
        "name": "Meta",
        "name_en": "Meta Platforms",
        "ticker": "META",
        "market": "US",
        "exchange": "NASDAQ",
        "industry": "社交广告",
        "sector": "科技平台",
        "description": "Facebook、Instagram、WhatsApp 和 AI 推荐广告系统构成的高现金流平台。",
        "tags": ["社交", "广告", "AI", "现金流", "美股"],
        "aliases": ["Facebook", "Meta", "META"],
        "snapshot": {"price": 474.2, "market_cap": 12100, "pe_ratio": 23.5, "pb_ratio": 7.4, "gross_margin": 81.5, "net_margin": 32.1, "roe": 34.4},
    },
]


def company_record(
    company_id: str,
    name: str,
    name_en: str,
    ticker: str,
    market: str,
    exchange: str,
    industry: str,
    sector: str,
    description: str,
    tags: list[str],
    aliases: list[str],
    price: float,
    market_cap: float,
    pe_ratio: float,
    pb_ratio: float,
    gross_margin: float,
    net_margin: float,
    roe: float,
) -> dict:
    return {
        "id": company_id,
        "name": name,
        "name_en": name_en,
        "ticker": ticker,
        "market": market,
        "exchange": exchange,
        "industry": industry,
        "sector": sector,
        "description": description,
        "tags": tags,
        "aliases": aliases,
        "snapshot": {
            "price": price,
            "market_cap": market_cap,
            "pe_ratio": pe_ratio,
            "pb_ratio": pb_ratio,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "roe": roe,
        },
    }


ADDITIONAL_COMPANIES = [
    company_record("co_pingan_a", "中国平安", "Ping An Insurance", "601318", "A", "SSE", "保险/金融", "金融服务", "中国综合金融集团，保险、银行、资管和科技平台协同发展。", ["保险", "金融", "低估值", "现金流", "A股"], ["平安", "601318.SH", "2318.HK"], 45.6, 8300, 9.1, 0.9, 0.0, 8.8, 10.7),
    company_record("co_cmb_a", "招商银行", "China Merchants Bank", "600036", "A", "SSE", "银行", "金融服务", "中国零售银行龙头，核心变量是资产质量、息差和财富管理能力。", ["银行", "金融", "现金流", "高ROE", "A股"], ["招行", "600036.SH", "3968.HK"], 36.2, 9100, 6.7, 1.0, 0.0, 35.5, 15.8),
    company_record("co_wuliangye_a", "五粮液", "Wuliangye", "000858", "A", "SZSE", "白酒", "高端消费", "中国浓香型白酒龙头，品牌、渠道和高端价格带是核心变量。", ["白酒", "消费", "品牌", "现金流", "A股"], ["000858.SZ", "五粮液"], 142.8, 5540, 19.8, 4.2, 75.6, 34.1, 22.6),
    company_record("co_luzhou_a", "泸州老窖", "Luzhou Laojiao", "000568", "A", "SZSE", "白酒", "高端消费", "国窖1573驱动的白酒公司，关注高端化、渠道库存和价格体系。", ["白酒", "消费", "品牌", "现金流", "A股"], ["000568.SZ", "泸州老窖"], 168.1, 2470, 22.1, 5.6, 87.1, 44.2, 27.5),
    company_record("co_midea_a", "美的集团", "Midea Group", "000333", "A", "SZSE", "家电/机器人", "制造消费", "白电、暖通、工业技术和机器人业务并行的中国制造龙头。", ["家电", "制造", "现金流", "全球化", "A股"], ["美的", "000333.SZ"], 68.4, 4780, 13.8, 2.7, 26.9, 9.5, 20.2),
    company_record("co_gree_a", "格力电器", "Gree Electric", "000651", "A", "SZSE", "家电", "制造消费", "空调龙头，关注渠道改革、分红、竞争格局和多元化成效。", ["家电", "制造", "分红", "低估值", "A股"], ["格力", "000651.SZ"], 41.3, 2320, 8.2, 1.7, 29.4, 12.7, 20.1),
    company_record("co_hikvision_a", "海康威视", "Hikvision", "002415", "A", "SZSE", "安防/AIoT", "科技硬件", "视频物联和安防龙头，受政府需求、海外限制和 AIoT 商业化影响。", ["安防", "AI", "硬件", "制造", "A股"], ["海康", "002415.SZ"], 33.5, 3090, 18.3, 2.9, 44.1, 15.8, 16.5),
    company_record("co_mindray_a", "迈瑞医疗", "Mindray", "300760", "A", "SZSE", "医疗器械", "医疗健康", "中国医疗器械龙头，监护、影像和体外诊断构成核心业务。", ["医疗器械", "医疗", "高ROE", "全球化", "A股"], ["迈瑞", "300760.SZ"], 292.0, 3540, 29.6, 8.7, 65.5, 32.8, 29.8),
    company_record("co_hengrui_a", "恒瑞医药", "Hengrui Medicine", "600276", "A", "SSE", "创新药", "医疗健康", "中国创新药龙头，关注研发管线、集采压力和国际化进展。", ["医药", "创新药", "研发", "A股"], ["恒瑞", "600276.SH"], 42.7, 2720, 55.0, 5.4, 84.2, 20.3, 10.8),
    company_record("co_wuxi_a", "药明康德", "WuXi AppTec", "603259", "A", "SSE", "CXO", "医疗服务", "全球医药研发外包平台，订单、地缘政治和客户资本开支是关键变量。", ["CXO", "医疗", "全球化", "政策风险", "A股"], ["药明", "603259.SH", "2359.HK"], 48.2, 1410, 18.0, 2.5, 38.6, 20.4, 14.2),
    company_record("co_zijin_a", "紫金矿业", "Zijin Mining", "601899", "A", "SSE", "有色金属", "资源周期", "金铜资源龙头，受金价、铜价、矿山扩产和地缘风险影响。", ["黄金", "铜", "资源", "周期", "A股"], ["紫金", "601899.SH", "2899.HK"], 17.8, 4690, 18.5, 4.2, 16.9, 9.6, 23.1),
    company_record("co_cmb_hk", "招商银行", "China Merchants Bank", "3968.HK", "HK", "HKEX", "银行", "金融服务", "招商银行港股证券，与 A 股 600036 映射同一家公司。", ["银行", "金融", "现金流", "高ROE", "港股"], ["招行", "600036", "招商银行"], 38.4, 9100, 6.3, 0.9, 0.0, 35.5, 15.8),
    company_record("co_popmart_hk", "泡泡玛特", "Pop Mart International Group", "9992.HK", "HK", "HKEX", "潮流玩具/IP消费", "可选消费", "中国潮流玩具与 IP 运营公司，核心变量是 IP 生命周期、渠道扩张、海外增长、盲盒监管和粉丝复购。", ["潮流玩具", "IP", "品牌", "高增长", "港股"], ["POP MART", "Pop Mart", "泡泡玛特国际", "09992.HK", "9992", "9992.HK"], 151.9, 2037.0, 14.8, 8.5, 72.1, 35.1, 77.5),
    company_record("co_xiaomi_hk", "小米集团-W", "Xiaomi", "1810.HK", "HK", "HKEX", "消费电子/汽车", "科技硬件", "手机、IoT 与智能电动车并行的消费科技公司。", ["消费电子", "IoT", "新能源汽车", "港股"], ["小米", "1810", "Xiaomi"], 18.9, 4700, 24.5, 2.4, 21.3, 5.6, 10.2),
    company_record("co_kuaishou_hk", "快手-W", "Kuaishou", "1024.HK", "HK", "HKEX", "短视频/广告", "互联网平台", "短视频、直播、电商和广告平台，关注用户时长与商业化效率。", ["短视频", "广告", "电商", "平台", "港股"], ["快手", "1024"], 48.7, 2110, 22.4, 3.0, 51.2, 9.8, 12.6),
    company_record("co_baidu_hk", "百度集团-SW", "Baidu", "9888.HK", "HK", "HKEX", "搜索/AI云", "中概互联网", "搜索广告、AI 云和自动驾驶构成的中国科技平台。", ["搜索", "AI", "云计算", "中概股", "港股"], ["百度", "BIDU", "9888"], 103.0, 2860, 12.6, 1.1, 51.5, 13.2, 9.6),
    company_record("co_baidu_us", "百度", "Baidu", "BIDU", "US", "NASDAQ", "搜索/AI云", "中概互联网", "百度美股 ADR，与港股 9888.HK 映射同一家公司。", ["搜索", "AI", "云计算", "中概股", "美股"], ["百度", "9888.HK", "BIDU"], 101.0, 350, 12.8, 1.1, 51.5, 13.2, 9.6),
    company_record("co_netease_hk", "网易-S", "NetEase", "9999.HK", "HK", "HKEX", "游戏/内容", "互联网平台", "游戏、音乐、教育和电商业务构成的现金流型互联网公司。", ["游戏", "内容", "现金流", "中概股", "港股"], ["网易", "NTES", "9999"], 155.2, 5000, 14.1, 3.2, 61.4, 25.1, 22.9),
    company_record("co_hkex_hk", "香港交易所", "Hong Kong Exchanges", "0388.HK", "HK", "HKEX", "交易所", "金融基础设施", "香港资本市场核心基础设施，受成交额、上市周期和互联互通影响。", ["交易所", "金融", "垄断", "港股"], ["港交所", "388.HK", "0388"], 278.0, 3520, 30.6, 7.2, 0.0, 56.4, 23.5),
    company_record("co_aia_hk", "友邦保险", "AIA", "1299.HK", "HK", "HKEX", "保险", "金融服务", "亚太寿险龙头，关注新业务价值、利率和区域增长。", ["保险", "金融", "现金流", "港股"], ["友邦", "AIA", "1299"], 62.5, 6900, 18.6, 1.9, 0.0, 12.4, 10.8),
    company_record("co_hsbc_hk", "汇丰控股", "HSBC", "0005.HK", "HK", "HKEX", "银行", "金融服务", "全球银行集团，利润受利率、信用成本和亚洲业务影响。", ["银行", "金融", "分红", "港股"], ["汇丰", "HSBC", "5.HK", "HSBC.US"], 68.9, 12400, 7.4, 1.0, 0.0, 18.0, 13.9),
    company_record("co_chinamobile_hk", "中国移动", "China Mobile", "0941.HK", "HK", "HKEX", "通信运营商", "通信服务", "中国通信运营商龙头，现金流、分红和云/算力业务为核心变量。", ["通信", "现金流", "分红", "港股"], ["中国移动", "941.HK", "600941"], 73.3, 15700, 10.5, 1.1, 0.0, 15.1, 10.6),
    company_record("co_cnooc_hk", "中国海洋石油", "CNOOC", "0883.HK", "HK", "HKEX", "油气", "能源资源", "中国海上油气龙头，受油价、资本开支和分红政策驱动。", ["能源", "油气", "分红", "周期", "港股"], ["中海油", "CNOOC", "883.HK"], 20.2, 9600, 7.1, 1.3, 0.0, 31.5, 18.0),
    company_record("co_nio_us", "蔚来", "NIO", "NIO", "US", "NYSE", "新能源汽车", "汽车/能源", "高端智能电动车公司，关注销量、毛利率、现金消耗和换电生态。", ["新能源汽车", "高波动", "中概股", "美股"], ["蔚来", "9866.HK", "NIO"], 5.1, 105, 0.0, 1.4, 7.5, -35.0, -42.0),
    company_record("co_lixiang_us", "理想汽车", "Li Auto", "LI", "US", "NASDAQ", "新能源汽车", "汽车/能源", "增程与纯电车型并行的中国新能源车公司，关注产品周期和毛利率。", ["新能源汽车", "成长股", "中概股", "美股"], ["理想", "2015.HK", "LI"], 31.5, 335, 21.0, 3.8, 22.2, 8.9, 18.2),
    company_record("co_xpeng_us", "小鹏汽车", "XPeng", "XPEV", "US", "NYSE", "新能源汽车", "汽车/能源", "智能电动车公司，重点看新车型、自动驾驶商业化和现金流改善。", ["新能源汽车", "自动驾驶", "中概股", "美股"], ["小鹏", "9868.HK", "XPEV"], 9.7, 95, 0.0, 1.7, 5.9, -24.2, -31.0),
    company_record("co_tsm_us", "台积电", "Taiwan Semiconductor", "TSM", "US", "NYSE", "半导体代工", "半导体", "全球先进制程代工龙头，受 AI/HPC、手机周期和地缘风险影响。", ["半导体", "先进制程", "AI算力", "美股"], ["台积电", "TSMC", "2330.TW", "TSM"], 142.6, 7380, 24.8, 5.6, 54.4, 38.6, 23.8),
    company_record("co_smic_a", "中芯国际", "Semiconductor Manufacturing International Corporation", "688981", "A", "SSE STAR", "晶圆代工", "半导体", "中芯国际科创板 A 股证券，与港股 00981.HK 对应同一经营主体，但交易币种、估值和流动性不同。", ["半导体", "晶圆代工", "制造", "周期", "A股", "港股映射"], ["中芯国际", "SMIC", "688981", "688981.SH", "00981.HK", "0981.HK", "981.HK"], 117.9, 9447.5, 173.5, 6.3, 17.8, 5.1, 3.6),
    company_record("co_smic_hk", "中芯国际", "Semiconductor Manufacturing International Corporation", "0981.HK", "HK", "HKEX", "晶圆代工", "半导体", "中芯国际港股证券，与科创板 688981 对应同一经营主体，但报价为港元且交易所流动性不同。", ["半导体", "晶圆代工", "制造", "周期", "港股", "A股映射"], ["中芯国际", "SMIC", "00981.HK", "0981.HK", "981.HK", "688981", "688981.SH"], 71.5, 5729.4, 0.0, 3.4, 17.8, 5.1, 3.6),
    company_record("co_asml_us", "ASML", "ASML", "ASML", "US", "NASDAQ", "半导体设备", "半导体", "EUV 光刻机垄断供应商，受先进制程资本开支和出口管制影响。", ["半导体设备", "垄断", "高ROE", "美股"], ["阿斯麦", "ASML.AS"], 921.0, 3640, 37.5, 18.1, 51.3, 28.0, 51.8),
    company_record("co_amd_us", "AMD", "Advanced Micro Devices", "AMD", "US", "NASDAQ", "半导体", "AI算力", "CPU/GPU 和数据中心芯片公司，关注 AI 加速器份额和毛利率。", ["半导体", "AI算力", "GPU", "高增长", "美股"], ["超威", "AMD"], 163.4, 2640, 46.2, 4.8, 50.6, 7.1, 10.5),
    company_record("co_intel_us", "英特尔", "Intel", "INTC", "US", "NASDAQ", "半导体", "芯片制造", "CPU 与晶圆制造转型公司，关注制程追赶、资本开支和利润率修复。", ["半导体", "制造", "反转", "美股"], ["Intel", "INTC", "英特尔"], 31.1, 1320, 0.0, 1.2, 40.0, -3.1, -1.8),
    company_record("co_broadcom_us", "博通", "Broadcom", "AVGO", "US", "NASDAQ", "半导体/软件", "基础设施科技", "网络芯片、AI ASIC 和基础设施软件并行的高现金流公司。", ["半导体", "软件", "AI", "现金流", "美股"], ["Broadcom", "AVGO", "博通"], 1350.0, 6250, 31.0, 16.2, 68.9, 24.5, 54.0),
    company_record("co_arm_us", "Arm", "Arm Holdings", "ARM", "US", "NASDAQ", "半导体 IP", "半导体", "CPU 架构 IP 授权公司，核心看授权费率、AI 终端和服务器渗透。", ["半导体", "IP", "高估值", "美股"], ["Arm", "ARM"], 121.0, 1260, 82.0, 19.0, 95.0, 18.5, 14.8),
    company_record("co_oracle_us", "Oracle", "Oracle", "ORCL", "US", "NYSE", "企业软件/云", "软件云", "数据库、企业软件和云基础设施公司，AI 云需求推动再定价。", ["软件", "云计算", "现金流", "美股"], ["甲骨文", "ORCL"], 124.2, 3420, 25.6, 43.0, 71.6, 20.7, 120.0),
    company_record("co_salesforce_us", "Salesforce", "Salesforce", "CRM", "US", "NYSE", "SaaS", "软件云", "CRM SaaS 龙头，关注收入增长、利润率提升和 AI 产品商业化。", ["软件", "SaaS", "现金流", "美股"], ["Salesforce", "CRM"], 282.0, 2730, 27.4, 4.4, 76.2, 15.8, 9.7),
    company_record("co_adobe_us", "Adobe", "Adobe", "ADBE", "US", "NASDAQ", "创意软件", "软件云", "创意与文档软件龙头，AI 生成式工具影响定价与竞争。", ["软件", "AI", "现金流", "美股"], ["Adobe", "ADBE"], 474.0, 2140, 29.8, 11.1, 88.0, 27.9, 36.0),
    company_record("co_netflix_us", "Netflix", "Netflix", "NFLX", "US", "NASDAQ", "流媒体", "线上娱乐", "全球流媒体平台，关注订阅增长、广告业务和内容投资回报。", ["流媒体", "内容", "平台", "美股"], ["Netflix", "NFLX", "奈飞"], 625.0, 2690, 34.8, 10.3, 42.5, 18.7, 30.9),
    company_record("co_costco_us", "Costco", "Costco", "COST", "US", "NASDAQ", "会员制零售", "消费零售", "会员制仓储零售龙头，低加价率和会员费构成护城河。", ["零售", "消费", "品牌", "现金流", "美股"], ["Costco", "COST", "开市客"], 812.0, 3600, 48.5, 16.0, 12.5, 2.7, 32.0),
    company_record("co_walmart_us", "Walmart", "Walmart", "WMT", "US", "NYSE", "零售", "消费零售", "全球零售龙头，规模、供应链和电商转型是核心变量。", ["零售", "消费", "现金流", "美股"], ["沃尔玛", "WMT"], 64.2, 5170, 26.0, 6.0, 24.4, 2.4, 21.0),
    company_record("co_coke_us", "可口可乐", "Coca-Cola", "KO", "US", "NYSE", "饮料", "消费品牌", "全球饮料品牌龙头，定价权、渠道和分红稳定性突出。", ["饮料", "品牌", "现金流", "分红", "美股"], ["Coca-Cola", "KO", "可口可乐"], 61.0, 2630, 22.4, 9.6, 59.5, 23.4, 41.0),
    company_record("co_visa_us", "Visa", "Visa", "V", "US", "NYSE", "支付网络", "金融科技", "全球支付网络龙头，受消费、跨境支付和监管费率影响。", ["支付", "金融科技", "网络效应", "现金流", "美股"], ["Visa", "V"], 274.0, 5400, 29.6, 13.5, 97.8, 52.5, 45.0),
    company_record("co_mastercard_us", "Mastercard", "Mastercard", "MA", "US", "NYSE", "支付网络", "金融科技", "全球支付网络公司，商业模式轻资产且现金流强。", ["支付", "金融科技", "网络效应", "现金流", "美股"], ["Mastercard", "MA", "万事达"], 456.0, 4230, 31.1, 52.0, 100.0, 46.1, 170.0),
    company_record("co_jpm_us", "摩根大通", "JPMorgan Chase", "JPM", "US", "NYSE", "银行", "金融服务", "美国大型综合银行，关注利率、信用周期、投行业务和资本回报。", ["银行", "金融", "现金流", "美股"], ["JPMorgan", "JPM", "摩根大通"], 196.0, 5650, 11.2, 1.8, 0.0, 29.0, 16.2),
    company_record("co_berkshire_us", "伯克希尔·哈撒韦", "Berkshire Hathaway", "BRK.B", "US", "NYSE", "保险/多元控股", "金融控股", "保险浮存金、多元实业和股票投资组合构成的复合型控股公司。", ["保险", "控股", "现金流", "美股"], ["Berkshire", "BRK.A", "BRK.B", "伯克希尔"], 408.0, 8900, 20.5, 1.5, 0.0, 18.0, 11.2),
    company_record("co_lilly_us", "礼来", "Eli Lilly", "LLY", "US", "NYSE", "制药", "医疗健康", "糖尿病、减重和创新药管线驱动的全球制药公司。", ["医药", "创新药", "高增长", "美股"], ["Eli Lilly", "LLY", "礼来"], 770.0, 7300, 58.0, 43.0, 80.2, 17.0, 65.0),
    company_record("co_novo_us", "诺和诺德", "Novo Nordisk", "NVO", "US", "NYSE", "制药", "医疗健康", "胰岛素、GLP-1 减重药和慢病管理龙头。", ["医药", "创新药", "高增长", "美股"], ["Novo Nordisk", "NVO", "诺和诺德"], 128.0, 5700, 42.0, 28.0, 84.0, 35.0, 88.0),
    company_record("co_disney_us", "迪士尼", "Disney", "DIS", "US", "NYSE", "传媒娱乐", "线上娱乐", "内容 IP、主题乐园和流媒体平台构成的全球娱乐公司。", ["内容", "品牌", "消费", "美股"], ["Disney", "DIS", "迪士尼"], 104.0, 1900, 21.0, 1.8, 35.0, 5.6, 6.8),
    company_record("co_uber_us", "Uber", "Uber", "UBER", "US", "NYSE", "出行/本地服务", "互联网平台", "全球网约车和配送平台，关注网络效应、利润率和监管。", ["平台", "本地生活", "高增长", "美股"], ["Uber", "UBER"], 70.0, 1460, 38.0, 10.0, 39.0, 6.5, 18.0),
    company_record("co_airbnb_us", "Airbnb", "Airbnb", "ABNB", "US", "NASDAQ", "在线旅游", "互联网平台", "短租住宿平台，关注旅行需求、供给增长、监管和品牌网络效应。", ["平台", "旅游", "现金流", "美股"], ["Airbnb", "ABNB", "爱彼迎"], 145.0, 930, 34.0, 10.4, 83.0, 48.0, 31.0),
]


ALL_COMPANIES = COMPANIES + ADDITIONAL_COMPANIES


def profile(philosophy: str, industries: list[str], styles: list[str], weaknesses: str, markets: list[str] | None = None) -> dict:
    return {
        "investment_philosophy": philosophy,
        "core_framework": f"核心框架：{philosophy}；关注能力圈、边际变化、赔率与风险暴露。",
        "decision_process": "先定义公司质量和关键变量，再判断估值、周期位置、风险补偿与可执行条件。",
        "question_template": "这家公司真正的护城河是什么？当前价格隐含了什么预期？什么事实会推翻判断？",
        "speaking_style": "直接、结构化、偏审慎，先讲事实再给倾向。",
        "strengths": " / ".join(industries + styles),
        "weaknesses": weaknesses,
        "preferred_industries": industries,
        "avoided_industries": ["概念炒作", "财务不透明", "不可验证增长"],
        "market_tags": markets or ["美股", "港股", "A股", "中概股"],
        "style_tags": styles,
        "risk_preference": "中等偏低" if "价值" in styles or "护城河" in styles else "中等",
        "time_horizon": "3-10年" if "长期" in styles or "复利" in styles else "6个月-3年",
        "source_summary": "基于公开访谈、股东信、著作、演讲、研究文章与历史投资案例抽象的 MVP 画像。",
    }


EXPERTS = [
    ("warren_buffett", "沃伦·巴菲特", "Warren Buffett", "投资大师", "美国", "伯克希尔·哈撒韦董事长", "长期主义、护城河、现金流和管理层质量。", profile("以可理解的好生意、持久护城河、优秀管理层和合理价格为核心", ["消费", "金融", "品牌", "现金流"], ["长期", "护城河", "价值"], "对早期科技、强周期和高估值成长更谨慎")),
    ("charlie_munger", "查理·芒格", "Charlie Munger", "投资大师", "美国", "伯克希尔·哈撒韦副主席", "多元思维模型、反脆弱和避免愚蠢错误。", profile("用多元思维模型识别高质量公司，并严厉排除低质量机会", ["消费", "平台", "品牌", "软件"], ["长期", "质量", "反脆弱"], "对财务工程和叙事型增长容忍度低")),
    ("ben_graham", "本杰明·格雷厄姆", "Benjamin Graham", "投资大师", "美国", "价值投资奠基人", "安全边际、净资产和市场先生。", profile("以安全边际和资产价值为第一原则，拒绝为乐观预期支付过高价格", ["金融", "工业", "低估值"], ["价值", "安全边际"], "对高成长无形资产公司的定性溢价较保守")),
    ("philip_fisher", "菲利普·费雪", "Philip Fisher", "投资大师", "美国", "成长股投资先驱", "闲聊法、管理层质量和长期成长空间。", profile("寻找长期可扩张、管理层优秀、研发和销售能力突出的成长公司", ["科技", "制造", "消费", "医疗"], ["成长", "长期", "质量"], "对短期估值波动容忍较高但要求增长可验证")),
    ("peter_lynch", "彼得·林奇", "Peter Lynch", "投资大师", "美国", "麦哲伦基金前经理", "从生活中发现 tenbagger，重视 PEG 和业务可理解性。", profile("寻找可理解、增长与估值匹配、还有认知差的公司", ["消费", "零售", "软件", "本地生活"], ["成长", "PEG", "自下而上"], "不喜欢过度复杂或财务口径难懂的公司")),
    ("howard_marks", "霍华德·马克斯", "Howard Marks", "投资大师", "美国", "橡树资本联合创始人", "周期、第二层思维、风险控制。", profile("用周期位置、市场情绪和风险补偿判断赔率", ["信用", "周期", "地产", "宏观"], ["周期", "风险控制", "逆向"], "不直接预测短期价格，偏重风险收益比")),
    ("seth_klarman", "赛斯·卡拉曼", "Seth Klarman", "投资大师", "美国", "Baupost 创始人", "绝对收益、安全边际和耐心。", profile("只在价格明显低于保守价值时出手，重视下行保护", ["低估值", "特殊机会", "金融"], ["价值", "安全边际", "耐心"], "可能错过高质量高估值成长股")),
    ("joel_greenblatt", "乔尔·格林布拉特", "Joel Greenblatt", "投资大师", "美国", "魔法公式投资者", "高资本回报率与低估值组合。", profile("用资本回报率和估值筛选可重复的高赔率组合", ["消费", "工业", "软件"], ["量化价值", "ROIC", "纪律"], "较少解释宏观叙事和非财务因素")),
    ("john_templeton", "约翰·邓普顿", "John Templeton", "投资大师", "美国", "全球逆向投资者", "全球配置、极度悲观时买入。", profile("在全球范围寻找悲观预期中被低估的优秀资产", ["全球市场", "金融", "周期", "消费"], ["逆向", "价值", "全球"], "对单一公司微观技术细节不会过度深入")),
    ("george_soros", "乔治·索罗斯", "George Soros", "投资大师", "美国", "量子基金创始人", "反身性、宏观趋势和仓位管理。", profile("用反身性理解价格、预期与基本面之间的反馈循环", ["宏观", "金融", "汇率", "周期"], ["交易", "宏观", "反身性"], "不适合长期静态估值型分析")),
    ("ray_dalio", "瑞·达利欧", "Ray Dalio", "投资大师", "美国", "桥水创始人", "经济机器、债务周期和全天候配置。", profile("从债务周期、流动性和政策框架判断资产风险", ["宏观", "金融", "大宗商品", "全球市场"], ["宏观", "周期", "风险平价"], "对单一公司产品细节不会给出过细判断")),
    ("stanley_druckenmiller", "斯坦利·德鲁肯米勒", "Stanley Druckenmiller", "投资大师", "美国", "宏观交易大师", "集中仓位、流动性和盈利趋势。", profile("重视流动性、盈利拐点、趋势强度和仓位纪律", ["宏观", "科技", "周期", "金融"], ["趋势", "宏观", "交易"], "长期静态持有不是核心能力圈")),
    ("david_tepper", "大卫·泰珀", "David Tepper", "投资大师", "美国", "Appaloosa 创始人", "困境反转、宏观流动性和风险资产。", profile("在恐慌和错杀中寻找强资产负债表反转机会", ["金融", "周期", "困境反转"], ["逆向", "事件驱动", "宏观"], "对高估值长期成长容忍度低")),
    ("bill_ackman", "比尔·阿克曼", "Bill Ackman", "投资大师", "美国", "Pershing Square 创始人", "高质量集中投资和主动主义。", profile("集中持有可理解、高质量、可改善治理或结构的公司", ["消费", "平台", "餐饮", "金融"], ["集中", "质量", "主动主义"], "容易对少数高确信观点过度集中")),
    ("michael_burry", "迈克尔·伯里", "Michael Burry", "投资大师", "美国", "Scion Asset Management 创始人", "逆向、泡沫识别和风险暴露。", profile("寻找市场错误定价和系统性脆弱点，尤其关注估值泡沫", ["金融", "地产", "周期", "空头"], ["逆向", "风险", "深度研究"], "可能过早识别风险而承受等待成本")),
    ("li_lu", "李录", "Li Lu", "投资大师", "中国/美国", "喜马拉雅资本创始人", "能力圈、长期复利和中国公司研究。", profile("寻找有长期护城河、优秀文化和巨大再投资空间的公司", ["中国市场", "消费", "科技平台", "制造"], ["长期", "复利", "价值"], "对短期交易和纯主题炒作兴趣低")),
    ("duan_yongping", "段永平", "Duan Yongping", "投资大师", "中国", "企业家/投资人", "本分、用户价值、商业模式和现金流。", profile("从用户价值、商业模式、企业文化和长期现金流判断公司", ["消费电子", "互联网", "消费品牌", "游戏"], ["长期", "商业模式", "品牌"], "不喜欢看不懂、靠融资续命或文化不正的企业", ["美股", "港股", "A股", "中概股"])),
    ("zhang_lei", "张磊", "Zhang Lei", "投资大师", "中国", "高瓴创始人", "长期结构变化、产业研究和企业赋能。", profile("寻找长期结构性变化中的优秀组织和产业链位置", ["消费", "医疗", "科技", "先进制造"], ["长期", "产业", "成长"], "对纯低估值烟蒂股兴趣不高", ["A股", "港股", "中概股", "美股"])),
    ("mohnish_pabrai", "莫尼什·帕伯莱", "Mohnish Pabrai", "投资大师", "美国", "Pabrai Funds 创始人", "低风险高不确定、复制优秀投资人。", profile("寻找下行有限、上行可观、市场暂时误解的机会", ["消费", "汽车", "金融", "低估值"], ["价值", "复制", "耐心"], "对强技术细节公司通常要求更高折价")),
    ("aswath_damodaran", "阿斯沃斯·达摩达兰", "Aswath Damodaran", "投资大师", "美国", "纽约大学估值教授", "叙事与数字、DCF 和估值纪律。", profile("把公司叙事转化为增长、利润率、再投资和风险参数", ["科技", "消费", "金融", "平台"], ["估值", "DCF", "叙事"], "不直接判断短期技术走势")),
    ("terry_smith", "Terry Smith", "Terry Smith", "投资大师", "英国", "Fundsmith 创始人", "好公司、不要多交易、不要过度付费。", profile("买好公司、不要付太多、然后什么都不做", ["消费", "医疗", "软件", "品牌"], ["质量", "长期", "复利"], "对周期和资产重公司更谨慎")),
    ("nick_sleep", "Nick Sleep", "Nick Sleep", "投资大师", "英国", "Nomad Investment Partnership", "规模经济共享和长期复利。", profile("寻找将规模经济让利给客户、从而扩大长期护城河的公司", ["平台", "零售", "电商", "互联网"], ["长期", "规模经济", "复利"], "对短期估值和季度波动不敏感")),
    ("tom_russo", "Tom Russo", "Tom Russo", "投资大师", "美国", "Gardner Russo & Quinn 合伙人", "品牌、家族控制和全球消费。", profile("偏好能承受短期投入、拥有全球品牌和家族文化的消费公司", ["消费", "品牌", "酒类", "全球化"], ["长期", "品牌", "耐心"], "对非消费科技公司的技术判断不是强项")),
    ("guy_spier", "Guy Spier", "Guy Spier", "投资大师", "瑞士", "Aquamarine Capital", "价值投资、投资环境和行为纪律。", profile("重视管理层诚信、长期复利和减少行为错误", ["消费", "金融", "平台"], ["价值", "纪律", "长期"], "不适合追逐短期催化")),
    ("jim_simons", "Jim Simons", "Jim Simons", "投资大师", "美国", "文艺复兴科技创始人", "量化、统计套利和模式识别。", profile("用数据、统计规律和风险模型识别可重复优势", ["量化", "交易结构", "技术面", "流动性"], ["量化", "交易", "风险模型"], "不以主观长期公司研究见长")),
    ("jeremy_grantham", "Jeremy Grantham", "Jeremy Grantham", "宏观/策略专家", "英国", "GMO 联合创始人", "泡沫、均值回归和长期资产回报。", profile("从估值均值回归、泡沫和资源约束判断长期风险", ["宏观", "周期", "能源", "市场估值"], ["逆向", "泡沫", "长期"], "容易提前警告泡沫")),
    ("michael_mauboussin", "Michael Mauboussin", "Michael Mauboussin", "研究专家", "美国", "投资研究员/作家", "期望值、竞争优势周期和决策质量。", profile("用期望值、资本配置和竞争优势周期评估赔率", ["平台", "科技", "消费", "资本配置"], ["决策科学", "质量", "期望值"], "不负责短线择时")),
    ("mohamed_el_erian", "Mohamed El-Erian", "Mohamed El-Erian", "宏观专家", "美国", "经济学家", "央行政策、利率和全球宏观风险。", profile("从利率、通胀、政策与全球资本流动识别风险", ["宏观", "利率", "金融", "全球市场"], ["宏观", "风险", "政策"], "较少做公司级产品判断")),
    ("ed_yardeni", "Ed Yardeni", "Ed Yardeni", "宏观策略专家", "美国", "Yardeni Research 总裁", "盈利周期、经济指标和市场宽度。", profile("用经济高频指标、盈利周期和市场广度判断环境", ["宏观", "行业周期", "科技", "消费"], ["策略", "周期", "数据"], "不以公司深度尽调为主")),
    ("mary_meeker", "Mary Meeker", "Mary Meeker", "互联网专家", "美国", "互联网趋势研究员", "互联网趋势、用户增长和商业模式。", profile("从用户行为、渗透率、网络效应和商业模式演化判断机会", ["互联网", "平台", "广告", "电商"], ["趋势", "成长", "用户"], "对传统资产负债表价值分析不是强项")),
    ("ben_thompson", "Ben Thompson", "Ben Thompson", "科技战略专家", "美国", "Stratechery 作者", "聚合理论、平台战略和分发权力。", profile("用聚合理论、平台控制点和价值链重组分析科技公司", ["平台", "软件", "广告", "云计算"], ["战略", "平台", "科技"], "不提供细致财务建模")),
    ("gene_munster", "Gene Munster", "Gene Munster", "科技研究员", "美国", "Deepwater 研究员", "消费科技、苹果、AR/AI 和平台。", profile("关注消费科技产品周期、用户体验和新技术商业化", ["消费电子", "AI", "平台", "软件"], ["科技", "成长", "产品"], "对低估值周期股不是强项")),
    ("dan_ives", "Dan Ives", "Dan Ives", "科技分析师", "美国", "Wedbush 科技分析师", "软件、云、AI 与大型科技盈利预期。", profile("从订单、渠道、云支出和 AI 采用判断科技股弹性", ["软件", "云计算", "AI", "大型科技"], ["成长", "情绪", "催化剂"], "观点偏乐观，需要风险校准")),
    ("stacy_rasgon", "Stacy Rasgon", "Stacy Rasgon", "半导体分析师", "美国", "Bernstein 半导体分析师", "半导体周期、毛利率和库存。", profile("从库存、ASP、毛利率、资本开支和终端需求分析芯片公司", ["半导体", "AI算力", "硬件", "制造"], ["行业研究", "周期", "财务质量"], "不直接覆盖非科技消费公司")),
    ("pierre_ferragu", "Pierre Ferragu", "Pierre Ferragu", "科技/汽车分析师", "法国", "New Street Research 分析师", "电动车、科技硬件和长期技术采用。", profile("关注技术采用曲线、单位经济和竞争格局变化", ["新能源汽车", "半导体", "通信", "科技硬件"], ["成长", "行业", "技术"], "估值安全边际要求需外部补充")),
    ("adam_jonas", "Adam Jonas", "Adam Jonas", "汽车分析师", "美国", "Morgan Stanley 汽车分析师", "汽车、电动车、自动驾驶和移动出行。", profile("从产品周期、价格、产能、软件和出行生态分析汽车公司", ["新能源汽车", "汽车", "自动驾驶", "电池"], ["行业研究", "成长", "情景分析"], "容易受长期叙事影响，需要财务约束")),
    ("ming_chi_kuo", "Ming-Chi Kuo", "Ming-Chi Kuo", "供应链专家", "中国台湾", "天风国际分析师", "苹果产业链、消费电子供应链。", profile("从供应链订单、出货量、BOM 和产品节奏判断硬件公司", ["消费电子", "苹果产业链", "硬件", "半导体"], ["供应链", "产品周期", "数据"], "对非硬件公司适配度低")),
    ("jim_chanos", "Jim Chanos", "Jim Chanos", "空头专家", "美国", "Kynikos 创始人", "财务质量、商业模式缺陷和空头视角。", profile("寻找财务口径、商业模式和增长叙事中的脆弱点", ["空头", "金融", "地产", "中概股"], ["风险", "逆向", "财务质量"], "可能低估优质公司长期复利")),
    ("cathie_wood", "Cathie Wood", "Cathie Wood", "成长投资专家", "美国", "ARK Invest 创始人", "颠覆式创新和长期 TAM。", profile("用技术学习曲线和长期 TAM 捕捉颠覆式增长", ["AI", "新能源汽车", "基因技术", "软件"], ["创新", "成长", "长期"], "对估值和现金流安全边际要求较低")),
    ("qiu_guolu", "邱国鹭", "Qiu Guolu", "中国市场专家", "中国", "高毅资产董事长", "中国价值投资、胜而后求战和产业比较。", profile("从行业格局、管理层、估值和中国市场周期寻找胜率", ["A股", "消费", "制造", "金融"], ["价值", "中国市场", "产业"], "对海外科技细节需要其他专家补充", ["A股", "港股", "中概股"])),
]


def seed_database(conn: sqlite3.Connection) -> None:
    for expert_id, name, name_en, category, nationality, role_title, bio, prof in EXPERTS:
        conn.execute(
            """
            INSERT INTO experts (id, name, name_en, category, nationality, role_title, bio, avatar_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                name_en = excluded.name_en,
                category = excluded.category,
                nationality = excluded.nationality,
                role_title = excluded.role_title,
                bio = excluded.bio,
                updated_at = CURRENT_TIMESTAMP
            """,
            (expert_id, name, name_en, category, nationality, role_title, bio, ""),
        )
        profile_row = conn.execute("SELECT id FROM expert_profiles WHERE expert_id = ?", (expert_id,)).fetchone()
        if not profile_row:
            conn.execute(
                """
                INSERT INTO expert_profiles (
                    id, expert_id, investment_philosophy, core_framework, decision_process,
                    question_template, speaking_style, strengths, weaknesses, preferred_industries,
                    avoided_industries, market_tags, style_tags, risk_preference, time_horizon, source_summary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    expert_id,
                    prof["investment_philosophy"],
                    prof["core_framework"],
                    prof["decision_process"],
                    prof["question_template"],
                    prof["speaking_style"],
                    prof["strengths"],
                    prof["weaknesses"],
                    to_json(prof["preferred_industries"]),
                    to_json(prof["avoided_industries"]),
                    to_json(prof["market_tags"]),
                    to_json(prof["style_tags"]),
                    prof["risk_preference"],
                    prof["time_horizon"],
                    prof["source_summary"],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE expert_profiles
                SET source_summary =
                    CASE
                      WHEN source_summary LIKE '%系统批量 AI 蒸馏已完成%' THEN source_summary
                      ELSE source_summary || CHAR(10) || ?
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE expert_id = ?
                """,
                (seed_distillation_note(name, prof), expert_id),
            )
        for industry in prof["preferred_industries"]:
            exists = conn.execute(
                "SELECT 1 FROM expert_company_fit WHERE expert_id = ? AND industry_tag = ?",
                (expert_id, industry),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO expert_company_fit (id, expert_id, industry_tag, company_tag, fit_score, reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid4()), expert_id, industry, industry, 82, f"{name} 对 {industry} 的公开框架适配度较高。"),
                )
        material_id = f"seed_material_{expert_id}"
        material_text = seed_material_text(name, name_en, bio, prof)
        conn.execute(
            """
            INSERT INTO expert_materials (
                id, expert_id, title, material_type, language, source_url,
                uploaded_file_path, raw_text, ai_summary, distilled_points
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                raw_text = excluded.raw_text,
                ai_summary = excluded.ai_summary,
                distilled_points = excluded.distilled_points
            """,
            (
                material_id,
                expert_id,
                f"{name} 公开材料蒸馏底稿",
                "seed_distillation",
                "zh",
                "",
                "",
                material_text,
                f"系统批量 AI 蒸馏已完成：{name} 的画像聚焦 {prof['investment_philosophy']}；能力圈为 {'、'.join(prof['preferred_industries'])}；盲区为 {prof['weaknesses']}。",
                to_json(
                    {
                        "thinking_models": prof["style_tags"],
                        "capability_circle": prof["preferred_industries"],
                        "blind_spots": prof["avoided_industries"],
                        "speaking_style": prof["speaking_style"],
                        "decision_rules": [
                            prof["core_framework"],
                            prof["decision_process"],
                            "最终发言必须给出评分、倾向、风险触发器和可执行条件。",
                        ],
                        "source_quality": "seed_public_material_distilled",
                    }
                ),
            ),
        )

    for item in ALL_COMPANIES:
        conn.execute(
            """
            INSERT INTO companies (id, name, name_en, ticker, market, exchange, industry, sector, description, tags, aliases)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = CASE WHEN companies.tags LIKE '%证券主数据%' THEN companies.name ELSE excluded.name END,
                name_en = CASE WHEN companies.tags LIKE '%证券主数据%' THEN companies.name_en ELSE excluded.name_en END,
                ticker = excluded.ticker,
                market = excluded.market,
                exchange = excluded.exchange,
                industry = CASE WHEN companies.tags LIKE '%证券主数据%' THEN companies.industry ELSE excluded.industry END,
                sector = CASE WHEN companies.tags LIKE '%证券主数据%' THEN companies.sector ELSE excluded.sector END,
                description = CASE WHEN companies.tags LIKE '%证券主数据%' THEN companies.description ELSE excluded.description END,
                tags = CASE WHEN companies.tags LIKE '%证券主数据%' THEN companies.tags ELSE excluded.tags END,
                aliases = CASE WHEN companies.tags LIKE '%证券主数据%' THEN companies.aliases ELSE excluded.aliases END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                item["id"],
                item["name"],
                item["name_en"],
                item["ticker"],
                item["market"],
                item["exchange"],
                item["industry"],
                item["sector"],
                item["description"],
                to_json(item["tags"]),
                to_json(item["aliases"]),
            ),
        )
        snap_exists = conn.execute("SELECT 1 FROM company_snapshots WHERE company_id = ?", (item["id"],)).fetchone()
        if not snap_exists:
            snap = item["snapshot"]
            conn.execute(
                """
                INSERT INTO company_snapshots (
                    id, company_id, snapshot_date, price, market_cap, pe_ratio, pb_ratio,
                    gross_margin, net_margin, roe, raw_data
                )
                VALUES (?, ?, DATE('now'), ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    item["id"],
                    snap["price"],
                    snap["market_cap"],
                    snap["pe_ratio"],
                    snap["pb_ratio"],
                    snap["gross_margin"],
                    snap["net_margin"],
                    snap["roe"],
                    to_json(snap),
                ),
            )


def seed_material_text(name: str, name_en: str, bio: str, prof: dict) -> str:
    return (
        f"专家：{name} / {name_en}\n"
        f"公开身份：{bio}\n"
        f"投资哲学：{prof['investment_philosophy']}\n"
        f"核心框架：{prof['core_framework']}\n"
        f"决策流程：{prof['decision_process']}\n"
        f"经典问题：{prof['question_template']}\n"
        f"发言风格：{prof['speaking_style']}\n"
        f"能力圈：{'、'.join(prof['preferred_industries'])}\n"
        f"不擅长领域：{prof['weaknesses']}\n"
        "蒸馏要求：在投委会发言时必须保留人物框架、能力圈边界、风险偏好和可执行结论。"
    )


def seed_distillation_note(name: str, prof: dict) -> str:
    return (
        f"系统批量 AI 蒸馏已完成：{name} 的画像聚焦 {prof['investment_philosophy']}；"
        f"能力圈为 {'、'.join(prof['preferred_industries'])}；发言需保持 {prof['speaking_style']}"
    )
