from types import TracebackType
from typing import Self

import httpx

from kbquant.client._base import (
    BaseClient,
    QuantClientAuthError,
    QuantClientConnectionError,
    QuantClientError,
    QuantClientHTTPError,
    QuantClientNotFoundError,
)
from kbquant.client._limiter import ClientConcurrencyLimiter
from kbquant.client.analysis import AnalysisClient
from kbquant.client.conflicts import ConflictClient
from kbquant.client.entities import EntityClient
from kbquant.client.evidence import EvidenceClient
from kbquant.client.feedback import FeedbackClient
from kbquant.client.information import InformationClient
from kbquant.client.macro_report import MacroReportClient
from kbquant.client.nodes import NodeClient
from kbquant.client.pipeline import PipelineClient
from kbquant.client.preference import PreferenceClient
from kbquant.client.queries import QueriesClient
from kbquant.client.ranking import RankingClient
from kbquant.client.search import SearchClient
from kbquant.client.trading import TradingClient
from kbquant.client.validity import ValidityClient
from kbquant.schemas import HealthResponse


class QuantClient:
    """量化知识库的异步 HTTP 客户端，所有 API 能力的统一入口。

    用法::

        async with QuantClient("http://localhost:8000") as client:
            info = await client.information.ingest(...)
            entities = await client.entities.list(...)

    也可手动管理生命周期::

        client = QuantClient("http://localhost:8000")
        await client.health()
        await client.close()
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float | httpx.Timeout | None = 120.0,
        limits: httpx.Limits | None = None,
        max_connections: int = 500,
        max_keepalive_connections: int = 200,
        keepalive_expiry: float = 30.0,
        enable_queuing: bool = True,
        concurrency_limits: dict[str, int] | None = None,
    ):
        """初始化客户端。

        Args:
            base_url: 服务端地址，默认 http://localhost:8000。
            api_key: API 密钥，服务端开启认证时必填。
            timeout: HTTP 请求超时秒数，默认 120 秒；传 None 则不设置超时。
            limits: 自定义 httpx 连接池限制；不传时使用 max_connections/max_keepalive_connections。
            max_connections: 默认连接池最大连接数，支持高并发 search。
            max_keepalive_connections: 默认 keep-alive 连接数。
            keepalive_expiry: keep-alive 连接空闲过期时间（秒），
                必须短于服务端 timeout_keep_alive（默认 65s）以防止 ReadError。
            enable_queuing: 是否启用客户端并发排队。默认 True，按 query/insert/search
                三级 Semaphore 限流。传 False 则完全关闭并发控制。
            concurrency_limits: 自定义各层并发上限，dict 格式如
                {"query": 100, "insert": 50, "search": 5}。不传使用默认值 (200/200/10)。
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        if limits is None:
            limits = httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                keepalive_expiry=keepalive_expiry,
            )
        self._session = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            limits=limits,
            trust_env=False,
        )
        self._closed = False
        self._limiter = ClientConcurrencyLimiter(
            enabled=enable_queuing,
            limits=concurrency_limits,
        )

        self._information: InformationClient | None = None
        self._entities: EntityClient | None = None
        self._nodes: NodeClient | None = None
        self._analysis: AnalysisClient | None = None
        self._trading: TradingClient | None = None
        self._feedback: FeedbackClient | None = None
        self._search: SearchClient | None = None
        self._pipeline: PipelineClient | None = None
        self._validity: ValidityClient | None = None
        self._conflicts: ConflictClient | None = None
        self._ranking: RankingClient | None = None
        self._evidence: EvidenceClient | None = None
        self._queries: QueriesClient | None = None
        self._macro_report: MacroReportClient | None = None
        self._preferences: PreferenceClient | None = None

    async def __aenter__(self) -> "QuantClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭客户端，释放底层 HTTP 连接。"""
        if not self._closed:
            await self._session.aclose()
            self._closed = True

    async def health(self) -> HealthResponse:
        """健康检查，确认服务端可达及数据库连接正常。

        Returns:
            包含 status、db、version 字段的响应。
        """
        resp = await self._session.get("/health")
        resp.raise_for_status()
        return HealthResponse(**resp.json())

    def _make_client(self, cls: type[BaseClient]) -> BaseClient:
        client = cls(self._session, self._api_key)
        client._limiter = self._limiter
        return client

    @property
    def information(self) -> InformationClient:
        """资讯子模块：录入、查询、去重、合并资讯，管理资讯-实体关联。"""
        if self._information is None:
            self._information = self._make_client(InformationClient)
        return self._information

    @property
    def entities(self) -> EntityClient:
        """实体子模块：增删改查实体，管理实体间关系及影响力传导路径。"""
        if self._entities is None:
            self._entities = self._make_client(EntityClient)
        return self._entities

    @property
    def nodes(self) -> NodeClient:
        """节点子模块：管理世界节点树、节点状态、附件和压缩。"""
        if self._nodes is None:
            self._nodes = self._make_client(NodeClient)
        return self._nodes

    @property
    def analysis(self) -> AnalysisClient:
        """分析子模块：提交和查询 Agent 分析结果。"""
        if self._analysis is None:
            self._analysis = self._make_client(AnalysisClient)
        return self._analysis

    @property
    def trading(self) -> TradingClient:
        """交易子模块：记录和查询交易操作。"""
        if self._trading is None:
            self._trading = self._make_client(TradingClient)
        return self._trading

    @property
    def feedback(self) -> FeedbackClient:
        """反馈子模块：提交交易决策反馈，积累经验教训。"""
        if self._feedback is None:
            self._feedback = self._make_client(FeedbackClient)
        return self._feedback

    @property
    def search(self) -> SearchClient:
        """搜索子模块：混合搜索、多粒度搜索、相似案例检索。"""
        if self._search is None:
            self._search = self._make_client(SearchClient)
        return self._search

    @property
    def pipeline(self) -> PipelineClient:
        """管线子模块：查看和管理资讯处理队列、状态和优先级。"""
        if self._pipeline is None:
            self._pipeline = self._make_client(PipelineClient)
        return self._pipeline

    @property
    def validity(self) -> ValidityClient:
        """时效性子模块：管理实体/关系/状态的时间有效期。"""
        if self._validity is None:
            self._validity = self._make_client(ValidityClient)
        return self._validity

    @property
    def conflicts(self) -> ConflictClient:
        """冲突子模块：检测和解决知识图谱中的信息冲突。"""
        if self._conflicts is None:
            self._conflicts = self._make_client(ConflictClient)
        return self._conflicts

    @property
    def ranking(self) -> RankingClient:
        """排序子模块：计算和查询实体/节点的重要性排名。"""
        if self._ranking is None:
            self._ranking = self._make_client(RankingClient)
        return self._ranking

    @property
    def evidence(self) -> EvidenceClient:
        """证据子模块：追溯信息来源和推导链路。"""
        if self._evidence is None:
            self._evidence = self._make_client(EvidenceClient)
        return self._evidence

    @property
    def queries(self) -> QueriesClient:
        """时点查询子模块：as-of 时间点状态还原和历史对比。"""
        if self._queries is None:
            self._queries = self._make_client(QueriesClient)
        return self._queries

    @property
    def macro_report(self) -> MacroReportClient:
        """宏观报告子模块：读写宏观形势报告。"""
        if self._macro_report is None:
            self._macro_report = self._make_client(MacroReportClient)
        return self._macro_report

    @property
    def preferences(self) -> PreferenceClient:
        """偏好设置子模块：管理资产/风控/分析偏好和行业认知。"""
        if self._preferences is None:
            self._preferences = self._make_client(PreferenceClient)
        return self._preferences


__all__ = [
    "QuantClient",
    "QuantClientError",
    "QuantClientHTTPError",
    "QuantClientAuthError",
    "QuantClientNotFoundError",
    "QuantClientConnectionError",
]