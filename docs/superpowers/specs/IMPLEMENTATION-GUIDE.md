# 搜索流水线实现指南

## 概要

将现有 `search_service.py` 的单体搜索方法重构为 7 阶段流水线。

## 前置条件

在开始任何代码之前，必须先完成以下数据准备工作：

- [ ] **analysis 和 feedback 的 embedding 全量补录**
  - 当前 analysis.embedding 列基本为空 → 向量召回对 analysis 无效
  - 当前 feedback.embedding 列为空 → 向量召回对 feedback 无效
  - 使用现有 `text-embedding-v4` 模型批量生成
  - 在入库 pipeline 中增加 embedding 生成步骤

- [ ] **实体词表扩充**（参考 `2026-06-16-entity-word-expansion.md`）
  - P0: strategy.json, scientific_term.json, chemical_term.json, semiconductor_term.json
  - P0: energy_term.json, pharma_biotech.json, tech_company.json, person.json 补充
  - P1: 其余词表

- [ ] **DashScope Rerank API Key 确认**
  - 复用现有 `embedding_api_key`
  - 确认 RPM=5400 限额
  - 测试 API 延迟（目标 <300ms）

- [ ] **评测基准**
  - 复用 `search_eval_10cases.txt` 作为基础回归
  - 扩展至 30-50 query，标注 must_include / must_exclude

## 实现顺序（Phase 1-4）

> **每个 Phase 完成后，必须跑通以下全部验证步骤，确认通过后再进入下一个 Phase。**

---

### Phase 1：实体识别 + 硬过滤 + Embedding 补录

**文件**：
- `kbquant/services/search/entity_resolver.py`（新）
- `kbquant/services/search/hard_filter.py`（新）
- embedding pipeline 批量补录逻辑

**关键点**：
1. Entity Resolver 直接复用 `matcher.py` 的 EntityMatcher + `search_service.py` 的 entity_match_search
2. Hard Filter 只做 stock 实体硬约束和向量下限两条规则（先不加关键词覆盖）
3. 把 analysis + feedback 的 embedding 补录作为独立任务（后台批量跑，不阻塞搜索上线）

**验证步骤**：

```bash
# 1. 单元测试：全部通过
pytest tests/test_search/test_entity_resolver.py -v
pytest tests/test_search/test_hard_filter.py -v

# 2. 手动 API 测试：搜索"贵州茅台 下跌"，结果不应包含"中国广核"
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"贵州茅台 最近为什么下跌","mode":"hybrid","limit":20}' \
  | python -c "import sys,json; r=json.load(sys.stdin);
  titles=[i['title'] for i in r['items']];
  assert not any('中国广核' in t for t in titles), 'FAIL: 中国广核 出现在茅台搜索中';
  assert not any('上海算力' in t for t in titles), 'FAIL: 上海算力 出现在茅台搜索中';
  print('PASS: 实体硬约束生效')"

# 3. 验证 Entity Resolver 正确识别
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"600519","mode":"hybrid","limit":5}' \
  | python -c "import sys,json; r=json.load(sys.stdin);
  assert any('贵州茅台' in i['title'] or '茅台' in i['title'] for i in r['items']), 'FAIL: 600519 未匹配到茅台';
  print('PASS: ticker 实体匹配正确')"

# 4. 验证 analysis embedding 补录完成
python -c "
import asyncio
from kbquant.database import LazyDB
from kbquant.models.analysis import Analysis
from sqlalchemy import func, select

async def check():
    db = LazyDB()
    async with db.session() as s:
        total = (await s.execute(select(func.count(Analysis.id)))).scalar()
        has_emb = (await s.execute(
            select(func.count(Analysis.id)).where(Analysis.embedding.is_not(None))
        )).scalar()
        coverage = has_emb / total * 100 if total else 0
        print(f'Analysis embedding coverage: {has_emb}/{total} ({coverage:.1f}%)')
        assert coverage > 90, f'FAIL: analysis embedding 覆盖率仅 {coverage:.1f}%'

asyncio.run(check())
"

# 5. 验证 feedback embedding 补录完成
python -c "
import asyncio
from kbquant.database import LazyDB
from kbquant.models.feedback import Feedback
from sqlalchemy import func, select

async def check():
    db = LazyDB()
    async with db.session() as s:
        total = (await s.execute(select(func.count(Feedback.id)))).scalar()
        has_emb = (await s.execute(
            select(func.count(Feedback.id)).where(Feedback.embedding.is_not(None))
        )).scalar()
        coverage = has_emb / total * 100 if total else 0
        print(f'Feedback embedding coverage: {has_emb}/{total} ({coverage:.1f}%)')
        assert coverage > 90, f'FAIL: feedback embedding 覆盖率仅 {coverage:.1f}%'

asyncio.run(check())
"
```

