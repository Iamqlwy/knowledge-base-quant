"""动态 Fusion 权重计算模块。

根据查询内容的语义特征（意图、领域、时间敏感度、实体精确度等）
动态调整 RRF 融合各通道的权重，替代固定权重方案。

设计原则:
- 纯计算，无 IO，无异步 —— 同步方法，零延迟
- 基于规则的特征提取 + 加权映射，可解释、可调参
- 与现有 SearchContext / FusionService 无缝集成

意图检测 v2:
- 累加式多信号评分（递减权重累加），替代 max-score
- 查询结构模式识别（6 种模板）
- 领域检测移至意图之前，辅助意图推断
- 4 轴多因子加权模型（关键词 40% + 结构 30% + 领域 20% + 元启发式 10%）
"""
import logging
import re
from dataclasses import dataclass, field

from kbquant.models.search_candidate import SearchContext

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# 1. 查询特征定义
# ──────────────────────────────────────────────────────────────────────

@dataclass
class QueryFeatures:
    """从查询中提取的特征向量。"""

    # 原始查询
    raw_query: str = ""

    # ── 意图特征 ──
    intent: str = "general"          # news / analysis / strategy / entity_lookup / concept / general
    intent_confidence: float = 0.0   # 0-1
    sub_intent: str = ""             # 细粒度意图（v2 新增），如 "verification" / "causal" / "case_study"

    # ── 时间敏感度 ──
    time_sensitivity: float = 0.0    # 0(无偏好) - 1(极强时效)

    # ── 领域特征 ──
    domains: list[str] = field(default_factory=list)  # [financial_report, macro, tech, strategy, market_event, supply_chain]
    domain_scores: dict[str, float] = field(default_factory=dict)

    # ── 地域意图 ──
    target_region: str | None = None           # "cn" | "hk" | "us" | "eu" | "uk" | "jp" | None
    target_region_confidence: float = 0.0

    # ── 实体精确度 ──
    entity_specificity: float = 0.0  # 0(模糊概念) - 1(精确实体/ticker)
    has_ticker: bool = False
    entity_count: int = 0

    # ── 查询复杂度 ──
    query_length: int = 0
    keyword_count: int = 0
    is_short_query: bool = False     # <= 4 字符
    is_long_query: bool = False      # >= 20 字符

    # ── 情绪/方向 ──
    has_sentiment: bool = False
    sentiment_direction: str = ""    # bullish / bearish / neutral

    def cache_fingerprint(self) -> str:
        """Stable string fingerprint for cache keying.

        Only includes fields that would change the weight calculation output.
        """
        return "|".join(str(x) for x in [
            self.intent,
            round(self.intent_confidence, 2),
            round(self.time_sensitivity, 2),
            round(self.entity_specificity, 2),
            ",".join(self.domains[:3]),
            self.has_ticker,
            self.is_short_query,
            self.is_long_query,
        ])


# ──────────────────────────────────────────────────────────────────────
# 2. 特征提取器
# ──────────────────────────────────────────────────────────────────────

# 多信号累加衰减因子：第 n 个信号的权重 = decay^(n-1)
_CUMULATIVE_SCORE_DECAY = 0.5

# 时间敏感词 → 权重
_TIME_SIGNALS: dict[str, float] = {
    # 极强时效 (0.9-1.0)
    "今天": 1.0, "今日": 1.0, "刚刚": 1.0, "实时": 0.95,
    "昨天": 0.85, "昨日": 0.85, "本周": 0.75, "本月": 0.6,
    # 强时效 (0.6-0.8)
    "最新": 0.8, "最近": 0.7, "近日": 0.7, "近期": 0.65,
    "突发": 0.9, "速递": 0.7, "速报": 0.75, "快讯": 0.75,
    "收盘": 0.65, "开盘": 0.65, "盘前": 0.6, "盘中": 0.6,
    # 中等时效 (0.3-0.5)
    "公告": 0.5, "新闻": 0.5, "异动": 0.6, "动态": 0.4,
}

