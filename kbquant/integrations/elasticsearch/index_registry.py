import logging

from elasticsearch import AsyncElasticsearch

from kbquant.config import settings

logger = logging.getLogger(__name__)

PREFIX = settings.elasticsearch_index_prefix

INDEX_DEFINITIONS = {
    f"{PREFIX}_raw_info": {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "-1",
            "analysis": {
                "analyzer": {
                    "ik_max_word_analyzer": {"type": "ik_max_word"},
                    "ik_smart_analyzer": {"type": "ik_smart"},
                },
            },
        },
        "mappings": {
            "properties": {
                "pg_id": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "body": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "source": {"type": "keyword"},
                "info_type": {"type": "keyword"},
                "published_at": {"type": "date"},
                "importance_score": {"type": "float"},
                "language": {"type": "keyword"},
            },
        },
    },
    f"{PREFIX}_analyses": {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "-1",
            "analysis": {
                "analyzer": {
                    "ik_max_word_analyzer": {"type": "ik_max_word"},
                    "ik_smart_analyzer": {"type": "ik_smart"},
                },
            },
        },
        "mappings": {
            "properties": {
                "pg_id": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "content": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "analysis_type": {"type": "keyword"},
                "agent_id": {"type": "keyword"},
                "created_at": {"type": "date"},
            },
        },
    },
    f"{PREFIX}_feedbacks": {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "-1",
            "analysis": {
                "analyzer": {
                    "ik_max_word_analyzer": {"type": "ik_max_word"},
                    "ik_smart_analyzer": {"type": "ik_smart"},
                },
            },
        },
        "mappings": {
            "properties": {
                "pg_id": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "lessons_learned": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "error_reason": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "adjustment_suggestions": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "judgment_correct": {"type": "boolean"},
                "created_at": {"type": "date"},
            },
        },
    },
    f"{PREFIX}_nodes": {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "-1",
            "analysis": {
                "analyzer": {
                    "ik_max_word_analyzer": {"type": "ik_max_word"},
                    "ik_smart_analyzer": {"type": "ik_smart"},
                },
            },
        },
        "mappings": {
            "properties": {
                "pg_id": {"type": "keyword"},
                "name": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "description": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "node_type": {"type": "keyword"},
                "ticker": {"type": "keyword"},
            },
        },
    },
    f"{PREFIX}_node_states": {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "-1",
            "analysis": {
                "analyzer": {
                    "ik_max_word_analyzer": {"type": "ik_max_word"},
                    "ik_smart_analyzer": {"type": "ik_smart"},
                },
            },
        },
        "mappings": {
            "properties": {
                "pg_id": {"type": "keyword"},
                "node_id": {"type": "keyword"},
                "state_summary": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "core_logic": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
                "version": {"type": "integer"},
                "created_at": {"type": "date"},
            },
        },
    },
}


async def create_all_indexes(es: AsyncElasticsearch):
    for index_name, body in INDEX_DEFINITIONS.items():
        exists = await es.indices.exists(index=index_name)
        if not exists:
            await es.indices.create(index=index_name, **body)
            logger.info("ES 索引已创建: %s", index_name)
        else:
            logger.debug("ES 索引已存在: %s", index_name)
