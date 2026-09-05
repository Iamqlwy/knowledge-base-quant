from fastapi import APIRouter, Depends

from kbquant.api.dependencies import get_read_search_service, verify_api_key
from kbquant.schemas.search import (
    SearchRequest, SearchResponse,
    FetchByIdsRequest, FetchByIdsResponse,
)
from kbquant.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(verify_api_key)])


@router.post("", response_model=SearchResponse)
async def search(data: SearchRequest, svc: SearchService = Depends(get_read_search_service)):
    """统一搜索接口。

    通过 mode 参数控制搜索策略：
    - hybrid: 混合搜索（BM25 + 向量 + 名称匹配 + 结构权重 + 时间衰减）
    - embedding: 纯向量语义搜索
    - bm25: 纯 BM25 关键词搜索
    """
    return await svc.search(
        data.query_text, mode=data.mode,
        filters=data.filters, weights=data.weights, limit=data.limit,
        date_range=data.date_range.model_dump() if data.date_range else None,
        only_tables=data.only_tables,
    )


@router.post("/fetch-by-ids", response_model=FetchByIdsResponse)
async def fetch_by_ids(data: FetchByIdsRequest, svc: SearchService = Depends(get_read_search_service)):
    result = await svc.fetch_by_ids(data.table_ids)
    return {"data": result}
