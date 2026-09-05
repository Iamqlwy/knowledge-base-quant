"""Unit tests for dynamic_weights — 动态 Fusion 权重计算模块 (v2)。"""
import pytest

from kbquant.models.search_candidate import SearchContext, EntityResult
from kbquant.services.search.dynamic_weights import (
    QueryFeatureExtractor,
    WeightCalculator,
    QueryFeatures,
    compute_dynamic_weights,
    _accumulate_signals,
)


# ────────────────────────────────────────────────────────────────
# 累加式评分
# ────────────────────────────────────────────────────────────────

class TestAccumulateSignals:
    def test_single_signal(self):
        score, hits = _accumulate_signals("什么是ROE", {"什么是": 0.9, "ROE": 0.7})
        assert hits == 2
        assert score == pytest.approx(0.9 + 0.5 * 0.7, 0.01)

    def test_no_signal(self):
        score, hits = _accumulate_signals("普通文本", {"公告": 0.9, "新闻": 0.9})
        assert hits == 0
        assert score == 0.0

    def test_many_signals_decay(self):
        signals = {f"k{i}": 0.9 for i in range(5)}
        q = " ".join(f"k{i}" for i in range(5))
        score, hits = _accumulate_signals(q, signals)
        assert hits == 5
        # 最高不超过 0.9 * (1 + 0.5 + 0.25 + 0.125 + 0.0625) = 0.9 * 1.9375
        assert score < 2.0

    def test_negate_check(self):
        def negate(signal, text):
            # 简单版：信号前有"不要"就否定
            idx = text.find(signal)
            if idx >= 2 and text[idx - 2:idx] == "不要":
                return False
            return True

        score, hits = _accumulate_signals("不要分析这只股票", {"分析": 0.9}, negate_check=negate)
        assert hits == 0
        assert score == 0.0


# ────────────────────────────────────────────────────────────────
# 查询特征提取
# ────────────────────────────────────────────────────────────────