# 意图信号词（v2 大幅扩充）
_INTENT_SIGNALS: dict[str, dict[str, float]] = {
    "news": {
        "公告": 0.9, "新闻": 0.9, "消息": 0.8, "异动": 0.85,
        "突发": 0.9, "速递": 0.85, "报道": 0.8, "披露": 0.7,
        "刚刚": 0.9, "今天": 0.8, "今日": 0.8, "最新": 0.7,
        # 盘面即时信息
        "开盘": 0.75, "收盘": 0.75, "盘前": 0.7, "盘中": 0.7,
        "速报": 0.85, "突发消息": 0.9, "快讯": 0.8,
        # 市场异动
        "停牌": 0.8, "复牌": 0.8, "异动公告": 0.85,
    },
    "analysis": {
        "分析": 0.9, "逻辑": 0.85, "研报": 0.9, "深度": 0.8,
        "报告": 0.7, "展望": 0.75, "评估": 0.8, "研究": 0.8,
        "估值": 0.8, "护城河": 0.75, "竞争力": 0.7, "基本面": 0.8,
        "核心逻辑": 0.9, "投资逻辑": 0.9,
        # 风险
        "风险": 0.7, "隐忧": 0.7, "潜在风险": 0.7,
        # 深度研究
        "深度研究": 0.85, "行业研究": 0.8,
    },
    "strategy": {
        "复盘": 0.95, "策略": 0.85, "打板": 0.9, "炸板": 0.9,
        "止损": 0.85, "仓位": 0.8, "教训": 0.9, "经验": 0.8,
        "操作": 0.7, "交易": 0.7, "回撤": 0.75, "封板": 0.85,
        "破位": 0.8, "龙头": 0.7, "天地板": 0.9,
        # 复盘 / 教训
        "案例": 0.7, "失败": 0.7, "判断错误": 0.85, "失误": 0.75,
        "前车之鉴": 0.85, "踩坑": 0.8, "亏损经验": 0.8,
        "复盘反馈": 0.9, "策略总结": 0.8, "经验总结": 0.8,
        # 证伪 / 不及预期
        "证伪": 0.85, "不及预期": 0.85, "高估": 0.75, "夸大": 0.75,
        "过于乐观": 0.8, "不准确": 0.65, "未必": 0.55,
        "质疑": 0.7, "见光死": 0.8, "回调失败": 0.8,
        "低于预期": 0.8, "未能达到": 0.7, "未达预期": 0.8,
        # 交易操作
        "止盈": 0.8, "止盈点": 0.8, "获利了结": 0.75,
        "斩仓": 0.8, "做空": 0.7, "做多": 0.7,
        "短线": 0.65, "中长线": 0.65, "波段": 0.65,
        # 策略分析
        "利好出尽": 0.8, "卖出": 0.6, "买入": 0.6,
    },
    "entity_lookup": {
        # 由 ticker 匹配和短查询触发，不依赖关键词
    },
    "concept": {
        "知识图谱": 0.85, "图谱": 0.8, "节点": 0.8, "关系图谱": 0.85,
        "实体关系": 0.8, "worldnode": 0.85, "worldnodes": 0.85,
        "世界节点": 0.8, "知识节点": 0.8, "实体节点": 0.8,
        "node": 0.75, "nodes": 0.75, "sector": 0.7,
    },
    "market_data": {
        "行情": 0.9, "走势": 0.8, "K线": 0.85, "均线": 0.8, "盘面": 0.85,
        "报价": 0.8, "分时": 0.8, "日线": 0.8, "周线": 0.75, "量价": 0.75,
        "技术面": 0.75, "成交量": 0.7, "换手率": 0.7, "涨跌幅": 0.75,
        "资金流向": 0.7, "股价": 0.7,"回调": 0.7,
    },
}

