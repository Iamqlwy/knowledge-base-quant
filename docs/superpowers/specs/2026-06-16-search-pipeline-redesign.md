# 搜索引擎化检索流水线设计

## 1. 背景与问题诊断

### 1.1 当前问题

基于 `search_eval_10cases.txt` 评测数据分析：

1. **BM25 对分析类文章区分度差**。搜索"贵州茅台为什么下跌"，top 结果包含"中国广核FCD深度分析"、"上海算力互联互通"、"节能装备高质量发展"等与茅台完全无关的内容。原因是 analysis 文章标题结构高度相似（"XX深度分析"），BM25 在 title 字段匹配到"分析"即给高分，不识别主语。

2. **RRF 融合后分数差异极小**。第 1 名 0.0197，第 20 名 0.0162，相邻名次差距仅 0.0002-0.0005。排名近乎随机，任何微小扰动都可能将无关内容推到前面。

3. **向量召回对 analysis 覆盖率不足**。analysis 类型结果的 vec 列几乎全为 None，向量召回主要命中 raw_information。analysis 类结果完全失去语义约束，仅凭 BM25 排序。

4. **特定类型（feedback）召回率为零**。搜索"打板策略失败经验"，feedback 类型命中为 0——因为 feedback 表没有 embedding 且 BM25 匹配不到专用术语映射。

5. **缺少精排阶段**。整条流水线只有检索 + RRF 粗排，没有第二阶段的语义相关性判断。无关文档被检索回来后原样返回。

### 1.2 目标

当前最需要解决的三件事：

1. **搜索结果不要离题**（降低无关内容出现率）
2. **正确内容能进前 20**（提升召回覆盖率）
3. **最好能进前 5**（提升精排准确度）

围绕这三个目标，按照**搜索质量优先**的原则，排定以下优先级：

```
最高优先：实体识别 → 主实体硬约束 → analysis/feedback 补 embedding
次优先：查询改写 → 多路召回 → 云端 Rerank
第三层：query-aware snippet → 简单 final ranking（无需多维度调参）
未来增强：隐式反馈学习
```

---

## 2. 总体架构

```
search(query_text=str)
    │
    ▼
┌───────────────────────────────────────────────┐
│  阶段1: Entity Resolver     | 实体识别+链接    │
│  阶段2: Query Rewrite        | 关键词扩展      │
├───────────────────────────────────────────────┤
│  阶段3: Multi-Recall         | BM25+Vector     │
│  阶段4: Hard Filter          | 场景化过滤      │
│  阶段5: RRF Fusion           | 加权融合        │
├───────────────────────────────────────────────┤
│  阶段6: Rerank               | 云精排(DashScope)│
│  阶段7: Final Ranking        | 简单排序        │
└───────────────────────────────────────────────┘
    │
    ▼
  返回 top-20 搜索结果
```

延迟预算：
| 阶段 | 方式 | 延迟 |
|------|------|------|
| 1-2 | DB 查询 + 规则 | <10ms |
| 3 | ES + pgvector 并行 | ~50ms |
| 4 | 规则过滤 | <1ms |
| 5 | RRF 计算 | <1ms |
| 6 | DashScope Rerank API | ~200-500ms |
| 7 | 加权融合 | <1ms |
| **Total** | | **~300-600ms** |

---

## 3. 输入格式

### 3.1 接口不变

```json
{
  "query_text": "ST景谷 600265",
  "mode": "hybrid",
  "filters": null,
  "weights": null,
  "limit": 20,
  "include_explanations": false,
  "date_range": null
}
```

`query_text` 由上游 agent 提供，可能是：
- agent 处理过的关键词串：`"ST景谷 600265"`
- agent 处理后带扩展词：`"汇银木业 合同诈骗 崔会军"`
- 原始句子：`"贵州茅台最近为什么下跌，批价和业绩怎么看"`

**SearchService 内部不做区分**，统一视为查询字符串。Entity Resolver 从字符串中识别实体，Query Rewriter 从字符串中提取关键词做扩展。

### 3.2 请求 schema 不变

```python
class SearchRequest(BaseSchema):
    query_text: str
    mode: SearchMode = "hybrid"
    filters: dict | None = None
    weights: dict | None = None
    limit: int = 20
    include_explanations: bool = False
    date_range: DateRange | None = None
```

