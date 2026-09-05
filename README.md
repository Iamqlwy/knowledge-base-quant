# kb-quant · 量化交易知识库系统

面向量化交易的**动态知识库后端服务**：存储并处理原始资讯、实体、分析记录、交易操作、反馈复盘与世界节点（world nodes），提供混合搜索（ES 多路召回 + RRF 融合 + Rerank + 规则重排）、证据追溯、影响路径、冲突检测、重要性排序等能力。

- 技术栈：Python 3.11+ / FastAPI / SQLAlchemy(Async) / PostgreSQL(pgvector) / Elasticsearch 9 / PgBouncer / Docker Compose
- 服务入口：`kbquant/main.py`（启动时同时拉起 ES 客户端与后台 PipelineWorker）
- API 文档：见 [`docs/API.md`](docs/API.md)；搜索管线设计见 [`docs/superpowers/specs/`](docs/superpowers/specs/)

## 快速开始

### 前置条件

- Docker（含 compose）；首次需预建外部网络与数据卷：
  ```bash
  docker network create kbquant-net
  docker volume create pgvector_data
  docker volume create esdata
  ```

### 启动

```bash
cp .env.example .env    # 填入真实 API Key 与密码（.env 不入库）
docker compose up -d --build
```

服务组成（端口均已绑定到 `127.0.0.1`，仅本机可访问）：

| 服务 | 容器 | 端口 | 说明 |
| --- | --- | --- | --- |
| postgres | `final-postgres` | 15432 | PostgreSQL 17 + pgvector，调优参数见 compose 的 `command` |
| elasticsearch | `final-elasticsearch` | 9200 | ES 9.3.3 + IK 中文分词（开启 security，密码来自 `.env` 的 `ELASTIC_PASSWORD`） |
| pgbouncer | `kbquant-pgbouncer` | 6432 | transaction 模式连接池，app 统一经它连接 PG |
| app | `kbquant-app` | 8000 | FastAPI 服务（uvicorn 多 worker + 内置 PipelineWorker） |

### 验证

```bash
curl http://localhost:8000/health          # 健康检查
curl http://localhost:8000/metrics         # 连接池 / 准入 / 搜索队列指标
```

### 本地开发（不经 Docker）

```bash
uv sync --extra dev        # 或 pip install -e .[dev]
# 配置 .env 后：
uvicorn kbquant.main:app --reload --port 8000
# 迁移：
alembic upgrade head
```

所有接口需携带请求头 `X-API-Key`（值对应 `.env` 中的 `API_KEY`）。

## 测试

```bash
uv run pytest            # 或 pytest
```

测试使用独立的 `quant_kb_test` 库（连 `localhost:15432`），要求本地 PG/ES 已就绪；见 `tests/conftest.py`。

## 目录结构

```
alembic/                 数据库迁移（29 个版本，含索引/触发器/向量索引）
kbquant/
  api/                   FastAPI 路由（v1）与中间件（准入控制、搜索并发限流）
  client/                Python 客户端 SDK（含按层并发限流）
  models/                SQLAlchemy 模型（world_node / raw_information / ...）
  schemas/               Pydantic 请求响应模型
  services/              业务服务层（analysis / entity / node / evidence / ...）
    search/              搜索管线：recall(多路) → hard_filter → fusion(RRF) → rerank → final_ranking
  pipeline/              后台管线：资讯 ingest → embedding → 实体匹配 → 分析 → 落库
  integrations/          ES 客户端与 embedding 适配
  utils/                 日志、全局限流器等工具
  main.py                应用入口
data/entities/           实体词典（公司/行业/机构/事件等）
es-config/               ES 配置与 IK 自定义词典
nodes/                   节点维护工作流（见 nodes/README.md）
pgbouncer/               PgBouncer 配置与用户列表
scripts/                 运维/分析脚本（见下）
tests/                   测试（含 search 管线单测）
docs/                    接口文档、实体设计、ER 图、搜索管线设计
```

## scripts 运维脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/import_entities.py` | 导入 `data/entities/` 实体词典 |
| `scripts/ingest_news.py` | 导入 news.csv 资讯 |
| `scripts/reset_and_import.py` | 清库并重新导入 |
| `scripts/backfill_embeddings.py` | 回填空 embedding |
| `scripts/sync_nodes_to_es.py` | 创建 nodes/node_states 的 ES 索引并全量同步 |
| `scripts/cleanup_es_orphans.py` | 清理 ES 中 DB 已不存在的孤儿文档 |
| `scripts/cleanup_node_states.py` | 清理 node_states 表 |
| `scripts/dedup_and_create_edges.py` | 节点去重 + 关联边创建 |
| `scripts/merge_worldnode_dupes.py` / `merge_cognitions.py` / `merge_phase1_20260627.py` | 节点 / 行业认知合并（一次性脚本） |
| `scripts/scan_similar_sectors.py` / `filter_merge_plan.py` | 相似行业扫描与合并计划过滤 |
| `scripts/cluster_industry_cognitions.py` / `leiden_communities.py` | 行业认知聚类 / Leiden 社区检测 |
| `scripts/analyze_*.py` | 各类数据质量分析 |
| `scripts/search.py` | 搜索链路调试 |
| `scripts/backup_data.sh` | 数据备份 |
| `scripts/perf/` | 压测与基线脚本（搜索链路、并发探针等） |

## 节点维护工作流

world_nodes 的定期维护（Phase 1 重复节点合并、Phase 2 新节点关联边发现）见 [`nodes/README.md`](nodes/README.md)，由 `nodes/workflow.js` 驱动。

## 安全须知

- `.env` 包含 **LLM / Embedding / Rerank 的 API Key 等敏感信息**，已在 `.gitignore` 中排除，切勿提交。
- 若任何 Key 曾意外进入版本库，请立即到对应平台**轮换密钥**，仓库历史无法真正抹除（本仓库历史已按此重建）。
- 数据库密码存在于 `pgbouncer/userlist.txt`，仓库本身不对外公开；如需轮换，更新该文件与 `.env` 后 `docker compose up -d --build` 重建即可。
