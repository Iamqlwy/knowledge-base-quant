# 阶段2：Query Rewrite（查询改写 / 关键词扩展）

## 目的

把阶段 1 输出的实体匹配结果扩展为搜索关键词集合，补全用户没说全但搜索需要的词。

## 设计原则

不调 LLM，零延迟。两个扩展渠道覆盖全部场景：

| 场景 | 扩展方式 | 说明 |
|------|---------|------|
| 有 WorldNode 实体命中 | ImpactPathService 关系图遍历 | 沿 EntityRelationship 图找出关联实体，带强度权重 |
| 无实体命中 / query 中非实体词 | 静态同义词词典 | funNLP 财经词库，启动时加载到内存 |

---

## 输入

阶段 1 `Entity Resolver` 的输出：

```python
{
    "resolved_entities": [
        {"id": "uuid-xxx", "type": "stock", "code": "600519", "name": "贵州茅台",
         "aliases": ["茅台", "飞天茅台"], "industry": "白酒", "score": 1.0},
    ],
    "main_entity_name": "贵州茅台",
    "main_entity_type": "stock",
}
```

## 输出

```python
{
    "expanded_keywords": {
        "贵州茅台", "600519", "茅台", "飞天茅台",  # 实体 alias + ticker
        "下跌", "回调", "跌幅", "走低", "下挫",     # 非实体词 → 同义词
        "白酒行业", "批价", "渠道库存", "消费税",   # 关系图扩展
    },
    "entity_context": {                            # 关系图低强度实体，仅用于加权
        "五粮液": 0.35,                            # 2跳衰减后
        "茅台经销商": 0.28,
    },
}
```

---

## 扩展逻辑

### 第 1 步：实体 alias + ticker

阶段 1 已匹配到实体，直接将其 name、code、aliases 全部加入 expanded_keywords。

```python
for ent in matched_entities:
    keywords.add(ent.name)
    if ent.code:
        keywords.add(ent.code)
    for alias in ent.aliases or []:
        keywords.add(alias)
```

### 第 2 步：ImpactPathService 关系图遍历

对阶段 1 命中的 WorldNode 实体（type=stock/industry/strategy），调用 `ImpactPathService.find_paths()`：

```python
impact_paths = await impact_path_service.find_paths(
    source_entity_id=entity.id,
    depth=2,          # 2跳，首跳完整强度，之后每跳衰减
    direction="both", # 上下游都看
)
```

**示例**：命中 "贵州茅台"

```
1跳关系 (strength ≥ 0.7):
  贵州茅台 → 白酒行业 (industry_of, strength=0.9)
  贵州茅台 → 飞天茅台 (product, strength=0.95)
  贵州茅台 → 批价 (price_indicator, strength=0.8)
  贵州茅台 → 600519 (ticker, strength=1.0)
  贵州茅台 → 茅台经销商 (stakeholder, strength=0.7)

2跳关系 (衰减后 strength < 0.4):
  白酒行业 → 消费税 (policy, strength=0.7 → 0.7×0.5×0.7=0.25)
  白酒行业 → 五粮液 (competitor, strength=0.6 → 0.35)
  飞天茅台 → 渠道库存 (supply_chain, strength=0.6 → 0.18)
```

**分档规则**：

| 档位 | 条件 | 数量 | 用途 |
|------|------|------|------|
| 高相关 | strength ≥ 0.4 | 5-15 个 | 加入 `expanded_keywords`，BM25 + 向量都搜 |
| 低相关 | strength < 0.4 | 不限 | 只进入 `entity_context`，用于 RRF 加权匹配信号 |

### 第 3 步：非实体词同义词扩展

query 中不在实体名列表内的词，走静态词典：

```
词典来源: funNLP 财经词库 → kbquant/assets/finance_synonyms.txt
格式: 每行 "原词\t同义词1,同义词2,..."
示例:
  下跌    下跌,回调,跌幅,走低,下挫
  上涨    上涨,涨幅,走高,拉升,大涨
  业绩    业绩,财报,营收,利润,净利润
  风险    风险,隐患,不确定性,利空
  政策    政策,法规,监管,制度,新规
  产能    产能,产量,开工率,负荷
```

启动时加载为 `dict[str, list[str]]`。

```python
entity_names = {e.name for e in matched_entities}
for word in query_words:
    if word not in entity_names:  # 不是实体 → 走同义词
        keywords.update(FINANCE_SYNONYMS.get(word, [word]))
```

### 第 4 步：保留原始 query 非停用词

```python
STOPWORDS = {"为什么", "怎么", "如何", "哪里", "哪些", "有没有",
             "是什么", "的", "和", "了", "呢", "吗", "吧", "啊", "是吧", "怎么看"}

query_words = [w for w in query_text.split() if w not in STOPWORDS]
keywords.update(query_words)
```

### 第 5 步：时间偏向词推断

```python
def infer_time_bias(query_text: str) -> int | None:
    if any(kw in query_text for kw in ["今天", "刚刚"]): return 3
    if any(kw in query_text for kw in ["昨日", "昨天"]): return 7
    if any(kw in query_text for kw in ["最近", "最新", "本周"]): return 30
    if any(kw in query_text for kw in ["本月"]): return 60
    if any(kw in query_text for kw in ["财报", "季报", "年报"]): return 90
    return None
```

时间偏向词本身不加入 expanded_keywords（"最近"搜不到东西），而是转为 `time_bias_days` 传给后续阶段的时间过滤。但时间**事件**词（"财报"、"公告"）应保留在 expanded_keywords 中。

---

## Entity Context 的后续使用

`entity_context` 传到阶段 4（Hard Filter）和阶段 5（RRF Fusion）：

### Hard Filter

```
stock 主实体必须命中 → entity_context 中的词命中了是加分信号（不强制丢弃）
```

### RRF Fusion

```python
w_entity_context = 0.15

for ctx_name, ctx_strength in entity_context.items():
    if ctx_name in doc.title or ctx_name in doc.body[:500]:
        rrf += w_entity_context * ctx_strength / (k + 1)
```

---

## RRF 权重更新

阶段 5 的 RRF 公式新增 `w_entity_context` 维度：

```python
RRF_WEIGHTS = {
    "bm25": 1.2,
    "vector": 1.0,
    "entity": 2.5,
    "entity_context": 0.15,  # 新增：关系图低相关实体匹配
    "structural": 0.2,
    "time_decay": 0.15,
}
```

---

## 文件结构

```
kbquant/
├── assets/
│   └── finance_synonyms.txt            # 金融同义词词典
├── services/
│   ├── search/
│   │   ├── entity_resolver.py          # 阶段1
│   │   └── query_rewriter.py           # 阶段2（本文件）
│   └── impact_path_service.py          # 已有，关系图遍历
```

---

## 测试

- 有实体命中 + 关系图有数据 → expanded_keywords 包含 1跳实体、entity_context 包含 2跳实体
- 有实体命中 + 关系图无数据 → 仅 alias+ticker 扩展，entity_context 为空
- 无实体命中 → 仅同义词扩展 + 原始关键词
- 实体名出现在 query 中 → 不触发同义词扩展（避免 "茅台 → 茅台 的 茅台酒" 冗余）
- 时间偏向词推断：完整句子 vs 纯关键词串

## 未决项

- [ ] Entity Relationship 表的当前覆盖率评估（多少实体有关联数据？）
- [ ] `impact_path_service.find_paths()` 的延迟（涉及 DB 查询，需要测试）
