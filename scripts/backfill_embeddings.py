"""Backfill empty embeddings in raw_information table.

Finds rows where embedding IS NULL in batches of 20, calls the embedding API
directly, and writes vectors back. Repeats until no null rows remain.

Usage:
    python scripts/backfill_embeddings.py [--batch-size 20] [--limit 1000] [--offset 0]
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from kbquant.config import settings
from kbquant.database import bg_write_async_session, read_async_session
from kbquant.models.raw_information import RawInformation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_embeddings")

EMBEDDING_URL = f"{settings.embedding_base_url}/embeddings"
EMBEDDING_HEADERS = {
    "Authorization": f"Bearer {settings.embedding_api_key}",
    "Content-Type": "application/json",
}
EMBEDDING_PAYLOAD_BASE = {
    "model": settings.embedding_model,
    "dimensions": settings.embedding_dimension,
    "encoding_format": "float",
}
MAX_RETRIES = 5

API_SEM = asyncio.Semaphore(70)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    payload = {**EMBEDDING_PAYLOAD_BASE, "input": texts}

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        for attempt in range(MAX_RETRIES):
            try:
                async with API_SEM:
                    resp = await client.post(EMBEDDING_URL, headers=EMBEDDING_HEADERS, json=payload)

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else (2 ** attempt)
                    logger.warning("429 (attempt %d/%d), waiting %.1fs", attempt + 1, MAX_RETRIES, wait)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Server error {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )

                resp.raise_for_status()
                data = resp.json()
                return [d["embedding"] for d in data["data"]]

            except (httpx.HTTPStatusError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                last_exc = e
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                last_exc = e

    raise RuntimeError(f"Embedding failed after {MAX_RETRIES} retries") from last_exc


async def backfill(batch_size: int = 20, limit: int = 0, offset: int = 0) -> int:
    # Count total for progress reporting
    async with read_async_session() as session:
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(RawInformation).where(
            RawInformation.embedding.is_(None)
        )
        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

    logger.info("Found %d rows with null embedding", total)

    if total == 0:
        logger.info("Nothing to do.")
        return 0

    processed = 0
    page = offset
    while processed < (limit or total):
        current_limit = min(batch_size, (limit or total) - processed)

        # Fetch one batch
        async with read_async_session() as session:
            stmt = (
                select(RawInformation)
                .where(RawInformation.embedding.is_(None))
                .limit(current_limit)
            )
            result = await session.execute(stmt)
            batch = result.scalars().all()

        if not batch:
            break

        texts = [f"{r.title} {r.body}" for r in batch]

        try:
            vectors = await embed_batch(texts)
        except Exception as e:
            logger.error("Embedding batch failed: %s", e)
            break

        async with bg_write_async_session() as session:
            for row, vector in zip(batch, vectors):
                row_obj = await session.get(RawInformation, row.id)
                if row_obj is not None and row_obj.embedding is None:
                    row_obj.embedding = vector
            await session.commit()

        processed += len(batch)
        logger.info("Progress: %d/%d (%.1f%%)", processed, total, processed / total * 100)

    return processed


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill null embeddings in raw_information")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0 = all)")
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()

    if not settings.embedding_api_key:
        logger.error("EMBEDDING_API_KEY not set. Set it in your .env file.")
        return

    updated = await backfill(batch_size=args.batch_size, limit=args.limit, offset=args.offset)
    logger.info("Done. Updated %d rows.", updated)


if __name__ == "__main__":
    asyncio.run(main())
