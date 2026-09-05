#!/usr/bin/env python3
"""
逐对合并表 — 仅保留「包含」和「连接词」两类确定性规则。
不做传递闭包 - 每条 pair 独立可审计。

筛选：
  1. 包含：短名完整出现在长名内（>= 4 字符）。
     总是以认知更丰富的作为 canonical。
  2. 连接词：去掉 "与/及/和/、" 后完全相同。
  3. 包含规则下，短名长度必须 >= 长名的 1/3（避免 2-3 字短词误吞长名）。
  4. de-duplicate: 每条 source 只在最终表出现一次（取 content-richest canonical）。
"""

import csv
import re
from collections import defaultdict
from pathlib import Path

_INPUT = Path(__file__).resolve().parent.parent / "scripts" / "merge_plan.csv"
_OUTPUT = Path(__file__).resolve().parent.parent / "scripts" / "merge_plan_final.csv"

_CONNECTORS_RE = re.compile(r"[与及和、]")
_MIN_CORE_LEN = 4


def strip_connectors(s: str) -> str:
    return _CONNECTORS_RE.sub("", s)


def load():
    with open(_INPUT, "r", newline="", encoding="utf-8") as f:
        raw = list(csv.DictReader(f))

    row_map: dict[str, dict] = {}
    for r in raw:
        for col in ("canonical_cognition_len", "source_cognition_len",
                    "canonical_append_count", "source_append_count"):
            r[col] = int(r[col])

    all_names = set()
    for r in raw:
        all_names.add(r["canonical_sector"])
        all_names.add(r["source_sector"])

    for r in raw:
        for col, name in [("canonical", r["canonical_sector"]), ("source", r["source_sector"])]:
            if name not in row_map:
                row_map[name] = {
                    "name": name,
                    "c_len": r[f"{col}_cognition_len"],
                    "c_count": r[f"{col}_append_count"],
                }

    return raw, row_map


def pick_richer(a: dict, b: dict) -> dict:
    """cognition richer → append larger → name shorter"""
    if a["c_len"] != b["c_len"]:
        return a if a["c_len"] > b["c_len"] else b
    if a["c_count"] != b["c_count"]:
        return a if a["c_count"] > b["c_count"] else b
    return a if len(a["name"]) <= len(b["name"]) else b


def main():
    raw_rows, row_map = load()
    print(f"原始 CSV: {len(raw_rows)} 条\n")

    # ── Evaluate each pair independently ──
    pairs: list[dict] = []

    for r in raw_rows:
        canon = r["canonical_sector"]
        src = r["source_sector"]
        rule = r["rule"]
        clen = r["canonical_cognition_len"]
        slen = r["source_cognition_len"]

        if rule == "包含" or rule == "连接词":
            # Shortest name must be >= 4 chars
            if min(len(canon), len(src)) < _MIN_CORE_LEN:
                continue

        if rule == "包含":
            # Short name must be >= 1/3 of long name (prevent 3-char eating 15-char)
            short_len = min(len(canon), len(src))
            long_len = max(len(canon), len(src))
            if short_len < long_len / 3:
                continue

            # Winner = richer cognition
            a = row_map[canon]
            b = row_map[src]
            winner = pick_richer(a, b)
            loser_name = src if winner["name"] == canon else canon
            if loser_name == winner["name"]:
                continue
            pairs.append({
                "canonical_sector": winner["name"],
                "source_sector": loser_name,
                "canonical_cognition_len": row_map[winner["name"]]["c_len"],
                "source_cognition_len": row_map[loser_name]["c_len"],
                "canonical_append_count": row_map[winner["name"]]["c_count"],
                "source_append_count": row_map[loser_name]["c_count"],
            })

        elif rule == "连接词":
            n1 = strip_connectors(canon)
            n2 = strip_connectors(src)
            if n1 == n2:
                a = row_map[canon]
                b = row_map[src]
                winner = pick_richer(a, b)
                loser_name = src if winner["name"] == canon else canon
                if loser_name == winner["name"]:
                    continue
                pairs.append({
                    "canonical_sector": winner["name"],
                    "source_sector": loser_name,
                    "canonical_cognition_len": row_map[winner["name"]]["c_len"],
                    "source_cognition_len": row_map[loser_name]["c_len"],
                    "canonical_append_count": row_map[winner["name"]]["c_count"],
                    "source_append_count": row_map[loser_name]["c_count"],
                })

    print(f"筛选后 pair 数: {len(pairs)}")

    # ── De-duplicate: each source only mapped to one canonical ──
    # Pick the canonical with the richest cognition
    best: dict[str, dict] = {}
    for p in pairs:
        src = p["source_sector"]
        if src in best:
            existing = best[src]
            if p["canonical_cognition_len"] > existing["canonical_cognition_len"]:
                best[src] = p
        else:
            best[src] = p

    deduped = list(best.values())
    deduped.sort(key=lambda x: (x["canonical_sector"], x["source_sector"]))
    print(f"去重后 pair 数: {len(deduped)}")

    # ── 检查 circular ──
    all_canon = {p["canonical_sector"] for p in deduped}
    all_src = {p["source_sector"] for p in deduped}
    circular = all_canon & all_src
    print(f"Circular (canonical 同时是 source): {len(circular)} 个")

    # ── 按 canonical 分组打印 ──
    by_canon = defaultdict(list)
    for p in deduped:
        by_canon[p["canonical_sector"]].append(p)

    for canon, sources in sorted(by_canon.items(), key=lambda g: -len(g[1])):
        info = row_map.get(canon, {})
        print(f"\n▸ {canon}  (认知{info.get('c_len','?')}字, append={info.get('c_count','?')})")
        for s in sources:
            sinfo = row_map.get(s["source_sector"], {})
            print(f"    ← {s['source_sector']}  (认知{sinfo.get('c_len','?')}字, append={sinfo.get('c_count','?')})")

    # ── 保存 ──
    with open(_OUTPUT, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "canonical_sector", "source_sector",
            "canonical_cognition_len", "source_cognition_len",
            "canonical_append_count", "source_append_count",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for p in deduped:
            w.writerow(p)

    print(f"\n最终合并表: {_OUTPUT}")


if __name__ == "__main__":
    main()