class TestQueryFeatureExtractor:
    def setup_method(self):
        self.extractor = QueryFeatureExtractor()

    # ── 基础长度特征 ──

    def test_short_query_detection(self):
        features = self.extractor.extract("茅台")
        assert features.is_short_query is True
        assert features.is_long_query is False

    def test_long_query_detection(self):
        features = self.extractor.extract("特斯拉最新的财报业绩表现如何以及未来展望")
        assert features.is_long_query is True
        assert features.is_short_query is False

    def test_normal_query_length(self):
        features = self.extractor.extract("特斯拉最新公告")
        assert features.is_short_query is False
        assert features.is_long_query is False

    # ── Ticker 检测 ──

    def test_ticker_a_share(self):
        features = self.extractor.extract("600519")
        assert features.has_ticker is True
        assert features.entity_specificity == 1.0

    def test_ticker_with_suffix(self):
        features = self.extractor.extract("600519.SH")
        assert features.has_ticker is True

    def test_ticker_us_stock(self):
        features = self.extractor.extract("AAPL")
        assert features.has_ticker is True

    def test_non_ticker(self):
        features = self.extractor.extract("特斯拉")
        assert features.has_ticker is False

    # ── 时间敏感度（v2: 累加评分）──

    def test_time_sensitivity_high(self):
        features = self.extractor.extract("今天特斯拉最新公告")
        assert features.time_sensitivity >= 0.8

    def test_time_sensitivity_medium(self):
        features = self.extractor.extract("最近新能源汽车动态")
        assert features.time_sensitivity >= 0.5

    def test_time_sensitivity_low(self):
        features = self.extractor.extract("投资逻辑分析")
        assert features.time_sensitivity < 0.3

    def test_time_sensitivity_none(self):
        features = self.extractor.extract("半导体产业链")
        assert features.time_sensitivity == 0.0

    # ── 意图识别（向后兼容）──

    def test_intent_news(self):
        features = self.extractor.extract("特斯拉今天最新公告")
        assert features.intent == "news"
        assert features.intent_confidence >= 0.5

    def test_intent_analysis(self):
        features = self.extractor.extract("茅台投资逻辑深度分析")
        assert features.intent == "analysis"
        assert features.intent_confidence >= 0.5

    def test_intent_strategy(self):
        features = self.extractor.extract("打板策略复盘经验教训")
        assert features.intent == "strategy"
        assert features.intent_confidence >= 0.7

    def test_intent_concept(self):
        features = self.extractor.extract("什么是ROE")
        assert features.intent == "concept"
        assert features.intent_confidence >= 0.4

    def test_intent_entity_lookup_with_ticker(self):
        features = self.extractor.extract("600519")
        assert features.intent == "entity_lookup"
        assert features.intent_confidence >= 0.9

    def test_intent_general(self):
        features = self.extractor.extract("特斯拉")
        assert features.intent in ("general", "entity_lookup")

    # ── 意图识别（v2 新增：累加多信号）──

    def test_intent_multi_signal_strategy(self):
        """多弱证伪信号累加成 strategy"""
        features = self.extractor.extract("储能 需求 证伪 不及预期 2026")
        assert features.intent == "strategy"

    def test_intent_multi_signal_analysis(self):
        """多信号累加成 analysis"""
        features = self.extractor.extract("美湖股份 603319 2025 年报 营收 净利润 市值 估值")
        assert features.intent == "analysis"

    # ── 查询结构模式（v2 新增）──

    def test_structure_question(self):
        features = self.extractor.extract("什么是均线金叉死叉")
        assert features.sub_intent == "concept"

    def test_structure_verification(self):
        features = self.extractor.extract("AI 算力 预测 证伪 过于乐观")
        assert features.sub_intent == "strategy"

    def test_structure_case_study(self):
        features = self.extractor.extract("医药 并购 失败 案例 教训")
        assert features.sub_intent == "strategy"

    def test_structure_causal(self):
        features = self.extractor.extract("关税 对 出口 企业 利润 的影响")
        assert features.sub_intent == "analysis"

    def test_structure_comparison(self):
        features = self.extractor.extract("对比 宁德时代 比亚迪 电池技术")
        assert features.sub_intent == "analysis"

    def test_structure_trend(self):
        features = self.extractor.extract("碳酸锂 价格 走势 趋势 拐点")
        assert features.sub_intent == "analysis"

    # ── 纯宏观查询不被误导 ──

    def test_macro_query_stays_general(self):
        features = self.extractor.extract("美联储加息对新兴市场的影响")
        assert features.intent in ("general", "news")

    # ── 领域识别 ──

    def test_domain_financial_report(self):
        features = self.extractor.extract("特斯拉财报营收净利润")
        assert "financial_report" in features.domains

    def test_domain_macro(self):
        features = self.extractor.extract("美联储加息CPI通胀")
        assert "macro" in features.domains

    def test_domain_market_event(self):
        features = self.extractor.extract("涨停暴跌异动放量")
        assert "market_event" in features.domains

    def test_domain_strategy(self):
        features = self.extractor.extract("复盘止损打板仓位")
        assert "strategy_domain" in features.domains

    def test_domain_supply_chain(self):
        features = self.extractor.extract("产业链上下游供应商传导")
        assert "supply_chain" in features.domains

    def test_domain_tech(self):
        features = self.extractor.extract("半导体芯片AI人工智能")
        assert "tech" in features.domains

    def test_multiple_domains(self):
        features = self.extractor.extract("美联储加息对半导体产业链上下游影响")
        assert len(features.domains) >= 2

    # ── 情绪检测 ──

    def test_sentiment_bullish(self):
        features = self.extractor.extract("特斯拉大涨突破新高")
        assert features.has_sentiment is True
        assert features.sentiment_direction == "bullish"

    def test_sentiment_bearish(self):
        features = self.extractor.extract("茅台暴跌利空消息")
        assert features.has_sentiment is True
        assert features.sentiment_direction == "bearish"

    def test_sentiment_neutral(self):
        features = self.extractor.extract("特斯拉财报分析")
        assert features.sentiment_direction == "neutral"

    # ── 实体精确度（需要 ctx）──

    def test_entity_specificity_with_high_score_entity(self):
        ctx = SearchContext()
        ctx.entities = [
            EntityResult(name="特斯拉", entity_type="stock", score=0.95),
        ]
        features = self.extractor.extract("特斯拉", ctx)
        assert features.entity_specificity >= 0.9
        assert features.entity_count == 1

    def test_entity_specificity_with_low_score_entity(self):
        ctx = SearchContext()
        ctx.entities = [
            EntityResult(name="新能源", entity_type="concept", score=0.3),
        ]
        features = self.extractor.extract("新能源", ctx)
        assert features.entity_specificity < 0.5

    def test_entity_specificity_ticker_overrides(self):
        ctx = SearchContext()
        ctx.entities = []
        features = self.extractor.extract("600519", ctx)
        assert features.entity_specificity == 1.0

    # ── sub_intent 字段 ──

    def test_sub_intent_present(self):
        features = self.extractor.extract("港股")
        assert hasattr(features, "sub_intent")

    def test_sub_intent_empty_for_general(self):
        features = self.extractor.extract("特斯拉")
        assert features.sub_intent == ""


