import pytest


@pytest.mark.asyncio
async def test_get_macro_report_current_empty(async_client):
    resp = await async_client.get("/api/v1/macro-report/current")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_macro_report_initial(async_client):
    resp = await async_client.put("/api/v1/macro-report", json={
        "content": "# 当前宏观定位\n\n货币政策宽松，信用扩张加速。",
        "summary": "货币宽松确立，信用扩张加速",
        "changed_sections": ["当前宏观定位", "信用环境"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert data["content"] == "# 当前宏观定位\n\n货币政策宽松，信用扩张加速。"
    assert data["summary"] == "货币宽松确立，信用扩张加速"
    assert data["changed_sections"] == ["当前宏观定位", "信用环境"]
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_put_macro_report_increments_version(async_client):
    await async_client.put("/api/v1/macro-report", json={
        "content": "# 第一版\n\n初始宏观判断。",
        "summary": "第一版摘要",
        "changed_sections": ["宏观判断"],
    })
    resp = await async_client.put("/api/v1/macro-report", json={
        "content": "# 第二版\n\n更新宏观判断：信用边际收紧。",
        "summary": "信用边际收紧",
        "changed_sections": ["宏观判断", "信用环境"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 2
    assert data["summary"] == "信用边际收紧"


@pytest.mark.asyncio
async def test_get_macro_report_current_returns_latest(async_client):
    await async_client.put("/api/v1/macro-report", json={
        "content": "# v1", "summary": "v1 summary", "changed_sections": ["A"],
    })
    await async_client.put("/api/v1/macro-report", json={
        "content": "# v2", "summary": "v2 summary", "changed_sections": ["B"],
    })
    resp = await async_client.get("/api/v1/macro-report/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 2
    assert data["content"] == "# v2"


@pytest.mark.asyncio
async def test_get_macro_report_history_empty(async_client):
    resp = await async_client.get("/api/v1/macro-report/history")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_get_macro_report_history(async_client):
    await async_client.put("/api/v1/macro-report", json={
        "content": "# v1", "summary": "第一版", "changed_sections": ["A"],
    })
    await async_client.put("/api/v1/macro-report", json={
        "content": "# v2", "summary": "第二版", "changed_sections": ["B", "C"],
    })
    resp = await async_client.get("/api/v1/macro-report/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["version"] == 2
    assert data["items"][1]["version"] == 1


@pytest.mark.asyncio
async def test_get_macro_report_history_fields(async_client):
    await async_client.put("/api/v1/macro-report", json={
        "content": "# 测试报告\n\n内容很长...",
        "summary": "测试摘要",
        "changed_sections": ["宏观定位", "资产观点"],
    })
    resp = await async_client.get("/api/v1/macro-report/history")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert "id" in item
    assert "version" in item
    assert "summary" in item
    assert "changed_sections" in item
    assert "updated_at" in item
    # content should NOT be in history items
    assert "content" not in item
