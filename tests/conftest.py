import os

# Override for test database BEFORE app import
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:15432/quant_kb_test"
os.environ["DATABASE_URL_SYNC"] = "postgresql+psycopg2://postgres:postgres@localhost:15432/quant_kb_test"
os.environ["PIPELINE_WORKER_ENABLED"] = "false"

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from kbquant.main import app

# --- Mock heavy background tasks to prevent freeze ---


async def _mock_store_embedding_async(info, text):
    """No-op replacement for _compute_and_store_embedding to skip API calls."""
    return None


async def _mock_sync_raw_info(info):
    """No-op replacement for sync_raw_info to skip ES calls."""
    return None


# Patch before any tests run — must patch where the functions are *called*
import kbquant.services.information_service as _info_svc

_info_svc._compute_and_store_embedding = _mock_store_embedding_async  # type: ignore
_info_svc.sync_raw_info = _mock_sync_raw_info  # type: ignore

# --- Database cleanup ---

TRUNCATE_ORDER = [
    "information_dedups",
    "information_entities",
    "entity_relationships",
    "node_attachments",
    "node_states",
    "time_validities",
    "conflict_detections",
    "importance_rankings",
    "macro_reports",
    "processing_queue",
    "feedbacks",
    "trading_operations",
    "analyses",
    "entities",
    "raw_information",
    "world_nodes",
]

_sync_engine = create_engine(
    "postgresql+psycopg2://postgres:postgres@localhost:15432/quant_kb_test",
    isolation_level="AUTOCOMMIT",
)


def _truncate_all():
    with _sync_engine.begin() as conn:
        conn.execute(text("SET session_replication_role = 'replica'"))
        for table in TRUNCATE_ORDER:
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        conn.execute(text("SET session_replication_role = 'origin'"))


@pytest.fixture(autouse=True)
def clean_db_each():
    """Truncate all tables before each test for isolation."""
    _truncate_all()
    yield


# --- Async client ---


@pytest.fixture
async def async_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# --- Helper to generate unique IDs ---


def _unique_suffix():
    return str(uuid4())[:8]


# --- Sample data fixtures ---


@pytest.fixture
def sample_info_data():
    u = _unique_suffix()
    return {
        "title": f"央行宣布降准50个基点-{u}",
        "body": f"中国人民银行决定于2026年6月1日起下调金融机构存款准备金率50个基点...{u}",
        "source": "央行官网",
        "source_url": "http://example.com/pboc/rrr-2026",
        "published_at": datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat(),
        "info_type": "news",
        "language": "zh",
    }


@pytest.fixture
def sample_info_data2():
    u = _unique_suffix()
    return {
        "title": f"美联储维持利率不变-{u}",
        "body": f"美联储FOMC会议决定维持联邦基金利率在5.25%-5.50%不变...{u}",
        "source": "Reuters",
        "source_url": "http://example.com/fed/rate-decision",
        "published_at": datetime(2026, 5, 21, 14, 0, 0, tzinfo=timezone.utc).isoformat(),
        "info_type": "news",
        "language": "zh",
    }


async def _create_info(client, data):
    resp = await client.post("/api/v1/information/", json=data)
    assert resp.status_code == 201, f"Failed to create info: {resp.text}"
    return resp.json()


async def _create_node(client, data):
    resp = await client.post("/api/v1/nodes/", json=data)
    assert resp.status_code == 201, f"Failed to create node: {resp.text}"
    return resp.json()


async def _create_entity(client, data):
    resp = await client.post("/api/v1/entities/", json=data)
    assert resp.status_code == 201, f"Failed to create entity: {resp.text}"
    return resp.json()


@pytest.fixture
def node_data():
    u = _unique_suffix()
    return {
        "name": f"贵州茅台-{u}",
        "node_type": "company",
        "description": "白酒龙头企业",
        "ticker": f"600519-{u}",
        "aliases": [f"茅台-{u}", f"贵州茅台酒-{u}"],
        "metadata_": {"sector": "白酒", "market": "A股"},
    }


@pytest.fixture
def node_data2():
    u = _unique_suffix()
    return {
        "name": f"白酒板块-{u}",
        "node_type": "sector",
        "description": "白酒行业板块",
        "aliases": [f"白酒-{u}", f"白酒行业-{u}"],
    }


@pytest.fixture
def entity_data():
    u = _unique_suffix()
    return {
        "name": f"贵州茅台-{u}",
        "entity_type": "company",
        "aliases": [f"茅台-{u}", f"600519-{u}"],
    }


@pytest.fixture
def entity_data2():
    u = _unique_suffix()
    return {
        "name": f"白酒-{u}",
        "entity_type": "sector",
        "aliases": [f"白酒行业-{u}", f"高端白酒-{u}"],
    }


@pytest.fixture
def analysis_data():
    return {
        "title": f"降准对白酒行业的影响分析-{_unique_suffix()}",
        "content": "降准释放流动性，利好消费板块，白酒行业将受益于流动性改善...",
        "analysis_type": "impact_analysis",
        "agent_id": "macro_analyst_01",
        "confidence": 0.85,
        "time_horizon": "medium_term",
    }


@pytest.fixture
def trading_data():
    return {
        "operation_type": "buy",
        "symbol": f"600519-{_unique_suffix()}",
        "quantity": 100.0,
        "price": 1650.50,
        "rationale": "降准释放流动性，利好白酒板块龙头",
        "expected_impact": "预期上涨5-10%",
        "risk_level": "medium",
        "status": "pending",
    }


@pytest.fixture
def feedback_data():
    u = _unique_suffix()
    return {
        "title": f"降准后白酒板块复盘-{u}",
        "expected_outcome": "白酒板块上涨5%",
        "actual_outcome": "白酒板块上涨7.2%，超出预期",
        "judgment_correct": True,
        "lessons_learned": "降准对消费板块的刺激效果强于预期，应加大仓位",
    }
