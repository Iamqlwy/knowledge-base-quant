# Service 层设计文档

## 一、架构概览

Service 层是知识库的业务逻辑核心，位于 API 路由（薄层，仅做参数解析和响应序列化）和 Model 层（纯数据访问对象）之间。每个 Service 接收 `AsyncSession`，在本事务内完成所有数据库操作。

```
API 路由 ──▶ Service ──▶ SQLAlchemy Model ──▶ PostgreSQL
  (薄层)      (业务逻辑)      (ORM 映射)        (持久化)
```

**设计原则**：

- **无状态**：Service 不持有请求间的状态，每次请求从依赖注入获取 `AsyncSession`
- **单事务**：一个 HTTP 请求的所有数据库操作在同一个异步事务内完成（由 `dependencies.py` 的 `get_db` 在请求结束时统一 `commit` 或 `rollback`）
- **sync flush**：写操作后立即 `await self.session.flush()` 确保数据库生成的默认值（UUID、时间戳）被回填到对象中，调用方可以立即拿到完整的 ORM 对象
- **子查询 count**：所有分页方法使用 `select(func.count()).select_from(count_base.subquery())` 模式，避免 `COUNT` 和主查询的会话冲突

---

## 二、Service 全景图

| Service | 对应功能 | 核心职责 |
|---|---|---|
| `InformationService` | 功能1(入库), 功能2(去重) | 资讯 CRUD、SHA-256 精确去重、去重关系记录 |
| `EntityService` | 功能3(实体识别) | 实体 CRUD、实体-资讯关联、实体关系管理 |
| `NodeService` | 功能4,5,6,19 | 节点 CRUD、挂载、状态版本化读写、摘要压缩 |
| `EmbeddingService` | 共享服务 | 文本转向量、LRU 缓存、并发流控、重试 |
| `AnalysisService` | 功能10 | 分析 CRUD、按类型/置信度搜索 |
| `EvidenceService` | 功能8 | 从任意目标回溯证据链 |
| `FeedbackService` | 功能12 | 复盘 CRUD、教训搜索 |
| `TradingService` | 功能11 | 交易操作 CRUD、多条件搜索 |
| `ImpactPathService` | 功能7 | BFS 图遍历查找影响路径 |
| `SimilarityService` | 功能9 | 向量相似度找历史案例 |
| `PipelineService` | 功能13 | 处理队列状态机、统计 |
| `ValidityService` | 功能14 | 时效条目管理、过期/延期 |
| `ConflictService` | 功能15 | 冲突记录、解决 |
| `RankingService` | 功能16 | 重要性评分、历史 |
| `SearchService` | 功能17,18 | 混合检索、多粒度搜索、任务型搜索 |
| `AsOfTimeService` | 功能20 | 历史时间点查询、状态 diff |

---

## 三、关键 Service 详解

### 3.1 InformationService — 资讯入口

**核心逻辑**：入库时先计算 SHA-256 哈希，如果已存在相同哈希的记录，直接返回已有记录（幂等入库）。不存在则写入。

```
ingest()
  ├── compute_content_hash(title, body)  → SHA-256
  ├── SELECT * WHERE content_hash = hash
  ├── 命中 → 返回已有记录（去重）
  └── 未命中 → INSERT 新记录
```

**关键方法**：

| 方法 | 说明 |
|---|---|
| `ingest()` | 资讯入库，自动 SHA-256 去重 |
| `check_duplicate()` | 仅检查不写入，返回是否重复及匹配列表 |
| `merge()` | 记录两条资讯的去重关系 |
| `get_duplicates()` | 查询某资讯的所有去重记录 |
| `list_items()` | 分页列表，支持 info_type/source/status 过滤 |

### 3.2 NodeService — 节点与版本化状态

这是整个 as-of-time 架构的核心。每次状态更新不是修改旧记录，而是**创建新版本**。

**版本化流程**：

```
update_state(node_id, new_fields)
  ├── 读取当前状态 (effective_to IS NULL)
  ├── 计算新版本号 version = old.version + 1
  ├── 关闭旧版本: old.effective_to = now
  ├── 写入新版本: new.effective_from = now, new.effective_to = NULL
  └── flush → 返回新状态
```

**时间点查询**：