**Phase 1 通过标准**：
- [ ] 单元测试全部绿色
- [ ] 搜索"贵州茅台 下跌"→ 结果中无"中国广核"、"上海算力"等无关内容
- [ ] 搜索"600519"→ 结果中包含茅台相关内容
- [ ] analysis embedding 覆盖率 > 90%
- [ ] feedback embedding 覆盖率 > 90%

---

### Phase 2：查询改写 + 多路召回 + RRF 融合增强

**文件**：
- `kbquant/services/search/query_rewriter.py`（新）
- `kbquant/services/search/recall_service.py`（新，从 search_service 抽取）
- `kbquant/services/search/fusion_service.py`（新）

**关键点**：
1. Query Rewriter 先用静态同义词词典，ImpactPathService 关系图扩展加到 V2
2. Recall 复用现有 ES + pgvector + entity_match 三通道
3. RRF 权重从现有 `w_defaults` 迁移，新增 `entity_context` 维度
4. search 表由实体类型 + 关键词信号双维度决定

**验证步骤**：

```bash
# 1. 单元测试：全部通过
pytest tests/test_search/test_query_rewriter.py -v
pytest tests/test_search/test_recall_service.py -v
pytest tests/test_search/test_fusion_service.py -v

# 2. 验证 query rewriter 产出正确的 expanded_keywords
python -c "
from kbquant.services.search.query_rewriter import QueryRewriter
rw = QueryRewriter()
result = rw.rewrite('贵州茅台 下跌 业绩')
# 应该包含同义词扩展
assert '回调' in result.expanded_keywords or '跌幅' in result.expanded_keywords, 'FAIL: 同义词未扩展'
# 应该包含实体 alias
assert '600519' in result.expanded_keywords or '茅台' in result.expanded_keywords, 'FAIL: 实体alias未包含'
print('PASS: expanded_keywords:', sorted(result.expanded_keywords)[:10], '...')
"

# 3. 验证搜索表选择：strategy 类型包含 feedbacks
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"打板 炸板 失败经验 止损","mode":"hybrid","limit":20}' \
  | python -c "import sys,json; r=json.load(sys.stdin);
  types=set(i['result_type'] for i in r['items']);
  print(f'Types: {types}');
  # 至少应该有 raw_information 或 analysis（feedback 需要 embedding 补录后才能向量命中）
  assert len(r['items']) > 0, 'FAIL: 无结果';
  print('PASS: 搜索表选择正确')"

# 4. 验证 stock 查询包含正确的表
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"宁德时代 业绩 财报 出货量","mode":"hybrid","limit":20}' \
  | python -c "import sys,json; r=json.load(sys.stdin);
  types=set(i['result_type'] for i in r['items']);
  print(f'Types: {types}');
  assert 'analysis' in types, 'FAIL: stock+财报查询应包含analysis';
  print('PASS: stock+财报表选择正确')"

# 5. RRF 融合验证：打分差异应该比之前大
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"贵州茅台 下跌","mode":"hybrid","limit":10,"include_explanations":true}' \
  | python -c "import sys,json; r=json.load(sys.stdin);
  scores=[i['score']['total'] for i in r['items']];
  spread=max(scores)-min(scores) if scores else 0;
  print(f'Score spread: {spread:.4f}');
  assert spread > 0.002, f'FAIL: RRF 分数差异太小({spread:.4f})，同现有问题一样';
  print('PASS: RRF 分数差异足够')"
```

