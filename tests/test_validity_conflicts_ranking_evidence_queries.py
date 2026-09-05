import pytest
from datetime import datetime, timezone
from uuid import uuid4


# ── Validity endpoints ──


@pytest.mark.asyncio
async def test_create_validity(async_client):
    resp = await async_client.post("/api/v1/validity/", json={
        "target_type": "driver",
        "target_id": "driver-001",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
        "valid_until": datetime(2026, 12, 31, tzinfo=timezone.utc).isoformat(),
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["target_type"] == "driver"
    assert data["target_id"] == "driver-001"
    assert data["extended_count"] == 0
    assert "id" in data


@pytest.mark.asyncio
async def test_create_validity_no_expiry(async_client):
    resp = await async_client.post("/api/v1/validity/", json={
        "target_type": "risk",
        "target_id": "risk-001",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
    })
    assert resp.status_code == 201
    assert resp.json()["valid_until"] is None


@pytest.mark.asyncio
async def test_list_validity_empty(async_client):
    resp = await async_client.get("/api/v1/validity/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_validity(async_client):
    await async_client.post("/api/v1/validity/", json={
        "target_type": "driver",
        "target_id": "d1",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
    })
    await async_client.post("/api/v1/validity/", json={
        "target_type": "risk",
        "target_id": "r1",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
    })
    resp = await async_client.get("/api/v1/validity/")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_list_validity_filter_by_type(async_client):
    await async_client.post("/api/v1/validity/", json={
        "target_type": "driver", "target_id": "d1",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
    })
    await async_client.post("/api/v1/validity/", json={
        "target_type": "risk", "target_id": "r1",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
    })
    resp = await async_client.get("/api/v1/validity/", params={"target_type": "driver"})
    assert resp.status_code == 200
    for item in resp.json():
        assert item["target_type"] == "driver"


@pytest.mark.asyncio
async def test_expire_validity(async_client):
    v = await async_client.post("/api/v1/validity/", json={
        "target_type": "driver", "target_id": "d1",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
    })
    vid = v.json()["id"]
    resp = await async_client.put(f"/api/v1/validity/{vid}/expire", json={
        "invalidation_reason": "数据已过时",
    })
    assert resp.status_code == 200
    assert resp.json()["invalidation_reason"] == "数据已过时"


@pytest.mark.asyncio
async def test_expire_validity_not_found(async_client):
    resp = await async_client.put(f"/api/v1/validity/{uuid4()}/expire", json={
        "invalidation_reason": "test",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_extend_validity(async_client):
    v = await async_client.post("/api/v1/validity/", json={
        "target_type": "driver", "target_id": "d1",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
        "valid_until": datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat(),
    })
    vid = v.json()["id"]
    new_date = datetime(2026, 12, 31, tzinfo=timezone.utc).isoformat()
    resp = await async_client.put(f"/api/v1/validity/{vid}/extend", json={
        "new_valid_until": new_date,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["extended_count"] >= 1


@pytest.mark.asyncio
async def test_extend_validity_not_found(async_client):
    resp = await async_client.put(f"/api/v1/validity/{uuid4()}/extend", json={
        "new_valid_until": datetime(2026, 12, 31, tzinfo=timezone.utc).isoformat(),
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_check_validity(async_client):
    await async_client.post("/api/v1/validity/", json={
        "target_type": "driver", "target_id": "d-valid",
        "valid_from": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
        "valid_until": datetime(2099, 12, 31, tzinfo=timezone.utc).isoformat(),
    })
    resp = await async_client.get("/api/v1/validity/check", params={
        "target_type": "driver",
        "target_id": "d-valid",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "is_valid" in data


@pytest.mark.asyncio
async def test_check_validity_not_found(async_client):
    resp = await async_client.get("/api/v1/validity/check", params={
        "target_type": "driver",
        "target_id": "nonexistent",
    })
    assert resp.status_code == 200
    assert resp.json()["is_valid"] is False


# ── Conflicts endpoints ──


@pytest.mark.asyncio
async def test_detect_conflict(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    resp = await async_client.post("/api/v1/conflicts/detect", json={
        "node_id": node["id"],
        "existing_claim": "白酒板块未来3个月将上涨10%",
        "conflicting_claim": "白酒板块存在下行风险，预计下跌5%",
        "conflict_type": "contradiction",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_conflict"] is True
    assert "conflict_id" in data


@pytest.mark.asyncio
async def test_detect_conflict_with_evidence(async_client, node_data, sample_info_data):
    from tests.conftest import _create_node, _create_info
    node = await _create_node(async_client, node_data)
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/conflicts/detect", json={
        "node_id": node["id"],
        "existing_claim": "A观点",
        "conflicting_claim": "B观点",
        "existing_evidence_id": str(info["id"]),
        "conflicting_evidence_id": str(info["id"]),
    })
    assert resp.status_code == 200
    assert resp.json()["has_conflict"] is True


@pytest.mark.asyncio
async def test_list_conflicts_empty(async_client):
    resp = await async_client.get("/api/v1/conflicts/")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_conflicts(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    await async_client.post("/api/v1/conflicts/detect", json={
        "node_id": node["id"],
        "existing_claim": "上涨10%",
        "conflicting_claim": "下跌5%",
    })
    resp = await async_client.get("/api/v1/conflicts/")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_list_conflicts_filter_by_node(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    await async_client.post("/api/v1/conflicts/detect", json={
        "node_id": node["id"],
        "existing_claim": "上涨",
        "conflicting_claim": "下跌",
    })
    resp = await async_client.get("/api/v1/conflicts/", params={"node_id": str(node["id"])})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["node_id"] == node["id"]


@pytest.mark.asyncio
async def test_resolve_conflict(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    conflict = await async_client.post("/api/v1/conflicts/detect", json={
        "node_id": node["id"],
        "existing_claim": "上涨",
        "conflicting_claim": "下跌",
    })
    cid = conflict.json()["conflict_id"]
    resp = await async_client.put(f"/api/v1/conflicts/{cid}/resolve", json={
        "resolution": "以A观点为准，B观点证据不足",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["resolution"] is not None
    assert data["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resolve_conflict_not_found(async_client):
    resp = await async_client.put(f"/api/v1/conflicts/{uuid4()}/resolve", json={
        "resolution": "test",
    })
    assert resp.status_code == 404


# ── Ranking endpoints ──


@pytest.mark.asyncio
async def test_compute_ranking(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/ranking/compute", json={
        "target_type": "raw_info",
        "target_id": info["id"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "score" in data
    assert 0.0 <= data["score"] <= 1.0


@pytest.mark.asyncio
async def test_compute_ranking_force(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/ranking/compute", json={
        "target_type": "raw_info",
        "target_id": info["id"],
        "force_recompute": True,
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_rankings_empty(async_client):
    resp = await async_client.get("/api/v1/ranking/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_rankings(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    await async_client.post("/api/v1/ranking/compute", json={
        "target_type": "raw_info",
        "target_id": info["id"],
    })
    resp = await async_client.get("/api/v1/ranking/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_rankings_filter_by_type(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    await async_client.post("/api/v1/ranking/compute", json={
        "target_type": "raw_info",
        "target_id": info["id"],
    })
    resp = await async_client.get("/api/v1/ranking/", params={"target_type": "raw_info"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_rankings_filter_by_score(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    await async_client.post("/api/v1/ranking/compute", json={
        "target_type": "raw_info",
        "target_id": info["id"],
    })
    resp = await async_client.get("/api/v1/ranking/", params={"min_score": 0.0})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_ranking_history(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    await async_client.post("/api/v1/ranking/compute", json={
        "target_type": "raw_info",
        "target_id": info["id"],
    })
    resp = await async_client.get(f"/api/v1/ranking/history/raw_info/{info['id']}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_ranking_history_empty(async_client):
    resp = await async_client.get(f"/api/v1/ranking/history/raw_info/{uuid4()}")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Evidence endpoints ──


@pytest.mark.asyncio
async def test_trace_evidence(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.get(f"/api/v1/evidence/trace/raw_info/{info['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert "root" in data
    assert "evidence_chain" in data


@pytest.mark.asyncio
async def test_trace_evidence_custom_depth(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    resp = await async_client.get(f"/api/v1/evidence/trace/world_node/{node['id']}",
                                   params={"depth": 5})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trace_evidence_not_found(async_client):
    resp = await async_client.get(f"/api/v1/evidence/trace/world_node/{uuid4()}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trace_node_evidence(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    resp = await async_client.get(f"/api/v1/evidence/trace-node/{node['id']}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trace_node_evidence_with_aspect(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    resp = await async_client.get(f"/api/v1/evidence/trace-node/{node['id']}",
                                   params={"aspect": "valuation"})
    assert resp.status_code == 200


# ── Queries endpoints ──


@pytest.mark.asyncio
async def test_query_as_of(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.get(f"/api/v1/queries/as-of/{datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()}",
                                   params={"info_id": info["id"]})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_query_as_of_no_filters(async_client):
    resp = await async_client.get(f"/api/v1/queries/as-of/{datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_diff_state(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    t1 = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    resp = await async_client.post("/api/v1/queries/as-of-diff", params={
        "node_id": str(node["id"]),
        "timestamp_a": t1,
        "timestamp_b": t2,
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_state_at(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    resp = await async_client.get(
        f"/api/v1/queries/nodes/{node['id']}/state/at/{ts}"
    )
    assert resp.status_code in (200, 404)


@pytest.mark.asyncio
async def test_get_state_at_with_existing_state(async_client, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "测试逻辑",
    })
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    resp = await async_client.get(
        f"/api/v1/queries/nodes/{node['id']}/state/at/{ts}"
    )
    assert resp.status_code == 200
