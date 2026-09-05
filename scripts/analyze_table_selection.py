#!/usr/bin/env python3
"""
统计 search_kb_queries_stats.json 中每条查询最终会命中哪些表。

完全复用搜索服务的关键模块，保证与线上行为一致:
  - EntityMatcher.match_with_scores() → Aho-Corasick 实体扫描
  - EntityResolver.resolve(session=None) → 实体去重 + 主实体判定
  - QueryRewriter.rewrite() → 同义词扩展 + 停用词过滤
  - QueryFeatureExtractor.extract() → 意图/领域/时间敏感度识别
  - select_tables() → 三维度（实体类型 + 关键词信号 + 意图）表决策

输出:
  1. 每种表组合的出现次数和占比（按频次加权）
  2. 各表被选中频率
  3. 主实体类型分布
  4. 关键词信号命中率
  5. 语义意图分布
  6. Top-N 表组合的示例查询
"""

import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

# Ensure the project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Prevent database.py from trying to connect to a real db
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://localhost:5432/dummy")

from kbquant.services.search.entity_resolver import EntityResolver, get_entity_matcher  # noqa: E402
from kbquant.services.search.query_rewriter import QueryRewriter  # noqa: E402
from kbquant.services.search.dynamic_weights import QueryFeatureExtractor  # noqa: E402
from kbquant.services.search.table_rules import select_tables  # noqa: E402
from kbquant.models.search_candidate import SearchContext  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INPUT_FILE = PROJECT_ROOT / "search_kb_queries_stats.json"
TOP_N_DETAIL = 20
ALL_TABLES = ["raw_information", "analyses", "nodes", "feedbacks"]


# ---------------------------------------------------------------------------
# Pipeline (per-query, stages 1→2→2.5→3)
# ---------------------------------------------------------------------------

async def process_one_query(
    query_text: str,
    resolver: EntityResolver,
    rewriter: QueryRewriter,
    extractor: QueryFeatureExtractor,
) -> dict:
    """Run stages 1 → 2 → 2.5 → 3 (table decision) for a single query.

    Matches the flow in search_service.py:
      resolve(session=None) → rewrite(no ImpactPath) → extract features → select_tables
    """
    ctx = SearchContext(query_text=query_text)

    # Stage 1: Entity resolution (Aho-Corasick only, no WorldNode DB)
    await resolver.resolve(query_text, session=None, ctx=ctx)

    # Stage 2: Query rewriting (synonyms + stopwords, no ImpactPath)
    await rewriter.rewrite(query_text, ctx=ctx)

    # Stage 2.5: Intent / domain detection
    features = extractor.extract(query_text, ctx)

    # Stage 3: Table selection
    # Same logic as recall_service.determine_tables():
    #   query_keywords = expanded_keywords (or fallback to raw split)
    #   intent = dynamic_weights_intent (features.intent)
    sel = select_tables(
        entities=ctx.entities,
        query_keywords=ctx.expanded_keywords or set(query_text.split()),
        intent=features.intent if features.intent != "general" else None,
    )

    return {
        "query": query_text,
        "tables": sel.tables,
        "base_from": sel.base_from,
        "keyword_signals": sel.keyword_signals,
        "intent": features.intent,
        "intent_confidence": round(features.intent_confidence, 3),
        "entity_count": len(ctx.entities),
    }


