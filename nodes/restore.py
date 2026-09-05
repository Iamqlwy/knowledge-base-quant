"""
Restore world_nodes, world_node_edges, node_states from CSV backups.

Usage:
    uv run python nodes/restore.py <backup_dir>
    uv run python nodes/restore.py nodes/backups/20260623_120218
"""
import ast
import csv
import json
import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from nodes.common import get_engine, write_session
from sqlalchemy import text


def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    if '.' in s:
        s_clean = s.replace(' ', 'T') if 'T' not in s and ' ' in s else s
        try:
            dt = datetime.strptime(s_clean, '%Y-%m-%dT%H:%M:%S.%f%z')
        except ValueError:
            dt = datetime.strptime(s_clean, '%Y-%m-%dT%H:%M:%S.%f')
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        s_clean = s.replace(' ', 'T') if 'T' not in s and ' ' in s else s
        try:
            dt = datetime.strptime(s_clean, '%Y-%m-%dT%H:%M:%S%z')
        except ValueError:
            dt = datetime.strptime(s_clean, '%Y-%m-%dT%H:%M:%S')
            dt = dt.replace(tzinfo=timezone.utc)
    return dt


def safe_parse_json(v):
    if not v or v == 'None':
        return None
    v = v.strip()
    if v.startswith('[') and "'" in v:
        try:
            obj = ast.literal_eval(v)
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            pass
    if v.startswith('[') or v.startswith('{'):
        try:
            json.loads(v)
            return v
        except Exception:
            pass
    return None