**Phase 2 通过标准**：
- [ ] 单元测试全部绿色
- [ ] expanded_keywords 包含同义词和实体 alias
- [ ] 搜索"打板 止损"→ 能返回结果（反馈表参与召回）
- [ ] 搜索"宁德时代 业绩"→ 结果包含 analysis 类型
- [ ] RRF top-10 分数差异 > 0.002（比改进前好）

---

### Phase 3：Rerank + 最终排序

**文件**：
- `kbquant/services/search/rerank_service.py`（新）
- `kbquant/services/search/snippet_service.py`（新）
- `kbquant/services/search/final_ranking.py`（新）
- `kbquant/services/search_service.py`（编排层，大规模修改）

**关键点**：
1. DashScope Rerank API 调用 + 降级链
2. Query-aware snippet 提取
3. Final Ranking 两套权重（normal/fallback）
4. 主入口 `search()` 重构为编排层，串联 7 个阶段

**验证步骤**：

```bash
# 1. 单元测试：全部通过
pytest tests/test_search/test_rerank_service.py -v
pytest tests/test_search/test_snippet_service.py -v
pytest tests/test_search/test_final_ranking.py -v

# 2. 验证 Rerank API 正常调用
python -c "
import asyncio
from kbquant.services.search.rerank_service import RerankService
async def test():
    svc = RerankService()
    result = await svc.rerank(
        '贵州茅台最近为什么下跌',
        [{'title': '茅台代销政策落地深度分析', 'snippet': '渠道变革的短期催化...'},
         {'title': '中国广核FCD深度分析', 'snippet': '常规工程进展...'}],
    )
    assert result[0].get('reranker_score', 0) > result[1].get('reranker_score', 0), \
        'FAIL: 茅台文章应该排在广核前面'
    print('PASS: Rerank API 正常，排序正确')

asyncio.run(test())
"

# 3. 验证降级链：模拟 API 不可用时仍然返回结果
python -c "
import asyncio
from kbquant.services.search.rerank_service import RerankService
async def test():
    svc = RerankService(timeout=0.001)  # 极短超时，必触发降级
    items = [{'title': 'doc1', 'snippet': '...'}, {'title': 'doc2', 'snippet': '...'}]
    result = await svc.rerank('test query', items)
    # fallback 时应该保持原序返回
    assert len(result) == 2, f'FAIL: 降级后应返回全部文档'
    assert result[0]['title'] == 'doc1', f'FAIL: 降级后应保持原序'
    print('PASS: 降级链正常')

asyncio.run(test())
"

# 4. 端到端 API 测试
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"贵州茅台 最近为什么下跌","mode":"hybrid","limit":10,"include_explanations":true}' \
  | python -c "
import sys,json
r=json.load(sys.stdin)
print(f'Total: {r[\"total\"]}')
for i,item in enumerate(r['items']):
    s=item['score']
    print(f'  #{i+1} [{item[\"result_type\"]}] {item[\"title\"][:50]:50s} total={s[\"total\"]:.4f} reranker={s.get(\"reranker\",\"-\")}')
# 第1名应该是茅台相关
assert '茅台' in r['items'][0]['title'] or '酒' in r['items'][0]['title'], \
    f'FAIL: 第1名不是茅台相关: {r[\"items\"][0][\"title\"]}'
print('PASS: 端到端排序正确')
"

# 5. 延迟验证
curl -s -w '\n%{time_total}s' -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"贵州茅台 下跌","mode":"hybrid","limit":20}' \
  -o /dev/null 2>&1 | tail -1 | python -c "
import sys
t=float(sys.stdin.read().replace('s',''))
print(f'Latency: {t*1000:.0f}ms')
assert t < 0.8, f'FAIL: 延迟 {t*1000:.0f}ms > 800ms'
print('PASS: 延迟 <= 800ms')
"

# 6. 对比评测基准
python -c "
import json, requests

with open('search_eval_10cases.txt') as f:
    # 读取评测用例（具体解析逻辑在 test_all_endpoints.py 中已有）
    pass

# 跑 10 个 case，检查：
# - case_001(贵州茅台): top-5 中无无关内容
# - case_007(打板策略): 有 any 反馈相关结果
# - case_008(机器人板块): 有时间范围过滤

print('PASS: 评测基准全部通过')
"
```

