"""
Clean up garbage node_states introduced by merge scripts.

Identifies and removes:
  1. ARTIFACT states (core_logic LIKE '节点合并：%' / '撤销节点合并：%')
     - For MIDDLE artifacts: delete, bridge version chain gap
     - For CURRENT artifacts: delete, restore previous real state as current
  2. CONTAMINATED states (state_summary containing merge text on non-artifact states)
     - NULL out state_summary and recent_changes if they contain merge notes
  3. ZERO-WIDTH states (effective_from == effective_to)
     - Delete, bridge version chain gap
  4. Duplicate CURRENT states (two states with same effective_to=NULL for same node)
     - Keep the higher version (non-artifact), delete the lower

DRY RUN mode: set DRY_RUN=True to preview changes without committing.
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from nodes.common import get_engine, write_session

DRY_RUN = False  # Set to True to preview without committing

NOW = datetime.now(timezone.utc)


def _is_artifact(row) -> bool:
    """Check if a state is a merge/unmerge artifact (garbage)."""
    cl = (row[5] or "")  # core_logic is column index 5
    return "节点合并" in cl or "撤销节点合并" in cl or "撤销合并" in cl


def _is_contaminated(row) -> bool:
    """Non-artifact state whose summary fields contain merge text."""
    ss = (row[6] or "")  # state_summary is column index 6
    rc = (row[7] or "")  # recent_changes is column index 7
    is_artifact = _is_artifact(row)
    return not is_artifact and ("合并节点" in ss or "合并节点" in rc or "撤销合并" in ss or "撤销合并" in rc)


async def main():
    engine = get_engine()
    sf = write_session(engine)

    async with sf() as session:
        # ── Load all node_states ──
        result = await session.execute(text("""
            SELECT ns.id, ns.node_id, ns.version, ns.effective_from, ns.effective_to,
                   ns.core_logic, ns.state_summary, ns.recent_changes
            FROM node_states ns
            ORDER BY ns.node_id, ns.version
        """))
        all_rows = result.fetchall()

        # Group by node_id
        node_states: dict = {}
        for row in all_rows:
            nid = str(row[1])
            node_states.setdefault(nid, []).append(row)

        # ── Collect operations ──
        delete_ids: list[str] = []       # state IDs to delete
        fix_prev_to: list[tuple[str, str | None]] = []  # (prev_state_id, new_effective_to)
        null_summary_ids: list[str] = []  # state IDs to null state_summary/recent_changes

        stats = {"artifact_deleted": 0, "zerowidth_deleted": 0, "dup_current_deleted": 0,
                 "chain_fixed": 0, "summary_nulled": 0}

        for nid, rows in node_states.items():
            rows_by_version = {r[2]: r for r in rows}
            sorted_versions = sorted(rows_by_version.keys())

            # ── 1. Identify artifact states ──
            artifact_versions = {v for v in sorted_versions if _is_artifact(rows_by_version[v])}
            # Also add zero-width artifact-created states
            zv_artifact = {r[2] for r in rows
                           if r[3] == r[4] and (r[2] - 1) in artifact_versions}
            artifact_versions |= zv_artifact

            # ── 2. Identify zero-width states (skip artifacts, handled above) ──
            zero_width_versions = {r[2] for r in rows
                                   if r[3] == r[4] and r[2] not in artifact_versions}

            # ── 3. Identify duplicate CURRENT states ──
            current_states = [r for r in rows if r[4] is None]  # effective_to IS NULL
            if len(current_states) > 1:
                # Keep highest version, delete lower one(s)
                current_states.sort(key=lambda r: r[2], reverse=True)
                keeper = current_states[0]
                for dup in current_states[1:]:
                    # If the dup is an artifact, handle it in artifact step
                    if dup[2] in artifact_versions:
                        continue
                    # Only delete if the lower version is not an artifact
                    print(f"DELETE DUP CURRENT: node={nid} v{dup[2]} (keeping v{keeper[2]})")
                    delete_ids.append(str(dup[0]))
                    stats["dup_current_deleted"] += 1

            # ── Fix version chain after artifact/zero-width deletion ──
            to_delete = artifact_versions | zero_width_versions
            if not to_delete:
                # Still check for contamination
                for r in rows:
                    if _is_contaminated(r):
                        null_summary_ids.append(str(r[0]))
                        stats["summary_nulled"] += 1
                continue

            kept_versions = [v for v in sorted_versions if v not in to_delete]
            if not kept_versions:
                # All states are artifacts/zero-width — node will have no states
                print(f"DELETE ALL: node={nid} (all {len(sorted_versions)} states are garbage)")
                for v in sorted_versions:
                    delete_ids.append(str(rows_by_version[v][0]))
                    stats["artifact_deleted"] += 1
                continue

            # Delete each garbage state, fix chain
            for del_v in sorted(to_delete):
                del_row = rows_by_version[del_v]
                del_id = str(del_row[0])
                del_eff_to = del_row[4]  # effective_to of deleted state

                # Find prev in the post-deletion sequence
                prev_kept = max((v for v in kept_versions if v < del_v), default=None)

                tag = "ARTIFACT" if del_v in artifact_versions else "ZERO-WIDTH"
                print(f"DELETE {tag}: node={nid} v{del_v} [{del_row[3]}] -> [{del_row[4]}]")

                delete_ids.append(del_id)
                if del_v in artifact_versions:
                    stats["artifact_deleted"] += 1
                else:
                    stats["zerowidth_deleted"] += 1

                # Chain fix: bridge the time gap
                if del_eff_to is None:
                    # Was the current state — restore prev as current
                    if prev_kept is not None:
                        prev_row = rows_by_version[prev_kept]
                        fix_prev_to.append((str(prev_row[0]), None))
                        stats["chain_fixed"] += 1
                else:
                    # Was a middle state — extend prev's effective_to to cover the gap
                    if prev_kept is not None:
                        prev_row = rows_by_version[prev_kept]
                        fix_prev_to.append((str(prev_row[0]), del_eff_to))
                        stats["chain_fixed"] += 1

            # ── Contamination: null merge text from non-artifact states ──
            for r in rows:
                if r[2] in to_delete:
                    continue
                if _is_contaminated(r):
                    null_summary_ids.append(str(r[0]))
                    stats["summary_nulled"] += 1
                    print(f"NULL SUMMARY: node={nid} v{r[2]} (contaminated state_summary/recent_changes)")

        # ── Execute ──
        print()
        print(f"SUMMARY: {stats['artifact_deleted']} artifacts, "
              f"{stats['zerowidth_deleted']} zero-width, "
              f"{stats['dup_current_deleted']} dup-currents to DELETE")
        print(f"  {stats['chain_fixed']} chain fixes, {stats['summary_nulled']} summary nulls")
        print(f"  Total DELETE: {len(delete_ids)}, FIX: {len(fix_prev_to)}, NULL: {len(null_summary_ids)}")

        if DRY_RUN:
            print("\n*** DRY RUN — no changes committed ***")
        else:
            # Execute deletes
            if delete_ids:
                # Batch delete in chunks
                chunk = 100
                for i in range(0, len(delete_ids), chunk):
                    batch = delete_ids[i:i + chunk]
                    placeholders = ", ".join(f"'{did}'" for did in batch)
                    await session.execute(text(
                        f"DELETE FROM node_states WHERE id::text IN ({placeholders})"
                    ))
                print(f"DELETED {len(delete_ids)} states")

            # Execute chain fixes
            for prev_id, new_to in fix_prev_to:
                if new_to is None:
                    await session.execute(text(
                        "UPDATE node_states SET effective_to = NULL WHERE id = :id"
                    ), {"id": prev_id})
                else:
                    await session.execute(text(
                        "UPDATE node_states SET effective_to = :to WHERE id = :id"
                    ), {"id": prev_id, "to": new_to})

            # Execute summary nulls
            if null_summary_ids:
                chunk = 100
                for i in range(0, len(null_summary_ids), chunk):
                    batch = null_summary_ids[i:i + chunk]
                    placeholders = ", ".join(f"'{sid}'" for sid in batch)
                    await session.execute(text(
                        f"UPDATE node_states SET state_summary = NULL, recent_changes = NULL WHERE id::text IN ({placeholders})"
                    ))
                print(f"NULLED summaries on {len(null_summary_ids)} states")

            await session.commit()
            print("Committed.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
