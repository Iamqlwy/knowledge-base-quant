from uuid import UUID

from kbquant.schemas import BaseSchema


class BatchGetRequest(BaseSchema):
    """批量按 ID 获取资源的通用请求"""
    ids: list[UUID]