async def process_all(
    queries: list[tuple[str, int]],
    resolver: EntityResolver,
    rewriter: QueryRewriter,
    extractor: QueryFeatureExtractor,
) -> list[dict]:
    """Process all queries through the pipeline, weighted by frequency."""
    results: list[dict] = []
    total = len(queries)
    t_batch = time.perf_counter()

    for i, (query_text, freq) in enumerate(queries):
        if (i + 1) % 2000 == 0:
            elapsed = time.perf_counter() - t_batch
            rate = 2000 / elapsed if elapsed > 0 else 0
            print(
                f"  {i + 1}/{total} ({100 * (i + 1) / total:.1f}%)  "
                f"{rate:.0f} q/s",
                flush=True,
            )
            t_batch = time.perf_counter()

        try:
            info = await process_one_query(query_text, resolver, rewriter, extractor)
        except Exception as exc:
            print(
                f"  WARN: query [{query_text[:60]}] failed: {exc}",
                flush=True,
            )
            info = {
                "query": query_text,
                "tables": ["raw_information", "analyses"],
                "base_from": "error",
                "keyword_signals": [],
                "intent": None,
                "intent_confidence": 0,
                "entity_count": 0,
            }

        info["frequency"] = freq
        results.append(info)

    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: list[dict]) -> None:
    total_freq = sum(r["frequency"] for r in results)
    n_unique = len(results)

    # ── 1. Table combination frequency ──
    combo_counter: Counter[str] = Counter()
    for r in results:
        key = "+".join(sorted(r["tables"])) if r["tables"] else "(none)"
        combo_counter[key] += r["frequency"]

    print("=" * 85)
    print("1. 表组合统计 (按搜索频次降序)")
    print("=" * 85)
    print(f"  {'表组合':<55} {'次数':>8} {'占比':>8} {'累计':>8}")
    print("  " + "-" * 83)
    cumulative = 0.0
    for combo, count in combo_counter.most_common():
        pct = count / total_freq * 100
        cumulative += pct
        print(f"  {combo:<53} {count:>8} {pct:>7.2f}% {cumulative:>7.1f}%")

    # ── 2. Per-table selection rate ──
    print()
    print("=" * 85)
    print("2. 各表被选中频率")
    print("=" * 85)
    table_counter: Counter[str] = Counter()
    for r in results:
        for t in r["tables"]:
            table_counter[t] += r["frequency"]
    for table in ALL_TABLES:
        count = table_counter.get(table, 0)
        pct = count / total_freq * 100
        bar = "#" * int(pct / 2)
        print(f"  {table:<30} {count:>8} ({pct:>5.1f}%)  {bar}")

    # ── 3. Base entity type (dimension 1) ──
    print()
    print("=" * 85)
    print("3. 主实体类型分布 (维度1: 决定基础表, BASE_TABLES)")
    print("=" * 85)
    base_counter: Counter[str] = Counter()
    for r in results:
        base_counter[r["base_from"]] += r["frequency"]
    for base, count in base_counter.most_common():
        pct = count / total_freq * 100
        # Show what base tables this maps to
        from kbquant.services.search.table_rules import BASE_TABLES
        bt = BASE_TABLES.get(base if base != "none" else None, [])
        print(f"  {base:<30} {count:>8} ({pct:>5.1f}%)  → {bt}")

    # ── 4. Keyword signal hits (dimension 2) ──
    print()
    print("=" * 85)
    print("4. 关键词信号命中率 (维度2: 追加表)")
    print("=" * 85)
    signal_counter: Counter[str] = Counter()
    for r in results:
        for sig in r["keyword_signals"]:
            signal_counter[sig] += r["frequency"]
    for sig, count in signal_counter.most_common():
        pct = count / total_freq * 100
        print(f"  {sig:<40} {count:>8} ({pct:>5.1f}%)")
    n_kw = sum(r["frequency"] for r in results if r["keyword_signals"])
    print(f"  {'(命中至少1个信号的查询)':<40} {n_kw:>8} ({n_kw/total_freq*100:.1f}%)")

    # ── 5. Intent distribution (dimension 3) ──
    print()
    print("=" * 85)
    print("5. 语义意图分布 (维度3: INTENT_TABLE_AUGMENT)")
    print("=" * 85)
    intent_counter: Counter[str] = Counter()
    for r in results:
        key = r["intent"] if r["intent"] and r["intent"] != "general" else "(none/general)"
        intent_counter[key] += r["frequency"]
    for intent, count in intent_counter.most_common():
        pct = count / total_freq * 100
        print(f"  {intent:<30} {count:>8} ({pct:>5.1f}%)")

    # ── 6. Top-N table combos with example queries ──
    print()
    print("=" * 85)
    print(f"6. Top {TOP_N_DETAIL} 表组合 × 示例查询")
    print("=" * 85)

    combo_examples: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        key = "+".join(sorted(r["tables"])) if r["tables"] else "(none)"
        if len(combo_examples[key]) < 5:
            combo_examples[key].append(r)

    for combo, count in combo_counter.most_common(TOP_N_DETAIL):
        pct = count / total_freq * 100
        print(f"\n  ── [{combo}]  {count}次 ({pct:.1f}%) ──")
        for ex in combo_examples[combo][:5]:
            sigs = ",".join(ex["keyword_signals"]) if ex["keyword_signals"] else "-"
            print(f"    freq={ex['frequency']:<4} base={ex['base_from']:<20} kw=[{sigs}]")
            print(f"      \"{ex['query'][:100]}\"")

    # ── 7. Summary ──
    print()
    print("=" * 85)
    print("7. 汇总")
    print("=" * 85)
    print(f"  输入: {n_unique} 条不同查询, {total_freq} 次搜索调用")
    print(f"  不同表组合数: {len(combo_counter)}")
    no_entity_pct = base_counter.get("none", 0) / total_freq * 100
    print(f"  无实体查询: {no_entity_pct:.1f}%")
    n_kw_pct = n_kw / total_freq * 100
    print(f"  命中关键词信号: {n_kw_pct:.1f}%")
    n_intent = sum(
        r["frequency"]
        for r in results
        if r["intent"] and r["intent"] != "general"
    )
    print(f"  命中非 general 意图: {n_intent / total_freq * 100:.1f}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    t0 = time.perf_counter()

    # Load queries
    print("加载查询数据...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    queries: list[tuple[str, int]] = [(q, f) for q, f in data["frequency"]]
    n_unique = len(queries)
    n_total = sum(f for _, f in queries)
    print(f"  不同查询: {n_unique:,}")
    print(f"  总搜索次数: {n_total:,}")

    # Initialize shared components (match service.py exactly)
    print("\n初始化模块 (与服务一致)...")
    matcher = get_entity_matcher()
    print(f"  EntityMatcher: {matcher.entity_count:,} 个实体已加载")

    resolver = EntityResolver(entity_matcher=matcher)
    rewriter = QueryRewriter()
    extractor = QueryFeatureExtractor()
    t_init = time.perf_counter() - t0
    print(f"  初始化耗时: {t_init:.1f}s")

    # Process all queries
    print(f"\n处理 {n_unique:,} 条查询...")
    t_proc = time.perf_counter()
    results = asyncio.run(process_all(queries, resolver, rewriter, extractor))
    t_proc_elapsed = time.perf_counter() - t_proc
    print(f"  处理耗时: {t_proc_elapsed:.1f}s  ({n_unique / t_proc_elapsed:.0f} q/s)")

    # Report
    print()
    print_report(results)

    t_total = time.perf_counter() - t0
    print(f"\n总耗时: {t_total:.1f}s")


if __name__ == "__main__":
    main()