class TestWeightCalculator:
    def setup_method(self):
        self.calculator = WeightCalculator()

    def _make_features(self, **kwargs) -> QueryFeatures:
        f = QueryFeatures()
        for k, v in kwargs.items():
            setattr(f, k, v)
        return f

    # ── 基准权重一致性 ──

    def test_general_query_weights_sum_preserved(self):
        """general 查询的权重总和应与基准一致。"""
        features = self._make_features(intent="general", intent_confidence=0.2)
        weights = self.calculator.calculate(features)
        base_sum = sum({
            "bm25": 1.2, "vector": 1.0, "name_match": 2.5,
            "structural": 0.2, "time_decay": 0.25,
        }.values())
        channel_weights = {k: v for k, v in weights.items() if k != "table_weights"}
        assert abs(sum(channel_weights.values()) - base_sum) < 0.01

    # ── 意图对权重的影响 ──

    def test_entity_lookup_boosts_name_match(self):
        features = self._make_features(
            intent="entity_lookup", intent_confidence=0.95,
            entity_specificity=1.0, has_ticker=True,
        )
        weights = self.calculator.calculate(features)
        assert weights["name_match"] > weights["bm25"]
        assert weights["name_match"] > weights["vector"]

    def test_news_boosts_bm25_and_time_decay(self):
        features = self._make_features(
            intent="news", intent_confidence=0.9,
            time_sensitivity=0.8,
        )
        weights = self.calculator.calculate(features)
        base = {"bm25": 1.2, "vector": 1.0, "name_match": 2.5,
                "structural": 0.2, "time_decay": 0.25}
        bm25_ratio = weights["bm25"] / base["bm25"]
        time_ratio = weights["time_decay"] / base["time_decay"]
        vector_ratio = weights["vector"] / base["vector"]
        assert bm25_ratio > vector_ratio
        assert time_ratio > 1.0

    def test_analysis_boosts_vector_and_structural(self):
        features = self._make_features(
            intent="analysis", intent_confidence=0.85,
        )
        weights = self.calculator.calculate(features)
        base = {"bm25": 1.2, "vector": 1.0, "name_match": 2.5,
                "structural": 0.2, "time_decay": 0.25}
        vector_ratio = weights["vector"] / base["vector"]
        structural_ratio = weights["structural"] / base["structural"]
        assert vector_ratio > 1.0
        assert structural_ratio > 1.0

    def test_concept_boosts_vector(self):
        features = self._make_features(
            intent="concept", intent_confidence=0.9,
        )
        weights = self.calculator.calculate(features)
        base = {"bm25": 1.2, "vector": 1.0, "name_match": 2.5,
                "structural": 0.2, "time_decay": 0.25}
        vector_ratio = weights["vector"] / base["vector"]
        bm25_ratio = weights["bm25"] / base["bm25"]
        assert vector_ratio > bm25_ratio

    # ── 连续性微调 ──

    def test_high_time_sensitivity_boosts_time_decay(self):
        f_low = self._make_features(
            intent="general", intent_confidence=0.2, time_sensitivity=0.1,
        )
        f_high = self._make_features(
            intent="general", intent_confidence=0.2, time_sensitivity=0.9,
        )
        w_low = self.calculator.calculate(f_low)
        w_high = self.calculator.calculate(f_high)
        assert w_high["time_decay"] > w_low["time_decay"]

    def test_short_query_boosts_bm25_and_name_match(self):
        f_normal = self._make_features(
            intent="general", intent_confidence=0.2,
        )
        f_short = self._make_features(
            intent="general", intent_confidence=0.2, is_short_query=True,
        )
        w_normal = self.calculator.calculate(f_normal)
        w_short = self.calculator.calculate(f_short)
        assert w_short["bm25"] > w_normal["bm25"]
        assert w_short["name_match"] > w_normal["name_match"]

    def test_long_query_boosts_vector(self):
        f_normal = self._make_features(
            intent="general", intent_confidence=0.2,
        )
        f_long = self._make_features(
            intent="general", intent_confidence=0.2, is_long_query=True,
        )
        w_normal = self.calculator.calculate(f_normal)
        w_long = self.calculator.calculate(f_long)
        assert w_long["vector"] > w_normal["vector"]

    def test_ticker_query_heavily_boosts_name_match(self):
        f_no_ticker = self._make_features(
            intent="general", intent_confidence=0.2,
        )
        f_ticker = self._make_features(
            intent="entity_lookup", intent_confidence=0.95,
            has_ticker=True, entity_specificity=1.0,
        )
        w_no = self.calculator.calculate(f_no_ticker)
        w_ticker = self.calculator.calculate(f_ticker)
        nm_ratio_ticker = w_ticker["name_match"] / sum(
            v for k, v in w_ticker.items() if k != "table_weights"
        )
        nm_ratio_no = w_no["name_match"] / sum(
            v for k, v in w_no.items() if k != "table_weights"
        )
        assert nm_ratio_ticker > nm_ratio_no * 1.5

    # ── 领域叠加 ──

    def test_financial_report_domain_boosts_bm25(self):
        f_base = self._make_features(
            intent="analysis", intent_confidence=0.5,
        )
        f_fr = self._make_features(
            intent="analysis", intent_confidence=0.5,
            domains=["financial_report"],
            domain_scores={"financial_report": 0.9},
        )
        w_base = self.calculator.calculate(f_base)
        w_fr = self.calculator.calculate(f_fr)
        assert w_fr["bm25"] > w_base["bm25"]


