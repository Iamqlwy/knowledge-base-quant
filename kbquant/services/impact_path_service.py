import math
import uuid

from sqlalchemy import or_, select

from kbquant.database import LazyDB
from kbquant.models.entity import Entity
from kbquant.models.entity_relationship import EntityRelationship


def _evidence_confidence(evidence_count: int) -> float:
    """证据置信度：证据越多置信越高，对数增长，上限 1.0。
       0条=0.15, 1条=0.40, 5条=0.72, 20条=1.0
    """
    if evidence_count <= 0:
        return 0.15
    return min(1.0, 0.40 + 0.20 * math.log(evidence_count))


class ImpactPathService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def find_paths_by_name(
        self, entity_name: str, depth: int = 2,
        direction: str = "both", decay: float = 0.5,
    ) -> dict:
        """通过实体名称查找关系图路径。

        先按名称匹配 Entity 记录，再调用 find_paths 遍历关系图。
        匹配规则: 精确匹配 name 或 normalized_name，回退 ILIKE。
        """
        async with self.db.session() as session:
            normalized = entity_name.lower().strip()
            result = await session.execute(
                select(Entity).where(
                    Entity.normalized_name == normalized,
                ).limit(1)
            )
            root = result.scalar_one_or_none()
            if not root:
                escaped = entity_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                result = await session.execute(
                    select(Entity).where(
                        Entity.name.ilike(f"%{escaped}%"),
                    ).limit(1)
                )
                root = result.scalar_one_or_none()
            if not root:
                return {"root": None, "paths": []}
            return await self._bfs_traverse(root, depth, direction, decay)

    async def find_paths(self, source_entity_id: uuid.UUID, depth: int = 3,
                         direction: str = "downstream", decay: float = 0.5) -> dict:
        """BFS 遍历实体关系图。direction: downstream(沿source→target方向) / upstream / both。

        impact 计算采用衰减传导模型：首跳保留完整强度，之后每跳乘以 decay 再乘以下一跳强度。
        路径越长，传导效果越弱。
        """
        async with self.db.session() as session:
            entity_result = await session.execute(
                select(Entity).where(Entity.id == source_entity_id)
            )
            root = entity_result.scalar_one_or_none()
        if not root:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Entity {source_entity_id} not found")
        return await self._bfs_traverse(root, depth, direction, decay)

    async def _bfs_traverse(
        self, root, depth: int, direction: str, decay: float,
    ) -> dict:

        paths = []
        frontier = [([{"entity": root, "relationship": None}], 0.0)]
        visited = set()

        while frontier:
            active_frontier: list[tuple[list[dict], float]] = []
            frontier_ids: list[uuid.UUID] = []

            for path, cumulative_strength in frontier:
                if len(path) > depth + 1:
                    continue

                current = path[-1]["entity"]
                # 允许起点重复探测（len==1），但不允许后续路径中出现环
                if current.id in visited and len(path) > 1:
                    continue
                visited.add(current.id)

                if len(path) > 1:
                    paths.append({
                        "path": [
                            {"entity_id": str(step["entity"].id), "entity_name": step["entity"].name,
                             "relationship_type": step["relationship"].relationship_type if step["relationship"] else None}
                            for step in path
                        ],
                        "total_impact_strength": cumulative_strength,
                    })

                active_frontier.append((path, cumulative_strength))
                frontier_ids.append(current.id)

            if not active_frontier:
                break

            async with self.db.session() as session:
                if direction == "downstream":
                    rel_stmt = select(EntityRelationship).where(
                        EntityRelationship.source_entity_id.in_(frontier_ids)
                    )
                elif direction == "upstream":
                    rel_stmt = select(EntityRelationship).where(
                        EntityRelationship.target_entity_id.in_(frontier_ids)
                    )
                else:
                    rel_stmt = select(EntityRelationship).where(
                        or_(
                            EntityRelationship.source_entity_id.in_(frontier_ids),
                            EntityRelationship.target_entity_id.in_(frontier_ids),
                        )
                    )

                rel_result = await session.execute(rel_stmt)
                relationship_rows = rel_result.scalars().all()

            down_map: dict[uuid.UUID, list[EntityRelationship]] = {}
            up_map: dict[uuid.UUID, list[EntityRelationship]] = {}
            # 预过滤：只收集未访问过的 next entity ID，避免不必要的 entity 查询
            next_entity_ids: set[uuid.UUID] = set()

            for rel in relationship_rows:
                down_map.setdefault(rel.source_entity_id, []).append(rel)
                up_map.setdefault(rel.target_entity_id, []).append(rel)
                # BFS 方向候选：从 source→target(downstream) 或 target→source(upstream)
                for nid in (rel.source_entity_id, rel.target_entity_id):
                    if nid not in visited:
                        next_entity_ids.add(nid)

            if next_entity_ids:
                async with self.db.session() as session:
                    entity_rows = await session.execute(
                        select(Entity).where(Entity.id.in_(next_entity_ids))
                    )
                    entity_map = {entity.id: entity for entity in entity_rows.scalars().all()}
            else:
                entity_map = {}

            next_frontier: list[tuple[list[dict], float]] = []
            for path, cumulative_strength in active_frontier:
                current = path[-1]["entity"]
                if direction == "downstream":
                    relationships = down_map.get(current.id, [])
                elif direction == "upstream":
                    relationships = up_map.get(current.id, [])
                else:
                    relationships = down_map.get(current.id, []) + up_map.get(current.id, [])

                for rel in relationships:
                    next_entity_id = (
                        rel.target_entity_id if rel.source_entity_id == current.id
                        else rel.source_entity_id
                    )
                    next_entity = entity_map.get(next_entity_id)
                    if next_entity and next_entity.id not in visited:
                        base_s = rel.strength or 0.0
                        ev_count = len(rel.evidence_info_ids) if rel.evidence_info_ids else 0
                        conf = _evidence_confidence(ev_count)
                        s = base_s * conf
                        # 首跳保留完整强度，后续每跳衰减
                        if cumulative_strength == 0.0:
                            new_strength = s
                        else:
                            new_strength = cumulative_strength * decay * s
                        next_frontier.append((
                            path + [{"entity": next_entity, "relationship": rel}],
                            new_strength,
                        ))

            frontier = next_frontier

        paths.sort(key=lambda p: p["total_impact_strength"], reverse=True)
        return {"root": root, "paths": paths[:20]}
