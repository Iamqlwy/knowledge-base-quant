"""
非 LLM 语义重要性评分模块。

通过 TF-IDF、位置权重、类型先验和密度评分，对字典匹配得到的实体进行
语义重要性排序，替代纯频率排序，过滤掉泛化/背景性实体。
"""
import json
import math
import os
from pathlib import Path
from dataclasses import dataclass, field

_project_root = Path(__file__).parent.parent.parent
DATA_DIR = os.path.join(_project_root, "data")

# 实体类型的语义重要性先验权重
# 权重越高，该类型的实体在文章中越有可能是核心主题
TYPE_PRIORS: dict[str, float] = {
    "person": 0.90,
    "event": 0.85,
    "policy": 0.80,
    "company": 0.80,
    "index": 0.75,
    "commodity": 0.70,
    "indicator": 0.70,
    "central_bank": 0.65,
    "regulation": 0.65,
    "institution": 0.60,
    "concept": 0.50,
    "product": 0.50,
    "industry_rule": 0.25,
    "sector": 0.45,
    "currency": 0.35,
    "region": 0.10,
}

# 需要做 IDF 降权的泛化实体类型
# 这些类型的实体经常作为背景/上下文出现，语料中出现频率越高，越不可能是文章核心
IDF_TYPES: set[str] = {"region", "currency", "sector", "concept", "industry_rule"}


@dataclass
class ScoringConfig:
    tf_weight: float = 0.25
    idf_weight: float = 0.25
    position_weight: float = 0.25
    density_weight: float = 0.25
    title_position_boost: float = 1.0
    early_position_boost: float = 0.8
    late_position_score: float = 0.3
    early_position_ratio: float = 0.10
    max_entities: int = 5          # 每篇文章最多保留的实体数
    gap_ratio: float = 0.40        # 断层阈值：相邻实体重要性比值低于此值则截断
    enable_idf: bool = True


class EntityScorer:
    """对字典匹配的实体进行语义重要性评分。"""

    def __init__(self, idf_cache: dict[str, float] | None = None,
                 config: ScoringConfig | None = None):
        self._idf_cache = idf_cache or {}
        self._config = config or ScoringConfig()

    def score(self, entities: list[dict], text: str, title: str = "") -> list[dict]:
        """对实体列表评分并排序，返回带 importance 字段的结果。"""
        if not entities:
            return []

        doc_len = len(text)
        max_occ = max(e["occurrences"] for e in entities)

        for e in entities:
            etype = e.get("entity_type", "")
            type_prior = TYPE_PRIORS.get(etype, 0.50)

            # TF 得分：文档内词频归一化
            tf_score = math.log(1 + e["occurrences"]) / math.log(1 + max_occ) if max_occ > 0 else 0

            # IDF 得分：仅对泛化类型做降权
            if self._config.enable_idf and etype in IDF_TYPES:
                idf = self._idf_cache.get(e["name"], 1.0)
                idf_score = idf
            else:
                idf_score = 1.0

            # 位置得分：越靠前越重要
            first_pos = e.get("first_position", doc_len)
            pos_ratio = first_pos / doc_len if doc_len > 0 else 1.0

            # 也检查是否在标题中出现
            if title and e["name"] in title:
                position_score = self._config.title_position_boost
            elif pos_ratio <= self._config.early_position_ratio:
                position_score = self._config.early_position_boost
            elif pos_ratio <= 0.25:
                position_score = 0.6
            else:
                position_score = self._config.late_position_score

            # 密度得分：实体在文档中的密度
            density = e["occurrences"] / doc_len if doc_len > 0 else 0
            density_score = min(1.0, density * 1000)

            # 综合评分
            c = self._config
            importance = type_prior * (
                c.tf_weight * tf_score +
                c.idf_weight * idf_score +
                c.position_weight * position_score +
                c.density_weight * density_score
            )

            e["importance"] = round(importance, 4)
            e["tf_score"] = round(tf_score, 4)
            e["idf_score"] = round(idf_score, 4)
            e["position_score"] = round(position_score, 4)

        # 按 importance 降序排列
        scored = sorted(entities, key=lambda e: -e["importance"])

        # 断层截断：找到重要性分数明显断崖的位置
        return _cut_by_gap(scored, self._config.max_entities, self._config.gap_ratio)


def _cut_by_gap(entities: list[dict], max_entities: int = 5,
                gap_ratio: float = 0.40) -> list[dict]:
    """基于重要性断层截断实体列表。

    从第1个实体开始，如果下一个实体的重要性不足前一个的 gap_ratio，
    则在此处截断。实体数量少时不强行删减。
    最后用 max_entities 做硬上限。
    """
    if len(entities) <= 1:
        return entities[:max_entities]

    cut = 1  # 至少保留第1个
    limit = min(len(entities), max_entities)
    for i in range(1, limit):
        prev_imp = entities[i - 1]["importance"]
        curr_imp = entities[i]["importance"]
        if prev_imp > 0 and (curr_imp / prev_imp) < gap_ratio:
            break
        cut = i + 1

    return entities[:min(cut, max_entities)]


def build_idf_cache(corpus_path: str,
                    entities: list[dict],
                    output_path: str | None = None) -> dict[str, float]:
    """从资讯语料构建 IDF 缓存。

    只为 IDF_TYPES 中的泛化实体类型计算 IDF。
    返回 {entity_name: idf_score} 字典，idf_score 已归一化到 0~1。
    """
    import csv

    # 收集需要计算 IDF 的实体名称
    target_names: set[str] = set()
    for e in entities:
        if e.get("entity_type") in IDF_TYPES:
            target_names.add(e["name"])

    if not target_names:
        return {}

    # 统计文档频率
    doc_count = 0
    df: dict[str, int] = {name: 0 for name in target_names}

    with open(corpus_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            content = row.get("content", "") or ""
            doc_count += 1
            for name in target_names:
                if name in content:
                    df[name] += 1

    if doc_count == 0:
        return {}

    # 计算 IDF 并归一化
    max_idf = math.log(doc_count + 1)
    idf_cache: dict[str, float] = {}
    for name in target_names:
        raw_idf = math.log((doc_count + 1) / (1 + df.get(name, 0)))
        idf_cache[name] = raw_idf / max_idf if max_idf > 0 else 1.0

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(idf_cache, f, ensure_ascii=False, indent=2)

    return idf_cache


def load_idf_cache(cache_path: str) -> dict[str, float]:
    """加载预计算的 IDF 缓存。"""
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path, "r", encoding="utf-8") as f:
        return json.load(f)
