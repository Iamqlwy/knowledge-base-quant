#!/bin/bash
# =============================================================================
# 数据备份脚本 — Docker 卷快照
#
# 用法:
#   bash scripts/backup_data.sh              # 备份到默认目录
#   bash scripts/backup_data.sh /path/to/dir # 备份到指定目录
#
# 输出:
#   backups/<timestamp>/pgdata.tar.gz  — PostgreSQL 全量数据卷
#   backups/<timestamp>/esdata.tar.gz  — Elasticsearch 全量数据卷
#
# 恢复:
#   停服务 → 解压到空卷 → 挂回卷 → 启服务
# =============================================================================
set -euo pipefail

# Prevent MSYS/MinGW from mangling Docker-internal paths (e.g. /source → C:/Program Files/Git/source)
export MSYS_NO_PATHCONV=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_ROOT="${1:-$SCRIPT_DIR/../backups}"
# Resolve to absolute path (Docker on Windows requires absolute paths)
BACKUP_ROOT="$(cd "$BACKUP_ROOT" 2>/dev/null && pwd || (mkdir -p "$BACKUP_ROOT" && cd "$BACKUP_ROOT" && pwd))"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TS"

echo "=== 备份到: $BACKUP_DIR ==="
mkdir -p "$BACKUP_DIR"

echo "[1/2] 备份 PostgreSQL 数据卷 (pgvector_data)..."
docker run --rm \
  -v pgvector_data:/source \
  -v "$BACKUP_DIR:/backup" \
  ubuntu:latest tar czf /backup/pgdata.tar.gz -C /source .

echo "[2/2] 备份 Elasticsearch 数据卷 (esdata)..."
docker run --rm \
  -v esdata:/source \
  -v "$BACKUP_DIR:/backup" \
  ubuntu:latest tar czf /backup/esdata.tar.gz -C /source .

echo "=== 完成 ==="
ls -lh "$BACKUP_DIR"/
