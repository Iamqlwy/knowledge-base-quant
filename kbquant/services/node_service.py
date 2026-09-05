import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from kbquant.database import LazyDB
from kbquant.models.world_node import WorldNode, WorldNodeEdge
from kbquant.models.node_state import NodeState
from kbquant.models.node_attachment import NodeAttachment

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


def _es_sync_node(node: WorldNode):
    from kbquant.integrations.elasticsearch.sync import sync_world_node
    from kbquant.services.information_service import _track_bg_task
    _track_bg_task(asyncio.create_task(sync_world_node(node)))


def _es_sync_state(state: NodeState):
    from kbquant.integrations.elasticsearch.sync import sync_node_state
    from kbquant.services.information_service import _track_bg_task
    _track_bg_task(asyncio.create_task(sync_node_state(state)))


async def _retry_on_version_conflict(db: LazyDB, node_id: uuid.UUID, *, custom_time=None, **kwargs):
    """重试 _update_state_scoped，处理并发的 version 冲突。"""
    from sqlalchemy.exc import IntegrityError, InternalError, PendingRollbackError

    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            async with db.session() as session:
                return await NodeService._update_state_scoped(
                    session, node_id, custom_time=custom_time, **kwargs
                )
        except (IntegrityError, PendingRollbackError) as exc:
            if isinstance(exc, PendingRollbackError):
                logger.warning(
                    "PendingRollbackError on node_id=%s attempt=%d, retrying",
                    node_id, attempt + 1,
                )
                last_exc = exc
                continue
            msg = str(exc).lower()
            if "unique" not in msg and "duplicate" not in msg:
                raise
            last_exc = exc
            logger.warning(
                "version conflict on node_id=%s attempt=%d, retrying",
                node_id, attempt + 1,
            )
        except InternalError as exc:
            msg = str(exc).lower()
            if "cannot switch to state" not in msg:
                raise
            last_exc = exc
            logger.warning(
                "InternalError (connection state) on node_id=%s attempt=%d, retrying",
                node_id, attempt + 1,
            )
    raise last_exc  # type: ignore[misc]