**不改 API，不改 schema，不增加任何新字段。**

### 3.3 内部表示

Service 内部解析后生成以下结构，仅用于流水线各阶段传递，不暴露给 API：

```python
{
    "query_text": "ST景谷 600265",          # 原始 query_text，不变
    "resolved_entities": [
        {"type": "stock", "code": "600265", "name": "ST景谷",
         "aliases": ["景谷林业", "*ST景谷"]},
    ],
    "main_entity_name": "ST景谷",            # 阶段1 识别的主实体
    "main_entity_type": "stock",            # 决定阶段4 Hard Filter 策略
    "expanded_keywords": [                  # 阶段2: 实体名+alias+ticker + 关系图扩展 + 同义词
        "ST景谷", "600265", "景谷林业", "*ST景谷",
        "合同诈骗", "崔会军",
    ],
    "entity_context": {                     # 阶段2: 关系图实体→匹配信号(RRF加权用)
        "合同诈骗": 0.6,                     #  非强制，命中了加分
    },
    "time_bias_days": None,                 # 无时间偏向词 → 不限时间
}

---

## 4. 阶段1：Entity Resolver（实体识别）— 最高优先

### 4.1 为什么实体识别是最优先

搜索"贵州茅台下跌"，结果混进"中国广核FCD深度分析"——不是因为语义模型不行，而是系统不知道"这次搜索的主实体是贵州茅台"。必须先明确主实体，后续 Hard Filter 才知道"至少标题/正文里得提到茅台"。

### 4.2 实体类型

```python
ENTITY_TYPES = ["stock", "industry", "strategy", "macro_indicator", "event"]
```

- `stock`：需要 ticker 字段不为空；最主要的实体类型
- `industry`：node_type = "industry"
- `strategy`：node_type = "strategy"

### 4.3 匹配规则（复用现有 `_entity_match_search`）

按优先级：

| 优先级 | 匹配方式 | Score | 示例 |
|--------|---------|-------|------|
| 1 | ticker 精确匹配 | 1.2 | `600519` → 贵州茅台 |
| 2 | name 精确匹配 | 1.0 | `贵州茅台` → 贵州茅台 |
| 3 | alias 精确匹配 | 0.95 | `茅台` → 贵州茅台 |
| 4 | name ILIKE | 0.8 | `长安` → 长安汽车 |
| 5 | alias ILIKE | 0.7 | `宁王` → 宁德时代 |
| 6 | industry ILIKE | 0.5 | `光伏` → 光伏产业链 |

### 4.4 主实体判定

- 如果最高分的匹配实体得分 ≥ 0.8 且类型为 `stock` → 设定为 `main_entity`
- 如果多个匹配实体得分接近（差 < 0.1）且都是 `stock` → 多个主实体，Hard Filter 要求至少命中其一
- 如果只有 `industry`/`strategy` 实体 → `main_entity_type` 为非 stock，Hard Filter 策略放宽
- 如果无实体匹配 → 不做实体硬约束，仅依赖关键词

### 4.5 已有 Index 利用

现有的 `_entity_match_search` 直接复用，不需要新建 Index。

---

## 5. 阶段2：Query Rewrite（关键词扩展）

### 5.1 设计原则

目标不是"生成聪明的查询"，而是**补齐用户没说全但搜索需要的词**。

扩展来源分两类：
| 场景 | 扩展方式 | 说明 |
|------|---------|------|
| 有实体命中 | ImpactPathService 关系图遍历 | 沿实体关系图找出关联实体，带权重 |
| 无实体命中或 query 中非实体词 | 静态同义词词典 | funNLP 财经词库，零延迟 |

### 5.2 有实体命中：ImpactPathService 关系图遍历

```
Entity Resolver 命中: "贵州茅台" (stock, 600519)
    │
    ▼ ImpactPathService.find_paths(entity_id=贵州茅台.id, depth=2)
    │
    ├─ 1跳关系 (strength ≥ 0.7):
    │    贵州茅台 → 白酒行业 (industry_of, strength=0.9)
    │    贵州茅台 → 飞天茅台 (product, strength=0.95)
    │    贵州茅台 → 批价 (price_indicator, strength=0.8)
    │    贵州茅台 → 600519 (ticker, strength=1.0)
    │    贵州茅台 → 茅台经销商 (stakeholder, strength=0.7)
    │
    └─ 2跳关系 (strength ≥ 0.4, 衰减后):
         白酒行业 → 消费税 (policy, strength=0.7 → 衰减 0.7*0.5*0.7=0.25)
         白酒行业 → 五粮液 (competitor, strength=0.6 → 衰减 0.35)
         飞天茅台 → 渠道库存 (supply_chain, strength=0.6 → 衰减 0.18)
