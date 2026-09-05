from __future__ import annotations

from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.schemas.evidence import EvidenceTraceResponse
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas.evidence import EvidenceTraceResponse


class EvidenceClient(BaseClient):
    """证据追溯客户端，追踪知识图谱中信息的原始来源和推导链路。"""

    @concurrency_limit("query")
    async def trace(
        self, target_type: str, target_id: str | UUID, *, depth: int = 3,
    ) -> EvidenceTraceResponse:
        """追溯目标节点/实体/关系的证据链。

        Args:
            target_type: 目标类型（node/entity/relationship）。
            target_id: 目标 ID。
            depth: 追溯深度，默认 3 层。

        Returns:
            证据追溯结果，包含从原始资讯到目标的完整链路。
        """
        resp = await self._request(
            "GET", f"/evidence/trace/{target_type}/{target_id}",
            params={"depth": depth},
        )
        return EvidenceTraceResponse(**resp)

    @concurrency_limit("query")
    async def trace_node(self, node_id: str | UUID, *, aspect: str | None = None) -> dict:
        """追溯节点的证据链，可按方面筛选。

        Args:
            node_id: 节点 ID。
            aspect: 要追溯的方面（如 price/risk/sentiment 等）。

        Returns:
            节点证据追溯结果。
        """
        params: dict = {}
        if aspect:
            params["aspect"] = aspect
        return await self._request("GET", f"/evidence/trace-node/{node_id}", params=params)