async def restore_nodes(backup_dir: str):
    engine = get_engine()
    sf = write_session(engine)
    async with sf() as session:
        await session.execute(text('DELETE FROM world_nodes'))
        with open(f'{backup_dir}/world_nodes.csv', 'r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        batch = []
        for i, row in enumerate(rows):
            aliases = []
            v = row.get('aliases', '')
            if v:
                try:
                    aliases = ast.literal_eval(v) if v.startswith('[') else [v]
                except Exception:
                    aliases = [v]
            vals = {
                'id': row['id'],
                'name': row['name'],
                'node_type': row['node_type'],
                'description': row['description'] or '',
                'ticker': row['ticker'] or '',
                'aliases': aliases,
                'is_active': row['is_active'].strip().lower() in ('true', '1', 't'),
                'created_at': parse_dt(row['created_at']) if row.get('created_at') else None,
                'updated_at': parse_dt(row['updated_at']) if row.get('updated_at') else None,
            }
            batch.append(vals)
            if len(batch) >= 200:
                await session.execute(
                    text('INSERT INTO world_nodes (id,name,node_type,description,ticker,aliases,is_active,created_at,updated_at) '
                         'VALUES (:id,:name,:node_type,:description,:ticker,:aliases,:is_active,:created_at,:updated_at)'),
                    batch,
                )
                batch = []
                print(f'  world_nodes: {i+1}/{len(rows)} rows', file=sys.stderr)
        if batch:
            await session.execute(
                text('INSERT INTO world_nodes (id,name,node_type,description,ticker,aliases,is_active,created_at,updated_at) '
                     'VALUES (:id,:name,:node_type,:description,:ticker,:aliases,:is_active,:created_at,:updated_at)'),
                batch,
            )
        await session.commit()
        print(f'  world_nodes: {len(rows)} rows - done', file=sys.stderr)
    await engine.dispose()


async def restore_edges(backup_dir: str):
    engine = get_engine()
    sf = write_session(engine)
    async with sf() as session:
        await session.execute(text('DELETE FROM world_node_edges'))
        with open(f'{backup_dir}/world_node_edges.csv', 'r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        batch = []
        for i, row in enumerate(rows):
            vals = {
                'id': row['id'],
                'parent_node_id': row['parent_node_id'],
                'child_node_id': row['child_node_id'],
                'relationship_type': row['relationship_type'],
                'weight': float(row['weight']) if row.get('weight') else 0.5,
                'created_at': parse_dt(row['created_at']) if row.get('created_at') else None,
                'updated_at': parse_dt(row['updated_at']) if row.get('updated_at') else None,
            }
            batch.append(vals)
            if len(batch) >= 200:
                await session.execute(
                    text('INSERT INTO world_node_edges (id,parent_node_id,child_node_id,relationship_type,weight,created_at,updated_at) '
                         'VALUES (:id,:parent_node_id,:child_node_id,:relationship_type,:weight,:created_at,:updated_at)'),
                    batch,
                )
                batch = []
                print(f'  world_node_edges: {i+1}/{len(rows)} rows', file=sys.stderr)
        if batch:
            await session.execute(
                text('INSERT INTO world_node_edges (id,parent_node_id,child_node_id,relationship_type,weight,created_at,updated_at) '
                     'VALUES (:id,:parent_node_id,:child_node_id,:relationship_type,:weight,:created_at,:updated_at)'),
                batch,
            )
        await session.commit()
        print(f'  world_node_edges: {len(rows)} rows - done', file=sys.stderr)
    await engine.dispose()


async def restore_states(backup_dir: str):
    engine = get_engine()
    sf = write_session(engine)
    async with sf() as session:
        await session.execute(text('DELETE FROM node_states'))
        with open(f'{backup_dir}/node_states.csv', 'r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        batch = []
        for i, row in enumerate(rows):
            ver_raw = (row.get('version') or '').strip()
            ver = int(ver_raw) if ver_raw.isdigit() else 1
            vals = {
                'id': row['id'],
                'node_id': row['node_id'],
                'version': ver,
                'core_logic': row['core_logic'] or '',
                'state_summary': row['state_summary'] or '',
                'primary_drivers': safe_parse_json(row.get('primary_drivers', '')),
                'recent_changes': row.get('recent_changes', '') or '',
                'effective_from': parse_dt(row['effective_from']) if row.get('effective_from') else None,
                'effective_to': parse_dt(row['effective_to']) if row.get('effective_to') else None,
                'created_at': parse_dt(row['created_at']) if row.get('created_at') else None,
                'updated_at': parse_dt(row['updated_at']) if row.get('updated_at') else None,
            }
            batch.append(vals)
            if len(batch) >= 100:
                await session.execute(
                    text('INSERT INTO node_states (id,node_id,version,core_logic,state_summary,primary_drivers,recent_changes,effective_from,effective_to,created_at,updated_at) '
                         'VALUES (:id,:node_id,:version,:core_logic,:state_summary,:primary_drivers,:recent_changes,:effective_from,:effective_to,:created_at,:updated_at)'),
                    batch,
                )
                batch = []
                print(f'  node_states: {i+1}/{len(rows)} rows', file=sys.stderr)
        if batch:
            await session.execute(
                text('INSERT INTO node_states (id,node_id,version,core_logic,state_summary,primary_drivers,recent_changes,effective_from,effective_to,created_at,updated_at) '
                     'VALUES (:id,:node_id,:version,:core_logic,:state_summary,:primary_drivers,:recent_changes,:effective_from,:effective_to,:created_at,:updated_at)'),
                batch,
            )
        await session.commit()
        print(f'  node_states: {len(rows)} rows - done', file=sys.stderr)
    await engine.dispose()


async def main():
    if len(sys.argv) < 2:
        print('Usage: uv run python nodes/restore.py <backup_dir>', file=sys.stderr)
        sys.exit(1)

    backup_dir = sys.argv[1]
    if not Path(backup_dir).is_dir():
        print(f'Error: {backup_dir} is not a directory', file=sys.stderr)
        sys.exit(1)

    required = ['world_nodes.csv', 'world_node_edges.csv', 'node_states.csv']
    missing = [f for f in required if not (Path(backup_dir) / f).exists()]
    if missing:
        print(f'Error: missing files in {backup_dir}: {missing}', file=sys.stderr)
        sys.exit(1)

    print(f'Restoring from: {backup_dir}', file=sys.stderr)
    await restore_nodes(backup_dir)
    await restore_edges(backup_dir)
    await restore_states(backup_dir)
    print('Restore done', file=sys.stderr)


if __name__ == '__main__':
    asyncio.run(main())
