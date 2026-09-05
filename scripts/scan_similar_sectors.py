#!/usr/bin/env python3
"""
扫描 industry_cognitions 表，生成保守的逐对合并建议表。
不传播传递链 — 每条建议都独立且可审计。

规则（仅确定性规则）：
  1. 精确包含：s1 完整出现在 s2 中，且 s1 长度 >= 4
  2. 同义连接词差异：去掉 "与/及/和/、" 后完全相同
  3. 后缀冗余：去掉行业/板块/股票/股/概念/赛道/运营商/运营/制造业/产业链/领域 后完全相同 (core >= 4)
  4. 后缀子串：去掉后缀后短名是长名的子串 (短 core >= 4)

输出: source → target 合并表 CSV
"""

import argparse
import asyncio
import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from kbquant.config import settings

_CONNECTORS_RE = re.compile(r"[与及和、]")
_SUFFIX_RE = re.compile(r"(行业|板块|股票|股|概念|赛道|运营商|运营|制造业?|产业链?|领域|方向|联动|子行业)$")
_MIN_CORE_LEN = 4


def strip_connectors(s: str) -> str:
    return _CONNECTORS_RE.sub("", s)


def strip_suffix(s: str) -> str:
    return _SUFFIX_RE.sub("", s).strip()


def pick_winner(row_a: tuple, row_b: tuple) -> tuple:
    """选择 canonical: cognition_text 更丰富 → append_count 更大 → 名称更短"""
    _, t1, c1 = row_a
    _, t2, c2 = row_b
    len1 = len(t1) if t1 else 0
    len2 = len(t2) if t2 else 0
    if len1 != len2:
        return row_a if len1 > len2 else row_b
    if c1 != c2:
        return row_a if c1 > c2 else row_b
    return row_a if len(row_a[0]) <= len(row_b[0]) else row_b


def judge(s1: str, s2: str, row1: tuple, row2: tuple) -> tuple[str, str, str] | None:
    """返回 (loser, winner, rule) 或 None"""
    if s1 == s2:
        return None

    # Rule 1: 精确包含
    if s1 in s2 and len(s1) >= _MIN_CORE_LEN:
        return (s1, s2, "包含")
    if s2 in s1 and len(s2) >= _MIN_CORE_LEN:
        return (s2, s1, "包含")

    # Rule 2: 同义连接词差异
    n1 = strip_connectors(s1)
    n2 = strip_connectors(s2)
    if n1 == n2:
        winner = pick_winner(row1, row2)[0]
        loser = s1 if winner == s2 else s2
        return (loser, winner, "连接词")

    # Rule 3: 后缀冗余
    c1 = strip_suffix(s1)
    c2 = strip_suffix(s2)
    if c1 and c2 and c1 == c2 and len(c1) >= _MIN_CORE_LEN:
        winner = pick_winner(row1, row2)[0]
        loser = s1 if winner == s2 else s2
        return (loser, winner, "后缀")

    # Rule 4: 后缀子串
    if c1 and c2 and len(c1) >= _MIN_CORE_LEN and len(c2) >= _MIN_CORE_LEN:
        if c1 in c2 and c1 != c2:
            return (s1, s2, "后缀子串")
        if c2 in c1 and c2 != c1:
            return (s2, s1, "后缀子串")

    return None


async def main():
    parser = argparse.ArgumentParser(description="生成 sector 逐对合并建议表")
    parser.add_argument("--csv", type=str, default="",
                        help="导出 CSV 到指定路径")
    args = parser.parse_args()

    url = settings.database_read_url or settings.database_url
    if not url:
        raise RuntimeError("未提供数据库连接 URL")
    if "asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1) \
                  .replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(
        url, echo=False,
        connect_args={"server_settings": {"application_name": "scan_merge_table"}},
    )

    try:
        async with AsyncSession(engine) as session:
            result = await session.execute(
                text("SELECT sector, cognition_text, append_count FROM industry_cognitions ORDER BY sector")
            )
            rows = result.fetchall()

        if not rows:
            print("表为空")
            return 0

        row_map: dict[str, tuple] = {r[0]: r for r in rows}
        sectors = list(row_map.keys())
        n = len(sectors)
        print(f"共 {n} 个 sector\n")

        # 收集直接配对
        pairs: list[tuple[str, str, str, int, int, int, int]] = []
        # (target, source, rule, target_cog_len, src_cog_len, target_ct, src_ct)

        for i in range(n):
            for j in range(i + 1, n):
                s1, s2 = sectors[i], sectors[j]
                decision = judge(s1, s2, row_map[s1], row_map[s2])
                if decision is None:
                    continue
                loser, winner, rule = decision

                wr = row_map[winner]
                lr = row_map[loser]
                pairs.append((
                    winner, loser, rule,
                    len(wr[1]) if wr[1] else 0,
                    len(lr[1]) if lr[1] else 0,
                    wr[2], lr[2],
                ))

        # 按 target 分组显示
        groups: dict[str, list[tuple[str, str, int, int, int, int]]] = {}
        for winner, loser, rule, wcl, lcl, wct, lct in pairs:
            groups.setdefault(winner, []).append((loser, rule, wcl, lcl, wct, lct))

        sorted_groups = sorted(groups.items(), key=lambda g: -len(g[1]))

        print(f"合并建议：{len(pairs)} 条 → {len(groups)} 个 canonical target\n")

        for canonical, sources in sorted_groups:
            cr = row_map[canonical]
            clen = len(cr[1]) if cr[1] else 0
            cct = cr[2]
            print(f"▸ {canonical}  (认知{clen}字, append={cct})")
            for src, rule, _wcl, slen, _wct, sct in sources:
                print(f"    ← {src:<45}  [{rule}]  (认知{slen}字, append={sct})")
            print()

        # 统计
        rule_counts: dict[str, int] = {}
        for _, _, rule, _, _, _, _ in pairs:
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
        print(f"按规则统计: {rule_counts}")

        merged_sources = {p[1] for p in pairs}
        print(f"去重后合并的 source 数: {len(merged_sources)}")

        # CSV
        if args.csv:
            csv_path = Path(args.csv)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["canonical_sector", "source_sector", "rule",
                            "canonical_cognition_len", "source_cognition_len",
                            "canonical_append_count", "source_append_count"])
                for canonical, src, rule, clen, slen, cct, sct in pairs:
                    w.writerow([canonical, src, rule, clen, slen, cct, sct])
            print(f"CSV 已导出到: {csv_path}")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
