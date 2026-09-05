import pytest
from uuid import uuid4


# ── POST /entities/ ──


@pytest.mark.asyncio
async def test_create_entity(async_client, entity_data):
    resp = await async_client.post("/api/v1/entities/", json=entity_data)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == entity_data["name"]
    assert data["entity_type"] == entity_data["entity_type"]
    assert data["aliases"] == entity_data["aliases"]
    assert data["normalized_name"] is not None
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_entity_minimal(async_client):
    resp = await async_client.post("/api/v1/entities/", json={
        "name": "测试实体",
        "entity_type": "concept",
    })
    assert resp.status_code == 201
    assert resp.json()["name"] == "测试实体"


@pytest.mark.asyncio
async def test_create_entity_with_node(async_client, entity_data, node_data):
    from tests.conftest import _create_node
    node = await _create_node(async_client, node_data)
    entity_data["linked_node_id"] = node["id"]
    resp = await async_client.post("/api/v1/entities/", json=entity_data)
    assert resp.status_code == 201
    assert resp.json()["linked_node_id"] == node["id"]


@pytest.mark.asyncio
async def test_create_entity_missing_required(async_client):
    resp = await async_client.post("/api/v1/entities/", json={"name": "no type"})
    assert resp.status_code == 422


# ── GET /entities/ ──


@pytest.mark.asyncio
async def test_list_entities_empty(async_client):
    resp = await async_client.get("/api/v1/entities/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_entities(async_client, entity_data, entity_data2):
    await _create_entity(async_client, entity_data)
    await _create_entity(async_client, entity_data2)
    resp = await async_client.get("/api/v1/entities/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_entities_filter_by_type(async_client, entity_data, entity_data2):
    await _create_entity(async_client, entity_data)
    await _create_entity(async_client, entity_data2)
    resp = await async_client.get("/api/v1/entities/", params={"entity_type": "company"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["entity_type"] == "company"


@pytest.mark.asyncio
async def test_list_entities_search(async_client, entity_data, entity_data2):
    await _create_entity(async_client, entity_data)
    await _create_entity(async_client, entity_data2)
    resp = await async_client.get("/api/v1/entities/", params={"search": "茅台"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_entities_pagination(async_client, entity_data):
    for i in range(5):
        d = dict(entity_data, name=f"实体{i}", aliases=[f"alias{i}"])
        await _create_entity(async_client, d)
    resp = await async_client.get("/api/v1/entities/", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) <= 2


# ── POST /entities/relationships ──


@pytest.mark.asyncio
async def test_create_relationship(async_client, entity_data, entity_data2):
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)
    resp = await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "part_of",
        "strength": 0.9,
        "description": "茅台属于白酒板块",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_entity_id"] == e1["id"]
    assert data["target_entity_id"] == e2["id"]
    assert data["relationship_type"] == "part_of"
    assert data["strength"] == 0.9


@pytest.mark.asyncio
async def test_create_relationship_minimal(async_client, entity_data, entity_data2):
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)
    resp = await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "correlated_with",
    })
    assert resp.status_code == 201


# ── GET /entities/{entity_id}/relationships ──


@pytest.mark.asyncio
async def test_get_relationships_empty(async_client, entity_data):
    e = await _create_entity(async_client, entity_data)
    resp = await async_client.get(f"/api/v1/entities/{e['id']}/relationships")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_relationships(async_client, entity_data, entity_data2):
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)
    await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "part_of",
    })
    resp = await async_client.get(f"/api/v1/entities/{e1['id']}/relationships")
    assert resp.status_code == 200
    rels = resp.json()
    assert len(rels) >= 1
    assert rels[0]["source_entity_id"] == e1["id"]


# ── GET /entities/impact-path/{entity_id} ──


@pytest.mark.asyncio
async def test_impact_path(async_client, entity_data, entity_data2):
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)
    await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "part_of",
    })
    resp = await async_client.get(f"/api/v1/entities/impact-path/{e1['id']}",
                                   params={"depth": 3, "direction": "downstream"})
    assert resp.status_code == 200
    data = resp.json()
    assert "root" in data
    assert "paths" in data


@pytest.mark.asyncio
async def test_impact_path_custom_depth(async_client, entity_data, entity_data2):
    e1 = await _create_entity(async_client, entity_data)
    e2 = await _create_entity(async_client, entity_data2)
    await async_client.post("/api/v1/entities/relationships", json={
        "source_entity_id": e1["id"],
        "target_entity_id": e2["id"],
        "relationship_type": "part_of",
    })
    resp = await async_client.get(f"/api/v1/entities/impact-path/{e1['id']}",
                                   params={"depth": 1, "direction": "upstream"})
    assert resp.status_code == 200


from tests.conftest import _create_entity