# 领域信号词（v2 扩充）
_DOMAIN_SIGNALS: dict[str, dict[str, float]] = {
    "financial_report": {
        "财报": 1.0, "季报": 0.95, "年报": 0.95, "半年报": 0.9,
        "业绩": 0.85, "营收": 0.9, "利润": 0.85, "净利润": 0.9,
        "毛利率": 0.85, "净利率": 0.85, "ROE": 0.8, "EPS": 0.8,
        "出货量": 0.75, "收入": 0.7, "成本": 0.65, "现金流": 0.8,
        "负债": 0.7, "增速": 0.7, "同比": 0.75, "环比": 0.7,
        # 扩充
        "亏损": 0.8, "盈利": 0.8, "预增": 0.85, "预减": 0.85,
        "业绩预告": 0.9, "业绩快报": 0.9, "分红": 0.7, "股息": 0.7,
        "订单": 0.7, "合同": 0.65, "产能": 0.65,
    },
    "macro": {
        "美联储": 0.95, "加息": 0.9, "降息": 0.9, "CPI": 0.85,
        "GDP": 0.9, "PMI": 0.85, "通胀": 0.85, "货币政策": 0.9,
        "财政政策": 0.85, "流动性": 0.8, "降准": 0.85, "利率": 0.8,
        "汇率": 0.75, "国债": 0.8, "收益率": 0.75,
        # 扩充
        "经济增速": 0.8, "经济衰退": 0.85, "经济复苏": 0.85,
        "贸易": 0.7, "关税": 0.75, "制裁": 0.7, "地缘": 0.75,
        "反倾销": 0.8, "出口政策": 0.75, "出口": 0.6, "进口": 0.6,
    },
    "market_event": {
        "涨停": 0.9, "跌停": 0.9, "异动": 0.85, "大涨": 0.8,
        "大跌": 0.8, "暴跌": 0.85, "暴涨": 0.85, "跳水": 0.8,
        "拉升": 0.75, "回调": 0.7, "反弹": 0.7, "放量": 0.7,
        "缩量": 0.7, "资金": 0.65, "主力": 0.7, "北向": 0.75,
        # 扩充
        "杀跌": 0.8, "冲高回落": 0.8, "高开低走": 0.8,
        "破发": 0.8, "破净": 0.75, "减持": 0.7, "增持": 0.7,
    },
    "strategy_domain": {
        "复盘": 0.9, "止损": 0.85, "仓位": 0.8, "打板": 0.9,
        "炸板": 0.9, "封板": 0.85, "回撤": 0.75, "教训": 0.9,
        "策略": 0.8, "龙头": 0.75, "妖股": 0.8, "破位": 0.8,
        # 扩充
        "证伪": 0.8, "不及预期": 0.8, "判断错误": 0.85,
        "案例": 0.7, "失败案例": 0.8, "前车之鉴": 0.8,
    },
    "supply_chain": {
        "产业链": 0.9, "上下游": 0.85, "供应商": 0.8, "客户": 0.7,
        "受益": 0.75, "受损": 0.75, "传导": 0.8, "利好": 0.7,
        "利空": 0.7, "带动": 0.65,
        # 扩充
        "供应链": 0.85, "采购": 0.65, "订单": 0.65,
        "配套": 0.7, "产能": 0.65, "产能爬坡": 0.75,
    },
    "knowledge_graph": {
        "知识图谱": 0.9, "图谱": 0.8, "节点": 0.8, "关系图谱": 0.9,
        "实体": 0.7, "worldnode": 0.85, "worldnodes": 0.85,
        # 扩充
        "世界节点": 0.85, "知识节点": 0.8, "实体节点": 0.8,
        "node": 0.75, "nodes": 0.75, "sector": 0.7,
    },
    "market_data": {
        "行情": 0.9, "走势": 0.85, "K线": 0.85, "均线": 0.8,
        "盘面": 0.85, "报价": 0.8, "分时": 0.8, "日线": 0.75,
        "周线": 0.75, "量价": 0.8, "技术面": 0.75,
        "成交量": 0.7, "换手率": 0.7, "涨跌幅": 0.75,
        "资金流向": 0.7,
    },
    "tech": {
        "半导体": 0.85, "芯片": 0.85, "AI": 0.8, "人工智能": 0.8,
        "新能源": 0.8, "光伏": 0.8, "机器人": 0.75, "大模型": 0.8,
        # 扩充
        "光模块": 0.8, "服务器": 0.75, "数据中心": 0.8,
        "自动驾驶": 0.8, "量子": 0.8, "创新药": 0.75,
        "锂电": 0.8, "储能": 0.75, "HBM": 0.8,
    },
}

# 情绪信号
_BULLISH_WORDS = {
    "上涨", "大涨", "暴涨", "拉升", "走高", "反弹", "回升", "利好",
    "看多", "做多", "牛市", "突破", "金叉", "放量", "乐观",
}
_BEARISH_WORDS = {
    "下跌", "大跌", "暴跌", "跳水", "回落", "利空", "看空", "做空",
    "熊市", "死叉", "缩量", "悲观", "恐慌", "踩踏", "爆仓",
}

# ── 地域市场信号词 → (region_code, weight) ──
# 用于推断用户查询的目标市场区域。
# 优先级：ticker exchange suffix（确定性最高）> 地域关键词 > 实体类型推断
_REGION_SIGNALS: dict[str, tuple[str, float]] = {
    # 中国市场 — 直接信号
    "A股": ("cn", 0.95), "沪市": ("cn", 0.95), "深市": ("cn", 0.95),
    "沪深": ("cn", 0.95), "央行": ("cn", 0.90), "北向": ("cn", 0.85),
    "创业板": ("cn", 0.85), "科创板": ("cn", 0.85), "北交所": ("cn", 0.90),
    # 港股 — 直接信号
    "港股": ("hk", 0.95), "恒生": ("hk", 0.90), "H股": ("hk", 0.85),
    # 美股 — 直接信号
    "美股": ("us", 0.95), "纳斯达克": ("us", 0.90), "纽交所": ("us", 0.90),
    "道琼斯": ("us", 0.85), "标普": ("us", 0.85),
    # 美国宏观 — 间接信号（美联储虽然是美国机构，但对中国市场也有外溢效应）
    "美联储": ("us", 0.75), "Fed": ("us", 0.70),
    # 欧洲 — 直接信号
    "欧洲央行": ("eu", 0.90), "ECB": ("eu", 0.85), "欧股": ("eu", 0.90),
    "英国央行": ("uk", 0.90), "BoE": ("uk", 0.85), "英股": ("uk", 0.85),
    # 日本 — 直接信号
    "日股": ("jp", 0.90), "日本央行": ("jp", 0.85), "日经": ("jp", 0.85),
}

