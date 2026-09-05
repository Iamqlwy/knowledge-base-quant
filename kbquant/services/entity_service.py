import asyncio
import uuid
from collections import OrderedDict

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError

from kbquant.database import LazyDB
from kbquant.models.entity import Entity
from kbquant.models.information_entity import InformationEntity
from kbquant.models.entity_relationship import EntityRelationship

# Entity lookup cache: (normalized_name, entity_type) -> Entity
_entity_cache: OrderedDict[tuple[str, str], Entity] = OrderedDict()
_entity_cache_lock = asyncio.Lock()
_ENTITY_CACHE_MAXSIZE = 10000


def _cache_get(normalized_name: str, entity_type: str) -> Entity | None:
    """Thread-safe cache get with LRU update."""
    key = (normalized_name, entity_type)
    if key in _entity_cache:
        _entity_cache.move_to_end(key)
        return _entity_cache[key]
    return None


def _cache_set(normalized_name: str, entity_type: str, entity: Entity) -> None:
    """Thread-safe cache set with LRU eviction."""
    key = (normalized_name, entity_type)
    if key in _entity_cache:
        _entity_cache.move_to_end(key)
    else:
        if len(_entity_cache) >= _ENTITY_CACHE_MAXSIZE:
            _entity_cache.popitem(last=False)
        _entity_cache[key] = entity


def _cache_invalidate(normalized_name: str, entity_type: str) -> None:
    """Remove entry from cache."""
    key = (normalized_name, entity_type)
    _entity_cache.pop(key, None)


