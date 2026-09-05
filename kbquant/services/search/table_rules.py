"""搜索表决策规则 —— 关键词信号→表的单一事实来源。

设计原则：
- 关键词只定义一次，不分散在 query_rewriter / dynamic_weights 等多处
- 每种关键词信号声明 target_table 和 match_type（exact / partial）
- 意图→表的增量也是预声明规则，不靠 ad-hoc if/elif
- 返回 TableSelection 对象，包含决策原因，可日志可调试
"""
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kbquant.models.search_candidate import EntityResult

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# 维度 1：主实体类型 → 基础表
# ═══════════════════════════════════════════════════════════════════════

BASE_TABLES: dict[str | None, list[str]] = {
    # 公司/企业 → 三表全覆盖 (资讯+分析+知识图谱)
    "company":         ["raw_information", "analyses", "nodes"],
    "tech_company":    ["raw_information", "analyses"],
    # 人物 → 也搜知识图谱 (人物与公司的关联)
    "person":          ["raw_information", "analyses"],
    # 行业/板块/概念 → 三表
    "sector":          ["raw_information", "analyses", "nodes"],
    "concept":         ["raw_information", "analyses", "nodes"],
    # 策略 → 复盘反馈很重要
    "strategy":        ["feedbacks"],
    # 指数/品种 → 资讯 + 分析 + 图谱
    "index":           ["raw_information", "analyses"],
    "commodity":       ["raw_information", "analyses", "nodes"],
    # 货币 → 资讯 + 分析
    "currency":        ["raw_information", "analyses"],
    # 指标 → 分析
    "indicator":       ["raw_information", "analyses","feedbacks"],
    # 事件/博弈/灾害/疫情 → 资讯 + 分析
    "event":               ["raw_information", "analyses"],
    "geopolitical_event":  ["raw_information", "analyses"],
    "natural_disaster":    ["raw_information", "analyses"],
    "epidemic":            ["raw_information", "analyses","nodes"],
    # 政策/法规/行业规则 → 资讯 + 分析
    "policy":         ["raw_information", "analyses", "nodes"],
    "regulation":     ["raw_information", "analyses"],
    "industry_rule":  ["raw_information", "analyses"],
    # 机构/央行/科研 → 资讯 + 分析 + 图谱
    "institution":          ["raw_information", "analyses"],
    "central_bank":         ["raw_information", "analyses", "nodes"],
    "research_institution": ["raw_information", "analyses"],
    # 关键技术 → 资讯 + 分析 + 图谱
    "key_technology":   ["raw_information", "analyses", "nodes"],
    # 产品 → 资讯 + 分析
    "product":          ["raw_information", "analyses"],
    # 地区 → 资讯 + 分析
    "region":           ["raw_information", "analyses"],
    # 半导体术语 → 资讯 + 分析 + 图谱
    "semiconductor_term": ["analyses", "nodes", "raw_information"],
    # 无实体 → 资讯 + 分析兜底
    None:              ["raw_information", "analyses"],
}

# ═══════════════════════════════════════════════════════════════════════
# 维度 2：关键词信号 → 追加表  （单一事实来源）
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class KeywordSignal:
    """一条关键词→表的决策规则。"""
    name: str                          # 信号名称（可读、可日志）
    keywords: frozenset[str]           # 触发关键词集合
    add_tables: tuple[str, ...]        # 追加哪些表
    match_type: str = "any"            # "any" = 子串命中; "exact" = 完整 token 匹配