```sql
-- get_state_at(node_id, timestamp)
SELECT * FROM node_states
WHERE node_id = $1
  AND effective_from <= $timestamp
  AND (effective_to IS NULL OR effective_to > $timestamp)
```

这保证了同一节点在任何时刻最多只有一条有效状态记录。

**关键方法**：

| 方法 | 说明 |
|---|---|
| `create_node()` | 创建世界节点 |
| `attach()` | 将资讯/分析挂载到节点 |
| `get_current_state()` | 查询节点当前状态（effective_to IS NULL） |
| `update_state()` | 版本化更新（关闭旧版本 + 创建新版本） |
| `get_state_history()` | 查询节点的版本历史链 |
| `get_state_at()` | 时间旅行查询 |
| `compress()` | 压缩节点摘要 |

### 3.3 EmbeddingService — 文本嵌入（共享服务）

这是整个系统最底层的共享设施，被 `SearchService`、`SimilarityService` 等调用。

**架构层次**：

```
EmbeddingService (实现 AbstractEmbeddingClient)
  └── get_text_embedding()  ← 模块级函数
        ├── 空文本 → [0.0] * dimension (零向量，不调 API)
        ├── LRU 缓存命中 → 返回缓存
        ├── IN_FLIGHT 去重 → await 进行中的 Future
        ├── API_SEMAPHORE(10) → _call_dashscope()
        │     └── 指数退避重试 3 次
        └── 写入缓存 + IN_FLIGHT 清理
```

**三级并发控制**：

| 层级 | 机制 | 作用 |
|---|---|---|
| 1. `_CACHE` + `_CACHE_LOCK` | LRU 字典缓存 | 避免对相同文本重复调用 API |
| 2. `_IN_FLIGHT` | Future 去重 | 相同文本并发请求只发起一次 API 调用 |
| 3. `_API_SEMAPHORE(10)` | 信号量 | 限制全局并发 API 调用数，防止触发限流 |

**空文本处理**：返回 `[0.0] * settings.embedding_dimension`（与配置维度匹配的零向量），确保下游 `cosine_distance()` 不会因维度不匹配而崩溃。

### 3.4 SearchService — 混合检索

对外提供两个入口：

| 方法 | 说明 |
|---|---|
| `hybrid_search()` | 组合向量相似度 + 全文搜索 + 结构化权重的混合检索 |
| `task_search()` | 任务型接口，Agent 只需描述意图，内部映射到对应检索策略 |
| `multi_granularity_search()` | 一次查询跨 raw_info/analysis/trading/feedback 四层 |

**任务路由**（`task_search`）：

```
task_type="find_evidence"         → _find_evidence()       → RawInformation 向量搜索
task_type="find_similar"          → _find_similar()        → Analysis 向量搜索
task_type="find_related_nodes"    → _find_related_nodes()  → WorldNode 名称模糊匹配
task_type="find_historical_analysis" → _find_historical_analysis() → Analysis 全量
```

这层抽象让上层 Agent 不需要知道底层用的是向量还是全文搜索。

### 3.5 ImpactPathService — 影响路径搜索

BFS 遍历 `entity_relationships` 表构成的实体关系图。

**遍历策略**：
- `direction="downstream"`：沿 `source_entity → target_entity` 方向
- `direction="upstream"`：沿 `target_entity → source_entity` 方向（逆查来源）
- `direction="both"`：同时查询两条方向的边
- 环检测：用 `visited` 集合记录已访问节点（起点除外，以允许从起点发散）
- 影响强度累加：沿途 `relationship.strength` 求和

**返回结构**：
```json
{
  "root": { "id": "...", "name": "..." },
  "paths": [
    {
      "path": [
        {"entity_id": "A", "relationship_type": null},
        {"entity_id": "B", "relationship_type": "supplies"},
        {"entity_id": "C", "relationship_type": "competes_with"}
      ],
      "total_impact_strength": 1.6
    }
  ]
}
```

### 3.6 EvidenceService — 证据回溯

给定任意目标（raw_info 或 node_state），向上追溯其分析派生链。

**target_type="raw_info"**：找到哪些分析引用了这条资讯
```
资讯 ──▶ 分析1 (root_raw_info_ids 包含此资讯 ID)
     ──▶ 分析2
```

