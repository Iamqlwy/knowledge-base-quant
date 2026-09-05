from sqlalchemy import Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class StructuredPreference(BaseModel):
    __tablename__ = "structured_preferences"

    asset_preferences: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "sector_weights": {},
            "avoid_list": [],
            "market_cap_preference": "any",
            "whitelist": [],
        },
    )
    risk_preferences: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "position_limits": {},
            "max_drawdown_pct": 20.0,
            "stop_loss_pct": 10.0,
            "take_profit_pct": 30.0,
        },
    )
    analysis_preferences: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "time_horizon": "medium",
            "depth": "standard",
            "focus_points": [],
        },
    )
    learned_rules: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )


class IndustryCognition(BaseModel):
    __tablename__ = "industry_cognitions"

    sector: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    cognition_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    append_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MarketCognition(BaseModel):
    """市场全局认知 —— 单例表，与 IndustryCognition 平行的自由文本认知。

    追加+阈值 LLM 重写语义与 IndustryCognition 一致，但作用于整个市场而非单个行业。
    """
    __tablename__ = "market_cognitions"
    cognition_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    append_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
