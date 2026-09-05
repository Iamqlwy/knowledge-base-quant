export const meta = {
  name: 'node-maintenance',
  description: 'Phase 1 dedup/merge similar nodes, Phase 2 discover missing edges for new nodes',
  phases: [
    { title: 'Phase 0: Backup', detail: 'Backup 3 core tables to CSV' },
    { title: 'Phase 1.1: Scan', detail: 'Scan for duplicate/similar node pairs' },
    { title: 'Phase 1.2: Resolve', detail: 'Per-pair: judge + execute MERGE/RENAME/KEEP_BOTH/SKIP' },
    { title: 'Phase 2: Scan Edges', detail: 'Multi-signal candidate edge scoring for new nodes' },
    { title: 'Phase 2: Resolve', detail: 'Per-candidate: judge + execute CREATE/SKIP' },
    { title: 'Report', detail: 'Summary + update maintenance timestamp' },
  ],
}

// ============================================================
// Set RUN_TS to the desired timestamp for all DB writes.
// It also becomes the next run's last_run
// (nodes created after this date are scanned next time).
// Leave empty to auto-generate (utcnow).
// ============================================================
const RUN_TS = "2026-04-13T00:00:00+00:00"

// ---- Schemas ----

const DEDUP_SCAN_SCHEMA = {
  type: 'object',
  properties: {
    pairs: { type: 'array', items: { type: 'object' } },
  },
  required: ['pairs'],
}

const DEDUP_DECISION_SCHEMA = {
  type: 'object',
  properties: {
    action: { type: 'string', enum: ['MERGE', 'RENAME', 'KEEP_BOTH', 'SKIP'] },
    rationale: { type: 'string' },
    survivor_id: { type: 'string' },
    victim_id: { type: 'string' },
    node_id: { type: 'string' },
    new_name: { type: 'string' },
    parent_id: { type: 'string' },
    child_id: { type: 'string' },
    edge_type: { type: 'string' },
  },
  required: ['action', 'rationale'],
}

const EDGE_SCAN_SCHEMA = {
  type: 'object',
  properties: {
    edges: { type: 'array', items: { type: 'object' } },
  },
  required: ['edges'],
}

const EDGE_DECISION_SCHEMA = {
  type: 'object',
  properties: {
    action: { type: 'string', enum: ['CREATE', 'SKIP'] },
    rationale: { type: 'string' },
    parent_id: { type: 'string' },
    child_id: { type: 'string' },
    edge_type: { type: 'string' },
    weight: { type: 'number', minimum: 0.1, maximum: 1.0 },
  },
  required: ['action', 'rationale'],
}

// ===================================================================
// Phase 0: Backup
// ===================================================================
phase('Phase 0: Backup')
const backup = await agent(
  "Run `uv run python nodes/phase0_backup.py` from the project root and report the result.",
  { label: 'backup' }
)
log('Backup: ' + (backup || 'done'))

// ---- Get a single run timestamp for all DB writes ----
let runTs = RUN_TS.trim()
if (runTs) {
  log('Run timestamp (manual): ' + runTs)
} else {
  const tsResp = await agent(
    "Run: `uv run python -c \"from nodes.common import utcnow; print(utcnow().isoformat())\"`. Return ONLY the ISO timestamp string, nothing else.",
    { label: 'get-run-ts' }
  )
  runTs = (tsResp || '').trim()
  log('Run timestamp (auto): ' + runTs)
}

// ===================================================================
// Phase 1.1: Scan duplicates
// ===================================================================
phase('Phase 1.1: Scan')
const scanResult = await agent(
  `Run \`uv run python nodes/phase1_scan.py\` from the project root.
The script prints JSONL lines (each line starting with '{' is a JSON object representing a duplicate pair).
Extract each JSONL line from the output and return them as an array in the "pairs" field.
For example, if the output contains:
  {"reason": "suffix_match_exact", "similarity": 1.0, "nodes": [...], "pair_id": "dup_0001"}
  {"reason": "name_contains", "similarity": 0.667, "nodes": [...], "pair_id": "dup_0002"}
Then return {"pairs": [<first object>, <second object>]}.`,
  { schema: DEDUP_SCAN_SCHEMA, label: 'scan-dupes' }
)
const pairs = (scanResult && scanResult.pairs) ? scanResult.pairs : []

