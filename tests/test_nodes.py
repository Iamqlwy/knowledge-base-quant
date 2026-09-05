import pytest
from uuid import uuid4
from kbquant.database import async_session
from kbquant.services.node_service import NodeService


# ── POST /nodes/ ──


@pytest.mark.asyncio
async def test_create_node(async_client, node_data):
    resp = await async_client.post("/api/v1/nodes/", json=node_data)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == node_data["name"]
    assert data["node_type"] == node_data["node_type"]
    assert data["ticker"] == node_data["ticker"]
    assert data["aliases"] == node_data["aliases"]
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_node_minimal(async_client):
    resp = await async_client.post("/api/v1/nodes/", json={
        "name": "测试节点",
        "node_type": "concept",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "测试节点"
    assert data["node_type"] == "concept"
    assert data["description"] is None
    assert data["ticker"] is None


@pytest.mark.asyncio
async def test_create_node_with_parent(async_client, node_data, node_data2):
    parent = await _create_node(async_client, node_data2)
    node_data["parent_node_id"] = parent["id"]
    resp = await async_client.post("/api/v1/nodes/", json=node_data)
    assert resp.status_code == 201
    assert resp.json()["parent_node_id"] == parent["id"]


@pytest.mark.asyncio
async def test_create_node_missing_name(async_client):
    resp = await async_client.post("/api/v1/nodes/", json={"node_type": "concept"})
    assert resp.status_code == 422


# ── GET /nodes/ ──


@pytest.mark.asyncio
async def test_list_nodes_empty(async_client):
    resp = await async_client.get("/api/v1/nodes/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_nodes(async_client, node_data, node_data2):
    await _create_node(async_client, node_data)
    await _create_node(async_client, node_data2)
    resp = await async_client.get("/api/v1/nodes/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_nodes_filter_by_type(async_client, node_data, node_data2):
    await _create_node(async_client, node_data)
    await _create_node(async_client, node_data2)
    resp = await async_client.get("/api/v1/nodes/", params={"node_type": "company"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["node_type"] == "company"


@pytest.mark.asyncio
async def test_list_nodes_pagination(async_client, node_data):
    for _ in range(5):
        # Each needs unique name
        n = dict(node_data, name=f"节点{_}")
        await _create_node(async_client, n)
    resp = await async_client.get("/api/v1/nodes/", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2


@pytest.mark.asyncio
async def test_list_node_names_and_aliases(async_client, node_data, node_data2):
    await _create_node(async_client, node_data)
    await _create_node(async_client, node_data2)

    async with async_session() as session:
        svc = NodeService(session)
        rows = await svc.list_node_names_and_aliases()
        names = {r["name"] for r in rows}
        assert node_data["name"] in names
        assert node_data2["name"] in names

    resp = await async_client.get("/api/v1/nodes/names-aliases")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    names_api = {r["name"] for r in items}
    assert node_data["name"] in names_api
    assert node_data2["name"] in names_api


# ── GET /nodes/{node_id} ──


@pytest.mark.asyncio
async def test_get_node(async_client, node_data):
    node = await _create_node(async_client, node_data)
    resp = await async_client.get(f"/api/v1/nodes/{node['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == node["id"]


@pytest.mark.asyncio
async def test_get_node_not_found(async_client):
    resp = await async_client.get(f"/api/v1/nodes/{uuid4()}")
    assert resp.status_code == 404


# ── POST /nodes/{node_id}/attachments ──


@pytest.mark.asyncio
async def test_attach_to_node_raw_info(async_client, node_data, sample_info_data):
    from tests.conftest import _create_info
    node = await _create_node(async_client, node_data)
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.post(f"/api/v1/nodes/{node['id']}/attachments", json={
        "attachment_type": "raw_info",
        "attachment_id": info["id"],
        "role": "primary",
        "relevance_score": 0.9,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["node_id"] == node["id"]
    assert data["attachment_type"] == "raw_info"
    assert data["attachment_id"] == info["id"]
    assert data["role"] == "primary"


@pytest.mark.asyncio
async def test_attach_to_node_analysis(async_client, node_data, sample_info_data, analysis_data):
    from tests.conftest import _create_info
    node = await _create_node(async_client, node_data)
    info = await _create_info(async_client, sample_info_data)
    analysis_data["root_raw_info_ids"] = [info["id"]]
    anal = await async_client.post("/api/v1/analysis/", json=analysis_data)
    assert anal.status_code == 201
    anal_json = anal.json()

    resp = await async_client.post(f"/api/v1/nodes/{node['id']}/attachments", json={
        "attachment_type": "analysis",
        "attachment_id": anal_json["id"],
        "role": "secondary",
    })
    assert resp.status_code == 201
    assert resp.json()["attachment_type"] == "analysis"


# ── GET /nodes/{node_id}/attachments ──


@pytest.mark.asyncio
async def test_get_attachments_empty(async_client, node_data):
    node = await _create_node(async_client, node_data)
    resp = await async_client.get(f"/api/v1/nodes/{node['id']}/attachments")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_attachments_with_filters(async_client, node_data, sample_info_data):
    from tests.conftest import _create_info
    node = await _create_node(async_client, node_data)
    info = await _create_info(async_client, sample_info_data)
    await async_client.post(f"/api/v1/nodes/{node['id']}/attachments", json={
        "attachment_type": "raw_info",
        "attachment_id": info["id"],
        "role": "primary",
    })
    resp = await async_client.get(f"/api/v1/nodes/{node['id']}/attachments",
                                   params={"role": "primary", "attachment_type": "raw_info"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert items[0]["role"] == "primary"


# ── GET /nodes/{node_id}/state/current ──


@pytest.mark.asyncio
async def test_get_current_state_not_found(async_client, node_data):
    node = await _create_node(async_client, node_data)
    resp = await async_client.get(f"/api/v1/nodes/{node['id']}/state/current")
    assert resp.status_code == 404


# ── POST /nodes/{node_id}/state ──


@pytest.mark.asyncio
async def test_update_state(async_client, node_data):
    node = await _create_node(async_client, node_data)
    resp = await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "降准利好白酒消费板块",
        "state_summary": "当前处于上升周期",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["node_id"] == node["id"]
    assert data["core_logic"] == "降准利好白酒消费板块"
    assert data["version"] == 1
    assert data["effective_from"] is not None


@pytest.mark.asyncio
async def test_update_state_full(async_client, node_data):
    node = await _create_node(async_client, node_data)
    resp = await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "M2扩张带动消费板块估值提升",
        "primary_drivers": [
            {"driver": "流动性宽松", "strength": 0.8, "evidence_ids": []},
            {"driver": "消费复苏", "strength": 0.6, "evidence_ids": []},
        ],
        "risks": [
            {"risk": "政策转向", "severity": 0.4},
        ],
        "focus_points": [
            {"point": "关注社融数据", "priority": "high"},
        ],
        "recent_changes": "最新社融数据超预期",
        "uncertainty_flags": ["海外加息风险"],
        "state_summary": "利多因素占主导",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["core_logic"] is not None
    assert len(data.get("primary_drivers") or []) == 2
    assert len(data.get("risks") or []) == 1
    assert len(data.get("focus_points") or []) == 1


@pytest.mark.asyncio
async def test_update_state_version_increment(async_client, node_data):
    node = await _create_node(async_client, node_data)
    s1 = await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "版本1", "state_summary": "初始状态",
    })
    assert s1.json()["version"] == 1

    s2 = await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "版本2", "state_summary": "更新状态",
    })
    assert s2.json()["version"] == 2


# ── GET /nodes/{node_id}/state/current (after state creation) ──


@pytest.mark.asyncio
async def test_get_current_state_after_update(async_client, node_data):
    node = await _create_node(async_client, node_data)
    await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "测试逻辑", "state_summary": "测试摘要",
    })
    resp = await async_client.get(f"/api/v1/nodes/{node['id']}/state/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == node["id"]
    assert data["core_logic"] == "测试逻辑"


# ── GET /nodes/{node_id}/state/history ──


@pytest.mark.asyncio
async def test_get_state_history_empty(async_client, node_data):
    node = await _create_node(async_client, node_data)
    resp = await async_client.get(f"/api/v1/nodes/{node['id']}/state/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_state_history(async_client, node_data):
    node = await _create_node(async_client, node_data)
    await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "版本1", "state_summary": "摘要1",
    })
    await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "版本2", "state_summary": "摘要2",
    })
    resp = await async_client.get(f"/api/v1/nodes/{node['id']}/state/history")
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) >= 2


# ── POST /nodes/{node_id}/compress ──


@pytest.mark.asyncio
async def test_compress_node(async_client, node_data, sample_info_data):
    from tests.conftest import _create_info
    node = await _create_node(async_client, node_data)
    info = await _create_info(async_client, sample_info_data)
    await async_client.post(f"/api/v1/nodes/{node['id']}/attachments", json={
        "attachment_type": "raw_info",
        "attachment_id": info["id"],
        "role": "primary",
    })
    resp = await async_client.post(f"/api/v1/nodes/{node['id']}/compress", json={
        "force": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == node["id"]
    assert "before_evidence_count" in data
    assert "after_evidence_count" in data
    assert "summary" in data


@pytest.mark.asyncio
async def test_compress_node_not_found(async_client):
    resp = await async_client.post(f"/api/v1/nodes/{uuid4()}/compress", json={"force": True})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_compress_node_default_params(async_client, node_data):
    """Compress without request body should use defaults."""
    node = await _create_node(async_client, node_data)
    resp = await async_client.post(f"/api/v1/nodes/{node['id']}/compress")
    assert resp.status_code == 200


from tests.conftest import _create_node
