import pytest
from uuid import uuid4
from uuid import UUID

from kbquant.database import async_session
from kbquant.services.pipeline_service import PipelineService


# ── GET /pipeline/queue ──


@pytest.mark.asyncio
async def test_list_queue_empty(async_client):
    resp = await async_client.get("/api/v1/pipeline/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_queue(async_client, sample_info_data, sample_info_data2):
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.get("/api/v1/pipeline/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_queue_filter_by_status(async_client, sample_info_data):
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/pipeline/queue", params={"status": "ingested"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["status"] == "ingested"


@pytest.mark.asyncio
async def test_list_queue_filter_by_status_list(async_client, sample_info_data):
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/pipeline/queue", params=[("status", "ingested"), ("status", "error")])
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["status"] in {"ingested", "error"}


@pytest.mark.asyncio
async def test_list_queue_filter_by_priority(async_client, sample_info_data):
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/pipeline/queue", params={"priority_min": 0})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_queue_pagination(async_client, sample_info_data):
    from tests.conftest import _create_info
    for _ in range(3):
        await _create_info(async_client, sample_info_data)
    resp = await async_client.get("/api/v1/pipeline/queue", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) <= 1


# ── PUT /pipeline/{raw_info_id}/status ──


@pytest.mark.asyncio
async def test_update_pipeline_status(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.put(f"/api/v1/pipeline/{info['id']}/status", json={
        "status": "deduped",
        "detail": "去重完成",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deduped"


@pytest.mark.asyncio
async def test_update_pipeline_status_with_priority(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.put(f"/api/v1/pipeline/{info['id']}/status", json={
        "status": "error",
        "detail": "处理失败",
        "priority": 5,
    })
    assert resp.status_code == 200


# ── GET /pipeline/stats ──


@pytest.mark.asyncio
async def test_pipeline_stats_empty(async_client):
    resp = await async_client.get("/api/v1/pipeline/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_status" in data
    assert "total_pending" in data


@pytest.mark.asyncio
async def test_pipeline_stats_with_data(async_client, sample_info_data, sample_info_data2):
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.get("/api/v1/pipeline/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_status" in data
    assert isinstance(data["by_status"], dict)


# ── POST /pipeline/reprioritize ──


@pytest.mark.asyncio
async def test_reprioritize(async_client, sample_info_data, sample_info_data2):
    from tests.conftest import _create_info
    info1 = await _create_info(async_client, sample_info_data)
    info2 = await _create_info(async_client, sample_info_data2)

    # Get queue item IDs
    queue_resp = await async_client.get("/api/v1/pipeline/queue")
    items = queue_resp.json()["items"]
    item_ids = [item["id"] for item in items]

    resp = await async_client.post("/api/v1/pipeline/reprioritize", json={
        "item_ids": item_ids,
        "new_priority": 10,
    })
    assert resp.status_code == 200
    assert "updated" in resp.json()


@pytest.mark.asyncio
async def test_reprioritize_empty(async_client):
    resp = await async_client.post("/api/v1/pipeline/reprioritize", json={
        "item_ids": [],
        "new_priority": 5,
    })
    assert resp.status_code == 200
    assert resp.json()["updated"] == 0


@pytest.mark.asyncio
async def test_update_preprocess_status_existing_entry(async_client, sample_info_data):
    from tests.conftest import _create_info

    info = await _create_info(async_client, sample_info_data)
    info_id = UUID(info["id"])

    async with async_session() as session:
        svc = PipelineService(session)
        entry = await svc.get_or_create_queue_entry(info_id)
        await session.commit()
        assert entry.preprocess_status == "ingested"

    async with async_session() as session:
        svc = PipelineService(session)
        updated = await svc.update_preprocess_status(info_id, "processing")
        await session.commit()
        assert updated.preprocess_status == "processing"
