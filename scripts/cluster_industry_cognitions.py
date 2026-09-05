#!/usr/bin/env python3
"""
基于 embedding 的 industry_cognitions 重复主题检测。

策略：
  1. 从数据库读取所有 industry_cognitions 记录
  2. 对 sector (标题) 和 cognition_text (经验) 分别生成 embedding
  3. 加权合并标题与经验的 embedding，得到综合向量
  4. 使用余弦相似度 + Union-Find 聚类（等价于 single-linkage 层次聚类）
  5. 输出每个簇（即重复/相似主题组），以及簇内相似度统计
  6. 结果保存为 CSV + JSON

用法：
  python scripts/cluster_industry_cognitions.py
  python scripts/cluster_industry_cognitions.py --threshold 0.80 --output data/cluster_results
"""

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from kbquant.config import settings

# ─────────────────────────── 配置 ───────────────────────────
DEFAULT_THRESHOLD = 0.78       # 余弦相似度阈值（高于此值归为同簇）
TITLE_WEIGHT = 0.4             # 标题 embedding 权重
EXPERIENCE_WEIGHT = 0.6        # 经验 embedding 权重
BATCH_SIZE = 50                # embedding 批处理大小
MIN_TEXT_LEN_FOR_EMBED = 4     # 最短文本长度，低于此用零向量


# ═══════════════════════════ 向量工具 ═══════════════════════════

def l2_normalize(arr: np.ndarray) -> np.ndarray:
    """批量 L2 归一化 (N, dim)"""
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return arr / norms


def combine_embeddings(
    title_vecs: np.ndarray,
    exp_vecs: np.ndarray,
    title_w: float,
    exp_w: float,
) -> np.ndarray:
    """加权合并并重新归一化"""
    title_norm = l2_normalize(title_vecs)
    exp_norm = l2_normalize(exp_vecs)
    merged = title_w * title_norm + exp_w * exp_norm
    return l2_normalize(merged)


# ═══════════════════════════ Union-Find 聚类 ═══════════════════════════

class UnionFind:
    """并查集 — 用于 single-linkage 聚类"""

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, x: int, y: int):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1

    def groups(self) -> dict[int, list[int]]:
        result: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self.parent)):
            result[self.find(i)].append(i)
        return dict(result)


def cluster_by_cosine_similarity(
    vecs: np.ndarray,
    threshold: float,
) -> tuple[dict[int, list[int]], int]:
    """
    Single-linkage 聚类：余弦相似度 >= threshold 的样本对合并到同一簇。

    等价于用余弦距离做 single-linkage 层次聚类，然后按阈值切割。
    时间复杂度 O(N^2)，对于几百个 topic 完全够用。

    Args:
        vecs: 已归一化的向量矩阵 (N, dim)
        threshold: 余弦相似度阈值

    Returns:
        (groups, merge_count): 聚类分组 和 合并次数
    """
    n = len(vecs)
    uf = UnionFind(n)

    # 两两比较余弦相似度（已归一化，直接点积 = 余弦相似度）
    merge_count = 0
    for i in range(n):
        # 批量计算第 i 个向量与后面所有向量的相似度
        sims = vecs[i] @ vecs[i + 1:].T  # shape: (n - i - 1,)
        for offset, sim in enumerate(sims):
            if float(sim) >= threshold:
                uf.union(i, i + 1 + offset)
                merge_count += 1

    return uf.groups(), merge_count


# ═══════════════════════════ Embedding 缓存 ═══════════════════════════

def _texts_hash(texts: list[str], model: str) -> str:
    """对输入文本列表 + 模型名做哈希，用于判断缓存是否过期。"""
    h = hashlib.sha256(model.encode())
    for t in texts:
        h.update(t.encode())
    return h.hexdigest()[:16]