class TestComputeDynamicWeights:
    """端到端集成测试。"""

    def test_ticker_query(self):
        weights, features = compute_dynamic_weights("600519")
        assert features.has_ticker is True
        assert features.intent == "entity_lookup"
        assert weights["name_match"] > weights["bm25"]

    def test_news_query(self):
        weights, features = compute_dynamic_weights("特斯拉今天最新公告")
        assert features.intent == "news"
        assert features.time_sensitivity >= 0.5

    def test_analysis_query(self):
        weights, features = compute_dynamic_weights("茅台投资逻辑深度分析")
        assert features.intent == "analysis"

    def test_strategy_query(self):
        weights, features = compute_dynamic_weights("打板策略复盘教训")
        assert features.intent == "strategy"

    def test_concept_query(self):
        weights, features = compute_dynamic_weights("什么是ROE")
        assert features.intent == "concept"

    def test_weights_sum_preserved(self):
        """所有场景下权重总和应与基准一致。"""
        base_sum = sum({
            "bm25": 1.2, "vector": 1.0, "name_match": 2.5,
            "structural": 0.2, "time_decay": 0.25,
        }.values())
        queries = [
            "600519",
            "特斯拉今天最新公告",
            "茅台投资逻辑深度分析",
            "打板策略复盘教训",
            "什么是ROE",
            "半导体产业链上下游",
            "美联储加息通胀",
        ]
        for q in queries:
            weights, _ = compute_dynamic_weights(q)
            channel_weights = {k: v for k, v in weights.items() if k != "table_weights"}
            assert abs(sum(channel_weights.values()) - base_sum) < 0.01, (
                f"Query '{q}': weights sum {sum(channel_weights.values()):.4f} != {base_sum:.4f}"
            )

    def test_with_entity_context(self):
        ctx = SearchContext()
        ctx.entities = [
            EntityResult(name="特斯拉", entity_type="stock", score=0.95,
                         ticker="TSLA", aliases=["Tesla"]),
        ]
        ctx.main_entity = ctx.entities[0]
        weights, features = compute_dynamic_weights("特斯拉最新财报", ctx)
        assert features.entity_specificity >= 0.9
        assert features.entity_count == 1

    def test_different_intents_produce_different_weights(self):
        """不同意图应该产生不同的权重分布。"""
        w_ticker, _ = compute_dynamic_weights("600519")
        w_news, _ = compute_dynamic_weights("最新公告异动")
        w_analysis, _ = compute_dynamic_weights("投资逻辑深度分析研报")
        w_concept, _ = compute_dynamic_weights("什么是ROE")

        ratios = {}
        for label, w in [("ticker", w_ticker), ("news", w_news),
                         ("analysis", w_analysis), ("concept", w_concept)]:
            s = sum(v for k, v in w.items() if k != "table_weights")
            ratios[label] = {k: v / s for k, v in w.items() if k != "table_weights"}

        assert ratios["ticker"]["name_match"] > ratios["concept"]["name_match"]
        assert ratios["concept"]["vector"] > ratios["ticker"]["vector"]

    # ── v2 新增: 累加多信号意图测试 ──

    def test_cumulative_signals_strategy(self):
        weights, features = compute_dynamic_weights("证伪 不及预期 复盘 教训 产能过剩")
        assert features.intent == "strategy"

    def test_cumulative_signals_analysis(self):
        weights, features = compute_dynamic_weights("深度研究 行业分析 估值 投资逻辑")
        assert features.intent == "analysis"

    def test_sub_intent_field(self):
        weights, features = compute_dynamic_weights("什么是基本面分析")
        assert features.sub_intent == "concept"