```

扩展出的关联实体按 strength 分两档：
| 档位 | 条件 | 用途 |
|------|------|------|
| 高相关（1跳） | strength ≥ 0.4 | 作为 expanded_keywords 加入搜索，BM25+向量都搜 |
| 低相关（2跳） | strength < 0.4 | 仅用于 RRF 加权的 entity_context 匹配信号，不参与关键词搜索 |

### 5.3 无实体命中：静态同义词词典

```
query 中非实体词: "下跌", "业绩", "风险"
    ↓ 查静态词典
SYNONYM_MAP = {
    "下跌": ["下跌", "回调", "跌幅", "走低", "下挫"],
    "上涨": ["上涨", "涨幅", "走高", "拉升"],
    "业绩": ["业绩", "财报", "营收", "利润", "净利润"],
    "风险": ["风险", "隐患", "不确定性", "利空"],
    ...
}
```

词典来自 funNLP 财经词库，打包在 `kbquant/assets/finance_synonyms.txt`，启动时加载到内存。

### 5.4 合并策略

```python
def expand_keywords(
    query_text: str,
    matched_entities: list[EntityResult],   # 阶段1输出
    impact_paths: dict | None,             # ImpactPathService 输出
) -> ExpandedQuery:
    keywords = set()

    # 1. 保留原始 query_text 中所有非停用词
    query_words = [w for w in query_text.split() if w not in STOPWORDS]
    keywords.update(query_words)

    # 2. 实体 alias + ticker（阶段1已解析，直接合并）
    for ent in matched_entities:
        keywords.add(ent.name)
        if ent.code:
            keywords.add(ent.code)
        for alias in ent.aliases or []:
            keywords.add(alias)

    # 3. ImpactPathService 关系图扩展
    entity_context = {}  # 用于 RRF 加权
    if impact_paths:
        for path in impact_paths["paths"]:
            if path["total_impact_strength"] >= 0.4:
                # 高相关：加入搜索关键词
                target = path["path"][-1]
                keywords.add(target["entity_name"])
            else:
                # 低相关：仅用于 RRF 匹配信号
                target = path["path"][-1]
                entity_context[target["entity_name"]] = path["total_impact_strength"]

    # 4. 非实体词的同义词扩展（从静态词典）
    entity_names = {e.name for e in matched_entities}
    for word in query_words:
        if word not in entity_names:  # 不是实体 → 走同义词
            keywords.update(FINANCE_SYNONYMS.get(word, [word]))

    return ExpandedQuery(
        expanded_keywords=list(keywords),
        entity_context=entity_context,  # {entity_name: strength}
    )
```

### 5.5 Entity Context 的后续使用

`entity_context` 传到阶段 4（Hard Filter）和阶段 5（RRF Fusion）：

- **Hard Filter**：stock 主实体必须命中，entity_context 中的词命中了是加分信号（不强制）
- **RRF Fusion**：entity_context 匹配到文档时加成 `w_entity_context * strength / (k+1)`

```python
# RRF 阶段
for ctx_name, ctx_strength in entity_context.items():
    if ctx_name in doc.title or ctx_name in doc.body[:500]:
        rrf += w_entity_context * ctx_strength / (k + 1)
```

### 5.6 Entity Relationship 表覆盖

现有的 `ImpactPathService` 依赖 `EntityRelationship` 表中已建立的实体关系。如果 Entity Relationship 表覆盖不全，V1 可以通过世界知识/静态词典补充低相关扩展词，V2 让关系图逐步完善。

---

## 6. 阶段3：Multi-Recall（多路召回）

### 6.1 设计

同一组扩展关键词走三条路，并行：

```
expanded_keywords → ──┬── ES BM25（每个词用 OR 连接，title^2 + body/content）
                       ├── pgvector（主 query embedding → 各表 cosine 召回）
                       └── entity_match（阶段1 已执行，复用结果）
