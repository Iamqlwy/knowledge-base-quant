# 阶段3：Multi-Recall（多路召回）

## 目的

把阶段 2 输出的 `expanded_keywords` 扔进三条召回通道，并行搜索 ES 和 PostgreSQL，合并去重后产出候选集。

## 设计原则

- **复用现有代码**。`_run_bm25_stage()` 和 `_run_vector_and_name_stage()` 已经在 `search_service.py` 中实现，阶段3 只做适配改造，不重写。
- **BM25 使用 expanded_keywords 拼成的搜索字符串**，每个词 OR 连接，保证关键词匹配覆盖。
- **向量使用原始 query_text 的 embedding**，不做子查询拆分（向量召回本身就是语义层面的，不需要关键词展开）。
- **同一次 search 中 ES 和 DB 并行**，与现有模式一致。

## 输入

阶段 2 `Query Rewriter` 的输出：

```python
{
    "expanded_keywords": {"贵州茅台", "600519", "飞天茅台", "白酒",
                           "下跌", "回调", "跌幅", "走低",
                           "业绩", "财报", "营收", "利润"},
    # 来自阶段1的原始 query_text 也在内部使用
}
```

## 输出

```python
candidates: dict[str, Candidate]

@dataclass
class Candidate:
    doc_id: str
    doc_type: str       # "raw_information" | "analysis" | "feedback"
    title: str
    content_body: str   # body 或 content，用于后续 snippet 提取
    bm25_score: float | None
    vector_score: float | None
    entity_matched: bool              # 是否被实体精确匹配命中
    bm25_rank: int | None
    vector_rank: int | None
    published_at: datetime | None
    importance_score: float           # 从 pg/row 中获取
```

---

## 三条召回通道

### 通道 1：ES BM25

**搜索字符串**：expanded_keywords 用空格拼接。

```
expanded_keywords = {"贵州茅台", "600519", "飞天茅台", "下跌", "回调", "批价", ...}
  ↓ 空格拼接
bm25_query = "贵州茅台 600519 飞天茅台 下跌 回调 批价 业绩 白酒 消费税"
  ↓
ES 在各表的 title^2 + body/content 字段中搜索
```

**为什么不用多个子查询**：BM25 本身就会对多词 OR 查询自动权重分配（出现多个关键词的文档天然得分更高）。分成多个子查询并各自 get top-30 再合并，不如一条查询拿 top-100 直接有效。前者可能漏掉命中多个关键词但不在任何单路子查询 top-30 中的文档。

**复用现有方法**：`_es_bm25_search(query_text, index, search_fields, ...)`，把 `query_text` 换为 bm25_query。

### 通道 2：pgvector

**输入**：原始 `query_text` 的 embedding。

向量召回是语义层面的，不需要关键词展开。用完整 query_text（如"贵州茅台最近为什么下跌"）的 embedding 去匹配更合理——它保留了原始语义信息，比起关键词展开后的词袋能更好地捕捉用户意图。

**复用现有方法**：`_pg_vector_search(query_embedding, table_class, ...)`，不变。

### 通道 3：Entity Match

**复用阶段 1 的结果**。`entity_resolver.resolve(query_text)` 在阶段 1 已经执行，返回了 `resolved_entities`。阶段 3 直接将 worldnode id 加入 candidates。

**不需要额外 DB 查询**。阶段 1 已经查过 WorldNode 表了。

---

## 搜索目标自主决定

忽略 `filters.target_tables`。SearchService 根据两个维度自主决定搜索哪些表。

### 决策维度

| 维度 | 来源 | 判断方式 |
|------|------|---------|
| 主实体类型 | 阶段 1 `main_entity_type` | stock/industry/strategy/person/tech_company/无 |
| query 关键词信号 | 阶段 2 `expanded_keywords` | 关键词分组匹配 |

### 维度 1：主实体类型 → 基础表

```python
BASE_TABLES = {
    "stock":           ["raw_information", "analyses", "nodes"],
    "person":          ["raw_information", "analyses", "nodes"],
    "tech_company":    ["raw_information", "analyses", "nodes"],
    "industry":        ["raw_information", "analyses"],
    "strategy":        ["raw_information", "feedbacks"],
    "macro_indicator": ["raw_information", "analyses"],
    "event":           ["raw_information", "analyses"],
    None:              ["raw_information", "analyses"],   # 无实体 → 默认
}
```

### 维度 2：关键词信号 → 追加/调整表

