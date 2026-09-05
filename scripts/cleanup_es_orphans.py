"""清理 Elasticsearch 中 DB 已不存在的文档（覆盖全部 5 个索引）"""
import asyncio
import logging

from kbquant.integrations.elasticsearch.client import get_es
from kbquant.database import read_lazy
from sqlalchemy import select
from kbquant.models.analysis import Analysis
from kbquant.models.feedback import Feedback
from kbquant.models.raw_information import RawInformation
from kbquant.models.world_node import WorldNode
from kbquant.models.node_state import NodeState

logger = logging.getLogger(__name__)


async def get_es_pg_ids(es, index: str) -> set[str]:
    """Scroll 获取 ES 索引中所有文档的 pg_id"""
    ids = set()
    resp = await es.search(
        index=index,
        body={"query": {"match_all": {}}, "_source": ["pg_id"]},
        scroll="2m",
        size=1000,
    )
    scroll_id = resp.get("_scroll_id")
    hits = resp["hits"]["hits"]
    for hit in hits:
        pg_id = hit["_source"].get("pg_id")
        if pg_id:
            ids.add(pg_id)

    while hits:
        resp = await es.scroll(scroll_id=scroll_id, scroll="2m")
        scroll_id = resp.get("_scroll_id")
        hits = resp["hits"]["hits"]
        for hit in hits:
            pg_id = hit["_source"].get("pg_id")
            if pg_id:
                ids.add(pg_id)

    if scroll_id:
        await es.clear_scroll(scroll_id=scroll_id)
    return ids


async def get_db_ids(session, model) -> set[str]:
    """获取 DB 中某张表的所有 ID"""
    result = await session.execute(select(model.id))
    return {str(r) for r in result.scalars().all()}


async def delete_es_docs(es, index: str, ids: set[str]):
    """批量删除 ES 中的文档"""
    from elasticsearch.helpers import async_bulk
    actions = [{"_op_type": "delete", "_index": index, "_id": pid} for pid in ids]
    if not actions:
        return 0, 0
    success, failed = await async_bulk(es, actions, refresh=True, raise_on_error=False, raise_on_exception=False)
    return success, len(failed)


async def main():
    es = get_es()

    async with read_lazy.session() as session:
        for label, index, model in [
            ("raw_info", "quant_kb_raw_info", RawInformation),
            ("analyses", "quant_kb_analyses", Analysis),
            ("feedbacks", "quant_kb_feedbacks", Feedback),
            ("nodes", "quant_kb_nodes", WorldNode),
            ("node_states", "quant_kb_node_states", NodeState),
        ]:
            print(f"--- {label} ---")
            es_ids = await get_es_pg_ids(es, index)
            db_ids = await get_db_ids(session, model)
            orphan_ids = es_ids - db_ids
            print(f"  ES: {len(es_ids)}, DB: {len(db_ids)}, 孤儿: {len(orphan_ids)}")
            for pid in sorted(orphan_ids):
                print(f"    {pid}")
            if orphan_ids:
                success, failed = await delete_es_docs(es, index, orphan_ids)
                print(f"  已删除 {success} 条，失败 {failed} 条")
            else:
                print(f"  无需清理")

    await es.close()
    print("完成")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