```

### 6.2 搜索目标：SearchService 自主决定

**忽略 `filters.target_tables` 输入**。Agent 不需要指定查哪些表。SearchService 根据实体类型自主决定：

```python
# 根据阶段1的实体解析结果，自动决定要搜索的表和字段

TABLE_STRATEGY = {
    "stock": {
        "tables": ["raw_information", "analyses", "nodes"],
        "es_fields": {
            "raw_information": ["title^2", "body"],
            "analyses": ["title^2", "content"],
            "nodes": ["name^2", "description", "node_type"],
        },
    },
    "industry": {
        "tables": ["raw_information", "analyses"],
        "es_fields": {
            "raw_information": ["title^2", "body"],
            "analyses": ["title^2", "content"],
        },
    },
    "strategy": {
        "tables": ["raw_information", "feedbacks"],
        "es_fields": {
            "raw_information": ["title^2", "body"],
            "feedbacks": ["title^2", "lessons_learned"],
        },
    },
    "default": {  # 无实体
        "tables": ["raw_information", "analyses"],
        "es_fields": {
            "raw_information": ["title^2", "body"],
            "analyses": ["title^2", "content"],
        },
    },
}

### 6.3 召回配置

```python
RECALL_CONFIG = {
    "bm25_limit": 100,       # ES 每张表召回上限
    "vector_limit": 100,     # pgvector 每张表召回上限
    "max_total_candidates": 300,  # 合并去重后上限
}
```

### 6.4 并发模型

现有 `search` 方法已经实现了 ES 和 PG 的并行，不变。ES 操作之间独立，与 DB 操作并行。ES 组内部和 DB 组内部各自顺序执行（共享连接约束）。

### 6.5 前置条件：analysis 和 feedback 必须补 embedding

当前 analysis 的 embedding 列基本为空——这就是为什么向量召回对 analysis 无效。

必须在 pipeline 中补上 analysis 和 feedback 的 embedding 生成。使用现有的 `text-embedding-v4` 模型。这是批量任务，在入库时完成，不影响搜索延迟。

---

## 7. 阶段4：Hard Filter（场景化过滤）

### 7.1 设计原则

过滤的目标是去掉"绝对不相关"的候选，而不是追求精确。规则根据主实体类型分场景：

### 7.2 场景 A：主实体是 stock（代码/股票名）

用户搜索的是某只股票，**必须要求结果中提到该股票**。

```python
if main_entity_type == "stock":
    for doc in candidates:
        title = doc.title.lower()
        body_head = doc.body[:500].lower() if doc.body else ""
        text = title + " " + body_head

        # 强制要求：title 或 body 前 500 字中包含主实体名或 ticker
        if not any(e.lower() in text for e in [main_entity_name, main_entity_code]):
            DROP doc  # 丢弃
```

### 7.3 场景 B：主实体是 industry/strategy，或无主实体

不强制单一实体匹配，使用宽松规则：

```python
else:
    for doc in candidates:
        # 向量分数过低且无 BM25 命中 → 丢弃
        if doc.only_vector_hit and doc.vector_score < 0.3:
            DROP doc

        # 有 3+ 个关键词但 title 一个都没命中 → 降权（不丢弃）
        if len(expanded_keywords) >= 3 and doc.title_hit_count == 0:
            DEMOTE doc
```

### 7.4 实现要点

- Filter 在 RRF 之前，直接处理 candidates dict
- 每条规则独立执行
- DROP/降权的结果记日志
- ~200 条候选全量过滤 <1ms

---

## 8. 阶段5：RRF Fusion（加权融合）

### 8.1 公式

```python
rrf = (
    w_bm25   / (k + bm25_rank)    # BM25 关键词匹配
  + w_vector / (k + vector_rank)  # 语义向量
  + w_entity / (k + entity_rank)  # 实体精确匹配
  + w_struct / (k + 1)            # 文档重要性（structural）
  + w_time   / (k + 1)            # 时间新鲜度
)

k = 60  # 平滑常数
```

### 8.2 默认权重