# ── 查询结构模式 ──
# 每个模式: (compiled_regex, preferred_intent, weight)
# weight: 该模式对意图的贡献权重 (0-1)

_STRUCTURE_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    # 提问型 → concept
    (re.compile(r"(?:什么是|是什么|如何|怎么|为什么|哪些|有无|是否存在|有哪些)"), "concept", 0.85),
    # 因果型 → analysis
    (re.compile(r"(?:对.{0,10}的影响|导致|引起|造成|推动|驱动.{0,5}增长)"), "analysis", 0.8),
    # 对比型 → analysis
    (re.compile(r"(?:[Vv][Ss]\b|对比|比较|哪一个|哪个更|区别|相较于)"), "analysis", 0.75),
    # 证伪型 → strategy
    (re.compile(r"(?:证伪|质疑|不及预期|高估|夸大|过于乐观|不准确|未必|低于预期|未达预期|未能达到)"), "strategy", 0.85),
    # 案例型 → strategy
    (re.compile(r"(?:案例|前车之鉴|历史.{0,5}教训|复盘.{0,5}案例|失败.{0,5}案例|教训)"), "strategy", 0.85),
    # 趋势型 → analysis
    (re.compile(r"(?:走势|趋势|持续性|延续|拐点|见顶|见底)"), "analysis", 0.75),
]

# Ticker 正则：纯数字 6 位（A 股）或字母数字混合（港股/美股）
_TICKER_RE = re.compile(r"^(?:\d{6}(?:\.[A-Z]{2})?|[A-Z]{1,5}(?:\.[A-Z]{1,2})?)$")


def _accumulate_signals(
    q: str,
    signals: dict[str, float],
    negate_check=None,
) -> tuple[float, int]:
    """累加式多信号评分，递减权重。

    第 1 个命中信号权重 = 1.0 * weight
    第 2 个命中信号权重 = 0.5 * weight
    第 3 个命中信号权重 = 0.25 * weight
    ...

    返回 (累加得分, 命中信号数)
    """
    hits: list[float] = []
    for signal, weight in signals.items():
        if negate_check is not None:
            if not negate_check(signal, q):
                continue
        elif signal not in q:
            continue
        hits.append(weight)

    if not hits:
        return 0.0, 0

    # 按权重降序排列
    hits.sort(reverse=True)
    score = 0.0
    for i, w in enumerate(hits):
        score += w * (_CUMULATIVE_SCORE_DECAY ** i)

    return score, len(hits)