```python
KEYWORD_TABLE_HINTS = {
    # 财务/业绩 → 补充 entity 状态信息
    "financial_report": {
        "keywords": {"财报", "季报", "年报", "业绩", "营收", "利润",
                     "净利润", "毛利率", "净利率", "ROE", "EPS", "出货量",
                     "收入", "成本", "费用", "现金流", "负债"},
        "add_tables": ["nodes"],
    },
    # 策略复盘 → 补充交易经验
    "strategy_review": {
        "keywords": {"炸板", "止损", "回撤", "复盘", "打板", "封板",
                     "失败经验", "教训", "策略总结", "竞价", "天地板",
                     "开盘不及预期", "仓位管理", "破位"},
        "add_tables": ["feedbacks"],
    },
    # 长期逻辑 → 补充深度分析
    "long_term_logic": {
        "keywords": {"投资逻辑", "核心逻辑", "风险", "基本面", "长期趋势",
                     "展望", "估值", "护城河", "竞争格局", "成长性"},
        "add_tables": ["analyses"],
    },
    # 市场异动 → 补充即时信息
    "market_event": {
        "keywords": {"异动", "涨停", "跌停", "公告", "新闻", "最新",
                     "下跌", "上涨", "暴跌", "大涨", "异动公告", "停牌"},
        "add_tables": ["raw_information"],
    },
    # 产业链/影响 → 补充实体关系
    "supply_chain": {
        "keywords": {"产业链", "上下游", "供应商", "客户", "影响",
                     "利好", "利空", "受益", "受损", "传导"},
        "add_tables": ["nodes"],
    },
}
```

### 决策函数

```python
def determine_target_tables(
    main_entity_type: str | None,
    query_keywords: set[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """
    返回 (tables, es_fields)。

    tables: 要搜索的表名列表
    es_fields: 每张表的 ES 搜索字段映射
    """
    # 步骤1：主实体类型 → 基础表
    tables = set(BASE_TABLES.get(main_entity_type, BASE_TABLES[None]))

    # 步骤2：关键词信号 → 追加表
    for hint_name, hint_config in KEYWORD_TABLE_HINTS.items():
        if any(kw in query_keywords for kw in hint_config["keywords"]):
            for t in hint_config["add_tables"]:
                tables.add(t)

    # 步骤3：构建 ES 字段映射
    ES_FIELD_MAP = {
        "raw_information": ["title^2", "body"],
        "analyses": ["title^2", "content"],
        "feedbacks": ["title^2", "lessons_learned"],
        "nodes": ["name^2", "description", "node_type"],
    }
    es_fields = {t: ES_FIELD_MAP[t] for t in tables}

    return list(tables), es_fields
```

### 完整决策表

| 主实体 | query 含关键词信号 | 搜索表 |
|--------|-----------------|--------|
| stock | financial_report | raw_information + analyses + nodes |
| stock | strategy_review | raw_information + analyses + nodes + feedbacks |
| stock | long_term_logic | raw_information + analyses |
| stock | market_event | raw_information + analyses + nodes |
| stock | supply_chain | raw_information + analyses + nodes |
| stock | 无特殊关键词 | raw_information + analyses + nodes |
| industry | long_term_logic | raw_information + analyses |
| industry | supply_chain | raw_information + analyses + nodes |
| industry | market_event | raw_information + analyses |
| industry | 无特殊关键词 | raw_information + analyses |
| strategy | long_term_logic | raw_information + feedbacks + analyses |
| strategy | 任何关键词 | raw_information + feedbacks |
| person | 任何关键词 | raw_information + analyses + nodes |
| tech_company | 任何关键词 | raw_information + analyses + nodes |
| 无实体 | strategy_review | raw_information + feedbacks |
| 无实体 | 其他 | raw_information + analyses |

### ES 与 pgvector 的表同步

ES 和 pgvector 搜索**同一组表**。如果 feedbacks 被加入 tables，ES BM25 和 pgvector 都会搜 feedbacks。唯一例外：nodes 的 pgvector 走 NodeState 表（间接映射到 node_id），不走 WorldNode 表直接向量检索。

### 时间偏向

