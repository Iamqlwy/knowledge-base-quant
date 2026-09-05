import pytest

from tests.conftest import _create_entity


@pytest.mark.asyncio
async def test_impact_path_preserves_direction_and_strength_order(async_client, entity_data, entity_data2):
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)
    e3 = await _create_entity(async_client, {
        "name": "消费复苏链条",
        "entity_type": "concept",
        "aliases": ["消费链"],
    })

    rel1 = await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "part_of",
        "strength": 0.9,
    })
    rel2 = await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e2["id"],
        "target_entity_id": e3["id"],
        "relationship_type": "impacts",
        "strength": 0.6,
    })
    assert rel1.status_code == 201
    assert rel2.status_code == 201

    resp = await async_client.get(
        f"/api/v1/entities/impact-path/{e1['id']}",
        params={"depth": 3, "direction": "downstream"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["root"]["id"] == e1["id"]
    assert len(data["paths"]) >= 2

    first_path = data["paths"][0]
    second_path = data["paths"][1]
    assert first_path["path"][-1]["entity_id"] == e2["id"]
    assert second_path["path"][-1]["entity_id"] == e3["id"]
    assert first_path["total_impact_strength"] >= second_path["total_impact_strength"]


@pytest.mark.asyncio
async def test_impact_path_upstream_works_after_batch_refactor(async_client, entity_data, entity_data2):
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)

    rel = await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "correlated_with",
        "strength": 0.5,
    })
    assert rel.status_code == 201

    resp = await async_client.get(
        f"/api/v1/entities/impact-path/{e2['id']}",
        params={"depth": 1, "direction": "upstream"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["root"]["id"] == e2["id"]
    assert len(data["paths"]) >= 1
    assert data["paths"][0]["path"][-1]["entity_id"] == e1["id"]