class QueryFeatureExtractor:
    """从查询文本 + SearchContext 中提取 QueryFeatures。"""

    # Tokens that, when appearing immediately before a signal word, negate
    # the signal (e.g. "不要/没有/不是 + 分析" should not trigger analysis intent).
    _NEGATION_PREFIXES = ("不要", "没有", "不是", "不会", "无需", "不")

    @staticmethod
    def _signal_matches(signal: str, text: str) -> bool:
        """Return True if `signal` appears in `text` and is not preceded by a
        negation prefix (avoiding cases like "不要分析" → analysis intent)."""
        idx = text.find(signal)
        if idx == -1:
            return False
        # Check for negation prefix immediately before the signal
        for prefix in QueryFeatureExtractor._NEGATION_PREFIXES:
            if idx >= len(prefix) and text[idx - len(prefix):idx] == prefix:
                return False
        return True

    def extract(
        self,
        query_text: str,
        ctx: SearchContext | None = None,
    ) -> QueryFeatures:
        features = QueryFeatures(raw_query=query_text)
        q = query_text.strip()
        q_lower = q.lower()

        # ── 基础长度特征 ──
        features.query_length = len(q)
        features.is_short_query = features.query_length <= 4
        features.is_long_query = features.query_length >= 20

        # ── Ticker 检测 ──
        features.has_ticker = bool(_TICKER_RE.match(q.strip()))

        # ── 实体数量 ──
        if ctx and ctx.entities:
            features.entity_count = len(ctx.entities)
            high_conf = [e for e in ctx.entities if e.score >= 0.8]
            if high_conf:
                features.entity_specificity = min(1.0, max(e.score for e in high_conf))
            elif ctx.entities:
                features.entity_specificity = max(e.score for e in ctx.entities) * 0.7

        # Ticker 查询 → 最高精确度
        if features.has_ticker:
            features.entity_specificity = 1.0

        # 短查询 + 有高分实体 → 高精确度（仅当实体分数 >= 0.7 时）
        if features.is_short_query and features.entity_count > 0:
            max_entity_score = max(e.score for e in ctx.entities)
            if max_entity_score >= 0.7:
                features.entity_specificity = max(features.entity_specificity, 0.8)

        # ── 时间敏感度 ──
        features.time_sensitivity = self._compute_time_sensitivity(q)

        # ── 领域识别（v2: 移到意图之前，辅助意图推断）──
        domains, domain_scores = self._detect_domains(q)
        features.domains = domains
        features.domain_scores = domain_scores

        # ── 查询结构识别（v2 新增）──
        structure_intent, structure_weight = self._detect_query_structure(q)
        features.sub_intent = structure_intent  # 细粒度意图

        # ── 意图识别（v2 重写：累加评分 + 多因子模型）──
        intent, confidence = self._detect_intent(
            q, features, structure_intent, structure_weight
        )
        features.intent = intent
        features.intent_confidence = confidence

        # ── 地域意图检测 ──
        region, region_conf = self._detect_region(q, ctx)
        features.target_region = region
        features.target_region_confidence = region_conf

        # ── 关键词数量（去停用词后的粗略估计）──
        features.keyword_count = max(1, len(q) // 3)  # 中文粗略估计

        # ── 情绪检测 ──
        features.has_sentiment, features.sentiment_direction = self._detect_sentiment(q_lower)

        logger.debug(
            "query_features: query=%r intent=%s(%.2f) sub=%s time=%.2f entity_spec=%.2f "
            "domains=%s ticker=%s",
            q[:40], intent, confidence, structure_intent, features.time_sensitivity,
            features.entity_specificity, domains, features.has_ticker,
        )
        return features

    def _compute_time_sensitivity(self, q: str) -> float:
        score, _ = _accumulate_signals(q, _TIME_SIGNALS)
        return min(1.0, score)

    # ── 查询结构识别（v2 新增）──
    def _detect_query_structure(self, q: str) -> tuple[str, float]:
        """识别查询结构模式。

        返回 (structure_name, weight)，weight 表示该模式的置信度。
        如果不匹配任何模式，返回 ("", 0.0)。
        """
        for pattern, intent, weight in _STRUCTURE_PATTERNS:
            if pattern.search(q):
                return intent, weight
        return "", 0.0

    # ── 领域检测 ──
    def _detect_domains(self, q: str) -> tuple[list[str], dict[str, float]]:
        """检测查询涉及的领域。返回 (domain_list, domain_scores)。"""
        scores: dict[str, float] = {}
        for domain, signals in _DOMAIN_SIGNALS.items():
            domain_score, hit_count = _accumulate_signals(q, signals)
            if domain_score > 0:
                scores[domain] = min(1.0, domain_score)

        # 按分数降序排列
        sorted_domains = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)
        return sorted_domains, scores

    # ── 地域意图检测 ──
    @staticmethod
    def _detect_region(q: str, ctx: SearchContext | None) -> tuple[str | None, float]:
        """三阶段地域意图检测。

        返回 (region_code, confidence) 或 (None, 0) 表示无法判断。
        region_code: "cn" | "hk" | "us" | "eu" | "uk" | "jp" | None
        """
        scores: dict[str, float] = {}

        # 阶段1: Ticker exchange suffix（极高确定性）
        if ctx and ctx.entities:
            ticker_regions = {}
            for e in ctx.entities:
                if e.ticker:
                    suffix = e.ticker.split(".")[-1].upper() if "." in e.ticker else ""
                    if suffix in ("SH", "SZ"):
                        ticker_regions["cn"] = ticker_regions.get("cn", 0) + 0.95
                    elif suffix == "HK":
                        ticker_regions["hk"] = ticker_regions.get("hk", 0) + 0.95
                    elif suffix in ("N", "O", "NASDAQ", "NYSE"):
                        ticker_regions["us"] = ticker_regions.get("us", 0) + 0.95
            if ticker_regions:
                best_region = max(ticker_regions, key=ticker_regions.get)
                scores[best_region] = min(1.0, ticker_regions[best_region])

        # 阶段2: 地域关键词
        for signal, (region, weight) in _REGION_SIGNALS.items():
            if signal in q:
                scores[region] = max(scores.get(region, 0), weight)

        # 阶段3: 实体类型推断（低确定性，仅在没有更强信号时使用）
        if not scores and ctx and ctx.entities:
            cn_count = 0
            for e in ctx.entities[:5]:
                if e.entity_type in ("company", "stock") and e.ticker and any(
                    e.ticker.endswith(s) for s in (".SH", ".SZ")
                ):
                    cn_count += 1
            if cn_count >= 2:
                scores["cn"] = 0.55  # 弱信号

        if not scores:
            return None, 0.0

        best_region = max(scores, key=scores.get)
        best_score = scores[best_region]

        # 置信度门控：低于 0.5 不返回
        if best_score < 0.5:
            return None, 0.0

        return best_region, round(best_score, 2)

    # ── 意图识别（v2 重写）──
    def _detect_intent(
        self,
        q: str,
        features: QueryFeatures,
        structure_intent: str = "",
        structure_weight: float = 0.0,
    ) -> tuple[str, float]:
        """多因子意图检测。

        4 轴加权：
        - 关键词信号得分 (40%)
        - 结构模式得分 (30%)
        - 领域一致性加成 (20%)
        - 元启发式 (10%): 长度、时间敏感度、实体精确度

        返回 (intent, confidence)。
        """
        # ── 先检查特殊意图: entity_lookup ──
        if features.has_ticker or (features.is_short_query and features.entity_count > 0
                                    and features.entity_specificity >= 0.8):
            return "entity_lookup", 0.95

        # ── 轴 1: 关键词信号累加评分 ──
        kw_scores: dict[str, float] = {}
        for intent, signals in _INTENT_SIGNALS.items():
            if intent == "entity_lookup":
                continue
            score, hit_count = _accumulate_signals(
                q, signals, negate_check=self._signal_matches
            )
            if score > 0:
                kw_scores[intent] = min(1.0, score)

        best_kw_intent = max(kw_scores, key=lambda k: kw_scores[k]) if kw_scores else ""
        best_kw_score = kw_scores.get(best_kw_intent, 0.0)

        # ── 轴 2: 结构 (30%) —— 仅在关键词无强信号时启用 ──
        structure_score: dict[str, float] = {}
        if structure_intent and best_kw_score < 0.70:
            structure_score[structure_intent] = structure_weight

        # ── 候选意图综合评分 ──
        candidates: dict[str, float] = {}
        all_intents = {"news", "analysis", "strategy", "concept", "market_data"}
        active_intents = set(kw_scores.keys()) | set(structure_score.keys())

        for intent in all_intents:
            score = 0.0

            # 轴 1: 关键词 (40%)
            kw = kw_scores.get(intent, 0.0)
            score += 0.40 * kw

            # 轴 2: 结构 (30%)
            st = structure_score.get(intent, 0.0)
            score += 0.30 * st

            # 轴 3: 领域一致性 (20%)
            domain_boost = 0.0
            if intent == "analysis":
                if any(d in features.domains for d in ("financial_report", "tech", "supply_chain")):
                    domain_boost = 0.7
                # No boost for macro-only or other domains — don't bias pure-macro queries
            elif intent == "news" and "market_event" in features.domains:
                domain_boost = 0.8
            elif intent == "strategy" and "strategy_domain" in features.domains:
                domain_boost = 0.8
            elif intent == "concept" and "knowledge_graph" in features.domains:
                domain_boost = 0.8
            elif intent == "market_data" and "market_data" in features.domains:
                domain_boost = 0.8
            score += 0.20 * domain_boost

            # 轴 4: 元启发式 (10%)
            meta = 0.0
            if intent == "news" and features.time_sensitivity >= 0.5:
                meta = 0.6
            elif intent == "entity_lookup" and features.entity_specificity >= 0.7:
                meta = 0.7
            elif intent == "analysis" and features.is_long_query:
                meta = 0.5
            elif intent == "strategy" and features.is_long_query:
                meta = 0.3
            score += 0.10 * meta

            candidates[intent] = score

        # ── 选取最高分意图 ──
        best_intent = max(candidates, key=lambda k: candidates[k])
        best_score = candidates[best_intent]

        # ── 置信度映射 ──
        # 无关键词信号 → 仅对极强领域信号做轻度推断（严格要求 ≥0.90）
        if best_kw_score < 0.1:
            if best_score >= 0.20:
                confidence = best_score
            elif features.domains and features.domain_scores.get(features.domains[0], 0) >= 0.90:
                top_domain = features.domains[0]
                if top_domain == "knowledge_graph":
                    return "concept", 0.30
                if top_domain == "strategy_domain":
                    return "strategy", 0.30
                if top_domain == "market_event":
                    return "news", 0.30
                if best_score > 0.10:
                    confidence = 0.25
                else:
                    return "general", 0.2
            elif features.time_sensitivity >= 0.7:
                return "news", 0.35
            elif features.entity_specificity >= 0.7:
                return "entity_lookup", 0.3
            else:
                return "general", 0.2

        if best_score >= 0.55:
            confidence = min(0.95, best_score + 0.15)
        elif best_score >= 0.25:
            confidence = best_score + 0.1
        else:
            confidence = 0.2

        logger.debug(
            "intent_detect: candidates=%s best=%s(%.3f) kw=%s(%.2f) struct=%s(%.2f)",
            {k: round(v, 3) for k, v in candidates.items()},
            best_intent, best_score,
            best_kw_intent, best_kw_score,
            structure_intent, structure_weight,
        )

        return best_intent, confidence

    def _detect_sentiment(self, q_lower: str) -> tuple[bool, str]:
        bull = sum(1 for w in _BULLISH_WORDS if w in q_lower)
        bear = sum(1 for w in _BEARISH_WORDS if w in q_lower)
        if bull > bear and bull >= 1:
            return True, "bullish"
        if bear > bull and bear >= 1:
            return True, "bearish"
        return False, "neutral"


