#!/usr/bin/env python3
"""
读取 merge_plan_final.csv，对每个 canonical group：
  1. 收集 canonical 和所有 source 的 cognition_text
  2. 调用 LLM 整合为一份连贯的认知摘要
  3. canonical sector 的 cognition_text → LLM 整合结果，append_count → -1
  4. 被合并的 source sector 的 append_count → -2（标记为已合并）

自动跳过已处理过的 group。source 不重复合并（先完成的 canonical 认领）。

用法:
  python scripts/merge_cognitions.py
  python scripts/merge_cognitions.py --dry-run
"""

import argparse
import asyncio
import csv
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from kbquant.database import write_async_session, read_async_session
from kbquant.services.llm_service import llm_service

logger = logging.getLogger(__name__)

_MERGE_SYSTEM = (
    "你是一位资深投资分析师。请将以下关于同一个行业主题的多份认知笔记整合为一份连贯、"
    "结构清晰的行业认知摘要。\n\n"
    "要求：\n"
    "1. 保留所有关键数据点、趋势判断和投资逻辑\n"
    "2. 去除重复内容，合并相近观点\n"
    "3. 按逻辑顺序组织：行业概况 → 核心驱动因素 → 投资逻辑与机会 → 风险提示\n"
    "4. 直接输出整合后的认知文本，不要添加\"以下是整合后的认知\"等额外说明\n"
    "5. 用中文输出"
)


async def load_merge_plan(csv_path: Path) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            canon = row["canonical_sector"].strip()
            src = row["source_sector"].strip()
            groups.setdefault(canon, []).append(src)
    return groups


async def get_already_done() -> tuple[set[str], set[str]]:
    """Return (canonicals_done, sources_done) — sectors with append_count in {-1, -2}."""
    async with read_async_session() as session:
        result = await session.execute(
            text("SELECT sector, append_count FROM industry_cognitions WHERE append_count IN (-1, -2)")
        )
        rows = result.fetchall()
    canons = {r[0] for r in rows if r[1] == -1}
    sources = {r[0] for r in rows if r[1] == -2}
    return canons, sources


async def main():
    parser = argparse.ArgumentParser(description="LLM 合并 industry_cognitions")
    parser.add_argument("--csv", type=str, default="scripts/merge_plan_final.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = PROJECT_ROOT / csv_path
    if not csv_path.exists():
        print(f"CSV 文件不存在: {csv_path}")
        return 1

    plan = await load_merge_plan(csv_path)
    print(f"CSV 计划: {len(plan)} canonical groups, {sum(len(v) for v in plan.values())} sources")

    # ── 过滤已完成的 ──
    done_canons, done_sources = await get_already_done()
    items = sorted(plan.items(), key=lambda x: x[0])
    todo: list[tuple[str, list[str]]] = []
    skipped = 0
    for canon, sources in items:
        sources_still_needed = [s for s in sources if s not in done_sources]
        if canon in done_canons and not sources_still_needed:
            skipped += 1
            continue
        todo.append((canon, sources_still_needed))

    print(f"已跳过: {skipped} 个 group（全部完成）")
    print(f"待处理: {len(todo)} 个 group, {sum(len(v) for _, v in todo)} 个 source")
    print(f"已标记完成的 sector: {len(done_canons)} canonical + {len(done_sources)} source = {len(done_canons | done_sources)} 个")

    if not todo:
        print("所有 group 已处理完毕。")
        return 0

    if args.dry_run:
        print("\n=== DRY RUN ===\n")
        for canon, sources in todo[:30]:
            print(f"▸ {canon}  ← {' '.join(sources[:3])}{'...' if len(sources) > 3 else ''} ({len(sources)} sources)")
        if len(todo) > 30:
            print(f"\n... 等 {len(todo) - 30} 个 group")
        return 0

    stats = {"merged": 0, "skipped_no_cog": 0, "errors": 0}

    for batch_start in range(0, len(todo), args.batch_size):
        batch = todo[batch_start: batch_start + args.batch_size]
        tasks = [_process_group(canon, sources, stats) for canon, sources in batch]
        await asyncio.gather(*tasks)
        print(f"[{batch_start + 1}-{min(batch_start + args.batch_size, len(todo))}/{len(todo)}] "
              f"merged={stats['merged']} skipped={stats['skipped_no_cog']} errors={stats['errors']}")

    print(f"\n完成: merged={stats['merged']} skipped(no_cog)={stats['skipped_no_cog']} errors={stats['errors']}")


async def _process_group(canon: str, sources: list[str], stats: dict) -> None:
    try:
        # ── Step 1: 无锁读 ──
        all_names = [canon] + sources
        async with read_async_session() as session:
            placeholders = ", ".join(f":s{i}" for i in range(len(all_names)))
            params = {f"s{i}": s for i, s in enumerate(all_names)}
            result = await session.execute(
                text(f"SELECT sector, cognition_text, append_count FROM industry_cognitions WHERE sector IN ({placeholders})"),
                params,
            )
            rows = {row[0]: (row[1] or "", row[2]) for row in result.fetchall()}

        canon_row = rows.get(canon)
        if canon_row is None:
            logger.warning("canonical '%s' 不在数据库中，跳过", canon)
            return

        # 收集 cognition_text
        texts: list[str] = []
        text_sources: list[str] = []
        if canon_row[0]:
            texts.append(f"[{canon}]\n{canon_row[0]}")
            text_sources.append(canon)

        for src in sources:
            sr = rows.get(src)
            if sr and sr[0]:
                texts.append(f"[{src}]\n{sr[0]}")
                text_sources.append(src)

        if not texts:
            stats["skipped_no_cog"] += 1
            return

        # ── Step 2: LLM 合并（锁外）──
        combined_input = "\n\n---\n\n".join(texts)
        merged_text = await llm_service.chat(
            _MERGE_SYSTEM,
            f"行业主题：{canon}\n\n各份认知笔记：\n{combined_input}",
            temperature=0.3,
        )
        merged_text = merged_text.strip()
        if not merged_text:
            logger.warning("LLM 返回空文本 for '%s'，保留原始", canon)
            merged_text = canon_row[0]

        # ── Step 3: 快速写入（短事务）──
        async with write_async_session() as session:
            # 仅标记还在 active 状态的 source（避免抢别人已经合并过的）
            for src in sources:
                if src in rows:
                    await session.execute(
                        text("UPDATE industry_cognitions SET append_count = -2 "
                             "WHERE sector = :s AND append_count >= 0"),
                        {"s": src},
                    )

            # 更新 canonical（如果还没被其他人标记为 -1）
            result = await session.execute(
                text("UPDATE industry_cognitions SET cognition_text = :t, append_count = -1 "
                     "WHERE sector = :s AND append_count >= 0"),
                {"t": merged_text, "s": canon},
            )
            if result.rowcount == 0:
                # 已经被别的 group 处理了
                await session.rollback()
                stats["skipped_no_cog"] += 1
                return

            await session.commit()

        stats["merged"] += 1
        print(f"  ✓ {canon}  ← {len(sources)} sources ({len(text_sources)} with cog), "
              f"{len(merged_text)} chars")

    except Exception:
        logger.exception("合并失败 canonical='%s'", canon)
        stats["errors"] += 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
