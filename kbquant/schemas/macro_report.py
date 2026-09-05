from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema, BeijingTime


class MacroReportResponse(BaseSchema):
    id: UUID
    version: int
    content: str
    summary: str
    changed_sections: list[str]
    created_at: datetime
    updated_at: datetime


class MacroReportUpdate(BaseSchema):
    content: str
    summary: str
    changed_sections: list[str]
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class MacroReportHistoryItem(BaseSchema):
    id: UUID
    version: int
    summary: str
    content: str
    changed_sections: list[str]
    updated_at: datetime


class MacroReportHistoryResponse(BaseSchema):
    items: list[MacroReportHistoryItem]