**target_type="node_state"**：通过 `key_evidence_ids` 数组找到原始资讯
```
节点状态 ──▶ key_evidence_ids[0..9] ──▶ 原始资讯列表
```

`trace_node()` 是附带方法，通过 `node_attachments` 表反向查找节点挂载的原始证据。

### 3.7 AsOfTimeService — 时间旅行

**核心机制**：利用 `node_states` 的 `effective_from/effective_to` 区间，配合其他表的 `created_at`/`ingested_at` 时间戳过滤。

**`query_at(timestamp)`**：还原该时刻的系统全貌
- 节点状态：`effective_from <= ts < effective_to`
- 资讯总量：`ingested_at <= ts`
- 分析总量：`created_at <= ts`

**`diff_state(node_id, ts_a, ts_b)`**：比较同一节点在两个时间点的状态差异（预留结构，完整 diff 需要对比 JSONB 字段）。

关键：所有查询都加上 `<= timestamp` 约束，**防止未来信息泄露**。

### 3.8 PipelineService — 处理流水线

**状态机**：

```
ingested → deduped → entities_extracted → attached_to_nodes
    → analyzed → world_model_updated → trade_validated → completed
    ↘ error（任意步骤失败）
```

**`update_status()`** 把每次状态变更追加到 `status_history` JSONB 数组：
```json
[
  {"status": "ingested", "timestamp": "2026-05-20T10:00:00", "detail": null},
  {"status": "deduped", "timestamp": "2026-05-20T10:00:01", "detail": "exact match"}
]
```

### 3.9 ValidityService — 时效管理

管理 driver/risk/focus_point 等元素的有效期。每条记录有 `valid_from`/`valid_until` 区间。

- **`expire()`**：将 `valid_until` 设为当前时间，记录失效原因
- **`extend()`**：延长 `valid_until`，递增 `extended_count`
- **`check()`**：判断在指定时间点是否仍然有效

### 3.10 ConflictService — 冲突检测

**设计决策：冲突检测不是全自动的**。系统不自动判断两条信息是否有矛盾（这需要 LLM 判断）。Service 只负责：
- **记录**：Agent 明确声明矛盾后写入数据库
- **查询**：按节点、类型、解决状态过滤
- **解决**：记录解决方案和解决时间

---

## 四、数据流向图

```
                          EmbeddingService（共享）
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   SearchService        SimilarityService     (可扩展到其他)
          │                    │
          ▼                    ▼
   PostgreSQL pgvector   cosine_distance
   (HNSW 索引)           排序
          
═══════════════════════════════════════════════════════

资讯入库流：

RawInformation ──▶ InformationService.ingest() ──▶ SHA-256 去重
                       │
                       ▼
              PipelineService.get_or_create_queue_entry()
                       │
                       ▼
              EntityService.extract_entities()
                       │
                       ▼
              NodeService.attach() ──▶ NodeService.update_state()

═══════════════════════════════════════════════════════

时间旅行：

AsOfTimeService.query_at(ts)
  ├── NodeState: effective_from <= ts < effective_to
  ├── RawInformation: ingested_at <= ts
  └── Analysis: created_at <= ts
```

---

## 五、通用模式

### 5.1 分页模式

所有 list 方法使用同样的分页模板：

```python
query = select(Model)
count_base = select(Model)
# 应用过滤条件到两个查询
total = await session.execute(select(func.count()).select_from(count_base.subquery()))
total = total.scalar_one()
query = query.order_by(...).offset((page-1)*page_size).limit(page_size)
items = list((await session.execute(query)).scalars().all())
return items, total
```

用子查询包装 count 的原因是：当过滤条件包含 JOIN 或复杂 WHERE 时，直接 `select(func.count())` 可能和主查询产生冲突。

### 5.2 DI 注入模式

Service 不持有全局状态，每个方法调用时从 FastAPI 依赖注入获得 session：

```python
async def get_db():
    async with async_session() as session:
        yield session

async def get_xxx_service(session = Depends(get_db)):
    return XxxService(session)
```

路由中直接 `Depends(get_xxx_service)` 即可。

### 5.3 模块级单例

`embedding_service` 是模块级单例（带 LRU 缓存和并发控制），不依赖 session，可以在任何地方导入使用：

```python
from src.services.embedding_service import embedding_service
vector = await embedding_service.embed_text("some text")
```
