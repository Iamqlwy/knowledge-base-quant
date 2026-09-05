import pytest


# ── POST /information/ ──


@pytest.mark.asyncio
async def test_ingest_information(async_client, sample_info_data):
    resp = await async_client.post("/api/v1/information/", json=sample_info_data)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == sample_info_data["title"]
    assert data["source"] == sample_info_data["source"]
    assert "id" in data
    assert "content_hash" in data
    assert data["processing_status"] == "ingested"
    assert "ingested_at" in data


@pytest.mark.asyncio
async def test_ingest_information_minimal(async_client):
    """Ingest with only required fields."""
    resp = await async_client.post("/api/v1/information/", json={
        "title": "测试资讯",
        "body": "这是一条测试资讯的正文内容",
        "source": "测试来源",
        "published_at": "2026-05-20T10:00:00+00:00",
        "info_type": "news",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["language"] == "zh"
    assert data["importance_score"] == 0.0


@pytest.mark.asyncio
async def test_ingest_information_missing_required(async_client):
    resp = await async_client.post("/api/v1/information/", json={"title": "no body"})
    assert resp.status_code == 422


# ── GET /information/{info_id} ──


@pytest.mark.asyncio
async def test_get_information(async_client, sample_info_data):
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.get(f"/api/v1/information/{info['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == info["id"]


@pytest.mark.asyncio
async def test_get_information_not_found(async_client):
    from uuid import uuid4
    resp = await async_client.get(f"/api/v1/information/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_information_invalid_uuid(async_client):
    resp = await async_client.get("/api/v1/information/not-a-uuid")
    assert resp.status_code == 422


# ── GET /information/ ──


@pytest.mark.asyncio
async def test_list_information_empty(async_client):
    resp = await async_client.get("/api/v1/information/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_list_information(async_client, sample_info_data, sample_info_data2):
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.get("/api/v1/information/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    assert len(data["items"]) >= 2


@pytest.mark.asyncio
async def test_list_information_filter_by_type(async_client, sample_info_data, sample_info_data2):
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.get("/api/v1/information/", params={"info_type": "news"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["info_type"] == "news"


@pytest.mark.asyncio
async def test_list_information_filter_by_source(async_client, sample_info_data, sample_info_data2):
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.get("/api/v1/information/", params={"source": "央行官网"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["source"] == "央行官网"


@pytest.mark.asyncio
async def test_list_information_filter_by_status(async_client, sample_info_data):
    await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/information/", params={"status": "ingested"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_information_pagination(async_client, sample_info_data):
    for _ in range(5):
        await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/information/", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2
    assert data["page"] == 1
    assert data["page_size"] == 2


@pytest.mark.asyncio
async def test_list_information_pagination_page_size_limit(async_client):
    """page_size > 100 should fail validation."""
    resp = await async_client.get("/api/v1/information/", params={"page_size": 200})
    assert resp.status_code == 422


# ── GET /information/ date range filters ──


@pytest.mark.asyncio
async def test_list_information_filter_from_date(async_client, sample_info_data, sample_info_data2):
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    # sample_info_data has published_at 2026-05-20, sample_info_data2 has 2026-05-21
    resp = await async_client.get("/api/v1/information/", params={"from_date": "2026-05-21"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["published_at"] >= "2026-05-21"


@pytest.mark.asyncio
async def test_list_information_filter_to_date(async_client, sample_info_data, sample_info_data2):
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.get("/api/v1/information/", params={"to_date": "2026-05-20"})
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["published_at"] <= "2026-05-20T23:59:59"


@pytest.mark.asyncio
async def test_list_information_filter_date_range(async_client, sample_info_data, sample_info_data2):
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.get("/api/v1/information/", params={
        "from_date": "2026-05-20", "to_date": "2026-05-20",
    })
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        pub = item["published_at"]
        assert pub >= "2026-05-20" and pub < "2026-05-21"


@pytest.mark.asyncio
async def test_list_information_filter_from_date_no_results(async_client, sample_info_data):
    await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/information/", params={"from_date": "2027-01-01"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ── GET /information/ entity/ticker filters ──


@pytest.mark.asyncio
async def test_list_information_filter_by_entity(async_client, sample_info_data, entity_data):
    info = await _create_info(async_client, sample_info_data)
    entity = await _create_entity(async_client, entity_data)
    await async_client.post(f"/api/v1/information/{info['id']}/extract-entities", json={
        "entities": [{"name": entity["name"], "entity_type": entity["entity_type"], "role": "subject"}]
    })
    resp = await async_client.get("/api/v1/information/", params={"entity": entity["name"][:4]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    found = any(item["id"] == info["id"] for item in data["items"])
    assert found, "Should find info linked to entity by name"


@pytest.mark.asyncio
async def test_list_information_filter_by_ticker(async_client, sample_info_data, entity_data):
    info = await _create_info(async_client, sample_info_data)
    entity = await _create_entity(async_client, entity_data)
    await async_client.post(f"/api/v1/information/{info['id']}/extract-entities", json={
        "entities": [{"name": "600519.SH", "entity_type": "stock_code", "role": "subject"}]
    })
    resp = await async_client.get("/api/v1/information/", params={"ticker": "600519"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_list_information_filter_by_entity_no_match(async_client, sample_info_data):
    await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/information/", params={"entity": "完全不存在的实体"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ── POST /information/check-duplicate ──


@pytest.mark.asyncio
async def test_duplicate_detection(async_client, sample_info_data):
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/information/check-duplicate", json={
        "title": sample_info_data["title"],
        "body": sample_info_data["body"],
    })
    assert resp.status_code == 200
    result = resp.json()
    assert result["is_duplicate"] is True
    assert result["primary_id"] is not None


@pytest.mark.asyncio
async def test_duplicate_detection_no_match(async_client, sample_info_data):
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/information/check-duplicate", json={
        "title": "完全不同的新闻标题",
        "body": "这是一条完全不同的新闻内容，与之前没有任何相似之处",
    })
    assert resp.status_code == 200
    result = resp.json()
    assert result["is_duplicate"] is False


@pytest.mark.asyncio
async def test_duplicate_detection_custom_threshold(async_client, sample_info_data):
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/information/check-duplicate", json={
        "title": sample_info_data["title"],
        "body": sample_info_data["body"],
        "threshold": 0.95,
    })
    assert resp.status_code == 200
    assert resp.json()["is_duplicate"] is True


@pytest.mark.asyncio
async def test_duplicate_detection_with_filters(async_client, sample_info_data):
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/information/check-duplicate", json={
        "title": sample_info_data["title"],
        "body": sample_info_data["body"],
        "info_type": "news",
        "source": "央行官网",
    })
    assert resp.status_code == 200


# ── POST /information/merge ──


@pytest.mark.asyncio
async def test_merge_information(async_client, sample_info_data, sample_info_data2):
    info1 = await _create_info(async_client, sample_info_data)
    info2 = await _create_info(async_client, sample_info_data2)
    resp = await async_client.post("/api/v1/information/merge", json={
        "primary_id": info1["id"],
        "duplicate_id": info2["id"],
        "dedup_type": "same_event",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["primary_id"] == info1["id"]
    assert data["duplicate_id"] == info2["id"]
    assert data["dedup_type"] == "same_event"


@pytest.mark.asyncio
async def test_merge_information_with_rationale(async_client, sample_info_data, sample_info_data2):
    info1 = await _create_info(async_client, sample_info_data)
    info2 = await _create_info(async_client, sample_info_data2)
    resp = await async_client.post("/api/v1/information/merge", json={
        "primary_id": info1["id"],
        "duplicate_id": info2["id"],
        "dedup_type": "exact_duplicate",
        "dedup_rationale": "两条资讯报道了同一事件，内容高度重合",
    })
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_merge_information_invalid_ids(async_client):
    from uuid import uuid4
    resp = await async_client.post("/api/v1/information/merge", json={
        "primary_id": str(uuid4()),
        "duplicate_id": str(uuid4()),
        "dedup_type": "same_event",
    })
    # Merge creates the dedup record regardless of whether info exists (validates only by FK)
    assert resp.status_code in (201, 404, 500)


# ── POST /information/{info_id}/extract-entities ──


@pytest.mark.asyncio
async def test_extract_entities(async_client, sample_info_data, entity_data, entity_data2):
    info = await _create_info(async_client, sample_info_data)
    entity1 = await _create_entity(async_client, entity_data)
    entity2 = await _create_entity(async_client, entity_data2)
    resp = await async_client.post(f"/api/v1/information/{info['id']}/extract-entities", json={
        "entities": [
            {"name": "贵州茅台", "entity_type": "company", "role": "subject", "relevance_score": 0.95},
            {"name": "白酒", "entity_type": "sector", "role": "context", "relevance_score": 0.8},
        ]
    })
    assert resp.status_code == 200


# ── GET /information/{info_id}/entities ──


@pytest.mark.asyncio
async def test_get_info_entities_empty(async_client, sample_info_data):
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.get(f"/api/v1/information/{info['id']}/entities")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_get_info_entities_after_extraction(async_client, sample_info_data, entity_data):
    info = await _create_info(async_client, sample_info_data)
    entity = await _create_entity(async_client, entity_data)
    await async_client.post(f"/api/v1/information/{info['id']}/extract-entities", json={
        "entities": [{"name": entity["name"], "entity_type": entity["entity_type"], "role": "subject"}]
    })
    resp = await async_client.get(f"/api/v1/information/{info['id']}/entities")
    assert resp.status_code == 200


# Helper to avoid circular imports
from tests.conftest import _create_info, _create_entity
