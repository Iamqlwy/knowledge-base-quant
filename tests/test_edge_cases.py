import pytest
from uuid import uuid4
from datetime import datetime, timezone


# ── Health check ──


@pytest.mark.asyncio
async def test_health_check(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


# ── 404 for unknown endpoints ──


@pytest.mark.asyncio
async def test_unknown_endpoint(async_client):
    resp = await async_client.get("/api/v1/nonexistent")
    assert resp.status_code == 404


# ── Validation errors (422) ──


@pytest.mark.asyncio
async def test_invalid_uuid_in_path(async_client):
    resp = await async_client.get("/api/v1/nodes/not-a-uuid")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_json_body(async_client):
    resp = await async_client.post("/api/v1/information/", content="not json", headers={"Content-Type": "application/json"})
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_pagination_page_less_than_one(async_client):
    resp = await async_client.get("/api/v1/information/", params={"page": 0})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pagination_page_size_exceeds_max(async_client):
    resp = await async_client.get("/api/v1/information/", params={"page_size": 200})
    assert resp.status_code == 422


# ── Optional field behavior ──


@pytest.mark.asyncio
async def test_create_info_with_all_fields(async_client):
    resp = await async_client.post("/api/v1/information/", json={
        "title": "完整字段测试",
        "body": "测试正文内容测试正文内容测试正文内容",
        "source": "测试来源",
        "source_url": "https://example.com/test",
        "published_at": datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat(),
        "info_type": "report",
        "language": "en",
        "raw_metadata": {"author": "test author", "tags": ["tag1", "tag2"]},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["language"] == "en"
    assert data["source_url"] == "https://example.com/test"
    assert data["raw_metadata"] == {"author": "test author", "tags": ["tag1", "tag2"]}


@pytest.mark.asyncio
async def test_create_analysis_with_all_fields(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/analysis/", json={
        "title": "完整分析测试",
        "content": "详细的分析内容，包含多角度的市场分析...",
        "analysis_type": "impact_analysis",
        "agent_id": "analyst_v2",
        "confidence": 0.92,
        "root_raw_info_ids": [info["id"]],
        "time_horizon": "long_term",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["time_horizon"] == "long_term"
    assert data["root_raw_info_ids"] == [info["id"]]


# ── Multiple CRUD operations in sequence ──


@pytest.mark.asyncio
async def test_info_lifecycle(async_client, sample_info_data):
    """Full lifecycle: create -> get -> list -> verify presence."""
    # Create
    resp = await async_client.post("/api/v1/information/", json=sample_info_data)
    assert resp.status_code == 201
    info = resp.json()

    # Get by ID
    get_resp = await async_client.get(f"/api/v1/information/{info['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == info["id"]

    # List and verify
    list_resp = await async_client.get("/api/v1/information/")
    assert list_resp.status_code == 200
    ids = [item["id"] for item in list_resp.json()["items"]]
    assert info["id"] in ids


@pytest.mark.asyncio
async def test_node_lifecycle(async_client, node_data):
    """Full lifecycle: create -> get -> update state -> get state -> compress."""
    # Create
    node = await _create_node(async_client, node_data)

    # Get
    get_resp = await async_client.get(f"/api/v1/nodes/{node['id']}")
    assert get_resp.status_code == 200

    # Update state
    state_resp = await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "核心逻辑",
        "state_summary": "摘要",
    })
    assert state_resp.status_code == 201

    # Get current state
    curr_resp = await async_client.get(f"/api/v1/nodes/{node['id']}/state/current")
    assert curr_resp.status_code == 200

    # Get state history
    hist_resp = await async_client.get(f"/api/v1/nodes/{node['id']}/state/history")
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()) >= 1


@pytest.mark.asyncio
async def test_entity_relationship_lifecycle(async_client, entity_data, entity_data2):
    """Entity lifecycle: create entities -> create relationship -> get relationships."""
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)

    # Create relationship
    rel_resp = await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "impacts",
        "strength": 0.75,
    })
    assert rel_resp.status_code == 201

    # Get relationships from source
    rels = await async_client.get(f"/api/v1/entities/{e1['id']}/relationships")
    assert rels.status_code == 200
    assert len(rels.json()) >= 1

    # Get relationships from target
    rels2 = await async_client.get(f"/api/v1/entities/{e2['id']}/relationships")
    assert rels2.status_code == 200


# ── Helpers ──
from tests.conftest import _create_node, _create_entity