# ──────────────────────────────────────────────────────────────────────
# 3. 权重计算模型
# ──────────────────────────────────────────────────────────────────────

# 基准权重 (与 FusionService._DEFAULT_WEIGHTS 对齐)
_BASE_WEIGHTS = {
    "bm25": 1.2,
    "vector": 1.0,
    "name_match": 2.5,
    "structural": 0.2,
    "time_decay": 0.25,
    "position": 0.12,
}

# Per-channel clamp bounds relative to base, applied BEFORE normalization.
#  - min: base * (1 + min_ratio)  →  base * 0.5
#  - max: base * (1 + max_ratio)  →  base * 3.0
_WEIGHT_CLAMP_MIN = -0.5
_WEIGHT_CLAMP_MAX = 2.0

# ── Query-condition profiles (replace cascading multiplication) ──

# Slot 1: query-length profile
_SHORT_QUERY_PROFILE = {
    "bm25": 1.20, "vector": 0.80, "name_match": 1.30, "position": 1.15,
}
_LONG_QUERY_PROFILE = {
    "bm25": 0.90, "vector": 1.20,
}

# Slot 2: entity-specificity profile
_HIGH_SPECIFICITY_PROFILE = {
    "name_match": 1.50, "bm25": 1.10,
}
_TICKER_PROFILE = {
    "name_match": 3.0, "bm25": 0.50, "vector": 0.30,
}

