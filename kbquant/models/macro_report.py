from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class MacroReport(BaseModel):
    """宏观形势报告 —— 宏观 Agent 读写"""
    __tablename__ = "macro_reports"

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    changed_sections: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=[])