```python
RRF_WEIGHTS = {
    "bm25": 1.2,
    "vector": 1.0,
    "entity": 2.5,       # 实体精确匹配权重最高
    "structural": 0.2,
    "time_decay": 0.15,  # 可被时间敏感关键词动态提升到 0.25+
}
```

### 8.3 设计原则

RRF 只能粗略融合召回结果，**不要花精力过度调参**。它的职责是把候选缩减到精排池，真正的相关性判断交给阶段6 的 Reranker。权重用默认值即可，后续根据实际效果微调。

---

## 9. 阶段6：Rerank（精排）

### 9.1 为什么 Rerank 是核心

RRF 只能融合多条通道的排名，不能判断"这篇文档是否真的回答了用户问题"。Rerank 是用语义模型逐条做 query-document 联合推理，这是解决 BM25 主语不准问题的关键。

### 9.2 API 调用

使用阿里云 DashScope qwen3-rerank API（云端服务，无需本地 GPU）：

```
POST https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank

{
    "model": "qwen3-rerank",
    "input": {
        "query": "贵州茅台最近为什么下跌，批价和业绩怎么看",
        "documents": [             ← RRF top-50
            "茅台代销政策落地深度分析：渠道变革的短期催化...",
            "多晶硅期货逼近前低深度分析...",
            ...
        ]
    },
    "parameters": {
        "return_documents": true,
        "top_n": 50
    }
}
```

> **为什么是 top-50 而不是 top-20**：精排池太小会导致"正确内容没进池子就没机会排上去"。
> 20 条太保守，50 条给 Reranker 足够的候选空间。

### 9.3 API 参数

| 参数 | 值 |
|------|-----|
| 模型 | `qwen3-rerank` |
| RPM | 5400 |
| 上下文窗口 | 30k tokens |
| 单次调用文档数 | 50（RRF top-K） |
| 超时 | 2s |
| 认证 | 复用现有 DashScope `embedding_api_key` |

### 9.4 降级链

```
Rerank API 调用
  ├─ 成功 → normal mode（reranker 权重 0.5）
  └─ 超时(>2s) / 错误 / 限流(429) → fallback mode（reranker 权重 0）
       └─ 最终排序退化为 RRF + entity + time + type
```

### 9.5 输入文档文本

Reranker 输入用 `title + snippet`，snippet 选取策略见 9.6 节。不是简单截取文档前 N 字。

### 9.6 Query-aware Snippet 选取

#### 设计原则

Reranker 的输入如果只是文章开头，很可能看不到真正相关的段落。必须在文档中找到与 query 最相关的一段文字。

#### 算法

```python
def extract_snippet(body: str, query_terms: set[str],
                    max_length: int = 512) -> str:
    """
    在文档 body 中选取与 query 关键词最密集的窗口。
    参考 Google 的搜索结果 snippet 选取逻辑。
    """
    if len(body) <= max_length:
        return body

    # 找到每个 query term 在文档中的出现位置
    positions = []
    for term in query_terms:
        pos = 0
        while True:
            idx = body.find(term, pos)
            if idx == -1:
                break
            positions.append(idx)
            pos = idx + 1

    if not positions:
        return body[:max_length]       # 无命中 → 取开头

    # 以匹配位置为中心选最佳窗口
    best_start = 0
    best_score = 0
    half_window = max_length // 2

    for pos in positions:
        start = max(0, pos - half_window)
        end = min(len(body), start + max_length)
        score = sum(1 for p in positions if start <= p < end)
        if score > best_score:
            best_score = score
            best_start = start

    snippet = body[best_start:best_start + max_length]

    # 调整到句边界（不截断在句子中间）
    stop_chars = ["。", "\n"]
    for sep in stop_chars:
        last = snippet.rfind(sep, 0, len(snippet) // 4)
        if last != -1:
            snippet = snippet[last + 1:]
            break
    for sep in stop_chars:
        next_ = snippet.find(sep, len(snippet) * 3 // 4)
        if next_ != -1:
            snippet = snippet[:next_ + 1]
            break

    return snippet.strip()
```

#### 各类型正文字段

| 类型 | 正文字段 |
|------|---------|
| raw_information | `body` |
| analysis | `content` |
| feedback | `lessons_learned`（若无则取 `content`） |
| node | `state_summary` 或 `core_logic` |

