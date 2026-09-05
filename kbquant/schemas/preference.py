from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema, BeijingTime


# ---------------------------------------------------------------------------
# 行业认知
# ---------------------------------------------------------------------------

class IndustryCognitionResponse(BaseSchema):
    sector: str
    text: str
    append_count: int


class IndustryCognitionSectorsResponse(BaseSchema):
    sectors: list[str]


class IndustryCognitionAppend(BaseSchema):
    text: str
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class IndustryCognitionAppendResponse(BaseSchema):
    sector: str
    status: str  # 始终为 "appended"；达到阈值时 rewrite 在后台异步执行


# ---------------------------------------------------------------------------
# 结构化偏好 - 子模型
# ---------------------------------------------------------------------------

class AssetPreferences(BaseSchema):
    sector_weights: dict[str, float] = {}
    avoid_list: list[str] = []
    market_cap_preference: str = "any"
    whitelist: list[str] = []


class RiskPreferences(BaseSchema):
    position_limits: dict[str, float] = {}
    max_drawdown_pct: float = 20.0
    stop_loss_pct: float = 10.0
    take_profit_pct: float = 30.0


class AnalysisPreferences(BaseSchema):
    time_horizon: str = "medium"
    depth: str = "standard"
    focus_points: list[str] = []


# ---------------------------------------------------------------------------
# 结构化偏好 - 请求/响应
# ---------------------------------------------------------------------------

class StructuredPreferencesResponse(BaseSchema):
    id: UUID | None = None
    asset_preferences: AssetPreferences
    risk_preferences: RiskPreferences
    analysis_preferences: AnalysisPreferences
    learned_rules: list[str]
    industry_cognition: dict[str, str] = {}
    industry_append_count: dict[str, int] = {}
    market_cognition: str | None = None
    market_append_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StructuredPreferencesUpdate(BaseSchema):
    asset_preferences: AssetPreferences | None = None
    risk_preferences: RiskPreferences | None = None
    analysis_preferences: AnalysisPreferences | None = None
    learned_rules: list[str] | None = None
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


# ---------------------------------------------------------------------------
# LLM 建议
# ---------------------------------------------------------------------------

class SuggestionWeightChange(BaseSchema):
    sector: str
    new_weight: float
    reason: str | None = None


class SuggestionRiskParam(BaseSchema):
    param_name: str
    new_value: float
    reason: str | None = None


class SuggestionFocusPoint(BaseSchema):
    action: str  # "add" | "remove"
    point: str


class SuggestionsPayload(BaseSchema):
    weight_changes: list[SuggestionWeightChange] = []
    risk_param_changes: list[SuggestionRiskParam] = []
    focus_points: list[SuggestionFocusPoint] = []
    learned_rules_to_add: list[str] = []
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class SuggestionsResponse(BaseSchema):
    status: str
    applied_changes: dict


# ---------------------------------------------------------------------------
# 市场全局认知
# ---------------------------------------------------------------------------

class MarketCognitionResponse(BaseSchema):
    text: str
    append_count: int


class MarketCognitionAppend(BaseSchema):
    text: str
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class MarketCognitionAppendResponse(BaseSchema):
    status: str  # "appended" | "rewritten"
