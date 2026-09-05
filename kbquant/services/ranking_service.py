import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func

from kbquant.database import LazyDB
from kbquant.models.importance_ranking import ImportanceRanking
from kbquant.models.raw_information import RawInformation
from kbquant.models.world_node import WorldNode

SOURCE_AUTHORITY: dict[str, float] = {
    "央行官网": 0.95,
    "证监会": 0.95,
    "政府网站": 0.90,
    "Reuters": 0.90,
    "Bloomberg": 0.90,
    "新浪财经": 0.50,
    "Twitter": 0.20,
    "自媒体": 0.15,
}

DEFAULT_AUTHORITY = 0.40
HALF_LIFE_DAYS = 7.0  # 资讯半衰期 7 天


def _compute_recency(published_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    delta_days = max(0, (now - published_at).total_seconds() / 86400)
    return math.exp(-math.log(2) * delta_days / HALF_LIFE_DAYS)


class RankingService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def compute(self, target_type: str, target_id: uuid.UUID,
                      force_recompute: bool = False) -> dict:
        now = datetime.now(timezone.utc)

        recency = 0.5
        source_authority = DEFAULT_AUTHORITY
        centrality = 0.1

        async with self.db.session() as session:
            if target_type == "raw_information":
                row = await session.get(RawInformation, target_id)
                if row:
                    if row.published_at:
                        recency = _compute_recency(row.published_at, now)
                    source_authority = SOURCE_AUTHORITY.get(row.source, DEFAULT_AUTHORITY)
            elif target_type == "world_node":
                row = await session.get(WorldNode, target_id)
                if row and row.created_at:
                    recency = _compute_recency(row.created_at, now)
                centrality = 0.3 if row else 0.1

        score = round(
            0.40 * recency + 0.30 * source_authority + 0.20 * centrality + 0.10 * 0.5,
            4,
        )
        components = {
            "recency": round(recency, 4),
            "source_authority": round(source_authority, 4),
            "centrality": round(centrality, 4),
            "agent_demand": 0.5,
        }

        async with self.db.session() as session:
            ranking = ImportanceRanking(
                target_type=target_type, target_id=target_id,
                importance_score=score, score_components=components,
                computed_at=now,
            )
            session.add(ranking)
            await session.flush()
            return {"score": ranking.importance_score, "components": ranking.score_components,
                    "rank_position": ranking.rank_position}

    async def list_rankings(self, target_type: str | None = None, min_score: float | None = None,
                            limit: int = 20) -> list[ImportanceRanking]:
        async with self.db.session() as session:
            query = select(ImportanceRanking)
            if target_type:
                query = query.where(ImportanceRanking.target_type == target_type)
            if min_score is not None:
                query = query.where(ImportanceRanking.importance_score >= min_score)
            query = query.order_by(ImportanceRanking.importance_score.desc()).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_history(self, target_type: str, target_id: uuid.UUID) -> list[ImportanceRanking]:
        async with self.db.session() as session:
            result = await session.execute(
                select(ImportanceRanking).where(
                    ImportanceRanking.target_type == target_type,
                    ImportanceRanking.target_id == target_id,
                ).order_by(ImportanceRanking.computed_at.desc()).limit(50)
            )
            return list(result.scalars().all())
