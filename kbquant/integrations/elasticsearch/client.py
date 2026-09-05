import logging

from elasticsearch import AsyncElasticsearch

from kbquant.config import settings

logger = logging.getLogger(__name__)

_es_client: AsyncElasticsearch | None = None


def get_es() -> AsyncElasticsearch:
    global _es_client
    if _es_client is None:
        _es_client = AsyncElasticsearch(
            settings.elasticsearch_url,
            connections_per_node=settings.es_connections_per_node,
            request_timeout=settings.es_request_timeout,
            max_retries=settings.es_max_retries,
            retry_on_timeout=True,
            http_compress=True,
        )
    return _es_client


async def es_startup():
    es = get_es()
    if not await es.ping():
        logger.warning("Elasticsearch 不可用，将使用 PG FTS 降级搜索")
    else:
        logger.info("Elasticsearch 连接成功: %s", settings.elasticsearch_url)


async def es_shutdown():
    global _es_client
    if _es_client is not None:
        await _es_client.close()
        _es_client = None
        logger.info("Elasticsearch 连接已关闭")
