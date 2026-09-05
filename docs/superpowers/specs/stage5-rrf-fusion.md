# 阶段5：RRF Fusion（加权融合）

## 目的

将 BM25、Vector、Entity 三条通道的排序融合为一个综合排序，产出 top-50 候选进入精排阶段。

## 设计原则

- RRF 只能粗略排序，不要过度调参。权重用默认值，后续根据实际效果微调。
- RRF 产出 top-50（不是 top-20），给 Reranker 足够的候选空间。

## 输入

阶段 4 过滤后的 `candidates` + 阶段 1/2 的 `entity_result`、`entity_context`、`expanded_keywords`

## 输出

```python
ranked_items: list[RankedItem]  # top-50

@dataclass
class RankedItem:
    doc_id: str
    doc_type: str
    title: str
    content_body: str           # 用于 snippet 提取
    rrf_score: float            # 原始 RRF 分
    bm25_rank: int | None
    vector_rank: int | None
    entity_rank: int | None
    entity_context_score: float  # entity_context 匹配加权
    structural_score: float
    time_score: float
    published_at: datetime | None
    demote_multiplier: float     # 来自阶段4的降权系数，默认 1.0
```

## RRF 公式

```python
k = 60  # 平滑常数

rrf = 0.0

# 1. BM25 rank 贡献
if doc.bm25_rank:
    rrf += w_bm25 / (k + doc.bm25_rank)

# 2. Vector rank 贡献
if doc.vector_rank:
    rrf += w_vector / (k + doc.vector_rank)

# 3. Entity 精确匹配 rank 贡献
if doc.entity_rank:
    rrf += w_entity / (k + doc.entity_rank)

# 4. Entity Context 匹配信号
for ctx_name, ctx_strength in entity_context.items():
    if ctx_name.lower() in (doc.title + " " + doc.content_body[:500]).lower():
        rrf += w_entity_context * ctx_strength / (k + 1)

# 5. Structural importance
importance = getattr(row, "importance_score", 0.0) or 0.0
importance_norm = min(max(importance, 0.0), 1.0)
rrf += w_structural * importance_norm / (k + 1)

# 6. Time decay
if doc.published_at:
    days = max(0, (now - doc.published_at).days)
    time_score = math.exp(-TIME_LAMBDA * days)
    rrf += w_time * time_score / (k + 1)

# 7. 阶段4 降权
if doc.demote_multiplier < 1.0:
    rrf *= doc.demote_multiplier
```

## 默认权重

```python
RRF_WEIGHTS = {
    "bm25": 1.2,
    "vector": 1.0,
    "entity": 2.5,
    "entity_context": 0.15,
    "structural": 0.2,
    "time_decay": 0.15,
}
TIME_LAMBDA = 0.01
```

## Rank 计算

```python
# BM25 rank：按 bm25_score 降序排列
bm25_ranked = sorted(candidates.items(), key=lambda x: (x[1].bm25_score or 0), reverse=True)
bm25_ranks = {doc_id: i + 1 for i, (doc_id, _) in enumerate(bm25_ranked)
              if _.bm25_score is not None}

# Vector rank：同上
vec_ranked = sorted(candidates.items(), key=lambda x: (x[1].vector_score or 0), reverse=True)
vec_ranks = {doc_id: i + 1 for i, (doc_id, _) in enumerate(vec_ranked)
             if _.vector_score is not None}

# Entity rank：同上
entity_ranked = sorted([(id, d) for id, d in candidates.items() if d.entity_matched],
                        key=lambda x: (x[1].entity_score or 0), reverse=True)
entity_ranks = {doc_id: i + 1 for i, (doc_id, _) in enumerate(entity_ranked)}
```

## 时间权重动态调整

```python
HINT_BOOST_KEYWORDS = (
    "今天", "昨日", "昨天", "最近", "最新", "刚刚",
    "本周", "本月", "公告", "财报", "新闻", "异动",
    "下跌", "上涨", "涨停", "跌停",
)

def infer_time_weight(query_text: str, default: float = 0.15) -> float:
    if any(x in query_text for x in HINT_BOOST_KEYWORDS):
        return max(default, 0.25)
    return default
```

## 输出截断

按 rrf_score 降序取 top-50，传给阶段 6。

## RRF 之后到 Rerank 之前

阶段 6 需要 snippet 作为输入。RRF 产出 top-50 后，调用 `snippet_service.extract()` 为每个 item 生成 query-aware snippet。

```python
top_items = rrf_fuse(candidates, ...)

for item in top_items:
    item.snippet = snippet_service.extract(
        body=item.content_body,
        query_terms=expanded_keywords,
        max_length=512,   # Reranker 输入
    )
```

## 文件

```
kbquant/services/search/fusion_service.py    [新] 本文件
```

## 测试

- BM25 + Vector + Entity 三条通道都有命中时 RRF 计算正确
- 仅 BM25 命中时 RRF 不使用其他 rank
- entity_context 匹配加权正确
- 时间衰减：1 天前 vs 100 天前
- demote_multiplier=0.5 时 RRF 减半
- RRF top-50 截断正确