# 每条规则只声明一次，按多维度分组（便于理解和维护）
KEYWORD_SIGNALS: list[KeywordSignal] = [
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2a：基本面 / 财报 → analyses + raw_information
    #        财报数据同时产生大量原始资讯，不应只召回分析报告。
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="financial_report",
        keywords=frozenset({
            # 报告类型
            "财报", "季报", "年报", "半年报", "业绩报告", "财务报表",
            "业绩预告", "业绩快报",
            # 收入 / 利润
            "业绩", "营收", "营业收入", "营业额", "销售收入",
            "利润", "净利润", "归母净利润", "盈利", "毛利", "净利",
            "毛利率", "净利率", "利润率",
            # 核心指标
            "ROE", "净资产收益率", "EPS", "每股收益", "每股盈利",
            "出货量", "销量", "发货量", "交付量",
            "收入", "成本", "费用", "现金流", "经营现金流", "自由现金流",
            "负债", "资产负债率", "杠杆",
            # 增长 / 趋势
            "增速", "增长", "增幅", "同比增长", "环比增长",
            "同比", "环比",
        }),
        add_tables=("analyses", "raw_information"),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2b：投资逻辑 / 深度研究 → analyses + nodes
    #        (不仅需要分析报告，还需要知识图谱节点理解业务)
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="long_term_logic",
        keywords=frozenset({
            # 投资方法论
            "投资逻辑", "核心逻辑", "投资主线", "核心驱动力", "主要矛盾",
            "长期趋势", "中长期", "长线",
            # 评估体系
            "估值", "市盈率", "PE", "市净率", "PB", "估值水平", "定价",
            "估值修复", "估值重估", "价值回归",
            # 竞争力分析
            "护城河", "竞争壁垒", "核心优势", "竞争格局", "壁垒",
            "基本面", "公司质地", "内在价值", "财务健康",
            "成长性", "增长空间", "成长空间", "增量空间",
            "展望", "前景", "预期",
            # 风险
            "风险", "隐患", "不确定性", "潜在风险", "风险点", "隐忧",
        }),
        add_tables=("analyses", "nodes"),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2c：策略复盘 → feedbacks (交易经验总结)
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="strategy_review",
        keywords=frozenset({
            # 打板相关
            "打板", "涨停板战法", "追涨停", "打板策略", "涨停板买入",
            "炸板", "开板", "破板", "涨停打开",
            "封板", "一字板", "涨停板",
            "天地板", "闷杀",
            # 龙头 / 妖股
            "龙头", "龙头股", "板块龙头", "领涨股", "标杆股",
            "妖股", "庄股", "异动股",
            # 止损 / 止盈
            "止损", "止损线", "止损点", "止损位", "风控止损", "斩仓",
            "止盈", "止盈点", "止盈位", "获利了结",
            # 仓位 / 回撤
            "仓位", "持仓", "仓位管理", "仓位控制",
            "回撤", "回吐", "调整", "洗盘",
            # 复盘 / 教训
            "复盘", "策略复盘", "交易复盘", "操作回顾", "经验总结",
            "失败经验", "教训", "亏损经验", "交易失误", "踩坑",
            "策略总结", "操作总结","案例","经验","总结"
            # 技术破位 / 竞价
            "破位", "跌破支撑", "破支撑位", "技术破位",
            "竞价", "开盘不及预期", "不及预期",
        }),
        add_tables=("feedbacks",),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2d：市场异动 / 即时资讯 → raw_information
    #        (确保原始资讯索引被覆盖，即使 entity type 已包含也要标记)
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="market_event",
        keywords=frozenset({
            # 价格异动
            "异动", "异动公告", "停牌", "复牌",
            # 涨跌
            "涨停", "跌停", "涨停板", "跌停板",
            "大涨", "大跌", "暴涨", "暴跌", "跳水",
            "上涨", "下跌", "拉升", "走高", "走低", "下行",
            "反弹", "回升", "回调", "冲高", "杀跌",
            # 量能
            "放量", "缩量", "成交额", "换手率", "量能",
            # 突发 / 公告
            "突发", "公告", "新闻", "最新", "速递",
            "披露", "报道",
            # 资金
            "资金", "主力资金", "北向资金", "北向", "外资",
            "机构资金", "游资", "主力",
        }),
        add_tables=("raw_information",),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2e：产业链 / 上下游 → nodes (知识图谱)
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="supply_chain",
        keywords=frozenset({
            # 产业链
            "产业链", "上下游", "供应链", "供应商", "客户", "生态链",
            # 影响 / 传导
            "影响", "利空", "利好", "受益", "受损", "传导",
            "溢出效应", "扩散", "传递", "带动",
            "正面影响", "积极影响", "负面影响",
            "催化剂", "正面消息", "积极因素", "负面消息", "风险因素",
        }),
        add_tables=("nodes",),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2f：宏观 / 政策 → analyses + raw_information
    #        宏观政策变动会产生大量即时资讯 (raw_information)，
    #        不应只召回分析报告。
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="macro",
        keywords=frozenset({
            # 央行 / 货币政策
            "美联储", "央行", "加息", "降息", "利率上调", "利率下调",
            "升息", "紧缩", "宽松", "货币政策", "财政政策",
            "降准", "存款准备金率", "准备金率",
            # 关键指标
            "CPI", "GDP", "PMI", "通胀", "通货膨胀", "通缩",
            "采购经理指数", "经济增速", "经济增长",
            # 利率 / 汇率 / 国债
            "利率", "汇率", "国债", "收益率",
            "流动性", "资金面", "货币环境",
        }),
        add_tables=("analyses", "raw_information"),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2g：行业 / 概念 → nodes + analyses
    #        (需要知识图谱了解行业结构 + 分析报告了解行业逻辑)
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="industry_analysis",
        keywords=frozenset({
            "行业", "板块", "赛道", "概念", "主题",
            "行业分析", "行业研究", "行业逻辑", "行业趋势",
            "市场空间", "渗透率", "国产替代", "进口替代",
        }),
        add_tables=("analyses", "nodes"),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2h：技术分析 → analyses
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="technical_analysis",
        keywords=frozenset({
            # 技术指标
            "MACD", "KDJ", "RSI", "均线", "移动平均线", "趋势线",
            # 信号
            "金叉", "死叉", "背离", "顶背离", "底背离",
            # 形态
            "突破", "向上突破", "放量突破", "有效突破",
            "支撑", "压力", "阻力",
        }),
        add_tables=("analyses",),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2i：情绪 / 方向 → analyses + raw_information
    #        市场情绪变化通常体现在即时资讯中，不应只查分析报告。
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="sentiment",
        keywords=frozenset({
            "恐慌", "悲观", "乐观", "恐惧", "信心",
            "看多", "看空", "做多", "做空",
            "牛市", "熊市", "多头", "空头",
            "抛售", "踩踏", "爆仓",
        }),
        add_tables=("analyses", "raw_information"),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2j：新闻 → raw_information (兜底)
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="news_signal",
        keywords=frozenset({
            "今天", "今日", "昨天", "昨日", "刚刚",
            "本周", "本月", "今年",
            "最新", "最近", "近期", "近日",
        }),
        add_tables=("raw_information",),
    ),
    # ═══════════════════════════════════════════════════════════════════
    # 维度 2k：知识图谱 / 节点 → nodes
    # ═══════════════════════════════════════════════════════════════════
    KeywordSignal(
        name="knowledge_graph",
        keywords=frozenset({
            # 英文
            "worldnode", "worldnodes", "world_node", "world node",
            "node", "nodes",
            # 中文
            "世界节点", "知识图谱", "图谱", "节点", "关系图谱",
            "知识节点", "实体节点", "实体关系",
        }),
        add_tables=("nodes",),
    ),
]

