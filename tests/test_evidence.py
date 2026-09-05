from uuid import UUID

import pytest

from tests.conftest import _create_info, _create_node


@pytest.mark.asyncio
async def test_trace_node_state_batches_key_evidence(async_client, node_data, sample_info_data, sample_info_data2):
    node = await _create_node(async_client, node_data)
    info1 = await _create_info(async_client, sample_info_data)
    info2 = await _create_info(async_client, sample_info_data2)

    state_resp = await async_client.post(f"/api/v1/nodes/{node['id']}/state", json={
        "core_logic": "流动性改善利多消费",
        "state_summary": "证据链测试",
        "key_evidence_ids": [info1["id"], info2["id"]],
    })
    assert state_resp.status_code == 201
    state = state_resp.json()

    resp = await async_client.get(f"/api/v1/evidence/trace/node_state/{state['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["root"]["type"] == "node_state"
    assert len(data["evidence_chain"]) == 3
    collected_ids = [
        str(level["items"][0]["id"])
        for level in data["evidence_chain"][1:]
    ]
    assert collected_ids == [info1["id"], info2["id"]]


@pytest.mark.asyncio
async def test_trace_node_evidence_batches_raw_info(async_client, node_data, sample_info_data, sample_info_data2):
    node = await _create_node(async_client, node_data)
    info1 = await _create_info(async_client, sample_info_data)
    info2 = await _create_info(async_client, sample_info_data2)

    for info_id, role in ((info1["id"], "primary"), (info2["id"], "secondary")):
        attach_resp = await async_client.post(f"/api/v1/nodes/{node['id']}/attachments", json={
            "attachment_type": "raw_info",
            "attachment_id": info_id,
            "role": role,
        })
        assert attach_resp.status_code == 201

    resp = await async_client.get(f"/api/v1/evidence/trace-node/{node['id']}")
    assert resp.status_code == 200
    data = resp.json()
    evidence_ids = {str(item["id"]) for item in data["evidence_items"]}
    assert evidence_ids == {info1["id"], info2["id"]}
    for item in data["evidence_items"]:
        UUID(str(item["id"]))
        assert item["type"] == "raw_info"