if (pairs.length === 0) {
  log('Phase 1.1: No duplicate pairs found — skipping Phase 1')
} else {
  log(`Phase 1.1: Found ${pairs.length} candidate pairs`)
  pairs.forEach(p => {
    const a = p.nodes[0], b = p.nodes[1]
    log(`  ${p.pair_id} [${p.reason}] "${a.name}" (${a.node_type}) <-> "${b.name}" (${b.node_type})`)
  })

  // ===================================================================
  // Phase 1.2: Resolve — pipeline each pair through judge agent
  // ===================================================================
  phase('Phase 1.2: Resolve')

  const p1Results = await pipeline(
    pairs,
    async (pair) => {
      const a = pair.nodes[0], b = pair.nodes[1]

      const decision = await agent(
        `Decide whether these two nodes are duplicates of the same thing.

## Node A
- ID: ${a.id}   Name: ${a.name}   Type: ${a.node_type}
- Description: ${a.description}
- Aliases: ${JSON.stringify(a.aliases)}   Ticker: ${a.ticker || 'none'}
- Edge count: ${a.edge_count}   States: ${a.state_count}   Attachments: ${a.attachment_count}

## Node B
- ID: ${b.id}   Name: ${b.name}   Type: ${b.node_type}
- Description: ${b.description}
- Aliases: ${JSON.stringify(b.aliases)}   Ticker: ${b.ticker || 'none'}
- Edge count: ${b.edge_count}   States: ${b.state_count}   Attachments: ${b.attachment_count}

## Detection: ${pair.reason}

## MERGE rules — MUST merge:
1. **exact_name_type** or **suffix_match_exact** → MERGE. "光伏"+"光伏行业"→merge.
2. **Place-prefixed concept vs pure sector** → MERGE into sector. "重庆空天信息"+"空天信息板块"→merge.
3. **Concept is sub-aspect of sector** → MERGE into sector. "具身智能开源生态"+"具身智能板块"→merge. "电动自行车换电"+"电动自行车板块"→merge.
4. **Same name different suffix** (sector vs concept) → MERGE to canonical name.
5. **Company sub-topic that is minor/experimental** → MERGE into company. "OpenAI广告业务"+"OpenAI"→merge.

## SKIP rules — do NOT merge:
1. Different companies with different tickers ("石四药集团" vs "石药集团", "招商局港口" vs "招商港口")
2. Different entity types ("中国银行" vs "中国银行业协会")
3. Company subsidiaries listed separately — different ticker = different entity ("比亚迪" vs "比亚迪电子" → SKIP, but consider KEEP_BOTH)
4. Ambiguous / unclear → SKIP

## KEEP_BOTH: distinct nodes with hierarchical relationship ("特斯拉"+"特斯拉Optimus")
## RENAME: redundant suffix on same thing ("太空光伏概念"→"太空光伏")

Output your decision.`,
        { schema: DEDUP_DECISION_SCHEMA, label: `judge:${a.name}<->${b.name}` }
      )

      return { pair, decision }
    }
  )

  // Execute each decision
  log('Executing decisions...')
  const p1Execs = await parallel(
    p1Results.filter(r => r && r.decision).map(r =>
      () => (async () => {
        const { pair, decision } = r
        const a = pair.nodes[0], b = pair.nodes[1]

        if (decision.action === 'SKIP') {
          log(`  SKIP: "${a.name}" <-> "${b.name}" — ${decision.rationale}`)
          return { ...r, executed: { status: 'skipped' } }
        }

        if (decision.action === 'MERGE') {
          const sid = decision.survivor_id || a.id
          const vid = decision.victim_id || (sid === a.id ? b.id : a.id)
          log(`  MERGE: ${vid.slice(0,8)} -> ${sid.slice(0,8)} — ${decision.rationale}`)
          const exec = await agent(
            `Execute this merge. Run in bash:
\`\`\`
uv run python -c "
import asyncio, sys
sys.path.insert(0, '.')
from nodes.common import get_engine
from nodes.phase1_resolve import execute_merge

async def run():
    engine = get_engine()
    result = await execute_merge('${sid}', '${vid}', engine, ts='${runTs}')
    await engine.dispose()
    import json
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(run())
"
\`\`\`
Return the raw JSON output.`,
            { label: `exec:merge` }
          )
          let executed
          try { executed = JSON.parse(exec.trim()) } catch { executed = { status: 'error', raw: exec } }
          return { ...r, executed }
        }

        if (decision.action === 'RENAME') {
          const nid = decision.node_id
          const newName = decision.new_name
          log(`  RENAME: ${nid.slice(0,8)} -> "${newName}"`)
          const exec = await agent(
            `Execute this rename. Run in bash:
\`\`\`
uv run python -c "
import asyncio, sys
sys.path.insert(0, '.')
from nodes.common import get_engine
from nodes.phase1_resolve import execute_rename

async def run():
    engine = get_engine()
    result = await execute_rename('${nid}', '${newName}', engine, ts='${runTs}')
    await engine.dispose()
    import json
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(run())
"
\`\`\`
Return the raw JSON output.`,
            { label: `exec:rename` }
          )
          let executed
          try { executed = JSON.parse(exec.trim()) } catch { executed = { status: 'error', raw: exec } }
          return { ...r, executed }
        }

        if (decision.action === 'KEEP_BOTH') {
          const pid = decision.parent_id
          const cid = decision.child_id
          if (!pid || !cid) {
            log(`  KEEP_BOTH: SKIP (missing parent_id or child_id in decision) — ${decision.rationale}`)
            return { ...r, executed: { status: 'skipped', reason: 'missing_ids' } }
          }
          // [note] KEEP_BOTH execution disabled — requires pipline fix, use auto-merge only
          log(`  KEEP_BOTH: (skipped — execution pending) — ${decision.rationale}`)
          return { ...r, executed: { status: 'skipped', reason: 'keep_both_exec_pending' } }
        }

        return { ...r, executed: { status: 'unknown_action', action: decision.action } }
      })()
    )
  )

  // Summary
  const p1Merges = p1Execs.filter(r => r?.decision?.action === 'MERGE').length
  const p1Renames = p1Execs.filter(r => r?.decision?.action === 'RENAME').length
  const p1KeepBoth = p1Execs.filter(r => r?.decision?.action === 'KEEP_BOTH').length
  const p1Skips = p1Execs.filter(r => r?.decision?.action === 'SKIP').length

  log('')
  log('=== Phase 1 Summary ===')
  log(`  MERGE: ${p1Merges}  RENAME: ${p1Renames}  KEEP_BOTH: ${p1KeepBoth}  SKIP: ${p1Skips}`)
}