# ═══════════════════════════════════════════════════════════════════════
# 维度 3：语义意图 → 追加表  （预声明，不做 ad-hoc if/elif）
# ═══════════════════════════════════════════════════════════════════════

# 哪些意图会自动追加哪些表（不管关键词是否命中）
INTENT_TABLE_AUGMENT: dict[str, tuple[str, ...]] = {
    "strategy":       ("feedbacks",),
    "analysis":       ("analyses", "raw_information"),
    "concept":        ("nodes",),
    "entity_lookup":  ("nodes",),
    "news":           ("raw_information",),
    "market_data":    ("raw_information",),
}

# ═══════════════════════════════════════════════════════════════════════
# 决策数据结构
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TableSelection:
    """一次表选择决策的结果，包含可审计的决策原因。"""
    tables: list[str]
    # 决策原因（用于日志和调试）
    base_from: str              # 主实体类型或 "none"
    keyword_signals: list[str]  # 命中的关键词信号名称
    intent_augment: str | None  # 意图追加（如果有）
    tables_by_dimension: dict[str, list[str]] = field(default_factory=dict)

    def __repr__(self) -> str:
        parts = [f"tables={self.tables}", f"base={self.base_from}"]
        if self.keyword_signals:
            parts.append(f"kw={self.keyword_signals}")
        if self.intent_augment:
            parts.append(f"intent={self.intent_augment}")
        return f"TableSelection({', '.join(parts)})"


