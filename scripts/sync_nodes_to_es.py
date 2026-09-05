"""创建 nodes/node_states 的 ES 索引，并将 DB 现有数据全量同步到 ES"""
import asyncio
import logging

from elasticsearch.helpers import async_bulk

from kbquant.integrations.elasticsearch.client import get_es
from kbquant.integrations.elasticsearch.index_registry import INDEX_DEFINITIONS, PREFIX
from kbquant.database import read_lazy
from kbquant.models.world_node import WorldNode
from kbquant.models.node_state import NodeState
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def create_indexes(es):
    for idx_name in [f"{PREFIX}_nodes", f"{PREFIX}_node_states"]:
        exists = await es.indices.exists(index=idx_name)
        if not exists:
            body = INDEX_DEFINITIONS[idx_name]
            await es.indices.create(index=idx_name, **body)
            print(f"  索引已创建: {idx_name}")
        else:
            print(f"  索引已存在: {idx_name}")


async def bulk_sync_nodes(es):
    """全量同步 WorldNode -> quant_kb_nodes"""
    async with read_lazy.session() as session:
        result = await session.execute(
            select(WorldNode).where(WorldNode.is_active == True)
        )
        nodes = result.scalars().all()
        print(f"  DB 中活跃节点: {len(nodes)}")

        actions = []
        for node in nodes:
            actions.append({
                "_index": f"{PREFIX}_nodes",
                "_id": str(node.id),
                "_source": {
                    "pg_id": str(node.id),
                    "name": node.name,
                    "description": node.description or "",
                    "node_type": node.node_type,
                    "ticker": node.ticker or "",
                    "is_active": True,
                },
            })

        if actions:
            success, failed = await async_bulk(
                es, actions, refresh=True, raise_on_error=False, raise_on_exception=False
            )
            print(f"  已索引 {success} 条，失败 {len(failed)} 条")
        else:
            print(f"  无数据")


async def bulk_sync_node_states(es):
    """全量同步 NodeState -> quant_kb_node_states"""
    async with read_lazy.session() as session:
        result = await session.execute(
            select(NodeState).where(NodeState.effective_to.is_(None))
        )
        states = result.scalars().all()
        print(f"  DB 中当前有效状态: {len(states)}")

        actions = []
        for state in states:
            actions.append({
                "_index": f"{PREFIX}_node_states",
                "_id": str(state.id),
                "_source": {
                    "pg_id": str(state.id),
                    "node_id": str(state.node_id),
                    "state_summary": state.state_summary or "",
                    "core_logic": state.core_logic or "",
                    "version": state.version,
                    "created_at": state.created_at.isoformat() if state.created_at else None,
                },
            })

        if actions:
            success, failed = await async_bulk(
                es, actions, refresh=True, raise_on_error=False, raise_on_exception=False
            )
            print(f"  已索引 {success} 条，失败 {len(failed)} 条")
        else:
            print(f"  无数据")


async def main():
    es = get_es()

    print("=== 创建索引 ===")
    await create_indexes(es)

    print("\n=== 同步 WorldNode ===")
    await bulk_sync_nodes(es)

    print("\n=== 同步 NodeState ===")
    await bulk_sync_node_states(es)

    # 验证
    print("\n=== 验证 ===")
    for idx in [f"{PREFIX}_nodes", f"{PREFIX}_node_states"]:
        r = await es.count(index=idx)
        print(f"  {idx}: {r['count']} docs")

    await es.close()
    print("完成")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