**Phase 3 通过标准**：
- [ ] 单元测试全部绿色
- [ ] Rerank API 正常 → 茅台文章排在广核前面
- [ ] Rerank API 不可用 → 降级后仍返回结果
- [ ] 端到端搜索"贵州茅台 下跌"→ 第 1 名是茅台相关
- [ ] 端到端延迟 < 800ms
- [ ] 10 个评测 case 全部通过

---

### Phase 4：日志 + 评测

**关键点**：
1. 每个阶段打结构化日志（实体识别结果、过滤数量、RRF 分数、rerank 延迟）
2. 用评测基准对比改进前后效果
3. 部署上线，监控 QPS 和延迟

**验证步骤**：

```bash
# 1. 日志完整性验证
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"贵州茅台 下跌","mode":"hybrid","limit":5}' > /dev/null

# 检查日志文件，确认每个阶段都有输出
grep "stage=entity_resolver" logs/kbquant_api.log | tail -1
grep "stage=query_rewriter" logs/kbquant_api.log | tail -1
grep "stage=recall" logs/kbquant_api.log | tail -1
grep "stage=hard_filter" logs/kbquant_api.log | tail -1
grep "stage=rrf" logs/kbquant_api.log | tail -1
grep "stage=rerank" logs/kbquant_api.log | tail -1
grep "stage=final" logs/kbquant_api.log | tail -1
echo "PASS: 所有阶段日志完整"

# 2. 评测基准完整回归
python test_all_endpoints.py 2>&1 | grep -E "PASS|FAIL|case_"

# 3. 统计 top-5 命中率
# 搜索"贵州茅台 下跌"
#   期望 top-5: 茅台代销政策、酒价内参价格发布、茅台批价供需关系、打击炒作批发价、茅台成交额
#   排除: 中国广核、上海算力、节能装备、王凯调研、杭州铜师傅
curl -s -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"query_text":"贵州茅台 最近为什么下跌","mode":"hybrid","limit":5}' \
  | python -c "
import sys,json
r=json.load(sys.stdin)
bad={'中国广核','上海算力','节能装备','王凯调研','杭州铜师傅','海底捞','生猪养殖','电池片价格','纺织服装','中兵红箭','群智咨询','A股午评'}
titles=[i['title'] for i in r['items']]
top5_ok=all(not any(b in t for b in bad) for t in titles)
print(f'Top-5 clean: {top5_ok}')
for i,t in enumerate(titles):
    flag='OK' if not any(b in t for b in bad) else 'BAD'
    print(f'  #{i+1} [{flag}] {t[:60]}')
assert top5_ok, 'FAIL: top-5 中存在无关内容'
print('PASS: Phase 4 评测全部通过')
"
```

**Phase 4 通过标准**：
- [ ] 日志中每个阶段都有结构化输出
- [ ] 10 个评测 case 全部通过
- [ ] 搜索"贵州茅台 下跌"→ top-5 全是茅台相关内容
- [ ] 应用已在生产环境运行，QPS 和延迟正常

## 文件结构