def load_cached_embeddings(
    cache_dir: Path,
    texts: list[str],
    model: str,
    tag: str,
) -> np.ndarray | None:
    """
    尝试从磁盘加载缓存的 embedding。

    Args:
        cache_dir: 缓存目录
        texts: 当前输入文本列表
        model: embedding 模型名
        tag: 标识，如 "title" 或 "exp"

    Returns:
        缓存的 embedding array，或 None（缓存不存在/已过期）
    """
    meta_path = cache_dir / f"{tag}_meta.json"
    npy_path = cache_dir / f"{tag}_embeddings.npy"

    if not meta_path.exists() or not npy_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        current_hash = _texts_hash(texts, model)
        if meta.get("hash") != current_hash:
            print(f"  缓存已过期 (hash 不匹配)，将重新生成 {tag} embeddings")
            return None
        if meta.get("model") != model:
            print(f"  模型已变更 ({meta.get('model')} → {model})，将重新生成")
            return None

        arr = np.load(npy_path)
        if arr.shape[0] != len(texts):
            print(f"  缓存数量不匹配 ({arr.shape[0]} vs {len(texts)})，将重新生成")
            return None

        print(f"  ✓ 从缓存加载 {tag} embeddings: {npy_path}  "
              f"(shape={arr.shape}, 生成于 {meta.get('created_at', '?')})")
        return arr
    except Exception as e:
        print(f"  ⚠ 加载缓存失败，将重新生成: {e}")
        return None


def save_embeddings_cache(
    cache_dir: Path,
    embeddings: np.ndarray,
    texts: list[str],
    model: str,
    tag: str,
):
    """将 embedding 保存到磁盘。"""
    cache_dir.mkdir(parents=True, exist_ok=True)

    npy_path = cache_dir / f"{tag}_embeddings.npy"
    np.save(npy_path, embeddings)

    meta_path = cache_dir / f"{tag}_meta.json"
    meta = {
        "hash": _texts_hash(texts, model),
        "model": model,
        "num_records": len(texts),
        "dimension": int(embeddings.shape[1]) if embeddings.ndim > 1 else 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已保存 {tag} embeddings 到: {npy_path}  (shape={embeddings.shape})")


# ═══════════════════════════ Embedding 生成 ═══════════════════════════

async def generate_embeddings(
    texts: list[str],
    batch_size: int = BATCH_SIZE,
) -> np.ndarray:
    """调用 embedding API 批量生成向量，返回 numpy array (N, dim)。"""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.embedding_api_key or settings.siliconflow_api_key,
        base_url=settings.embedding_base_url,
    )
    model = settings.embedding_model
    dim = settings.embedding_dimension

    all_embeddings: list[list[float]] = []
    total = len(texts)

    for start in range(0, total, batch_size):
        batch = texts[start : start + batch_size]
        # 过滤空文本
        safe_batch = [
            t if len(t.strip()) >= MIN_TEXT_LEN_FOR_EMBED else "空"
            for t in batch
        ]
        try:
            resp = await client.embeddings.create(model=model, input=safe_batch)
            embs = [d.embedding for d in resp.data]
        except Exception as e:
            print(f"  ⚠ embedding batch [{start}:{start + batch_size}] 失败: {e}")
            print(f"    尝试逐条重试...")
            embs = []
            for t in safe_batch:
                try:
                    r = await client.embeddings.create(model=model, input=[t])
                    embs.append(r.data[0].embedding)
                except Exception as e2:
                    print(f"    ⚠ 单条也失败，使用零向量: {e2}")
                    embs.append([0.0] * dim)
                await asyncio.sleep(0.1)  # 限流

        all_embeddings.extend(embs)
        done = min(start + batch_size, total)
        print(f"  embedding 进度: {done}/{total}")

    await client.close()
    return np.array(all_embeddings, dtype=np.float32)


# ═══════════════════════════ 主流程 ═══════════════════════════

