import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from kbquant.config import settings
from kbquant.database import LazyDB
from kbquant.models.processing_queue import ProcessingQueue
from kbquant.models.raw_information import RawInformation
from kbquant.models.information_entity import InformationEntity
from kbquant.pipeline.matcher import EntityMatcher
from kbquant.pipeline.prompts import PipelineAgent
from kbquant.pipeline.scoring import load_idf_cache
from kbquant.services.entity_service import EntityService
from kbquant.services.llm_service import llm_service
from kbquant.services.pipeline_service import PipelineService

logger = logging.getLogger(__name__)
_project_root = Path(__file__).parent.parent.parent
_idf_default = os.path.join(_project_root, "data", "idf_cache.json")

# 独立的线程池，避免与系统默认线程池争抢
_MATCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(1, settings.pipeline_matcher_max_workers)
)


class PipelineWorker:
    def __init__(self, session_factory: async_sessionmaker,
                 poll_interval: int | None = None,
                 batch_size: int | None = None):
        self._sf = session_factory
        self._db = LazyDB(session_factory, commit=True)
        self._poll_interval = poll_interval or settings.pipeline_worker_poll_interval
        self._batch_size = batch_size or settings.pipeline_worker_batch_size
        self._max_concurrency = max(1, settings.pipeline_worker_max_concurrency)
        self._worker_semaphore = asyncio.Semaphore(self._max_concurrency)
        self._matcher = EntityMatcher(idf_cache=load_idf_cache(_idf_default))
        self._agent = PipelineAgent(llm_service)
        logger.info("PipelineWorker 初始化: 实体库 %d 条, 轮询间隔 %ds, 批大小 %d, 最大并发 %d, 匹配线程 %d",
                    self._matcher.entity_count, self._poll_interval, self._batch_size,
                    self._max_concurrency, settings.pipeline_matcher_max_workers)

    async def run(self):
        logger.info("Pipeline Worker 启动")
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("Worker tick 异常")
            await asyncio.sleep(self._poll_interval)

    async def _tick(self):
        items = []
        async with self._sf() as session:
            items_result = await session.execute(
                select(ProcessingQueue)
                .where(ProcessingQueue.preprocess_status == "ingested")
                .order_by(ProcessingQueue.priority.desc(), ProcessingQueue.created_at.asc())
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
            items = items_result.scalars().all()

            if items:
                ids = [item.id for item in items]
                await session.execute(
                    update(ProcessingQueue)
                    .where(ProcessingQueue.id.in_(ids))
                    .values(preprocess_status="processing")
                )
                await session.commit()
                logger.info("处理 %d 条待处理资讯", len(items))
            else:
                return

        progress = {"total": len(items), "completed": 0, "failed": 0}
        progress_lock = asyncio.Lock()

        async def _handle(item):
            async with self._worker_semaphore:
                try:
                    await self._process_one(item)
                except Exception as e:
                    logger.exception("处理失败 raw_info_id=%s", item.raw_info_id)
                    try:
                        svc = PipelineService(self._db)
                        await svc.update_preprocess_status(item.raw_info_id, "error", detail=str(e)[:500])
                    except Exception:
                        pass
                    async with progress_lock:
                        progress["failed"] += 1
                finally:
                    async with progress_lock:
                        progress["completed"] += 1
                        completed = progress["completed"]
                        total = progress["total"]
                        failed = progress["failed"]
                        if completed == total or completed % 10 == 0:
                            logger.info("当前批处理进度 %d/%d，失败 %d", completed, total, failed)

        await asyncio.gather(*[_handle(item) for item in items])

    async def _process_one(self, item):
        raw_info_id = item.raw_info_id

        # === Phase 1: DB read (release connection before CPU-bound matching) ===
        async with self._sf() as session:
            info = await session.get(RawInformation, raw_info_id)
            if info is None:
                logger.warning("资讯不存在 raw_info_id=%s", raw_info_id)
                svc = PipelineService(self._db)
                await svc.update_preprocess_status(raw_info_id, "error", detail="资讯不存在")
                await session.commit()
                return

            # Extract data before releasing connection
            title = info.title
            body = info.body
            info_id = info.id
            text = f"{title} {body}"

        # CPU-bound entity matching (no DB connection held)
        loop = asyncio.get_running_loop()
        matched = await loop.run_in_executor(
            _MATCH_EXECUTOR, self._matcher.match_with_scores, text, title, 5
        )

        if not matched:
            logger.info("无匹配实体 raw_info_id=%s title=%s", info_id, title[:50])
            svc = PipelineService(self._db)
            await svc.update_preprocess_status(info_id, "preprocessed", detail="无匹配实体")
            return

        # === Phase 2: LLM 调用（释放 DB 连接） ===
        logger.info("匹配到 %d 个实体 raw_info_id=%s: %s",
                    len(matched), info_id,
                    [m["name"] for m in matched])

        try:
            scores, relations = await self._agent.analyze(title, body, matched)
        except Exception as e:
            logger.warning("LLM 分析失败 raw_info_id=%s, 仅保留实体", info_id)
            scores = [
                {"name": m["name"], "entity_type": m["entity_type"],
                 "importance_score": m.get("importance", 0.5)}
                for m in matched
            ]
            relations = []

        # === Phase 3: DB 持久化（重新获取连接） ===
        async with self._sf() as session:
            entity_svc = EntityService(self._db)
            score_by_name = {e["name"]: e for e in scores}
            entity_pairs = [
                (e["name"], e["entity_type"])
                for e in scores
                if e.get("name") and e.get("entity_type")
            ]
            entity_map = await entity_svc.get_or_create_many(entity_pairs)

            # Batch create InformationEntity objects
            info_entities = []
            for e in scores:
                entity = entity_map[(e["name"], e["entity_type"])]
                ie = InformationEntity(
                    raw_info_id=info_id,
                    entity_id=entity.id,
                    relevance_score=e["importance_score"],
                    extraction_confidence=round(e["importance_score"], 4),
                )
                info_entities.append(ie)

            if info_entities:
                session.add_all(info_entities)

            relations_to_upsert = []
            for r in relations:
                src_name = r.get("source", "")
                tgt_name = r.get("target", "")
                rel_type = r.get("relationship_type", "correlated_with")
                description = r.get("description", "")

                src_entity = score_by_name.get(src_name)
                tgt_entity = score_by_name.get(tgt_name)
                if not src_entity or not tgt_entity:
                    continue

                src_db = entity_map[(src_entity["name"], src_entity["entity_type"])]
                tgt_db = entity_map[(tgt_entity["name"], tgt_entity["entity_type"])]

                relations_to_upsert.append({
                    "source_entity_id": src_db.id,
                    "target_entity_id": tgt_db.id,
                    "relationship_type": rel_type,
                    "strength": r.get("strength"),
                    "description": description,
                })

            if relations_to_upsert:
                await entity_svc.upsert_relationships_many(
                    relations_to_upsert, evidence_info_id=info_id
                )

            svc = PipelineService(self._db)
            await svc.update_preprocess_status(
                info_id,
                "preprocessed",
                detail=f"提取 {len(scores)} 个实体, {len(relations)} 条关系",
            )
            await session.commit()
            logger.info("处理完成 raw_info_id=%s: %d 实体, %d 关系",
                        info_id, len(scores), len(relations))