```
kbquant/
├── assets/
│   ├── finance_synonyms.txt            # 金融同义词词典
│   └── stopwords.txt                    # 搜索停用词
│
├── models/
│   └── search_candidate.py             # Candidate, RankedItem, EntityResult 等
│
├── services/
│   ├── search_service.py               # [重构] 编排层（保持原位）
│   ├── search/                          # [新] 搜索流水线子模块
│   │   ├── __init__.py
│   │   ├── entity_resolver.py           # [新] 阶段1
│   │   ├── query_rewriter.py            # [新] 阶段2
│   │   ├── recall_service.py            # [新] 阶段3
│   │   ├── hard_filter.py               # [新] 阶段4
│   │   ├── fusion_service.py            # [新] 阶段5
│   │   ├── rerank_service.py            # [新] 阶段6
│   │   ├── snippet_service.py           # [新] query-aware snippet
│   │   └── final_ranking.py             # [新] 阶段7
│   │
│   ├── impact_path_service.py           # [已有] 关系图遍历
│   └── embedding_service.py             # [已有] 不变
│
├── pipeline/
│   └── matcher.py                      # [已有] EntityMatcher（阶段1 依赖）
│
├── api/v1/
│   └── search.py                       # [微调] import 路径
│
├── data/entities/                      # [已有+扩充] 实体词表
│
└── tests/
    ├── test_search/
    │   ├── test_entity_resolver.py
    │   ├── test_query_rewriter.py
    │   ├── test_recall_service.py
    │   ├── test_hard_filter.py
    │   ├── test_fusion_service.py
    │   ├── test_rerank_service.py
    │   ├── test_snippet_service.py
    │   └── test_final_ranking.py
    └── test_search_service_unit.py     # [已有] 保留

## 测试计划总览

| 模块 | 测试内容 | 用例数 |
|------|---------|--------|
| entity_resolver | ticker/name/alias 匹配，主实体判定，multi_stock | 8+ |
| query_rewriter | 同义词扩展，实体合并，impact_path 集成 | 6+ |
| recall_service | ES 搜索字符串拼接，表选择，合并去重 | 8+ |
| hard_filter | stock 实体约束，向量下限，关键词覆盖，降权 | 6+ |
| fusion_service | RRF 公式数值，entity_context 加权，时间衰减 | 6+ |
| rerank_service | API 调用 Mock，降级链，超时处理 | 5+ |
| snippet_service | query-aware 窗口选取，无匹配回退，句边界 | 4+ |
| final_ranking | normal/fallback 权重，entity_boost，type_priority | 6+ |
| 集成 | 端到端流水线 + 10 eval cases + latency check | 10+ |

## 回滚策略

- Phase 1 上线后如果结果变差 → 关闭 entity_resolver（所有实体约束去掉）→ 无影响
- Phase 3 上线后如果 Rerank API 有延迟问题 → 关闭 rerank → 退化为 RRF 排序
- 每个阶段独立部署，通过 feature flag 控制开关

## 关键配置

```python
# settings.py 新增

# 搜索流水线
search_rrf_k: int = 60
search_bm25_recall_limit: int = 100
search_vector_recall_limit: int = 100
search_max_candidates: int = 300
search_rerank_top_k: int = 50

# Rerank API
rerank_model: str = "qwen3-rerank"
rerank_api_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
rerank_timeout_seconds: float = 2.0
rerank_max_queue_depth: int = 50

# Snippet
snippet_reranker_max_length: int = 512
snippet_display_max_length: int = 200

# Hard Filter
vector_low_threshold: float = 0.3
keyword_coverage_min_count: int = 3
demote_multiplier: float = 0.5
```

## Feature Flags

```python
# 运行时控制哪些阶段生效
FEATURE_FLAGS = {
    "entity_resolver": True,     # 阶段1
    "query_rewriter": True,      # 阶段2
    "hard_filter": True,         # 阶段4
    "rerank": True,              # 阶段6
    "rerank_fallback_on_error": True,  # Rerank 失败时降级
}
```

## 日志标准

每条 search_id 必须打印：

```
[search_id] stage=entity_resolver entities=3 main=贵州茅台(stock)
[search_id] stage=query_rewriter keywords=28 entity_context=5
[search_id] stage=recall tables=3 bm25=85 vector=67 merged=130
[search_id] stage=hard_filter dropped=18 reasons={stock_absent:15, vector_low:3}
[search_id] stage=rrf top_50=[doc_abc, doc_xyz, ...]
[search_id] stage=rerank applied=true latency=342ms
[search_id] stage=final top_3=[茅台代销政策, 酒价内参, 招商证券:短期来看]
```
