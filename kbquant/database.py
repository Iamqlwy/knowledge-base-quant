import logging
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, pool

from kbquant.config import settings

logger = logging.getLogger(__name__)

# Connection pool metrics
_pool_metrics = {
    "write_checkouts": 0,
    "write_checkins": 0,
    "write_connects": 0,
    "read_checkouts": 0,
    "read_checkins": 0,
    "read_connects": 0,
    "bg_checkouts": 0,
    "bg_checkins": 0,
    "bg_connects": 0,
}


def _create_engine(url: str, *, pool_size: int, max_overflow: int, engine_name: str = "unknown") -> AsyncEngine:
    engine = create_async_engine(
        url,
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=settings.database_pool_timeout,
        # PgBouncer transaction 模式不需要 pre-ping — 每次 checkout 已经从池里拿到干净连接
        connect_args={
            "server_settings": {
                "application_name": f"kbquant_{engine_name}",
                "timezone": "UTC",
            },
            "statement_cache_size": 100,
            "command_timeout": 60,
            "timeout": 10,
        },
    )

    max_capacity = pool_size + max_overflow

    # Add pool event listeners for monitoring
    @event.listens_for(engine.sync_engine.pool, "checkout")
    def _receive_checkout(dbapi_conn, connection_record, connection_proxy):
        _pool_metrics[f"{engine_name}_checkouts"] += 1
        pool_obj = connection_proxy._pool
        checked_out = pool_obj.checkedout()
        utilization = checked_out / max_capacity if max_capacity > 0 else 0
        if utilization > 0.8 and checked_out > 5:
            logger.warning(
                "数据库连接池利用率高: %s pool %.1f%% (checked_out=%d, max_capacity=%d)",
                engine_name, utilization * 100, checked_out, max_capacity
            )

    @event.listens_for(engine.sync_engine.pool, "checkin")
    def _receive_checkin(dbapi_conn, connection_record):
        _pool_metrics[f"{engine_name}_checkins"] += 1

    @event.listens_for(engine.sync_engine.pool, "connect")
    def _receive_connect(dbapi_conn, connection_record):
        _pool_metrics[f"{engine_name}_connects"] += 1

    return engine


# expire_on_commit=False: commit 后对象的属性仍然可访问，避免在序列化阶段触发额外的 lazy load

engine = _create_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    engine_name="write",
)

read_engine = _create_engine(
    settings.database_read_url or settings.database_url,
    pool_size=settings.database_read_pool_size,
    max_overflow=settings.database_read_max_overflow,
    engine_name="read",
)

# 后台任务专用 engine — 固定连接串行化所有后台写
bg_engine = _create_engine(
    settings.database_url,
    pool_size=settings.database_bg_pool_size,
    max_overflow=settings.database_bg_max_overflow,
    engine_name="bg",
)

# DEPRECATED: 指向 read_engine（旧代码兼容），新代码请使用 write_async_session / read_async_session
async_session = async_sessionmaker(read_engine, class_=AsyncSession, expire_on_commit=False)
write_async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
read_async_session = async_sessionmaker(read_engine, class_=AsyncSession, expire_on_commit=False)
bg_write_async_session = async_sessionmaker(bg_engine, class_=AsyncSession, expire_on_commit=False)

# 启动时打印连接池规模汇总
_total_write = (settings.database_pool_size + settings.database_max_overflow) * settings.uvicorn_workers
_total_read = (settings.database_read_pool_size + settings.database_read_max_overflow) * settings.uvicorn_workers
_total = _total_write + _total_read
logger.info("DB 连接池规模: write=%d+%d read=%d+%d workers=%d -> 最大 %d 连接",
            settings.database_pool_size, settings.database_max_overflow,
            settings.database_read_pool_size, settings.database_read_max_overflow,
            settings.uvicorn_workers, _total)
if _total > 200:
    logger.warning("DB 连接池总量 (%d) 超过 200；存在 Postgres max_connections 耗尽风险", _total)


def get_pool_metrics() -> dict:
    """获取连接池指标"""
    write_pool = engine.sync_engine.pool
    read_pool = read_engine.sync_engine.pool

    return {
        "write_pool": {
            "size": write_pool.size(),
            "checked_out": write_pool.checkedout(),
            "overflow": write_pool.overflow(),
            "total_checkouts": _pool_metrics.get("write_checkouts", 0),
            "total_checkins": _pool_metrics.get("write_checkins", 0),
            "total_connects": _pool_metrics.get("write_connects", 0),
        },
        "read_pool": {
            "size": read_pool.size(),
            "checked_out": read_pool.checkedout(),
            "overflow": read_pool.overflow(),
            "total_checkouts": _pool_metrics.get("read_checkouts", 0),
            "total_checkins": _pool_metrics.get("read_checkins", 0),
            "total_connects": _pool_metrics.get("read_connects", 0),
        },
        "bg_pool": {
            "size": bg_engine.sync_engine.pool.size(),
            "checked_out": bg_engine.sync_engine.pool.checkedout(),
            "overflow": bg_engine.sync_engine.pool.overflow(),
            "total_checkouts": _pool_metrics.get("bg_checkouts", 0),
            "total_checkins": _pool_metrics.get("bg_checkins", 0),
            "total_connects": _pool_metrics.get("bg_connects", 0),
        },
    }


class LazyDB:
    """Reusable session factory wrapper — opens/closes on demand instead of holding connections.

    Usage in a service::

        class MyService:
            def __init__(self, db: LazyDB):
                self.db = db

            async def do_query(self):
                async with self.db.session() as session:
                    result = await session.execute(...)
                    return result.scalars().all()
    """

    __slots__ = ("_factory", "_commit")

    def __init__(self, factory, *, commit: bool = False):
        self._factory = factory
        self._commit = commit

    def session(self):
        """Return a context-managed session that auto-closes and auto-commits/rolls back."""
        return _LazySessionCtx(self._factory, commit=self._commit)


class _LazySessionCtx:
    __slots__ = ("_factory", "_commit", "_session")

    def __init__(self, factory, *, commit: bool):
        self._factory = factory
        self._commit = commit
        self._session = None

    async def __aenter__(self):
        self._session = self._factory()
        return self._session

    async def __aexit__(self, *args):
        if self._session is None:
            return
        try:
            if self._commit:
                if args[0] is None:
                    await self._session.commit()
                else:
                    await self._session.rollback()
        except Exception:
            if self._commit:
                await self._session.rollback()
            raise
        finally:
            await self._session.close()


class Base(DeclarativeBase):
    pass


# Pre-built LazyDB instances for use in dependency injection
read_lazy = LazyDB(read_async_session, commit=False)
write_lazy = LazyDB(write_async_session, commit=True)
