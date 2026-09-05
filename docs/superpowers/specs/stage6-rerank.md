# 阶段6：Rerank（精排）

## 目的

用语义模型对 RRF top-50 做逐条 query-document 联合推理，解决 BM25 主语不准问题。这是整个流水线的核心质量提升环节。

## 设计原则

- 使用阿里云 DashScope qwen3-rerank API（云端服务，无需本地 GPU）
- 单次调用传入 top-50 文档，API 内部 listwise 排序
- 降级链完整：API 超时/限流/错误 → 跳过 rerank → 退化为 RRF 排序
- Snippet 质量直接影响精排效果，使用 query-aware 提取

## 输入

```python
query_text: str                    # 原始查询
ranked_items: list[RankedItem]    # 阶段5 输出的 top-50
```

## 输出

```python
reranked_items: list[RankedItem]  # 按 reranker_score 降序排列的 top-50
                                  # fallback: 保持 RRF 原始排序
```

## API 调用

```
POST https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
Authorization: Bearer $DASHSCOPE_API_KEY
Content-Type: application/json

{
    "model": "qwen3-rerank",
    "input": {
        "query": "贵州茅台最近为什么下跌，批价和业绩怎么看",
        "documents": [
            "茅台代销政策落地深度分析：渠道变革的短期催化与长期隐忧",
            "多晶硅期货逼近前低深度分析——供过于求下的持续承压...",
            ...
        ]
    },
    "parameters": {
        "return_documents": true,
        "top_n": 50
    }
}

# 返回
{
    "output": {
        "results": [
            {"index": 2, "document": {...}, "relevance_score": 0.92},
            {"index": 0, "document": {...}, "relevance_score": 0.87},
            {"index": 5, "document": {...}, "relevance_score": 0.65},
            ...
        ]
    }
}
```

## API 参数

| 参数 | 值 | 说明 |
|------|-----|------|
| model | qwen3-rerank | DashScope 原生 rerank 模型 |
| 上下文窗口 | 30k tokens | 50 个 snippet(512 chars each) ≈ 5k tokens，完全够 |
| RPM | 5400 | QPS ≈ 90 |
| 超时 | 2s | 超时即降级 |
| 认证 | 复用 embedding_api_key | DashScope 统一 API Key |

## 触发策略

```python
MAX_QUEUE_DEPTH = 50  # pending 请求数上限

async def rerank(query_text, items):
    queue_depth = get_current_queue_depth()

    if queue_depth > 50:
        logger.warning("rerank queue depth %d > %d, skipping", queue_depth, MAX_QUEUE_DEPTH)
        return items  # fallback: 原始 RRF 排序

    # 构建 documents 列表
    documents = [
        item.title + " " + (item.snippet or item.content_body[:512])
        for item in items
    ]

    try:
        async with asyncio.timeout(2.0):
            resp = await http_client.post(
                RERANK_API_URL,
                headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                json={
                    "model": "qwen3-rerank",
                    "input": {"query": query_text, "documents": documents},
                    "parameters": {"return_documents": True, "top_n": len(items)},
                },
            )

            if resp.status == 429:
                # 限流：退避重试 1 次
                await asyncio.sleep(1 + random.random())
                resp = await http_client.post(...)
                if resp.status == 429:
                    raise RerankUnavailable("rate limited")

            if resp.status >= 400:
                raise RerankUnavailable(f"API error {resp.status}")

            results = resp.json()["output"]["results"]

            # 将 reranker 分数注入 RankedItem
            score_map = {r["index"]: r["relevance_score"] for r in results}
            for i, item in enumerate(items):
                item.reranker_score = score_map.get(i, item.rrf_score)
                # API 返回缺了某个 index → 给最低分（极少发生）

            # 按 reranker_score 重排
            items.sort(key=lambda x: x.reranker_score, reverse=True)
            return items

    except asyncio.TimeoutError:
        logger.warning("rerank API timeout, fallback to RRF")
    except RerankUnavailable as e:
        logger.warning("rerank unavailable: %s", e)
    except Exception as e:
        logger.error("rerank unexpected error: %s", e)

    # fallback
    for item in items:
        item.reranker_score = None
    return items
```

## 降级链

```
API 调用
  ├─ 成功 → items 按 reranker_score 重排
  └─ 失败（任何原因）→ items 保持 RRF 排序不变，reranker_score=None
       → 阶段7 自动切换到 fallback 权重
```

## Snippet 提取

在阶段 5 和阶段 6 之间，对 top-50 每个 item 调用 `snippet_service.extract()`：

```python
snippet = snippet_service.extract(
    body=item.content_body,
    query_terms=expanded_keywords,
    max_length=512,
)
item.snippet = snippet
```

## 配置

```python
# settings.py
rerank_model: str = "qwen3-rerank"
rerank_api_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
rerank_top_k: int = 50
rerank_timeout_seconds: float = 2.0
rerank_max_queue_depth: int = 50
```

## 文件

```
kbquant/services/search/rerank_service.py    [新] 本文件
kbquant/services/search/snippet_service.py   [新] query-aware snippet 提取
```

## 测试

- API 正常返回 → items 按 reranker_score 重排
- API 超时 → fallback，items 保持 RRF 排序
- API 限流 → 重试 + fallback
- snippet 未提取 → 使用 content_body[:512]
- 空 items → 直接返回空列表
