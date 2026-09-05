import logging
from uuid import UUID

from elasticsearch import AsyncElasticsearch, exceptions as es_exceptions

from kbquant.config import settings
from kbquant.models.raw_information import RawInformation
from kbquant.models.analysis import Analysis
from kbquant.models.feedback import Feedback
from kbquant.models.world_node import WorldNode
from kbquant.models.node_state import NodeState
from kbquant.integrations.elasticsearch.client import get_es

logger = logging.getLogger(__name__)
PREFIX = settings.elasticsearch_index_prefix


async def _index_doc(es: AsyncElasticsearch, index: str, pg_id: UUID, doc: dict):
    try:
        await es.index(index=index, id=str(pg_id), document=doc, refresh=False)
    except es_exceptions.ConnectionError:
        logger.debug("ES 不可用，跳过同步 pg_id=%s", pg_id)
    except Exception:
        logger.exception("ES 同步失败 pg_id=%s", pg_id)


async def sync_raw_info(info: RawInformation):
    es = get_es()
    await _index_doc(es, f"{PREFIX}_raw_info", info.id, {
        "pg_id": str(info.id),
        "title": info.title,
        "body": info.body,
        "source": info.source,
        "info_type": info.info_type,
        "published_at": info.published_at.isoformat() if info.published_at else None,
        "importance_score": info.importance_score,
        "language": info.language,
    })


async def sync_analysis(analysis: Analysis):
    es = get_es()
    await _index_doc(es, f"{PREFIX}_analyses", analysis.id, {
        "pg_id": str(analysis.id),
        "title": analysis.title,
        "content": analysis.content,
        "analysis_type": analysis.analysis_type,
        "agent_id": analysis.agent_id,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
    })


async def sync_feedback(feedback: Feedback):
    es = get_es()
    await _index_doc(es, f"{PREFIX}_feedbacks", feedback.id, {
        "pg_id": str(feedback.id),
        "title": feedback.title,
        "lessons_learned": feedback.lessons_learned,
        "error_reason": feedback.error_reason,
        "adjustment_suggestions": feedback.adjustment_suggestions,
        "judgment_correct": feedback.judgment_correct,
        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
    })


async def sync_world_node(node: WorldNode):
    es = get_es()
    if not node.is_active:
        await remove_from_index(f"{PREFIX}_nodes", node.id)
        return
    await _index_doc(es, f"{PREFIX}_nodes", node.id, {
        "pg_id": str(node.id),
        "name": node.name,
        "description": node.description,
        "node_type": node.node_type,
        "ticker": node.ticker,
        "is_active": True,
    })


async def sync_node_state(state: NodeState):
    es = get_es()
    await _index_doc(es, f"{PREFIX}_node_states", state.id, {
        "pg_id": str(state.id),
        "node_id": str(state.node_id),
        "state_summary": state.state_summary,
        "core_logic": state.core_logic,
        "version": state.version,
        "created_at": state.created_at.isoformat() if state.created_at else None,
    })


async def remove_from_index(index: str, pg_id: UUID):
    es = get_es()
    try:
        await es.delete(index=index, id=str(pg_id))
    except Exception:
        logger.debug("ES 删除失败 index=%s pg_id=%s", index, pg_id)


async def bulk_sync_raw_info(infos: list[RawInformation]) -> dict:
    """批量同步 RawInformation 到 Elasticsearch

    Args:
        infos: RawInformation 对象列表

    Returns:
        包含成功和失败数量的字典
    """
    if not infos:
        return {"success": 0, "failed": 0}

    es = get_es()
    actions = []

    for info in infos:
        action = {
            "_index": f"{PREFIX}_raw_info",
            "_id": str(info.id),
            "_source": {
                "pg_id": str(info.id),
                "title": info.title,
                "body": info.body,
                "source": info.source,
                "info_type": info.info_type,
                "published_at": info.published_at.isoformat() if info.published_at else None,
                "importance_score": info.importance_score,
                "language": info.language,
            }
        }
        actions.append(action)

    try:
        from elasticsearch.helpers import async_bulk
        success, failed = await async_bulk(
            es,
            actions,
            refresh=False,
            raise_on_error=False,
            raise_on_exception=False,
        )
        logger.info("ES 批量索引完成: 成功 %d, 失败 %d", success, len(failed))
        return {"success": success, "failed": len(failed)}
    except es_exceptions.ConnectionError:
        logger.debug("ES 不可用，跳过批量同步 %d 条", len(infos))
        return {"success": 0, "failed": len(infos)}
    except Exception:
        logger.exception("ES 批量同步失败")
        return {"success": 0, "failed": len(infos)}