async def main():
    parser = argparse.ArgumentParser(
        description="基于 embedding 聚类检测 industry_cognitions 重复主题"
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"余弦相似度阈值（高于此值归为同簇），默认 {DEFAULT_THRESHOLD}"
    )
    parser.add_argument(
        "--title-weight", type=float, default=TITLE_WEIGHT,
        help=f"标题 embedding 权重，默认 {TITLE_WEIGHT}"
    )
    parser.add_argument(
        "--output", type=str, default="data/cluster_results",
        help="输出目录，默认 data/cluster_results"
    )
    parser.add_argument(
        "--cache-dir", type=str, default="data/cluster_results/embeddings_cache",
        help="embedding 缓存目录，默认 data/cluster_results/embeddings_cache"
    )
    parser.add_argument(
        "--force-recompute", action="store_true",
        help="强制重新计算 embedding，忽略缓存"
    )
    args = parser.parse_args()

    threshold = args.threshold
    title_w = args.title_weight
    exp_w = 1.0 - title_w
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    force_recompute = args.force_recompute

    # ── 1. 从数据库读取数据 ──
    print("=" * 70)
    print("Industry Cognitions 重复主题检测 (Embedding 聚类)")
    print("=" * 70)
    print(f"  余弦相似度阈值: {threshold}")
    print(f"  标题/经验权重:  {title_w}/{exp_w}")
    print(f"  Embedding 模型: {settings.embedding_model}")

    url = settings.database_read_url or settings.database_url
    if not url:
        raise RuntimeError("未提供数据库连接 URL")
    if "asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1) \
                 .replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(
        url, echo=False,
        connect_args={"server_settings": {"application_name": "cluster_cognitions"}},
    )

    try:
        async with AsyncSession(engine) as session:
            result = await session.execute(text(
                "SELECT id, sector, cognition_text, append_count, "
                "created_at, updated_at FROM industry_cognitions ORDER BY sector"
            ))
            rows = result.fetchall()

        if not rows:
            print("表为空，无数据可分析")
            return

        print(f"\n共 {len(rows)} 条 industry_cognition 记录\n")

    finally:
        await engine.dispose()

    # ── 2. 准备文本 ──
    records: list[dict] = []
    for r in rows:
        rid, sector, cog_text, append_count, created_at, updated_at = r
        records.append({
            "id": str(rid),
            "sector": sector,
            "cognition_text": cog_text or "",
            "append_count": append_count,
            "created_at": str(created_at) if created_at else "",
            "updated_at": str(updated_at) if updated_at else "",
        })

    title_texts = [r["sector"] for r in records]
    # 经验文本：截取前 500 字避免超 token；空文本用 sector 兜底
    exp_texts = [
        r["cognition_text"][:500] if r["cognition_text"] else r["sector"]
        for r in records
    ]

    # ── 3. 生成 / 加载 embeddings ──
    model_name = settings.embedding_model

    print("── 标题 embeddings ──")
    title_embeddings = None
    if not force_recompute:
        title_embeddings = load_cached_embeddings(cache_dir, title_texts, model_name, "title")
    if title_embeddings is None:
        print("  调用 API 生成...")
        title_embeddings = await generate_embeddings(title_texts)
        save_embeddings_cache(cache_dir, title_embeddings, title_texts, model_name, "title")
    print(f"  shape: {title_embeddings.shape}")

    print("\n── 经验 embeddings ──")
    exp_embeddings = None
    if not force_recompute:
        exp_embeddings = load_cached_embeddings(cache_dir, exp_texts, model_name, "exp")
    if exp_embeddings is None:
        print("  调用 API 生成...")
        exp_embeddings = await generate_embeddings(exp_texts)
        save_embeddings_cache(cache_dir, exp_embeddings, exp_texts, model_name, "exp")
    print(f"  shape: {exp_embeddings.shape}")

    # ── 4. 加权合并 ──
    print(f"\n── 加权合并 (标题权重={title_w}, 经验权重={exp_w}) ──")
    combined = combine_embeddings(title_embeddings, exp_embeddings, title_w, exp_w)
    print(f"  combined shape: {combined.shape}")

    # ── 5. 聚类 ──
    print(f"\n── Single-linkage 聚类 (余弦相似度阈值={threshold}) ──")

    t0 = time.time()
    groups, merge_count = cluster_by_cosine_similarity(combined, threshold)
    cluster_time = time.time() - t0

    n_clusters = len(groups)
    print(f"  检测到 {n_clusters} 个聚类 (共 {len(records)} 条记录, "
          f"合并 {merge_count} 对, 耗时 {cluster_time:.2f}s)")

    # ── 6. 组织聚类结果 ──
    cluster_stats: list[dict] = []
    for cluster_id, member_indices in groups.items():
        members = [records[i] for i in member_indices]

        # 计算簇内平均/最小余弦相似度
        if len(members) == 1:
            avg_sim = 1.0
            min_sim = 1.0
        else:
            member_vecs = combined[member_indices]  # (k, dim)
            # 两两相似度矩阵
            sim_matrix = member_vecs @ member_vecs.T  # (k, k)
            # 取上三角（不含对角线）
            k = len(member_indices)
            sims: list[float] = []
            for i in range(k):
                for j in range(i + 1, k):
                    sims.append(float(sim_matrix[i, j]))
            avg_sim = sum(sims) / len(sims) if sims else 1.0
            min_sim = min(sims) if sims else 1.0

        # 选择 canonical：cognition_text 最长的；若都空则名称最长的
        canonical = max(members, key=lambda m: (len(m["cognition_text"]), len(m["sector"])))

        cluster_stats.append({
            "cluster_id": cluster_id,
            "size": len(members),
            "avg_similarity": round(avg_sim, 4),
            "min_similarity": round(min_sim, 4),
            "canonical_sector": canonical["sector"],
            "canonical_id": canonical["id"],
            "canonical_cognition_len": len(canonical["cognition_text"]),
            "members": members,
            "member_indices": member_indices,
        })

    # 按大小排序 (大簇在前)，大小相同按相似度排序
    cluster_stats.sort(key=lambda c: (-c["size"], -c["avg_similarity"]))

    # ── 7. 打印结果 ──
    print("\n" + "=" * 70)
    print("聚类结果")
    print("=" * 70)

    dup_count = sum(1 for c in cluster_stats if c["size"] > 1)
    dup_members = sum(c["size"] for c in cluster_stats if c["size"] > 1)
    isolated_count = sum(1 for c in cluster_stats if c["size"] == 1)
    print(f"\n重复簇数:       {dup_count}")
    print(f"涉及记录数:     {dup_members}")
    print(f"孤立主题数:     {isolated_count}")

    print("\n── 重复簇 (size > 1) ──\n")
    for cs in cluster_stats:
        if cs["size"] <= 1:
            continue
        print(f"▸ 簇 #{cs['cluster_id']}  (size={cs['size']}, "
              f"avg_sim={cs['avg_similarity']:.3f}, "
              f"min_sim={cs['min_similarity']:.3f})")
        print(f"  推荐 canonical: 「{cs['canonical_sector']}」 "
              f"(认知{cs['canonical_cognition_len']}字)")
        for m in cs["members"]:
            marker = " ★" if m["sector"] == cs["canonical_sector"] else ""
            cog_preview = (
                m["cognition_text"][:60].replace("\n", " ")
                if m["cognition_text"] else "(空)"
            )
            print(f"    - {m['sector']:<40} "
                  f"认知{len(m['cognition_text']):>5}字  "
                  f"append={m['append_count']:>2}  "
                  f"{cog_preview}{marker}")
        print()

    print("\n── 孤立主题 (size = 1, 无重复) ──\n")
    for cs in cluster_stats:
        if cs["size"] > 1:
            continue
        m = cs["members"][0]
        print(f"  · {m['sector']}")

    # ── 8. 保存结果 ──
    print(f"\n── 保存结果到 {output_dir} ──")

    # 8a. CSV — 每条记录的聚类归属
    csv_path = output_dir / "cluster_assignments.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "cluster_id", "sector", "cognition_text_len", "append_count",
            "is_canonical", "cluster_size", "avg_similarity",
            "canonical_sector", "updated_at",
        ])
        for cs in cluster_stats:
            for m in cs["members"]:
                w.writerow([
                    cs["cluster_id"],
                    m["sector"],
                    len(m["cognition_text"]),
                    m["append_count"],
                    m["sector"] == cs["canonical_sector"],
                    cs["size"],
                    cs["avg_similarity"],
                    cs["canonical_sector"],
                    m["updated_at"],
                ])
    print(f"  ✓ 聚类归属表: {csv_path}")

    # 8b. CSV — 合并建议 (仅重复簇, source → canonical)
    merge_csv = output_dir / "merge_suggestions.csv"
    with open(merge_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "canonical_sector", "source_sector", "cluster_id",
            "avg_similarity", "canonical_cognition_len", "source_cognition_len",
        ])
        for cs in cluster_stats:
            if cs["size"] <= 1:
                continue
            for m in cs["members"]:
                if m["sector"] == cs["canonical_sector"]:
                    continue
                w.writerow([
                    cs["canonical_sector"],
                    m["sector"],
                    cs["cluster_id"],
                    cs["avg_similarity"],
                    cs["canonical_cognition_len"],
                    len(m["cognition_text"]),
                ])
    print(f"  ✓ 合并建议表: {merge_csv}")

    # 8c. JSON — 完整聚类结果
    json_path = output_dir / "cluster_results.json"
    json_data = {
        "meta": {
            "total_records": len(records),
            "total_clusters": n_clusters,
            "duplicate_clusters": dup_count,
            "duplicate_members": dup_members,
            "isolated_topics": isolated_count,
            "threshold": threshold,
            "title_weight": title_w,
            "experience_weight": exp_w,
            "embedding_model": settings.embedding_model,
        },
        "clusters": [
            {
                "cluster_id": cs["cluster_id"],
                "size": cs["size"],
                "avg_similarity": cs["avg_similarity"],
                "min_similarity": cs["min_similarity"],
                "canonical_sector": cs["canonical_sector"],
                "canonical_id": cs["canonical_id"],
                "canonical_cognition_len": cs["canonical_cognition_len"],
                "members": [
                    {
                        "id": m["id"],
                        "sector": m["sector"],
                        "cognition_text": m["cognition_text"],
                        "cognition_text_len": len(m["cognition_text"]),
                        "append_count": m["append_count"],
                        "is_canonical": m["sector"] == cs["canonical_sector"],
                    }
                    for m in cs["members"]
                ],
            }
            for cs in cluster_stats
        ],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 完整结果 JSON: {json_path}")

    # 8d. 两两相似度矩阵 (仅存 sim >= 0.5 的对)
    sim_path = output_dir / "pairwise_similarities.csv"
    n = len(records)
    # 批量计算全部两两相似度
    full_sim_matrix = combined @ combined.T  # (N, N)
    with open(sim_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["sector_a", "sector_b", "cosine_similarity"])
        for i in range(n):
            for j in range(i + 1, n):
                s = float(full_sim_matrix[i, j])
                if s >= 0.5:
                    w.writerow([records[i]["sector"], records[j]["sector"], round(s, 4)])
    print(f"  ✓ 两两相似度表 (sim>=0.5): {sim_path}")

    # ── 9. 统计摘要 ──
    print("\n" + "=" * 70)
    print("统计摘要")
    print("=" * 70)
    print(f"  总记录数:            {len(records)}")
    print(f"  聚类数:              {n_clusters}")
    print(f"  重复簇 (size>1):     {dup_count}")
    print(f"  涉及重复记录:        {dup_members}")
    print(f"  孤立主题 (size=1):   {isolated_count}")
    print(f"  余弦相似度阈值:      {threshold}")
    print(f"  标题/经验权重:       {title_w}/{exp_w}")
    print(f"  Embedding 模型:      {settings.embedding_model}")

    # 最大的几个簇
    print(f"\n  最大的 10 个簇:")
    for cs in cluster_stats[:10]:
        if cs["size"] <= 1:
            break
        names = [m["sector"] for m in cs["members"]]
        print(f"    簇#{cs['cluster_id']} ({cs['size']}个, "
              f"sim={cs['avg_similarity']:.3f}): {', '.join(names)}")

    print(f"\n✅ 所有结果已保存到: {output_dir}/")


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")
