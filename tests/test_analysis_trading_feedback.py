import pytest
from uuid import uuid4


# ── Analysis endpoints ──


@pytest.mark.asyncio
async def test_create_analysis(async_client, analysis_data):
    resp = await async_client.post("/api/v1/analysis/", json=analysis_data)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == analysis_data["title"]
    assert data["analysis_type"] == analysis_data["analysis_type"]
    assert data["confidence"] == 0.85
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_analysis_minimal(async_client):
    resp = await async_client.post("/api/v1/analysis/", json={
        "title": "测试分析",
        "content": "分析正文",
        "analysis_type": "macro",
    })
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_analysis_with_parent(async_client, analysis_data, sample_info_data):
    from tests.conftest import _create_info
    info = await _create_info(async_client, sample_info_data)
    parent = await async_client.post("/api/v1/analysis/", json={
        "title": "父分析", "content": "父分析内容",
        "analysis_type": "macro", "root_raw_info_ids": [info["id"]],
    })
    parent_id = parent.json()["id"]
    analysis_data["parent_analysis_id"] = parent_id
    resp = await async_client.post("/api/v1/analysis/", json=analysis_data)
    assert resp.status_code == 201
    assert resp.json()["parent_analysis_id"] == parent_id


@pytest.mark.asyncio
async def test_list_analysis_empty(async_client):
    resp = await async_client.get("/api/v1/analysis/")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_analysis(async_client, analysis_data):
    await async_client.post("/api/v1/analysis/", json=analysis_data)
    await async_client.post("/api/v1/analysis/", json=dict(analysis_data, title="分析2"))
    resp = await async_client.get("/api/v1/analysis/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_analysis_filter_by_type(async_client, analysis_data):
    await async_client.post("/api/v1/analysis/", json=analysis_data)
    await async_client.post("/api/v1/analysis/", json={
        "title": "风险评估", "content": "风险分析内容", "analysis_type": "risk_evaluation",
    })
    resp = await async_client.get("/api/v1/analysis/", params={"analysis_type": "impact_analysis"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["analysis_type"] == "impact_analysis"


@pytest.mark.asyncio
async def test_list_analysis_filter_by_confidence(async_client, analysis_data):
    await async_client.post("/api/v1/analysis/", json=analysis_data)
    await async_client.post("/api/v1/analysis/", json={
        "title": "低置信度分析", "content": "内容", "analysis_type": "technical",
        "confidence": 0.3,
    })
    resp = await async_client.get("/api/v1/analysis/", params={"confidence_min": 0.7})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item.get("confidence", 0) >= 0.7


@pytest.mark.asyncio
async def test_get_analysis(async_client, analysis_data):
    anal = await async_client.post("/api/v1/analysis/", json=analysis_data)
    anal_id = anal.json()["id"]
    resp = await async_client.get(f"/api/v1/analysis/{anal_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == anal_id


@pytest.mark.asyncio
async def test_get_analysis_not_found(async_client):
    resp = await async_client.get(f"/api/v1/analysis/{uuid4()}")
    assert resp.status_code == 404


# ── Trading endpoints ──


@pytest.mark.asyncio
async def test_create_trade(async_client, trading_data):
    resp = await async_client.post("/api/v1/trading/", json=trading_data)
    assert resp.status_code == 201
    data = resp.json()
    assert data["operation_type"] == trading_data["operation_type"]
    assert data["symbol"] == trading_data["symbol"]
    assert data["quantity"] == trading_data["quantity"]
    assert data["price"] == trading_data["price"]
    assert data["status"] == "pending"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_trade_minimal(async_client):
    resp = await async_client.post("/api/v1/trading/", json={
        "operation_type": "sell",
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_create_trade_with_nodes(async_client, trading_data, node_data, analysis_data, sample_info_data):
    from tests.conftest import _create_node, _create_info
    node = await _create_node(async_client, node_data)
    info = await _create_info(async_client, sample_info_data)
    analysis_data["root_raw_info_ids"] = [info["id"]]
    anal = await async_client.post("/api/v1/analysis/", json=analysis_data)
    anal_id = anal.json()["id"]

    trading_data["target_node_id"] = node["id"]
    trading_data["trigger_analysis_id"] = anal_id
    trading_data["trigger_raw_ids"] = [info["id"]]
    resp = await async_client.post("/api/v1/trading/", json=trading_data)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_trades_empty(async_client):
    resp = await async_client.get("/api/v1/trading/")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_trades(async_client, trading_data):
    await async_client.post("/api/v1/trading/", json=trading_data)
    await async_client.post("/api/v1/trading/", json=dict(trading_data, symbol="000858.SZ"))
    resp = await async_client.get("/api/v1/trading/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_trades_filter_by_type(async_client, trading_data):
    await async_client.post("/api/v1/trading/", json=trading_data)
    await async_client.post("/api/v1/trading/", json={"operation_type": "skip"})
    resp = await async_client.get("/api/v1/trading/", params={"operation_type": "buy"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["operation_type"] == "buy"


@pytest.mark.asyncio
async def test_list_trades_filter_by_symbol(async_client, trading_data):
    await async_client.post("/api/v1/trading/", json=trading_data)
    await async_client.post("/api/v1/trading/", json={"operation_type": "sell", "symbol": "000858.SZ"})
    resp = await async_client.get("/api/v1/trading/", params={"symbol": "600519.SH"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["symbol"] == "600519.SH"


@pytest.mark.asyncio
async def test_list_trades_filter_by_status(async_client, trading_data):
    await async_client.post("/api/v1/trading/", json=trading_data)
    await async_client.post("/api/v1/trading/", json={"operation_type": "sell", "status": "executed"})
    resp = await async_client.get("/api/v1/trading/", params={"status": "pending"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["status"] == "pending"


@pytest.mark.asyncio
async def test_get_trade(async_client, trading_data):
    trade = await async_client.post("/api/v1/trading/", json=trading_data)
    trade_id = trade.json()["id"]
    resp = await async_client.get(f"/api/v1/trading/{trade_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == trade_id


@pytest.mark.asyncio
async def test_get_trade_not_found(async_client):
    resp = await async_client.get(f"/api/v1/trading/{uuid4()}")
    assert resp.status_code == 404


# ── PUT /trading/{trade_id} ──


@pytest.mark.asyncio
async def test_update_trade_approve(async_client, trading_data):
    trade = await async_client.post("/api/v1/trading/", json=trading_data)
    trade_id = trade.json()["id"]
    resp = await async_client.put(f"/api/v1/trading/{trade_id}", json={"status": "approved"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["executed_at"] is not None


@pytest.mark.asyncio
async def test_update_trade_reject(async_client, trading_data):
    trade = await async_client.post("/api/v1/trading/", json=trading_data)
    trade_id = trade.json()["id"]
    resp = await async_client.put(f"/api/v1/trading/{trade_id}", json={
        "status": "rejected",
        "reason": "风控拒绝：风险等级过高",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["rationale"] == "风控拒绝：风险等级过高"


@pytest.mark.asyncio
async def test_update_trade_not_found(async_client):
    from uuid import uuid4
    resp = await async_client.put(f"/api/v1/trading/{uuid4()}", json={"status": "approved"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_trade_approved_sets_executed_at(async_client, trading_data):
    trade = await async_client.post("/api/v1/trading/", json=trading_data)
    trade_id = trade.json()["id"]
    assert trade.json()["executed_at"] is None
    resp = await async_client.put(f"/api/v1/trading/{trade_id}", json={"status": "approved"})
    assert resp.status_code == 200
    assert resp.json()["executed_at"] is not None


# ── Feedback endpoints ──


@pytest.mark.asyncio
async def test_create_feedback(async_client, feedback_data):
    resp = await async_client.post("/api/v1/feedback/", json=feedback_data)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == feedback_data["title"]
    assert data["judgment_correct"] is True
    assert data["lessons_learned"] == feedback_data["lessons_learned"]
    assert "id" in data


@pytest.mark.asyncio
async def test_create_feedback_minimal(async_client):
    resp = await async_client.post("/api/v1/feedback/", json={"title": "复盘测试"})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_feedback_incorrect(async_client):
    resp = await async_client.post("/api/v1/feedback/", json={
        "title": "判断错误复盘",
        "expected_outcome": "上证指数上涨2%",
        "actual_outcome": "上证指数下跌1.5%",
        "judgment_correct": False,
        "error_reason": "低估了外部冲击的影响",
        "missed_factors": "海外CPI数据超预期",
        "adjustment_suggestions": "增加对海外宏观数据的监控",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["judgment_correct"] is False
    assert data["error_reason"] is not None


@pytest.mark.asyncio
async def test_list_feedback_empty(async_client):
    resp = await async_client.get("/api/v1/feedback/")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_feedback(async_client, feedback_data):
    await async_client.post("/api/v1/feedback/", json=feedback_data)
    await async_client.post("/api/v1/feedback/", json={"title": "复盘2"})
    resp = await async_client.get("/api/v1/feedback/")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 2


@pytest.mark.asyncio
async def test_list_feedback_filter_judgment(async_client, feedback_data):
    await async_client.post("/api/v1/feedback/", json=feedback_data)
    await async_client.post("/api/v1/feedback/", json={
        "title": "错误复盘", "judgment_correct": False,
    })
    resp = await async_client.get("/api/v1/feedback/", params={"judgment_correct": True})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["judgment_correct"] is True


@pytest.mark.asyncio
async def test_get_feedback(async_client, feedback_data):
    fb = await async_client.post("/api/v1/feedback/", json=feedback_data)
    fb_id = fb.json()["id"]
    resp = await async_client.get(f"/api/v1/feedback/{fb_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == fb_id


@pytest.mark.asyncio
async def test_get_feedback_not_found(async_client):
    resp = await async_client.get(f"/api/v1/feedback/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_lessons_empty(async_client):
    resp = await async_client.get("/api/v1/feedback/lessons")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_lessons_with_search(async_client, feedback_data):
    await async_client.post("/api/v1/feedback/", json=feedback_data)
    resp = await async_client.get("/api/v1/feedback/lessons", params={"search_text": "降准"})
    assert resp.status_code == 200