// ===================================================================
// Phase 2: Edge candidates
// ===================================================================
phase('Phase 2: Scan Edges')
const edgeScan = await agent(
  `Run \`uv run python nodes/phase2_scan.py\` from the project root.
The script prints JSONL lines (each line starting with '{' is a JSON candidate edge object).
Extract each JSONL line from the output and return them as an array in the "edges" field.
For example, if the output contains:
  {"new_node": {...}, "candidate_node": {...}, "proposed": {...}}
  {"new_node": {...}, "candidate_node": {...}, "proposed": {...}}
Then return {"edges": [<first object>, <second object>]}.`,
  { schema: EDGE_SCAN_SCHEMA, label: 'scan-edges' }
)
const edgeLines = (edgeScan && edgeScan.edges) ? edgeScan.edges : []

if (edgeLines.length === 0) {
  log('Phase 2: No candidate edges found — skipping')
} else {
  log(`Phase 2: ${edgeLines.length} candidate edges found`)

  phase('Phase 2: Resolve')

  // Strip scores before judging — agent should decide on semantics only
  const cleanEdgeLines = edgeLines.map(({ scores, ...rest }) => rest)

  const p2Results = await pipeline(
    cleanEdgeLines,
    async (cand) => {
      const decision = await agent(
        `Decide whether to create this edge for a new node with no existing connections.

## New node
- ID: ${cand.new_node.id}   Name: ${cand.new_node.name}   Type: ${cand.new_node.type}
- Description: ${cand.new_node.description}

## Candidate
- ID: ${cand.candidate_node.id}   Name: ${cand.candidate_node.name}   Type: ${cand.candidate_node.type}
- Description: ${cand.candidate_node.description || '(none)'}

## Proposed edge
- Direction: ${cand.proposed.parent_id === cand.new_node.id ? cand.new_node.name + ' -> ' + cand.candidate_node.name : cand.candidate_node.name + ' -> ' + cand.new_node.name}
- Type: ${cand.proposed.edge_type}

## Key edge types and their correct direction (PARENT -> CHILD):
- **belongs_to**: company->sector, sector->macro_theme, product->sector
- **classified_as**: concept->sector
- **competes_in**: company->company (bi-directional)
- **based_in**: company->region, institution->region
- **led_by**: company->person (公司->创始人), institution->person
- **affiliated_with**: person->institution
- **has_business_segment**: company->product, company->concept
- **regulated_by**: sector->policy, company->policy

## Judgment criteria:
- Look at BOTH node descriptions. If either is blank/empty, the candidate is a guess — require stronger evidence.
- company<->company: do they compete in the same market? Different industry = SKIP.
- company->sector: does the company operate in that sector? Missing description = SKIP.
- sector->sector: is one a clear sub-domain of the other? Superficial text overlap (both mention "AI") is not enough.
- If the proposed direction is reversed, flip parent_id and child_id in your output to correct it — then CREATE.
- weight 0.3-0.5 = weak evidence, only use when names or descriptions are explicitly cross-referencing.
- weight 0.5-0.7 = moderate evidence.
- weight 0.7+ = strong explicit evidence (e.g. description names the other entity).
- If neither name NOR description shows a clear link, SKIP.

Output your decision.`,
        { schema: EDGE_DECISION_SCHEMA, label: `edge:${cand.new_node.name}->${cand.candidate_node.name}` }
      )
      return { candidate: cand, decision }
    }
  )

  // Execute
  log('Executing edge decisions...')
  const p2Execs = await parallel(
    p2Results.filter(r => r && r.decision).map(r =>
      () => (async () => {
        const { candidate: cand, decision } = r
        if (decision.action === 'CREATE' && decision.weight >= 0.3) {
          const pid = decision.parent_id || cand.proposed.parent_id
          const cid = decision.child_id || cand.proposed.child_id
          const etype = decision.edge_type || cand.proposed.edge_type
          const weight = decision.weight || 0.5
          log(`  CREATE: "${cand.new_node.name}" --(${etype}, w=${weight})--> "${cand.candidate_node.name}"`)
          const exec = await agent(
            `Run in bash:
\`\`\`
uv run python nodes/phase2_resolve.py --create '${pid}' '${cid}' '${etype}' ${weight} '${runTs}'
\`\`\`
Return the raw JSON output.`,
            { label: `exec:edge${cand.new_node.name.slice(0,10)}` }
          )
          try { const parsed = JSON.parse(exec.trim()); return { ...r, executed: parsed } }
          catch { return { ...r, executed: { status: 'error', raw: exec } } }
        }
        // SKIP or low-confidence
        if (decision.weight < 0.3) log(`  SKIP (low confidence ${decision.weight}): "${cand.new_node.name}" -> "${cand.candidate_node.name}"`)
        return { ...r, executed: { status: 'skipped' } }
      })()
    )
  )
  const created = p2Execs.filter(r => r?.executed && r.executed.status !== 'skipped' && r.executed.status !== 'error').length
  const skipped = p2Execs.filter(r => r?.executed?.status === 'skipped').length
  log(`Phase 2 edges: ${created} created, ${skipped} skipped`)
}