def _resolve_main_entity_type(entities: list | None) -> str | None:
    """从实体列表中提取主实体类型。优先 stock/company。"""
    if not entities:
        return None
    for e in entities:
        et = e.entity_type if hasattr(e, "entity_type") else str(e)
        if et in ("stock", "company"):
            return et
    # 取第一个在 BASE_TABLES 中有映射的类型
    if hasattr(entities[0], "entity_type"):
        et0 = entities[0].entity_type
        if et0 in BASE_TABLES:
            return et0
    return None


def _match_keyword_signals(
    query_keywords: set[str],
    raw_query: str = "",
) -> tuple[set[str], list[str]]:
    """遍历 KEYWORD_SIGNALS，返回 (追加表集合, 命中信号名列表)。

    匹配策略: 先检查关键词是否为 query_keywords 中某个 token 的子串
    (支持 "年财报分析" 匹配 "财报")，再检查是否为原始查询文本的子串
    (兜底，当 expanded_keywords 未初始化时 "query.split()" 只产生一个 token)。
    """
    extra_tables: set[str] = set()
    hit_signals: list[str] = []
    query_lower = raw_query.lower()
    for sig in KEYWORD_SIGNALS:
        matched = False
        for kw in sig.keywords:
            kw_lower = kw.lower()
            if any(kw_lower in t.lower() for t in query_keywords):
                matched = True
                break
            if query_lower and kw_lower in query_lower:
                matched = True
                break
        if matched:
            hit_signals.append(sig.name)
            for t in sig.add_tables:
                extra_tables.add(t)
    return extra_tables, hit_signals


def select_tables(
    entities: list | None = None,
    query_keywords: set[str] | None = None,
    intent: str | None = None,
    raw_query: str = "",
) -> TableSelection:
    """三合一表选择：实体类型 + 关键词信号 + 语义意图。

    Args:
        entities: 阶段 1 的实体列表（EntityResult 或兼容对象）
        query_keywords: 阶段 2 的 expanded_keywords（或原始查询词集）
        intent: 阶段 2.5 的意图分类（"news"/"analysis"/"strategy"/"concept"/...）
        raw_query: 原始查询文本（用于关键词信号子串匹配兜底）

    Returns:
        TableSelection 包含最终表列表和决策原因
    """
    if query_keywords is None:
        query_keywords = set()

    # 维度 1：主实体类型 → 基础表
    main_type = _resolve_main_entity_type(entities)
    tables = set(BASE_TABLES.get(main_type, BASE_TABLES[None]))

    # 维度 2：关键词信号 → 追加表
    extra_kw, kw_signals = _match_keyword_signals(query_keywords, raw_query=raw_query)
    tables.update(extra_kw)

    # 维度 3：语义意图 → 追加表
    intent_sig: str | None = None
    if intent and intent in INTENT_TABLE_AUGMENT:
        aug_tables = INTENT_TABLE_AUGMENT[intent]
        tables.update(aug_tables)
        intent_sig = intent

    result = list(tables)

    return TableSelection(
        tables=result,
        base_from=main_type or "none",
        keyword_signals=kw_signals,
        intent_augment=intent_sig,
    )


# ═══════════════════════════════════════════════════════════════════════
# 向后兼容的便捷函数
# ═══════════════════════════════════════════════════════════════════════

def determine_target_tables(
    main_entity_type: str | None,
    query_keywords: set[str],
) -> list[str]:
    """向后兼容的便捷函数 —— 只做实体类型 + 关键词两个维度。"""
    sel = select_tables(
        entities=(
            [type("_E", (), {"entity_type": main_entity_type})()]  # noqa: F821
            if main_entity_type else None
        ),
        query_keywords=query_keywords,
        intent=None,
    )
    return sel.tables