class NodeService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def create_node(self, *, name: str, node_type: str, description: str | None = None,
                          ticker: str | None = None,
                          aliases: list[str] | None = None, metadata_: dict | None = None,
                          initial_state: dict | None = None,
                          custom_time: datetime | None = None) -> WorldNode:
        async with self.db.session() as session:
            existing = await session.execute(
                select(WorldNode).where(
                    WorldNode.name == name, WorldNode.node_type == node_type
                )
            )
            node = existing.scalar_one_or_none()
            if node is not None:
                new_state = None
                if initial_state:
                    new_state = await self._update_state_scoped(session, node.id, custom_time=custom_time, **initial_state)
                await session.flush()
                _es_sync_node(node)
                if new_state is not None:
                    _es_sync_state(new_state)
                return node

            node = WorldNode(
                name=name, node_type=node_type, description=description,
                ticker=ticker, aliases=aliases, metadata_=metadata_ or {},
            )
            if custom_time is not None:
                node.created_at = custom_time
                node.updated_at = custom_time
            session.add(node)
            await session.flush()
            if initial_state:
                await self._update_state_scoped(session, node.id, custom_time=custom_time, **initial_state)
            await session.flush()

        _es_sync_node(node)
        return node

    async def get_node(self, node_id: uuid.UUID) -> WorldNode | None:
        async with self.db.session() as session:
            result = await session.execute(select(WorldNode).where(WorldNode.id == node_id))
            return result.scalar_one_or_none()

    async def get_nodes_many(self, node_ids: list[uuid.UUID]) -> list[WorldNode]:
        if not node_ids:
            return []
        async with self.db.session() as session:
            result = await session.execute(
                select(WorldNode).where(WorldNode.id.in_(node_ids))
            )
            return list(result.scalars().all())

    async def list_nodes(self, *, node_type: str | None = None, page: int = 1,
                         page_size: int = 20) -> tuple[list[WorldNode], int]:
        async with self.db.session() as session:
            query = select(WorldNode).where(WorldNode.is_active == True)
            count_query = select(func.count()).select_from(WorldNode).where(WorldNode.is_active == True)
            if node_type:
                query = query.where(WorldNode.node_type == node_type)
                count_query = count_query.where(WorldNode.node_type == node_type)
            query = query.order_by(WorldNode.name).offset((page - 1) * page_size).limit(page_size)
            total_result = await session.execute(count_query)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())
            return items, total

    async def attach(self, *, node_id: uuid.UUID, attachment_type: str, attachment_id: uuid.UUID,
                     role: str, relevance_score: float | None = None,
                     custom_time: datetime | None = None) -> NodeAttachment:
        async with self.db.session() as session:
            attach = NodeAttachment(
                node_id=node_id, attachment_type=attachment_type,
                attachment_id=attachment_id, role=role, relevance_score=relevance_score,
            )
            if custom_time is not None:
                attach.created_at = custom_time
                attach.updated_at = custom_time
            session.add(attach)
            await session.flush()
            return attach

    async def get_attachments(self, node_id: uuid.UUID, *, role: str | None = None,
                              attachment_type: str | None = None) -> list[NodeAttachment]:
        async with self.db.session() as session:
            query = select(NodeAttachment).where(NodeAttachment.node_id == node_id)
            if role:
                query = query.where(NodeAttachment.role == role)
            if attachment_type:
                query = query.where(NodeAttachment.attachment_type == attachment_type)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_current_state(self, node_id: uuid.UUID) -> NodeState | None:
        async with self.db.session() as session:
            return await self._get_current_state_scoped(session, node_id)

    @staticmethod
    async def _get_current_state_scoped(session, node_id: uuid.UUID) -> NodeState | None:
        result = await session.execute(
            select(NodeState).where(
                NodeState.node_id == node_id,
                NodeState.effective_to == None,
            ).options(defer(NodeState.embedding)).order_by(NodeState.version.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    _state_fields = ("core_logic", "primary_drivers", "risks", "focus_points",
                     "recent_changes", "uncertainty_flags", "key_evidence_ids", "state_summary")

    @classmethod
    def _merge_state(cls, kwargs: dict, old_state: NodeState | None) -> tuple[dict, set]:
        merged = {}
        for field in cls._state_fields:
            if field in kwargs:
                if field == "key_evidence_ids" and old_state is not None and old_state.key_evidence_ids:
                    existing = old_state.key_evidence_ids
                    incoming = kwargs[field] or []
                    seen = set(existing)
                    merged[field] = list(existing) + [x for x in incoming if x not in seen]
                else:
                    merged[field] = kwargs[field]
            elif old_state is not None:
                merged[field] = getattr(old_state, field)
        extra = set(kwargs) - set(cls._state_fields)
        return merged, extra

    @staticmethod
    async def _update_state_scoped(session, node_id: uuid.UUID, *, custom_time: datetime | None = None, **kwargs) -> NodeState:
        """创建新版本：关闭当前版本(effective_to=now)，写入新版本。未传入的字段沿用上一版本的值。"""
        now = custom_time if custom_time is not None else datetime.now(timezone.utc)

        # Lock the WorldNode row to serialize concurrent state updates for the same node.
        # Without this, concurrent callers read the same old_state version and all try
        # to INSERT version+1, causing UniqueViolation on ix_node_states_version.
        await session.execute(
            select(WorldNode).where(WorldNode.id == node_id).with_for_update()
        )

        old_state = await NodeService._get_current_state_scoped(session, node_id)
        if old_state is not None:
            new_version = old_state.version + 1
            old_state.effective_to = now
            session.add(old_state)
        else:
            # No current state — either first version ever, or all existing
            # versions have been closed (e.g. by custom_time backfill).  Compute
            # the next version from MAX(version) to avoid colliding with
            # historical versions.
            from sqlalchemy import func as _func
            result = await session.execute(
                select(_func.max(NodeState.version)).where(NodeState.node_id == node_id)
            )
            max_ver = result.scalar() or 0
            new_version = max_ver + 1
        merged, extra = NodeService._merge_state(kwargs, old_state)
        if extra:
            logger.debug("update_state ignoring unknown kwargs: %s", extra)
        new_state = NodeState(
            node_id=node_id, version=new_version, effective_from=now,
            **merged,
        )
        if custom_time is not None:
            new_state.created_at = custom_time
            new_state.updated_at = custom_time
        session.add(new_state)
        await session.flush()
        return new_state

    async def update_state(self, node_id: uuid.UUID, **kwargs) -> NodeState:
        custom_time = kwargs.pop("custom_time", None)
        new_state = await _retry_on_version_conflict(
            self.db, node_id, custom_time=custom_time, **kwargs
        )
        _es_sync_state(new_state)

        state_id = new_state.id
        embed_text = f"{new_state.core_logic or ''} {new_state.state_summary or ''}".strip()

        # Embedding: HTTP call outside semaphore, only DB write inside
        if embed_text:
            import asyncio
            from kbquant.services.information_service import _track_bg_task, _get_bg_write_semaphore

            async def _store_embedding():
                from kbquant.services.embedding_service import generate_embedding_for
                from kbquant.database import bg_write_async_session
                import logging
                _logger = logging.getLogger(__name__)
                try:
                    vector = await generate_embedding_for(embed_text)
                except Exception:
                    _logger.warning("Embedding call failed for node_state_id=%s", state_id)
                    return
                try:
                    async with _get_bg_write_semaphore():
                        async with bg_write_async_session() as bg_session:
                            state_obj = await bg_session.get(NodeState, state_id)
                            if state_obj:
                                state_obj.embedding = vector
                                await bg_session.commit()
                except Exception:
                    _logger.warning("Embedding DB write failed for node_state_id=%s", state_id)
            _track_bg_task(asyncio.create_task(_store_embedding()))

        return new_state

    async def get_state_history(self, node_id: uuid.UUID) -> list[NodeState]:
        async with self.db.session() as session:
            result = await session.execute(
                select(NodeState)
                .where(NodeState.node_id == node_id)
                .options(defer(NodeState.embedding))
                .order_by(NodeState.version.desc())
            )
            return list(result.scalars().all())

    async def get_state_at(self, node_id: uuid.UUID, timestamp: datetime) -> NodeState | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(NodeState).where(
                    NodeState.node_id == node_id,
                    NodeState.effective_from <= timestamp,
                    (NodeState.effective_to == None) | (NodeState.effective_to > timestamp),
                ).options(defer(NodeState.embedding)).order_by(NodeState.version.desc()).limit(1)
            )
            return result.scalar_one_or_none()

    async def compress(self, node_id: uuid.UUID, force: bool = False,
                       target_ratio: float = 0.3) -> dict | None:
        async with self.db.session() as session:
            node_result = await session.execute(select(WorldNode).where(WorldNode.id == node_id))
            node = node_result.scalar_one_or_none()
            if not node:
                return None

            current_state = await self._get_current_state_scoped(session, node_id)

            before_total_chars = 0
            if current_state:
                before_total_chars = sum(
                    len(str(getattr(current_state, f, None) or ""))
                    for f in self._state_fields if f != "key_evidence_ids"
                )

            # 仅在内容量达到阈值或强制压缩时才执行
            min_chars = 500
            if not force and before_total_chars < min_chars:
                return {
                    "node_id": node_id,
                    "name": node.name,
                    "before_evidence_count": 0,
                    "before_total_chars": before_total_chars,
                    "after_evidence_count": 0,
                    "after_total_chars": before_total_chars,
                    "summary": "内容量未达到压缩阈值，跳过",
                    "skipped": True,
                }

            summary_text = (
                f"节点 {node.name} 摘要："
                f"核心逻辑: {current_state.core_logic if current_state else '无'}"
            )
            target_len = max(int(before_total_chars * target_ratio), len(summary_text))
            summary_text = summary_text[:target_len]

            new_state = await self._update_state_scoped(
                session, node_id,
                state_summary=summary_text,
            )

            return {
                "node_id": node_id,
                "name": node.name,
                "before_evidence_count": 0,
                "before_total_chars": before_total_chars,
                "after_evidence_count": 0,
                "after_total_chars": len(summary_text),
                "new_state_id": new_state.id,
                "summary": summary_text,
                "skipped": False,
            }

    async def list_node_names_and_aliases(self) -> list[dict]:
        async with self.db.session() as session:
            result = await session.execute(
                select(WorldNode.id, WorldNode.name, WorldNode.node_type, WorldNode.aliases).order_by(WorldNode.name)
            )
            return [{"id": id, "name": name, "node_type": node_type, "aliases": aliases} for id, name, node_type, aliases in result.all()]

    # --- WorldNodeEdge CRUD ---

    async def add_edge(self, *, parent_node_id: uuid.UUID, child_node_id: uuid.UUID,
                       relationship_type: str = "belongs_to", weight: float = 1.0,
                       evidence_ids: list[uuid.UUID] | None = None) -> WorldNodeEdge:
        async with self.db.session() as session:
            edge = WorldNodeEdge(
                parent_node_id=parent_node_id, child_node_id=child_node_id,
                relationship_type=relationship_type, weight=weight,
                evidence_ids=evidence_ids,
            )
            session.add(edge)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # Another request concurrently created the same edge; re-query it.
                existing = await session.execute(
                    select(WorldNodeEdge).where(
                        WorldNodeEdge.parent_node_id == parent_node_id,
                        WorldNodeEdge.child_node_id == child_node_id,
                        WorldNodeEdge.relationship_type == relationship_type,
                    )
                )
                edge = existing.scalar_one()
            return edge

    async def remove_edge(self, edge_id: uuid.UUID) -> bool:
        async with self.db.session() as session:
            edge = await session.get(WorldNodeEdge, edge_id)
            if edge is None:
                return False
            await session.delete(edge)
            return True

    async def get_parents(self, node_id: uuid.UUID) -> list[WorldNodeEdge]:
        async with self.db.session() as session:
            result = await session.execute(
                select(WorldNodeEdge).where(WorldNodeEdge.child_node_id == node_id)
            )
            return list(result.scalars().all())

    async def get_children(self, node_id: uuid.UUID) -> list[WorldNodeEdge]:
        async with self.db.session() as session:
            result = await session.execute(
                select(WorldNodeEdge).where(WorldNodeEdge.parent_node_id == node_id)
            )
            return list(result.scalars().all())

