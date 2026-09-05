"""
Read all worldnodes names and detect duplicate/similar names.
Example: "光伏" vs "光伏板块" — one is a substring of the other.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kbquant.database import read_async_session
from kbquant.models.world_node import WorldNode
from sqlalchemy import select


async def main():
    async with read_async_session() as session:
        result = await session.execute(
            select(WorldNode.name, WorldNode.node_type, WorldNode.id, WorldNode.is_active)
            .order_by(WorldNode.name)
        )
        rows = result.all()

    print(f"Total nodes: {len(rows)}")
    print()

    names = [(r[0], r[1], r[2], r[3]) for r in rows]

    # Group by name for exact duplicates
    name_counts: dict[str, list] = {}
    for name, ntype, nid, active in names:
        name_counts.setdefault(name, []).append((ntype, nid, active))

    exact_dupes = {k: v for k, v in name_counts.items() if len(v) > 1}
    if exact_dupes:
        print("=" * 70)
        print("EXACT DUPLICATES (same name, different rows):")
        print("=" * 70)
        for name, entries in exact_dupes.items():
            print(f"  [{name}] appears {len(entries)} times:")
            for ntype, nid, active in entries:
                print(f"    id={nid}  type={ntype}  active={active}")
        print()
    else:
        print("No exact duplicates found.")
        print()

    # Near-duplicate detection: name A is substring of name B
    print("=" * 70)
    print("NEAR-DUPLICATES (one name is a substring of another):")
    print("=" * 70)

    unique_names = sorted(set(n[0] for n in names))
    found_pairs = []

    for i, short in enumerate(unique_names):
        for j, long in enumerate(unique_names):
            if i == j:
                continue
            if short in long and short != long:
                found_pairs.append((short, long))

    if found_pairs:
        for short, long in found_pairs:
            short_info = name_counts[short]
            long_info = name_counts[long]
            print(f"  [{short}] ⊂ [{long}]")
            print(f"    {short}: types={[s[0] for s in short_info]}")
            print(f"    {long}:  types={[s[0] for s in long_info]}")
            print()
    else:
        print("  None found.")

    print(f"Total near-duplicate pairs: {len(found_pairs)}")


if __name__ == "__main__":
    asyncio.run(main())
