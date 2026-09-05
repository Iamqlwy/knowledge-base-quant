# 阶段4：Hard Filter（场景化硬过滤）

## 目的

在 RRF 融合之前，用低成本规则去除"绝对不相关"的候选，减少后续 RRF 和 Reranker 阶段的计算量。

## 设计原则

- 纯规则引擎，零延迟（~200 条候选 <1ms）
- 场景化：根据主实体类型决定过滤策略
- DROP 和 DEMOTE 分开处理

## 输入

```python
candidates: dict[str, Candidate]  # 阶段3 输出
entity_result: EntityResult       # 阶段1 输出
entity_context: dict[str, float]  # 阶段2 输出（关系图低相关实体）
```

## 输出

```python
filtered_candidates: dict[str, Candidate]  # 删除了无关联候选，降权的标记了 demote
# 同时记录日志：
#   dropped: int  (被丢弃的数量)
#   demoted: int  (降权但不丢弃的数量)
#   drop_reasons: dict[str, int]  ({"stock_entity_absent": 18, "vector_low": 5})
```

## 过滤规则

### 规则 1：股票实体硬约束（DROP）

```python
if main_entity_type == "stock" and not multi_stock:
    for doc in candidates:
        name = main_entity_name
        code = main_entity_code
        text = (doc.title + " " + doc.content_body[:500]).lower()

        # 要求文档中存在至少一个股票标识
        has_name = name.lower() in text if name else False
        has_code = code in text if code else False
        has_alias = any(a.lower() in text for a in main_aliases) if main_aliases else False

        if not (has_name or has_code or has_alias):
            DROP  # 丢弃

elif main_entity_type == "stock" and multi_stock:
    # 多实体场景：放宽为命中任一
    for doc in candidates:
        text = (doc.title + " " + doc.content_body[:500]).lower()
        if not any(e_name.lower() in text for e_name in multi_stock_names):
            DROP
```

### 规则 2：向量分数下限（DROP）

```python
# 仅被 pgvector 召回（无 BM25、无 entity_match）且分数过低
for doc in candidates:
    if (doc.bm25_score is None
        and doc.entity_matched == False
        and (doc.vector_score or 0) < 0.3):
        DROP
```

### 规则 3：关键词覆盖下限（DEMOTE，不丢弃）

```python
# 有 3+ 个关键词的查询，title 中至少命中 1 个
if len(expanded_keywords) >= 3:
    for doc in candidates:
        title_lower = doc.title.lower()
        hit_count = sum(1 for kw in expanded_keywords if kw.lower() in title_lower)
        if hit_count == 0:
            doc.demote_multiplier = 0.5  # RRF 分数减半
```

### 规则 4：时间范围过滤（DROP）

```python
# date_range 已在 ES 查询层面过滤（写在 query bool.filter 中），
# 这里仅对 pgvector 结果做二次确认
if date_range and doc.source == "vector_only" and doc.published_at:
    if not (start <= doc.published_at <= end):
        DROP
```

### 规则 5：Entity Context 信号（加分，不丢弃）

```python
# entity_context 中的关系图实体命中 → 标记加分
for ctx_name, ctx_strength in entity_context.items():
    if ctx_name.lower() in (doc.title + " " + doc.content_body[:500]).lower():
        doc.entity_context_hits.append((ctx_name, ctx_strength))
        # RRF 阶段会用它加权，这里不做 DROP/DEMOTE
```

## 执行流

```python
def apply(context: FilterContext) -> FilterResult:
    candidates = context.candidates
    dropped = []
    demoted = []
    drop_reasons = {}

    for doc_id, doc in list(candidates.items()):
        keep = True
        reason = None

        # 规则1：股票实体硬约束
        if context.main_entity_type == "stock":
            if context.is_multi_stock:
                if not any(n.lower() in text for n in context.multi_stock_names):
                    keep = False
                    reason = "stock_entity_absent_multi"
            else:
                if not (context.main_name.lower() in text
                        or (context.main_code and context.main_code in text)):
                    keep = False
                    reason = "stock_entity_absent"

        # 规则2：向量下限
        if keep and doc.bm25_score is None and not doc.entity_matched:
            if (doc.vector_score or 0) < VECTOR_LOW_THRESHOLD:
                keep = False
                reason = "vector_low"

        # 规则3：关键词覆盖
        if keep and len(context.expanded_keywords) >= 3:
            if sum(1 for kw in context.expanded_keywords
                   if kw.lower() in doc.title.lower()) == 0:
                doc.demote_multiplier = 0.5
                demoted.append(doc_id)

        # 规则4：时间范围
        if keep and context.date_range and doc.source == "vector_only":
            if doc.published_at and not (start <= doc.published_at <= end):
                keep = False
                reason = "date_range"

        if not keep:
            dropped.append(doc_id)
            drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
        else:
            filtered[doc_id] = doc

    logger.debug(
        "hard_filter: before=%d after=%d dropped=%d demoted=%d reasons=%s",
        len(candidates), len(filtered), len(dropped), len(demoted), drop_reasons,
    )

    return FilterResult(
        candidates=filtered,
        dropped_count=len(dropped),
        demoted_count=len(demoted),
        drop_reasons=drop_reasons,
    )
```

## 配置

```python
VECTOR_LOW_THRESHOLD = 0.3
KEYWORD_COVERAGE_MIN_COUNT = 3   # 少于3个关键词时不触发覆盖检查
DEMOTE_MULTIPLIER = 0.5          # 降权系数
```

## 文件

```
kbquant/services/search/hard_filter.py    [新] 本文件
```

## 测试

- stock 实体 + 文档 title 不含实体名 → DROP
- multi_stock + 文档命中任一实体 → KEEP
- industry 实体 → 不触发规则1
- 仅向量命中 + 低分 → DROP
- title 无关键词 + 3+ 关键词 → DEMOTE
- entity_context 匹配 → 文档被标记 (不丢弃)
