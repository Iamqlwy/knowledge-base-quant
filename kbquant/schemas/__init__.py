from datetime import datetime, timezone, timedelta
from typing import Annotated, Any
from uuid import UUID

import numpy as np

from pydantic import BaseModel as PydanticBaseModel, BeforeValidator, field_serializer, model_serializer, SerializationInfo

# 北京时间 UTC+8
_BEIJING_TZ = timezone(timedelta(hours=8))


def _to_beijing(dt: datetime) -> datetime:
    """将 datetime 转换为北京时间。naive datetime 视为 UTC。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING_TZ)


def _beijing_to_utc(v: datetime | str) -> datetime:
    """将北京时间输入（datetime 或 ISO 字符串）转为 UTC。naive datetime 视为北京时间。"""
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    if v.tzinfo is None:
        v = v.replace(tzinfo=_BEIJING_TZ)
    return v.astimezone(timezone.utc)


# 用于 custom_time / published_at 字段：自动将北京时间的输入转为 UTC
BeijingTime = Annotated[datetime, BeforeValidator(_beijing_to_utc)]


def _convert_utc_in_obj(obj: Any) -> Any:
    """递归遍历序列化后的 dict/list，将 UTC ISO 时间字符串转为北京时间。"""
    if isinstance(obj, str) and len(obj) >= 25 and obj[10] == "T":
        if obj.endswith("+00:00") or obj.endswith("Z"):
            try:
                dt = datetime.fromisoformat(obj)
                return dt.astimezone(_BEIJING_TZ).isoformat()
            except (ValueError, OSError):
                pass
        return obj
    if isinstance(obj, dict):
        return {k: _convert_utc_in_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_utc_in_obj(v) for v in obj]
    return obj


class BaseSchema(PydanticBaseModel):
    """所有 schema 的基类，配置 from_attributes=True 以支持 ORM 对象直接序列化"""
    model_config = {"from_attributes": True}

    @model_serializer(mode="wrap")
    def _serialize_beijing(self, handler, info: SerializationInfo) -> Any:
        """所有子类序列化时自动将 UTC 时间转为北京时间。"""
        data = handler(self)
        return _convert_utc_in_obj(data)


def _serialize_value(val: Any) -> Any:
    """Recursively convert values to JSON-serializable types."""
    if isinstance(val, np.ndarray):
        return val.tolist()
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, datetime):
        return _to_beijing(val).isoformat()
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    return val


def _orm_to_dict(obj: Any) -> Any:
    """Convert a SQLAlchemy ORM object to a plain dict for serialization."""
    if not hasattr(obj, "__table__"):
        return _serialize_value(obj)

    result = {}
    for c in obj.__table__.columns:
        try:
            val = getattr(obj, c.key)
        except AttributeError:
            continue
        # Skip SQLAlchemy internal objects (e.g. MetaData from column named "metadata")
        if val is not None:
            mod = getattr(type(val), "__module__", "") or ""
            if "sqlalchemy" in mod:
                continue
        result[c.name] = _serialize_value(val)
    return result


class PaginatedResponse(BaseSchema):
    """分页响应，所有列表类接口统一使用"""
    items: list[Any]  # 当前页数据
    total: int  # 总记录数
    page: int  # 当前页码
    page_size: int  # 每页大小

    @field_serializer("items")
    def serialize_items(self, items: list[Any], _info: Any) -> list[Any]:
        return [_orm_to_dict(item) for item in items]


class ErrorResponse(BaseSchema):
    """统一错误响应"""
    detail: str  # 错误描述
    error_code: str | None = None  # 错误码，用于程序判断


class HealthResponse(BaseSchema):
    """健康检查响应，用于监控和探活"""
    status: str  # 服务状态，ok 表示正常
    db: str  # 数据库连接状态
    version: str = "0.1.0"  # 服务版本号