搜索表确定后，raw_information 和 analyses 按 published_at 时间倒序（recency bias），feedback 和 nodes 不加强时间限制。
```

---

## 召回配置

```python
RECALL_CONFIG = {
    "bm25_limit": 100,       # ES 每张表召回上限
    "vector_limit": 100,     # pgvector 每张表召回上限
    "max_total_candidates": 300,  # 合并去重后上限
}
```

## 并发模型

不变。现有代码已有 `asyncio.gather(_run_bm25_stage(), _run_vector_and_name_stage(session))`，ES 操作（无 DB）和 DB 操作（需 session）两组并行：

```
┌──────────────────────┐    ┌──────────────────────────────┐
│ _run_bm25_stage()    │    │ _run_vector_and_name_stage() │
│ (ES 连接池, 无 DB)    │    │ (单个 session, 顺序执行)      │
│                      │    │                              │
│ raw_information ES   │    │ raw_information pgvector     │
│ analyses ES          │    │ analyses pgvector            │
│ feedbacks ES         │    │ feedbacks pgvector           │
│ nodes ES        ─────┼────┼─ nodes pgvector              │
│                      │    │ entity_match (复用阶段1结果)  │
└──────────────────────┘    └──────────────────────────────┘
          │                            │
          └──────── asyncio.gather ─────┘
                       │
                       ▼
                  合并去重 → candidates
```

---

## 合并去重

三条通道可能召回同一个文档（如某个 raw_information 同时被 BM25 和 vector 召回）：

```python
candidates: dict[str, Candidate] = {}

# 1. ES results → candidates
for pg_id, hit in es_results.items():
    candidates[pg_id] = Candidate(
        doc_id=pg_id,
        title=hit["source"].get("title", ""),
        content_body=hit["source"].get("body", ""),
        bm25_score=hit["score"],
        bm25_rank=None,  # RRF 阶段再计算 rank
    )

# 2. Vector results → merge or insert
for pg_id, hit in pg_results.items():
    if pg_id in candidates:
        candidates[pg_id].vector_score = hit["score"]
        candidates[pg_id].content_body = (
            candidates[pg_id].content_body
            or getattr(hit.get("row"), "body", "")
            or getattr(hit.get("row"), "content", "")
        )
    else:
        candidates[pg_id] = Candidate(
            doc_id=pg_id,
            vector_score=hit["score"],
            content_body=...,
        )

# 3. Entity match results → merge or insert
for node_id, hit in entity_match_results.items():
    if node_id in candidates:
        candidates[node_id].entity_matched = True
    else:
        candidates[node_id] = Candidate(
            doc_id=node_id,
            entity_matched=True,
            doc_type="node",
            title=hit["row"].name,
        )

# cap to max_total_candidates
if len(candidates) > RECALL_CONFIG["max_total_candidates"]:
    # 按 best_score 截断
    ...
```

---

## 前置条件：analysis 和 feedback 必须补 embedding

当前 analysis 的 embedding 列基本为空——向量召回对 analysis 无效。同样的 feedback 也没有 embedding。在阶段 3 之后，pipeline 应该给 analysis 和 feedback 补上 `text-embedding-v4` 的 embedding。这是批量任务，在入库时完成，不影响搜索延迟。

> 具体实现不在阶段 3 范围内，但必须在搜索上线前完成。

---

## 改造清单（相对现有 search_service.py）

| 现有代码 | 改造 |
|---------|------|
| `query_text` 直接传给 ES | 改为 `" ".join(expanded_keywords)` 传给 ES |
| `query_text` 直接传给 embedding | 不变（向量用原始 query_text） |
| `target_tables` 从 `filters` 中读取 | 忽略 filters，按双维度决策规则自主决定 |
| `_entity_match_search` 在 DB 阶段执行 | 移到阶段 1 执行，阶段 3 复用结果 |
| 没有 candidates 数据结构 | 新增 `Candidate` dataclass |
| 没有结果来源跟踪 | 新增 `bm25_hit` / `vector_hit` / `entity_hit` 标记 |
| 代码在 `services/search_service.py` | 抽取到 `services/search/recall_service.py`，编排层留在 `services/search/search_service.py` |

---

## 文件结构

```
kbquant/
├── services/
│   ├── search/
│   │   ├── recall_service.py           # [新] 阶段3：多路召回（从 search_service 抽取）
│   │   ├── entity_resolver.py          # [新] 阶段1
│   │   ├── query_rewriter.py           # [新] 阶段2
│   │   └── search_service.py           # [修改] 编排层，调用各阶段
│   └── ...
│
└── models/
    └── search_candidate.py             # [新] Candidate dataclass
```

---

## 测试

- expanded_keywords 正常拼接到 ES query
- entity_type=stock → feedback 表不参与召回
- entity_type=strategy → feedback 表参与召回
- 同一文档被 BM25 + vector 都命中 → 正确合并为一个 candidate
- entity_match 结果正确合并到 candidates
- total > max_total_candidates → 截断
- date_range 正确过滤

---

## 未决项

- [ ] analysis/feedback embedding 批量补录的进度和验证方案
- [ ] `_es_bm25_search` 在 expanded_keywords 很长（30+ 词）时的 ES 查询性能