#### Snippet 三用途

| 用途 | 长度 | 说明 |
|------|------|------|
| Reranker 输入 | 512 字 | 传入 DashScope API |
| 搜索结果展示 | 200 字 | 前端列表展示 |
| 全文 | 不限 | fetch_by_ids 返回 |

---

## 10. 阶段7：Final Ranking（最终排序）

### 10.1 设计原则

**简洁，不超过 5 个维度**。Reranker 好了就多信它，不好就退回 RRF。不要叠多维度调黑盒。

### 10.2 公式

```python
final_score = (
    alpha * reranker_score              # qwen3-rerank 分数（API 返回，0~1）
  + beta  * norm_rrf_score              # RRF 分数归一化到 0~1
  + gamma * entity_match_boost          # 实体匹配加成
  + delta * time_freshness              # 时间新鲜度
  + epsilon * result_type_priority      # 结果类型优先级（根据实体类型）
)
```

### 10.3 场景权重

| 场景 | alpha | beta | gamma | delta | epsilon |
|------|-------|------|-------|-------|---------|
| normal（reranker 可用） | 0.5 | 0.2 | 0.1 | 0.1 | 0.1 |
| fallback（reranker 不可用） | 0 | 0.5 | 0.2 | 0.15 | 0.15 |

### 10.4 各分量含义

**entity_match_boost**：
- title 包含主实体名（精确匹配 +0.15，包含匹配 +0.08）
- ticker 精确匹配 +0.2

**time_freshness**：复用现有 `time_decay`，不改变。

**result_type_priority**：根据主实体类型调整：

```python
TYPE_PRIORITY = {
    "stock": {           # 股票搜索：信息优先
        "raw_information": 1.0,
        "analysis": 0.8,
        "node": 0.7,
        "feedback": 0.6,
    },
    "industry": {        # 行业搜索：分析优先
        "analysis": 1.0,
        "raw_information": 0.8,
        "node": 0.7,
        "feedback": 0.6,
    },
    "strategy": {        # 策略搜索：feedback 优先
        "feedback": 1.0,
        "analysis": 0.8,
        "raw_information": 0.7,
        "node": 0.6,
    },
    "default": {
        "raw_information": 1.0,
        "analysis": 0.8,
        "node": 0.7,
        "feedback": 0.6,
    },
}
```

---

## 11. 日志与评测体系

### 11.1 日志要求

搜索系统不是一次写对的，后续主要靠 case-by-case 调试。每个阶段都必须打日志：

```
[search_id=xxx] 阶段1: entity_resolver → main_entity=贵州茅台(type=stock, code=600519)
[search_id=xxx] 阶段2: query_rewrite → expanded_keywords=30个
[search_id=xxx] 阶段3: recall → bm25=85, vector=67, entity=12, merged=142
[search_id=xxx] 阶段4: hard_filter → dropped=23 (stock_entity_absent=18, vector_low=5)
[search_id=xxx] 阶段5: rrf → top_50生成
[search_id=xxx] 阶段6: rerank → api_latency=342ms, rerank_applied=true
[search_id=xxx] 阶段7: final_ranking → top_20生成
[search_id=xxx] 最终结果: #1 茅台代销政策落地深度分析(score=0.91) #2 酒价内参...(score=0.87) ...
```

### 11.2 评测基准

维护 30-50 个真实复杂 query，每个标注：
- `must_include`：理想结果（必须出现在 top-10）
- `must_exclude`：绝对不应该出现的结果
- `doc_type_priority`：期望的结果类型排序

**每次改动前后对比评测基准，否则不知道整体有没有变好。**

### 11.3 回归测试

复用 `search_eval_10cases.txt` 作为基础回归。重点关注：
- 无关内容（如"中国广核"出现在茅台搜索）是否被过滤
- 相关结果 top-5 占比
- feedback 类型召回率

---

## 12. 迁移策略

