# 阶段7：Final Ranking（最终排序）

## 目的

将 Reranker 分数、RRF 分数、实体匹配、时间新鲜度、类型优先级加权融合，产出最终排序。

## 设计原则

- **简洁，不超过 5 个维度**。Reranker 好了就多信它，不好就退回 RRF。
- 场景化权重：normal（reranker 可用）和 fallback（reranker 不可用）两套权重。

## 输入

```python
query_text: str
reranked_items: list[RankedItem]   # 阶段6 输出
entity_result: EntityResult        # 阶段1 输出
```

## 输出

```python
final_items: list[RankedItem]  # 按 final_score 降序排列的 top-20
```

## 加权公式

```python
final_score = (
    alpha * norm_reranker_score               # Reranker 分（0~1 归一化）
  + beta  * norm_rrf_score                    # RRF 分（0~1 归一化）
  + gamma * entity_match_boost                # 实体精确匹配加成
  + delta * time_freshness                    # 时间新鲜度
  + epsilon * result_type_priority            # 结果类型优先级
)
```

## 场景权重

| 场景 | alpha | beta | gamma | delta | epsilon |
|------|-------|------|-------|-------|---------|
| normal（reranker 可用） | 0.5 | 0.2 | 0.1 | 0.1 | 0.1 |
| fallback（reranker 不可用） | 0 | 0.5 | 0.2 | 0.15 | 0.15 |

```python
if reranker_applied:
    weights = FINAL_RANKING_WEIGHTS["normal"]
else:
    weights = FINAL_RANKING_WEIGHTS["fallback"]
```

## 各分量计算

### norm_reranker_score

```python
# API 返回的 relevance_score 已经是 0~1
norm_reranker_score = item.reranker_score  # 0~1
if norm_reranker_score is None:
    norm_reranker_score = 0.0
```

### norm_rrf_score

```python
# RRF 分数 z-score 归一化到 0~1
all_rrf = [item.rrf_score for item in reranked_items]
rrf_mean = statistics.mean(all_rrf)
rrf_std = statistics.stdev(all_rrf) if len(all_rrf) > 1 else 1.0

for item in reranked_items:
    z = (item.rrf_score - rrf_mean) / rrf_std
    item.norm_rrf_score = 1.0 / (1.0 + math.exp(-z))  # sigmoid 到 0~1
```

### entity_match_boost

```python
name = entity_result.main_entity_name or ""
code = entity_result.main_entity_code or ""

title_lower = item.title.lower()

boost = 0.0
if code and code in title_lower:
    boost += 0.2           # ticker 精确匹配最高
elif name and name.lower() in title_lower:
    boost += 0.15          # name 精确匹配
elif any(a.lower() in title_lower for a in entity_result.main_aliases or []):
    boost += 0.08          # alias 包含匹配

item.entity_boost = boost
```

### time_freshness

```python
# 复用现有 time_decay，不改变已有逻辑
# time_score 已在 RRF 阶段计算，这里降权归入 final_score
item.time_freshness = item.time_score  # 已在 RRF 阶段计算
```

### result_type_priority

根据主实体类型调整：

```python
TYPE_PRIORITY = {
    "stock": {
        "raw_information": 1.0,
        "analysis": 0.85,
        "node": 0.8,
        "feedback": 0.6,
    },
    "industry": {
        "analysis": 1.0,
        "raw_information": 0.85,
        "node": 0.7,
        "feedback": 0.6,
    },
    "strategy": {
        "feedback": 1.0,
        "analysis": 0.85,
        "raw_information": 0.7,
        "node": 0.6,
    },
    "default": {
        "raw_information": 1.0,
        "analysis": 0.85,
        "node": 0.7,
        "feedback": 0.6,
    },
}

priority_map = TYPE_PRIORITY.get(
    entity_result.main_entity_type or "default",
    TYPE_PRIORITY["default"],
)
item.type_priority_score = priority_map.get(item.doc_type, 0.6)
```

## 执行函数

```python
def rank(
    reranked_items: list[RankedItem],
    entity_result: EntityResult,
    reranker_applied: bool,
) -> list[RankedItem]:
    weights = (
        FINAL_RANKING_WEIGHTS["normal"] if reranker_applied
        else FINAL_RANKING_WEIGHTS["fallback"]
    )

    # RRF 归一化
    all_rrf = [item.rrf_score for item in reranked_items]
    rrf_mean = statistics.mean(all_rrf)
    rrf_std = statistics.stdev(all_rrf) if len(all_rrf) > 1 else 1.0

    priority_map = TYPE_PRIORITY.get(
        entity_result.main_entity_type or "default",
        TYPE_PRIORITY["default"],
    )

    name = entity_result.main_entity_name or ""
    code = entity_result.main_entity_code or ""

    for item in reranked_items:
        reranker = item.reranker_score or 0.0
        norm_rrf = sigmoid((item.rrf_score - rrf_mean) / rrf_std)
        entity_boost = compute_entity_boost(item.title, name, code, entity_result)
        time = item.time_score
        type_priority = priority_map.get(item.doc_type, 0.6)

        item.final_score = (
            weights["alpha"] * reranker
            + weights["beta"] * norm_rrf
            + weights["gamma"] * entity_boost
            + weights["delta"] * time
            + weights["epsilon"] * type_priority
        )

    reranked_items.sort(key=lambda x: x.final_score, reverse=True)
    return reranked_items
```

## 最终输出

取 top-20 构建 SearchResponse：

```python
items = []
for item in final_items[:limit]:
    items.append({
        "result_type": item.doc_type,
        "id": item.doc_id,
        "title": item.title,
        "snippet": item.snippet or item.content_body[:200],  # 展示用 200 字
        "time": item.published_at,
        "score": {
            "total": round(item.final_score, 4),
            "reranker": round(item.reranker_score, 4) if item.reranker_score else None,
            "bm25_rank": item.bm25_rank,
            "vector_rank": item.vector_rank,
            "structural": round(item.structural_score, 4),
            "time_score": round(item.time_score, 4),
        },
    })

return {"items": items, "total": len(filtered_candidates)}
```

## 文件

```
kbquant/services/search/final_ranking.py    [新] 本文件
```

## 测试

- normal + reranker 可用 → alpha=0.5, beta=0.2
- fallback + reranker 不可用 → alpha=0, beta=0.5
- stock 实体 → raw_information 优先级最高
- strategy 实体 → feedback 优先级最高
- entity_boost 各匹配级别正确