# Slot 3: time-sensitivity profile
_TIME_SENSITIVE_PROFILE = {
    "time_decay": 1.80,
}

# ── Intent multipliers ──
_INTENT_MULTIPLIERS: dict[str, dict[str, float]] = {
    "entity_lookup": {
        "bm25": 0.6, "vector": 0.5, "name_match": 2.0,
        "structural": 0.5, "time_decay": 0.3, "position": 0.5,
    },
    "news": {
        "bm25": 1.5, "vector": 0.8, "name_match": 1.0,
        "structural": 0.6, "time_decay": 2.5, "position": 1.8,
    },
    "analysis": {
        "bm25": 1.0, "vector": 1.5, "name_match": 0.8,
        "structural": 2.0, "time_decay": 0.6, "position": 1.5,
    },
    "strategy": {
        "bm25": 1.2, "vector": 1.2, "name_match": 0.7,
        "structural": 1.0, "time_decay": 0.5, "position": 1.2,
    },
    "concept": {
        "bm25": 0.7, "vector": 1.8, "name_match": 0.6,
        "structural": 1.5, "time_decay": 0.4, "position": 0.6,
    },
    "market_data": {
        "bm25": 1.4, "vector": 0.7, "name_match": 0.8,
        "structural": 0.5, "time_decay": 2.0, "position": 1.0,
    },
    "general": {
        "bm25": 1.0, "vector": 1.0, "name_match": 1.0,
        "structural": 1.0, "time_decay": 1.0, "position": 1.0,
    },
}

# ── Domain multipliers ──
_DOMAIN_MULTIPLIERS: dict[str, dict[str, float]] = {
    "financial_report": {
        "bm25": 1.2, "vector": 0.9, "structural": 1.3, "position": 1.2,
    },
    "macro": {
        "bm25": 1.1, "vector": 1.1,
    },
    "market_event": {
        "bm25": 1.2, "time_decay": 1.5,
    },
    "strategy_domain": {
        "bm25": 1.1, "vector": 1.1, "position": 1.1,
    },
    "supply_chain": {
        "vector": 1.2,
    },
    "knowledge_graph": {
        "bm25": 1.5, "name_match": 2.0, "structural": 1.8, "position": 0.7,
    },
}

# ── Per-table weights for RRF fusion ──
_INTENT_TABLE_WEIGHTS: dict[str, dict[str, float]] = {
    "concept": {
        "nodes": 1.4,
        "raw_information": 0.85,
        "analyses": 0.90,
        "feedbacks": 0.85,
    },
    "analysis": {
        "analyses": 1.10,
        "raw_information": 0.95,
    },
    "strategy": {
        "feedbacks": 1.3,
        "raw_information": 0.85,
    },
    "news": {
        "raw_information": 1.2,
    },
    "market_data": {
        "raw_information": 1.25,
        "analyses": 0.85,
        "nodes": 0.85,
    },
    # entity_lookup / general: all 1.0 (no table preference; implicit default)
}


