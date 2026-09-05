import pytest


# ── POST /search ──


@pytest.mark.asyncio
async def test_search_hybrid(async_client, sample_info_data):
    """默认 mode=hybrid：BM25 + 向量 + 名称匹配"""
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/search", json={
        "query_text": "降准 央行 货币政策",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_search_embedding(async_client, sample_info_data):
    """mode=embedding：仅向量语义搜索"""
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/search", json={
        "query_text": "降准 央行 货币政策",
        "mode": "embedding",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_search_bm25(async_client, sample_info_data):
    """mode=bm25：仅 BM25 关键词搜索"""
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/search", json={
        "query_text": "降准 央行 货币政策",
        "mode": "bm25",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_search_with_filters(async_client, sample_info_data, sample_info_data2):
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    await _create_info(async_client, sample_info_data2)
    resp = await async_client.post("/api/v1/search", json={
        "query_text": "央行",
        "filters": {"source": "央行官网"},
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_search_with_weights(async_client, sample_info_data):
    from tests.conftest import _create_info
    await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/search", json={
        "query_text": "降准",
        "limit": 5,
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_search_empty(async_client):
    resp = await async_client.post("/api/v1/search", json={
        "query_text": "nothing should match this query",
    })
    assert resp.status_code == 200


# ── POST /search/fetch-by-ids ──


@pytest.mark.asyncio
async def test_fetch_by_ids(async_client, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    resp = await async_client.post("/api/v1/search/fetch-by-ids", json={
        "table_ids": {
            "raw_information": [info["id"]],
        }
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert len(data["data"]["raw_information"]) == 1
    assert data["data"]["raw_information"][0]["id"] == info["id"]
