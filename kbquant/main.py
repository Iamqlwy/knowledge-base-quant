import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from kbquant.api.router import api_router
from kbquant.config import settings
from kbquant.database import engine, read_engine, bg_engine
from kbquant.schemas import ErrorResponse
from kbquant.integrations.elasticsearch.client import es_startup, es_shutdown
from kbquant.utils.logging import setup_logging
from kbquant.api.middleware import RequestAdmissionMiddleware, SearchConcurrencyMiddleware, get_search_queue_metrics, get_admission_metrics

setup_logging()
logger = logging.getLogger(__name__)

# File lock to ensure only one worker starts the pipeline worker
_pipeline_lock_path = os.path.join(tempfile.gettempdir(), "kbquant_pipeline.lock")


def _try_acquire_pipeline_lock() -> bool:
    try:
        fd = os.open(_pipeline_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False



@asynccontextmanager
async def lifespan(app: FastAPI):
    await es_startup()

    # 预热 DB 连接池
    from sqlalchemy import text as _text
    async with engine.connect() as conn:
        await conn.execute(_text("SELECT 1"))
    logger.info("DB 连接池预热完成")

    # 预热 embedding 缓存
    if settings.embedding_warmup_queries:
        logger.info("开始预热 embedding 缓存，共 %d 个查询词", len(settings.embedding_warmup_queries))
        try:
            from kbquant.services.embedding_service import embedding_service
            warmup_tasks = [
                embedding_service.embed_text(query)
                for query in settings.embedding_warmup_queries
            ]
            await asyncio.gather(*warmup_tasks, return_exceptions=True)
            logger.info("Embedding 缓存预热完成")
        except Exception:
            logger.exception("Embedding 缓存预热失败，将继续启动")

    worker_task = None
    if settings.pipeline_worker_enabled:
        if _try_acquire_pipeline_lock():
            from kbquant.pipeline.worker import PipelineWorker
            from kbquant.database import write_async_session
            worker = PipelineWorker(write_async_session)
            worker_task = asyncio.create_task(worker.run())
            logger.info('Pipeline Worker started (primary)')
        else:
            logger.info('Pipeline Worker skipped (non-primary worker)')

    yield

    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        logger.info("Pipeline Worker 已停止")

    from kbquant.services.information_service import drain_background_tasks
    await drain_background_tasks(timeout=15.0)

    await es_shutdown()
    await read_engine.dispose()
    await engine.dispose()
    await bg_engine.dispose()


app = FastAPI(
    title="量化交易知识库",
    description="知识库系统 - 存储原始资讯、分析、反馈、交易操作和节点信息",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(api_router)

app.add_middleware(RequestAdmissionMiddleware)
app.add_middleware(SearchConcurrencyMiddleware, path_prefixes=("/api/v1/search",))

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(detail="Internal server error", error_code="INTERNAL_ERROR").model_dump(),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "db": "connected", "version": "0.1.0"}


@app.get("/metrics")
async def metrics():
    """返回性能指标，包括连接池状态"""
    from kbquant.database import get_pool_metrics
    pool_metrics = get_pool_metrics()

    return {
        "database": pool_metrics,
        "admission": get_admission_metrics(),
        "search_queue": get_search_queue_metrics(),
        "background_tasks": {
            "max_concurrent": settings.background_task_max_concurrent,
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "kbquant.main:app", host="0.0.0.0", port=8000,
        workers=settings.uvicorn_workers,
        timeout_keep_alive=65,
        timeout_graceful_shutdown=30,
        limit_max_requests=10000,
        log_config=None,
    )