```
Phase 1（本周）:
  阶段1 Entity Resolver（复用现有 _entity_match_search）
  + 阶段4 Hard Filter（stock 场景实体硬约束）
  + analysis/feedback embedding 批量补录
  → 不改 API 接口，不影响现有调用方

Phase 2（下周）:
  阶段2 Query Rewrite（关键词扩展）
  + 阶段3 Multi-Recall（增强召回）
  + 阶段5 RRF Fusion（微调权重）
  → 搜索覆盖面提升

Phase 3（第三周）:
  阶段6 Rerank（DashScope API）
  + 阶段7 Final Ranking（简单加权）
  + Query-aware Snippet
  + 详细日志
  → 搜索质量核心提升

Phase 4（稳定后）:
  评测基准建立（30-50 query）
  → 可持续优化的基础

Phase 5（未来）:
  隐式反馈学习系统（search_cache + fetch 信号）
  → 搜索个性化 + 自动改善
```

每个 phase 独立可发布，不破坏现有 API。

---

## 13. 配置汇总

### 13.1 新增 settings

```python
# 搜索流水线
search_rrf_k: int = 60
search_bm25_recall_limit: int = 100
search_vector_recall_limit: int = 100
search_max_candidates: int = 300

# Rerank API
rerank_model: str = "qwen3-rerank"
rerank_api_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
rerank_top_k: int = 50
rerank_timeout_seconds: float = 2.0

# Snippet
snippet_reranker_max_length: int = 512
snippet_display_max_length: int = 200
```

### 13.2 RRF 默认权重

```python
DEFAULT_RRF_WEIGHTS = {
    "bm25": 1.2,
    "vector": 1.0,
    "entity": 2.5,
    "structural": 0.2,
    "time_decay": 0.15,
}
```

### 13.3 Final Ranking 权重

```python
FINAL_RANKING_WEIGHTS = {
    "normal":   {"alpha": 0.5, "beta": 0.2, "gamma": 0.1, "delta": 0.1, "epsilon": 0.1},
    "fallback": {"alpha": 0.0, "beta": 0.5, "gamma": 0.2, "delta": 0.15, "epsilon": 0.15},
}
```

---

## 14. 文件结构规划

```
kbquant/
├── assets/
│   ├── finance_synonyms.txt            # 金融领域同义词词典
│   └── stopwords.txt                    # 搜索停用词
│
├── services/
│   ├── search_service.py                # [修改] 7 阶段流水线（主入口 + 日志）
│   ├── entity_resolver.py               # [新] 阶段1：实体识别
│   ├── query_rewriter.py                # [新] 阶段2：关键词扩展
│   ├── recall_service.py                # [新] 阶段3：多路召回（从 search_service 抽取）
│   ├── hard_filter.py                   # [新] 阶段4：场景化硬过滤
│   ├── fusion_service.py                # [新] 阶段5：RRF 融合
│   ├── rerank_service.py                # [新] 阶段6：精排（DashScope API）
│   ├── final_ranking.py                 # [新] 阶段7：最终排序
│   ├── snippet_service.py               # [新] Query-aware snippet 提取
│   ├── search_logger.py                 # [新] 搜索日志工具
│   └── embedding_service.py             # [不变]
│
└── api/
    └── v1/
        └── search.py                    # [微调] 传入 query_text 入口
```

---

## 15. 测试计划

### 15.1 单元测试

| 模块 | 测试内容 |
|------|---------|
| entity_resolver | ticker/name/alias/industry 各路径匹配；主实体判定逻辑 |
| query_rewriter | 同义词扩展正确性；实体 alias + ticker 合并；未知词回退 |
| hard_filter | stock 场景实体硬约束；非 stock 场景宽松规则 |
| fusion_service | RRF 公式数值验证 |
| final_ranking | normal/fallback 两种场景权重策略 |
| snippet_service | query-aware 窗口选取正确性；无匹配回退；句边界调整 |

### 15.2 集成测试

- 复用 `search_eval_10cases.txt` 做回归对比
- 扩展至 30-50 query 的评测基准
- 验证无关内容过滤效果
- 验证端到端延迟 <600ms

### 15.3 性能测试

- 100 QPS 下 DashScope API 限流行为
- 降级链正确触发
- 内存占用

---

## 16. 未决项

- [ ] 阿里云 DashScope rerank API 实际延迟和 RPM 上限验证
- [ ] analysis/feedback embedding 全量补录的任务编排和进度监控
- [ ] 评测基准的 30-50 query 标注（需要人工标注 must_include / must_exclude）
- [ ] Phase 5 隐式反馈系统（设计已完成，待时机成熟时实现）