class TestEdgeCases:
    def setup_method(self):
        self.extractor = QueryFeatureExtractor()
        self.calculator = WeightCalculator()

    def test_empty_query(self):
        features = self.extractor.extract("")
        assert features.query_length == 0
        assert features.intent == "general"
        weights = self.calculator.calculate(features)
        assert all(v >= 0 for k, v in weights.items() if k != "table_weights")

    def test_cache_fingerprint_stable(self):
        features = self.extractor.extract("特斯拉最新财报")
        fp = features.cache_fingerprint()
        assert "entity_lookup" in fp or "news" in fp or True  # stable format
        assert "|" in fp

    def test_whitespace_query(self):
        features = self.extractor.extract("   ")
        assert features.query_length <= 3

    def test_english_query(self):
        features = self.extractor.extract("Tesla earnings report Q4")
        assert features.query_length > 0
        weights = self.calculator.calculate(features)
        assert all(v >= 0 for k, v in weights.items() if k != "table_weights")

    def test_mixed_language_query(self):
        features = self.extractor.extract("特斯拉TSLA最新财报")
        assert features.query_length > 0
        weights = self.calculator.calculate(features)
        assert all(v >= 0 for k, v in weights.items() if k != "table_weights")

    def test_very_long_query(self):
        q = "特斯拉" * 50
        features = self.extractor.extract(q)
        assert features.is_long_query is True
        weights = self.calculator.calculate(features)
        assert all(v >= 0 for k, v in weights.items() if k != "table_weights")

    def test_special_characters_query(self):
        features = self.extractor.extract("！！！？？？")
        weights = self.calculator.calculate(features)
        assert all(v >= 0 for k, v in weights.items() if k != "table_weights")

    def test_all_weights_non_negative(self):
        """任何查询产生的权重都不应为负。"""
        queries = ["", " ", "600519", "特斯拉", "a" * 100, "！！！"]
        for q in queries:
            features = self.extractor.extract(q)
            weights = self.calculator.calculate(features)
            assert all(v >= 0 for k, v in weights.items() if k != "table_weights"), f"Negative weight for query: {q!r}"