// ===================================================================
// Final: Report + update timestamp
// ===================================================================
phase('Report')
const updateTs = await agent(
  `Set last_run to the current UTC time and record the stats from this run. Run in bash:
\`\`\`
uv run python -c "
import json, sys
sys.path.insert(0, '.')
from nodes.common import save_maintenance_state, load_maintenance_state
state = load_maintenance_state()
state['last_run'] = '${runTs}'
state['phase1_pairs_processed'] = ${pairs.length}
state['phase2_edges_created'] = ${typeof p2Execs !== 'undefined' ? p2Execs.filter(r => r?.executed && r.executed.status !== 'skipped' && r.executed.status !== 'error').length : 0}
state['phase1_merges'] = ${typeof p1Execs !== 'undefined' ? p1Execs.filter(r => r?.decision?.action === 'MERGE').length : 0}
state['phase1_renames'] = ${typeof p1Execs !== 'undefined' ? p1Execs.filter(r => r?.decision?.action === 'RENAME').length : 0}
state['phase1_skips'] = ${typeof p1Execs !== 'undefined' ? p1Execs.filter(r => r?.decision?.action === 'SKIP').length : 0}
state['phase1_keep_both'] = ${typeof p1Execs !== 'undefined' ? p1Execs.filter(r => r?.decision?.action === 'KEEP_BOTH').length : 0}
save_maintenance_state(state)
print('Maintenance state saved to nodes/last_run.json — last_run=' + state['last_run'])
"
\`\`\`
Return the output.`,
  { label: 'update-ts' }
)
log('Final report: ' + (updateTs || ''))
log('Done.')
