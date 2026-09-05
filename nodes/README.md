# 节点维护工作流

world_nodes 的定期维护脚本：Phase 1 扫描并合并重复/相似节点，Phase 2 为新节点发现并补齐关联边（edges）。

## 使用方式

可以通过 `nodes/workflow.js`（Claude Code workflow）驱动：

```
/工作流 nodes/workflow.js
```

也可以手动按阶段运行：

1. **Phase 0 备份**：`uv run python nodes/phase0_backup.py` — 备份 world_nodes / world_node_edges / node_states 到 `nodes/backups/`
2. **Phase 1.1 扫描**：`uv run python nodes/phase1_scan.py [--output pairs.json]` — 扫描重复/相似节点对
3. **Phase 1.2 判定**：`uv run python nodes/phase1_resolve.py <pairs.json> ...` — 对每对节点执行合并/改名/保留
4. **Phase 2 扫描**：`uv run python nodes/phase2_scan.py [--output candidates.json]` — 为新节点发现候选关联边
5. **Phase 2 判定**：`uv run python nodes/phase2_resolve.py <candidates.json> ...` — 创建/跳过候选边
6. **恢复**：`uv run python nodes/restore.py nodes/backups/<timestamp>` — 从备份目录恢复三张核心表

> 注意：运行 workflow 前必须先传入本次扫描的「新建时间」阈值（`workflow.js` 顶部的 `RUN_TS`），
> Phase 2 只扫描该时间之后创建的节点，运行结束后会写入 `nodes/last_run.json` 作为下次的起点。
