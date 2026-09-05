"""
Shared utilities for node maintenance scripts.

Usage:
    from nodes.common import (
        get_engine, write_session, read_session,
        clean_name, load_maintenance_state, save_maintenance_state,
        MAINTENANCE_FILE, BACKUP_DIR, utcnow,
    )
"""
import json
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

# Add project root to path so we can import kbquant
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from kbquant.config import settings

NODES_DIR = Path(__file__).resolve().parent
BACKUP_DIR = NODES_DIR / "backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
MAINTENANCE_FILE = NODES_DIR / "last_run.json"


def utcnow() -> datetime:
    """Return the current UTC time (convenience, for DB write timestamps)."""
    return datetime.now(timezone.utc)

SUFFIXES = ["板块", "概念", "行业", "赛道", "产业链", "领域", "产业"]

# Common noise words in Chinese financial node names — stripped before similarity
# computation so "华峰化学股份" and "华峰化学" match, not "华峰化学控" and "华峰股份".
NOISE_WORDS = [
    "股份", "控股", "公司", "集团", "有限", "科技", "中国", "国际",
    "行业", "赛道", "概念", "板块", "产业", "领域", "产业链",
    "有限公", "有限公司", "股份有限", "股份有限公司",
]


def clean_name(name: str) -> str:
    """Strip common Chinese financial suffixes from node names."""
    name = name.strip()
    for suf in SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[:-len(suf)].strip()
    return name


def strip_noise_words(name: str) -> str:
    """Remove common low-signal words from anywhere in the name.
    Order matters: longer phrases removed first so '股份有限公司' doesn't leave
    '有限' behind."""
    for w in NOISE_WORDS:
        name = name.replace(w, "")
    return name.strip()


def normalized_name(name: str) -> str:
    """Full normalization: strip noise words, then suffixes, lowercase."""
    return clean_name(strip_noise_words(name)).lower()


def text_similarity(a: str, b: str) -> float:
    """Compute SequenceMatcher ratio between two strings.
    Uses fast bail-outs: exact match and real_quick_ratio pre-filter."""
    if not a or not b:
        return 0.0
    a = a.lower().strip()
    b = b.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sm = SequenceMatcher(None, a, b)
    # real_quick_ratio is O(1) length comparison — skip expensive ratio() when
    # strings differ dramatically in length (ratio can't exceed 2*min/|a|+|b|)
    if sm.real_quick_ratio() < 0.35:
        return 0.0
    return sm.ratio()


def keyword_overlap_score(text: str, target: str) -> float:
    """Simple keyword overlap: fraction of text words found in target."""
    if not text or not target:
        return 0.0
    words_a = set(text.lower().split())
    words_b = set(target.lower().split())
    if not words_a:
        return 0.0
    return len(words_a & words_b) / len(words_a)


def get_engine():
    """Create an async engine for script use (separate from app pool)."""
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=2,
        max_overflow=3,
        pool_timeout=30,
    )


def read_session(engine=None):
    """Return a read-only sessionmaker."""
    if engine is None:
        engine = get_engine()
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def write_session(engine=None):
    """Return a write sessionmaker."""
    if engine is None:
        engine = get_engine()
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def load_maintenance_state() -> dict:
    """Load last maintenance timestamp and stats."""
    if MAINTENANCE_FILE.exists():
        with open(MAINTENANCE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_run": "2026-01-01T00:00:00+00:00",
        "phase1_pairs_processed": 0,
        "phase2_edges_created": 0,
    }


def save_maintenance_state(state: dict) -> None:
    """Save maintenance state to disk."""
    MAINTENANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MAINTENANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ensure_backup_dir():
    """Create backup directory for this run."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR
