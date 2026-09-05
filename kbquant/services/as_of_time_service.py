import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import defer

from kbquant.database import LazyDB
from kbquant.models.node_state import NodeState
from kbquant.models.raw_information import RawInformation
from kbquant.models.analysis import Analysis
from kbquant.models.trading_operation import TradingOperation


class AsOfTimeService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def query_at(self, timestamp: datetime, node_id: uuid.UUID | None = None,
                       entity_id: uuid.UUID | None = None, info_id: uuid.UUID | None = None) -> dict:
        """查询某个历史时间点的系统状态。所有读取都过滤 created_at/ingested_at <= timestamp 以防止未来信息泄露。"""
        result = {}

        async with self.db.session() as session:
            if node_id:
                state = await session.execute(
                    select(NodeState).where(
                        NodeState.node_id == node_id,
                        NodeState.effective_from <= timestamp,
                        (NodeState.effective_to == None) | (NodeState.effective_to > timestamp),
                    )
                )
                result["node_state"] = state.scalar_one_or_none()

            raw_info_count = await session.execute(
                select(func.count()).select_from(RawInformation).where(RawInformation.ingested_at <= timestamp)
            )
            result["raw_info_count_at_time"] = raw_info_count.scalar_one()

            analysis_count = await session.execute(
                select(func.count()).select_from(Analysis).where(Analysis.created_at <= timestamp)
            )
            result["analysis_count_at_time"] = analysis_count.scalar_one()

        return result

    async def diff_state(self, node_id: uuid.UUID, timestamp_a: datetime,
                         timestamp_b: datetime) -> dict:
        async with self.db.session() as session:
            state_a = await session.execute(
                select(NodeState).where(
                    NodeState.node_id == node_id,
                    NodeState.effective_from <= timestamp_a,
                    (NodeState.effective_to == None) | (NodeState.effective_to > timestamp_a),
                )
            )
            state_b = await session.execute(
                select(NodeState).where(
                    NodeState.node_id == node_id,
                    NodeState.effective_from <= timestamp_b,
                    (NodeState.effective_to == None) | (NodeState.effective_to > timestamp_b),
                )
            )
            a = state_a.scalar_one_or_none()
            b = state_b.scalar_one_or_none()

        diff = {
            "state_a": a,
            "state_b": b,
            "diff": {
                "added_drivers": [],
                "removed_drivers": [],
                "changed_risks": [],
            },
        }
        return diff