class EntityService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def create_entity(self, *, name: str, entity_type: str, aliases: list[str] | None = None,
                            metadata_: dict | None = None, linked_node_id: uuid.UUID | None = None) -> Entity:
        normalized = name.lower().strip()

        # Check cache first
        async with _entity_cache_lock:
            cached = _cache_get(normalized, entity_type)
            if cached:
                return cached

        async with self.db.session() as session:
            existing = await session.execute(
                select(Entity).where(
                    Entity.normalized_name == normalized,
                    Entity.entity_type == entity_type,
                )
            )
            if entity := existing.scalar_one_or_none():
                async with _entity_cache_lock:
                    _cache_set(normalized, entity_type, entity)
                return entity

            entity = Entity(
                name=name,
                entity_type=entity_type,
                normalized_name=normalized,
                aliases=aliases,
                metadata_=metadata_,
                linked_node_id=linked_node_id,
            )
            session.add(entity)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # Another request concurrently created the same entity; re-query it.
                existing = await session.execute(
                    select(Entity).where(
                        Entity.normalized_name == normalized,
                        Entity.entity_type == entity_type,
                    )
                )
                entity = existing.scalar_one()

        # Add to cache
        async with _entity_cache_lock:
            _cache_set(normalized, entity_type, entity)

        return entity

    async def get_or_create(self, name: str, entity_type: str) -> Entity:
        return await self.create_entity(name=name, entity_type=entity_type)

    async def get_or_create_many(self, entity_pairs: list[tuple[str, str]]) -> dict[tuple[str, str], Entity]:
        ordered_pairs = OrderedDict()
        needs_db_lookup = []
        result_map = {}

        # Build lookup map and check cache
        async with _entity_cache_lock:
            for name, entity_type in entity_pairs:
                normalized = name.lower().strip()
                key = (name, entity_type)
                lookup = (normalized, entity_type)

                if key not in ordered_pairs:
                    ordered_pairs[key] = lookup

                    # Check cache
                    cached = _cache_get(normalized, entity_type)
                    if cached:
                        result_map[key] = cached
                    else:
                        needs_db_lookup.append(lookup)

        if not needs_db_lookup and not ordered_pairs:
            return {}

        # If all found in cache, return immediately
        if not needs_db_lookup:
            return result_map

        async with self.db.session() as session:
            # Query DB for cache misses
            existing_result = await session.execute(
                select(Entity).where(
                    tuple_(Entity.normalized_name, Entity.entity_type).in_(needs_db_lookup)
                )
            )
            lookup_to_entity = {
                (entity.normalized_name, entity.entity_type): entity
                for entity in existing_result.scalars().all()
            }

            # Create missing entities and update cache
            async with _entity_cache_lock:
                for (name, entity_type), lookup in ordered_pairs.items():
                    if (name, entity_type) in result_map:
                        continue

                    if lookup in lookup_to_entity:
                        entity = lookup_to_entity[lookup]
                        _cache_set(lookup[0], lookup[1], entity)
                        result_map[(name, entity_type)] = entity
                    else:
                        entity = Entity(
                            name=name,
                            entity_type=entity_type,
                            normalized_name=lookup[0],
                        )
                        session.add(entity)
                        lookup_to_entity[lookup] = entity
                        result_map[(name, entity_type)] = entity

            if any(e for e in lookup_to_entity.values() if e.id is None):
                try:
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    # Re-query entities created by concurrent tasks
                    missing = [
                        lookup for lookup, entity in lookup_to_entity.items()
                        if entity.id is None
                    ]
                    if missing:
                        async with self.db.session() as retry_session:
                            retry_result = await retry_session.execute(
                                select(Entity).where(
                                    tuple_(Entity.normalized_name, Entity.entity_type).in_(missing)
                                )
                            )
                            for entity in retry_result.scalars().all():
                                key = (entity.normalized_name, entity.entity_type)
                                lookup_to_entity[key] = entity
                                # Replace stale ORM object in result_map with the re-fetched one
                                for (name, entity_type), lookup in ordered_pairs.items():
                                    if lookup == key and result_map.get((name, entity_type)) is not None:
                                        result_map[(name, entity_type)] = entity
                            # Commit retry session read-only, no writes needed
                # Update cache with flushed entities
                async with _entity_cache_lock:
                    for lookup, entity in lookup_to_entity.items():
                        _cache_set(lookup[0], lookup[1], entity)

        return result_map

    async def extract_entities(self, info_id: uuid.UUID, entities: list[dict]) -> list[InformationEntity]:
        async with self.db.session() as session:
            existing_links = await session.execute(
                select(InformationEntity).where(InformationEntity.raw_info_id == info_id)
            )
            linked_entity_ids = {ie.entity_id for ie in existing_links.scalars().all()}

        # 批量 get_or_create 替代 N 次独立查询
        pairs = [(e["name"], e["entity_type"]) for e in entities if e.get("name") and e.get("entity_type")]
        entity_map = await self.get_or_create_many(pairs)

        async with self.db.session() as session:
            results = []
            for e in entities:
                key = (e["name"], e["entity_type"])
                if key not in entity_map:
                    continue
                entity = entity_map[key]
                if entity.id in linked_entity_ids:
                    continue
                ie = InformationEntity(
                    raw_info_id=info_id,
                    entity_id=entity.id,
                    role=e.get("role"),
                    relevance_score=e.get("relevance_score"),
                    extraction_confidence=e.get("extraction_confidence", 1.0),
                )
                session.add(ie)
                linked_entity_ids.add(entity.id)
                results.append(ie)
            if results:
                try:
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    # Concurrent extract_entities for the same info_id may create
                    # overlapping (raw_info_id, entity_id) pairs. Re-query for what
                    # was concurrently created and skip those.
                    for ie in results:
                        if ie.id is not None:
                            continue
                        existing = await session.execute(
                            select(InformationEntity).where(
                                InformationEntity.raw_info_id == info_id,
                                InformationEntity.entity_id == ie.entity_id,
                            )
                        )
                        if row := existing.scalar_one_or_none():
                            results[results.index(ie)] = row
            return results

    async def get_entities_for_info(self, info_id: uuid.UUID) -> list[InformationEntity]:
        async with self.db.session() as session:
            result = await session.execute(
                select(InformationEntity).where(InformationEntity.raw_info_id == info_id)
            )
            return list(result.scalars().all())

    async def list_entities(self, *, entity_type: str | None = None, search: str | None = None,
                            page: int = 1, page_size: int = 20) -> tuple[list[Entity], int]:
        async with self.db.session() as session:
            query = select(Entity)
            count_query = select(func.count()).select_from(Entity)
            if entity_type:
                query = query.where(Entity.entity_type == entity_type)
                count_query = count_query.where(Entity.entity_type == entity_type)
            if search:
                escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                query = query.where(Entity.name.ilike(f"%{escaped}%"))
                count_query = count_query.where(Entity.name.ilike(f"%{escaped}%"))
            query = query.order_by(Entity.name).offset((page - 1) * page_size).limit(page_size)
            total_result = await session.execute(count_query)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())
            return items, total

    async def upsert_relationships_many(self, rels: list[dict], *,
                                         evidence_info_id: uuid.UUID | None = None) -> list[EntityRelationship]:
        """批量 upsert 关系，替代循环调用 upsert_relationship。

        Args:
            rels: 关系列表，每项包含 source_entity_id, target_entity_id, relationship_type, strength, description
            evidence_info_id: 共享的证据资讯 ID
        """
        if not rels:
            return []

        from sqlalchemy import or_, and_

        async with self.db.session() as session:
            # 一次 SELECT 查询所有已有关系
            conditions = or_(*[
                and_(
                    EntityRelationship.source_entity_id == r["source_entity_id"],
                    EntityRelationship.target_entity_id == r["target_entity_id"],
                    EntityRelationship.relationship_type == r["relationship_type"],
                )
                for r in rels
            ])
            existing = (await session.execute(
                select(EntityRelationship).where(conditions)
            )).scalars().all()
            existing_map = {
                (er.source_entity_id, er.target_entity_id, er.relationship_type): er
                for er in existing
            }

            results = []
            for r in rels:
                key = (r["source_entity_id"], r["target_entity_id"], r["relationship_type"])
                if key in existing_map:
                    rel = existing_map[key]
                    if r.get("strength") is not None:
                        n = min(len(rel.evidence_info_ids or []), 20)
                        old = rel.strength or 0.0
                        rel.strength = round((old * n + r["strength"]) / (n + 1), 4)
                    if evidence_info_id and evidence_info_id not in (rel.evidence_info_ids or []):
                        ids = list(rel.evidence_info_ids or [])
                        ids.append(evidence_info_id)
                        rel.evidence_info_ids = ids
                    if r.get("description"):
                        rel.description = r["description"]
                    session.add(rel)
                    results.append(rel)
                else:
                    rel = EntityRelationship(
                        source_entity_id=r["source_entity_id"],
                        target_entity_id=r["target_entity_id"],
                        relationship_type=r["relationship_type"],
                        strength=r.get("strength"),
                        evidence_info_ids=[evidence_info_id] if evidence_info_id else [],
                        description=r.get("description"),
                    )
                    session.add(rel)
                    results.append(rel)

            if results:
                await session.flush()
            return results

    async def upsert_relationship(self, *, source_entity_id: uuid.UUID, target_entity_id: uuid.UUID,
                                   relationship_type: str, strength: float | None = None,
                                   evidence_info_id: uuid.UUID | None = None,
                                   description: str | None = None) -> EntityRelationship:
        """创建或更新实体关系。同对、同类型的关系唯一，新证据追加到 evidence_info_ids。"""
        async with self.db.session() as session:
            result = await session.execute(
                select(EntityRelationship).where(
                    EntityRelationship.source_entity_id == source_entity_id,
                    EntityRelationship.target_entity_id == target_entity_id,
                    EntityRelationship.relationship_type == relationship_type,
                )
            )
            rel = result.scalar_one_or_none()

            if rel:
                if strength is not None:
                    n = min(len(rel.evidence_info_ids or []), 20)
                    old = rel.strength or 0.0
                    rel.strength = round((old * n + strength) / (n + 1), 4)
                if evidence_info_id and evidence_info_id not in (rel.evidence_info_ids or []):
                    ids = list(rel.evidence_info_ids or [])
                    ids.append(evidence_info_id)
                    rel.evidence_info_ids = ids
                if description:
                    rel.description = description
                session.add(rel)
                await session.flush()
                return rel

            rel = EntityRelationship(
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
                relationship_type=relationship_type,
                strength=strength,
                evidence_info_ids=[evidence_info_id] if evidence_info_id else [],
                description=description,
            )
            session.add(rel)
            await session.flush()
            return rel

    async def get_relationships(self, entity_id: uuid.UUID) -> list[EntityRelationship]:
        async with self.db.session() as session:
            result = await session.execute(
                select(EntityRelationship).where(
                    (EntityRelationship.source_entity_id == entity_id) |
                    (EntityRelationship.target_entity_id == entity_id)
                )
            )
            return list(result.scalars().all())