class WeightCalculator:
    """根据 QueryFeatures 计算动态 fusion 权重。

    权重调整分四步，每步都是 single-pass 乘法（不叠加）：
    1. Intent: confidence 插值后的意图乘数
    2. Domain: 每通道取最强领域乘数（非连乘）
    3. Condition profiles: 短/长查询、高特异性/ticker、时间敏感 ——
       每通道最多应用一个 profile
    4. Clamp + 归一化
    """

    def __init__(
        self,
        base_weights: dict | None = None,
        intent_multipliers: dict | None = None,
        domain_multipliers: dict | None = None,
    ):
        self.base_weights = base_weights or _BASE_WEIGHTS
        self.intent_multipliers = intent_multipliers or _INTENT_MULTIPLIERS
        self.domain_multipliers = domain_multipliers or _DOMAIN_MULTIPLIERS

    def calculate(self, features: QueryFeatures) -> dict[str, float]:
        weights = dict(self.base_weights)

        # ── Step 1: Intent 乘数（confidence 插值）──
        intent_mult = self.intent_multipliers.get(
            features.intent, self.intent_multipliers["general"]
        )
        for key in weights:
            m = intent_mult.get(key, 1.0)
            if m != 1.0:
                adjusted_m = 1.0 + features.intent_confidence * (m - 1.0)
                weights[key] *= adjusted_m

        # ── Step 2: Domain 乘数 —— 每通道取 max，不连乘 ──
        if features.domains:
            for key in list(weights):
                best_mult = 1.0
                for domain in features.domains[:3]:
                    domain_mult = self.domain_multipliers.get(domain, {})
                    mult = domain_mult.get(key)
                    if mult is None:
                        continue
                    strength = features.domain_scores.get(domain, 0.0)
                    adjusted = 1.0 + strength * (mult - 1.0)
                    # For a boosting mult (>1) pick the largest; for a
                    # demoting mult (<1) pick the smallest.
                    if adjusted > 1.0:
                        best_mult = max(best_mult, adjusted)
                    else:
                        best_mult = min(best_mult, adjusted)
                if best_mult != 1.0:
                    weights[key] *= best_mult

        # ── Step 3: Condition profiles —— 每通道最多应用一个 ──
        profile: dict[str, float] = {}

        # 3a. Query-length profile
        if features.is_short_query and not features.has_ticker:
            for k, v in _SHORT_QUERY_PROFILE.items():
                profile[k] = v
        elif features.is_long_query:
            for k, v in _LONG_QUERY_PROFILE.items():
                profile[k] = v

        # 3b. Entity-specificity / ticker (stronger overwrites length)
        if features.has_ticker:
            for k, v in _TICKER_PROFILE.items():
                profile[k] = v  # ticker profile dominates
        elif features.entity_specificity > 0.6:
            for k, v in _HIGH_SPECIFICITY_PROFILE.items():
                if k not in profile:
                    profile[k] = v

        # 3c. Time sensitivity
        if features.time_sensitivity > 0.3:
            for k, v in _TIME_SENSITIVE_PROFILE.items():
                if k not in profile:
                    profile[k] = v

        for key, mult in profile.items():
            if key in weights:
                weights[key] *= mult

        # ── Step 4a: Clamp per-channel ──
        for key in weights:
            base = self.base_weights.get(key, 1.0)
            floor = base * (1.0 + _WEIGHT_CLAMP_MIN)
            ceiling = base * (1.0 + _WEIGHT_CLAMP_MAX)
            weights[key] = max(floor, min(ceiling, weights[key]))

        # ── Step 4b: 归一化 ──
        base_sum = sum(self.base_weights.values())
        current_sum = sum(weights.values())
        if current_sum > 0:
            scale = base_sum / current_sum
            for key in weights:
                weights[key] = round(weights[key] * scale, 4)

        # ── Step 5: Per-table weights (intent-driven table boosting) ──
        table_weights = _INTENT_TABLE_WEIGHTS.get(
            features.intent, _INTENT_TABLE_WEIGHTS.get("general", {})
        )
        if features.intent_confidence < 1.0 and table_weights:
            interpolated: dict[str, float] = {}
            for tbl, mult in table_weights.items():
                interpolated[tbl] = 1.0 + features.intent_confidence * (mult - 1.0)
            table_weights = interpolated
        weights["table_weights"] = table_weights

        logger.debug(
            "dynamic_weights: intent=%s(%.2f) domains=%s profile_keys=%s → %s",
            features.intent, features.intent_confidence,
            features.domains[:2], list(profile.keys()), weights,
        )
        return weights


# ──────────────────────────────────────────────────────────────────────
# 4. 便捷入口
# ──────────────────────────────────────────────────────────────────────

_extractor = QueryFeatureExtractor()
_calculator = WeightCalculator()


def compute_dynamic_weights(
    query_text: str,
    ctx: SearchContext | None = None,
) -> tuple[dict[str, float], QueryFeatures]:
    """一站式入口: 提取特征 → 计算权重。

    Returns:
        (weights, features) — 权重字典和特征对象（供日志/调试用）
    """
    features = _extractor.extract(query_text, ctx)
    weights = _calculator.calculate(features)
    return weights, features
