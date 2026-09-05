# 阶段1：Entity Resolver（实体识别与链接）

## 目的

从 `query_text` 中识别金融实体，并将其链接到知识库（WorldNode / Entity 表），确定主实体类型。

## 设计原则

- **复用 `kbquant/pipeline/matcher.py` 的 EntityMatcher**：Aho-Corasick 自动机 + 边界检查 + 货币排除等逻辑已成熟，不需要重写。
- **复用 `data/entities/` 下的 18 个 JSON 词表**：自动机已加载这些词表。
- **复用 WorldNode 表的 `_entity_match_search`**：作为补充通道，匹配 ticker/name/alias。
- **零 DB 查询**（纯内存匹配 + 已加载的自动机），延迟 < 5ms。

## 输入

```python
query_text: str  # 如 "ST景谷 600265" 或 "贵州茅台 最近 为什么 下跌"
```

## 输出

```python
@dataclass
class EntityResult:
    resolved_entities: list[dict]   # 匹配到的实体列表
    main_entity_name: str | None   # 最高分实体的名称
    main_entity_type: str | None   # "stock"|"industry"|"strategy"|"person"|"tech_company"|None
    main_entity_code: str | None   # ticker 代码（仅 stock）
    main_entity_id: str | None     # WorldNode ID

# resolved_entities 中每个元素的格式:
{
    "name": "贵州茅台",
    "entity_type": "stock",     # 来自 entity JSON 的 entity_type 字段
    "score": 0.95,              # 匹配得分
    "matched_term": "贵州茅台",  # 实际匹配到的词
    "code": "600519",           # ticker（仅 stock）
    "aliases": ["茅台", "飞天茅台"],
    "industry": "白酒",
    "id": "uuid-xxx",           # WorldNode ID（从 DB 查到的）
}
```

## 实现步骤

### 步骤 1：EntityMatcher 内存扫描

```python
from kbquant.pipeline.matcher import EntityMatcher

# 启动时已加载，单例
matcher = get_entity_matcher()

# 扫描 query_text
matches = matcher.match(query_text, max_entities=10)
# matches: [{"name": "贵州茅台", "entity_type": "stock", "matched_term": "贵州茅台",
#             "position": 0, "occurrences": 1, "matched_terms": ["贵州茅台"]}, ...]
```

### 步骤 2：WorldNode 补充匹配

对 matcher 未命中的 query 词（空格分词后的 token），走 WorldNode 的 ILIKE 查询：

```python
# 只在 matcher 无命中时走 DB
unmatched_tokens = [t for t in query_tokens if t not in matched_names]

if unmatched_tokens:
    node_matches = await entity_match_search(" ".join(unmatched_tokens), session, limit=5)
    # 按优先级: ticker精确 → name精确 → alias精确 → ILIKE
    matches.extend(node_matches)
```

### 步骤 3：合并去重 + 主实体判定

```python
def resolve(query_text: str, session: AsyncSession) -> EntityResult:
    # Step 1: matcher
    matcher = get_entity_matcher()
    matches = matcher.match(query_text, max_entities=10)

    # Step 2: WorldNode 补充（对未匹配到的 token）
    matched_names = {m["name"] for m in matches}
    query_tokens = [w for w in query_text.split() if w not in STOPWORDS]
    unmatched = [t for t in query_tokens if t not in matched_names]
    if unmatched:
        node_matches = await entity_match_search(" ".join(unmatched), session, limit=5)
        matches.extend(node_matches)

    # Step 3: 去重（同 name 取最高分）
    deduped = {}
    for m in matches:
        name = m["name"]
        if name not in deduped or m["score"] > deduped[name]["score"]:
            deduped[name] = m

    resolved = sorted(deduped.values(), key=lambda m: -m["score"])

    # Step 4: 主实体判定
    main = None
    for ent in resolved:
        if ent.get("entity_type") in ("stock", "company") and ent["score"] >= 0.8:
            main = ent
            main["entity_type"] = "stock"  # 归一化
            break
        elif ent.get("entity_type") in ("strategy",) and ent["score"] >= 0.8:
            main = ent
            break
        elif ent.get("entity_type") in ("industry",) and ent["score"] >= 0.5:
            if not main or ent["score"] > main["score"]:
                main = ent
            break
        elif ent.get("entity_type") in ("person",) and ent["score"] >= 0.8:
            main = ent
            break

    # Step 5: 多 stock 实体场景
    if main is None:
        stock_matches = [e for e in resolved if e.get("entity_type") == "stock" and e["score"] >= 0.6]
        if len(stock_matches) >= 2:
            main = stock_matches[0]
            main["multi_stock"] = True  # Hard Filter 放宽为"命中至少一个"

    return EntityResult(
        resolved_entities=resolved,
        main_entity_name=main["name"] if main else None,
        main_entity_type=main.get("entity_type") if main else None,
        main_entity_code=main.get("code") if main else None,
        main_entity_id=main.get("id") if main else None,
    )
```

### 主实体判定规则

| 条件 | 结果 |
|------|------|
| 有 type=stock + score ≥ 0.8 | main=stock（最高分那个） |
| 多个 stock + score ≥ 0.6 | main=stock + multi_stock=True |
| 有 type=strategy + score ≥ 0.8 | main=strategy |
| 有 type=industry + score ≥ 0.5 | main=industry |
| 有 type=person + score ≥ 0.8 | main=person |
| 以上都不满足 | main=None（无实体约束） |

## 错误处理

- **空 query_text**：返回 `EntityResult([], None, None, None, None)`
- **matcher 无命中**：仅走 WorldNode 补充匹配
- **DB 不可用**：跳过 WorldNode，仅返回 matcher 结果

## 文件

```
kbquant/services/search/entity_resolver.py    [新] 本文件
kbquant/pipeline/matcher.py                    [已有] EntityMatcher + 词表加载
kbquant/data/entities/                         [已有] 18 个 JSON 词表
```

## 依赖

| 依赖 | 来源 | 状态 |
|------|------|------|
| EntityMatcher | `kbquant.pipeline.matcher` | 已有 |
| _entity_match_search | `search_service.py` | 已有，直接复用 |
| STOPWORDS | `kbquant/assets/stopwords.txt` | 需新建 |
| data/entities/*.json | `data/entities/` | 已有 |

## 测试

- stock 实体精确匹配 → main_entity_type="stock"
- strategy 实体匹配 → main_entity_type="strategy"
- 多个 stock + 分数相近 → multi_stock=True
- 无实体匹配 → main_entity_type=None
- "中国广核" 不出现在 "贵州茅台" 的匹配中（Aho-Corasick 不跨句合并）
