"""
Leiden community detection on the entity graph (entities + entity_relationships).

Loads all entities and their relationships from the DB, builds an undirected
weighted igraph, runs the Leiden algorithm, and reports community structure.
"""

import asyncio
from collections import defaultdict

import igraph as ig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kbquant.config import settings


async def load_graph():
    """Load entities (nodes) and relationships (edges) from the database."""
    engine = create_async_engine(settings.database_url)
    dbsession = async_sessionmaker(engine, class_=AsyncSession)

    async with dbsession() as session:
        # Load all entities
        r = await session.execute(
            text("SELECT id, name, entity_type FROM entities")
        )
        nodes = {row[0]: {"name": row[1], "type": row[2]} for row in r.fetchall()}
        print(f"Loaded {len(nodes)} entities")

        # Load all relationships
        r = await session.execute(
            text(
                """SELECT source_entity_id, target_entity_id, relationship_type, strength
                   FROM entity_relationships"""
            )
        )
        edges = []
        orphan_count = 0
        for row in r.fetchall():
            src, tgt, rel_type, strength = row
            if src not in nodes or tgt not in nodes:
                orphan_count += 1
                continue
            edges.append((src, tgt, rel_type, float(strength or 1.0)))

        if orphan_count:
            print(f"Skipped {orphan_count} edges referencing deleted entities")
        print(f"Loaded {len(edges)} relationships")

        await session.close()
    await engine.dispose()
    return nodes, edges


def build_igraph(nodes, edges):
    """Build an undirected weighted igraph."""
    node_ids = list(nodes.keys())
    id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

    g = ig.Graph(n=len(node_ids))

    g.vs["db_id"] = node_ids
    g.vs["name"] = [nodes[nid]["name"] for nid in node_ids]
    g.vs["entity_type"] = [nodes[nid]["type"] for nid in node_ids]

    edge_list = []
    edge_weights = []
    edge_rel_types = []
    for src, tgt, rel_type, strength in edges:
        edge_list.append((id_to_idx[src], id_to_idx[tgt]))
        edge_weights.append(strength)
        edge_rel_types.append(rel_type)

    if edge_list:
        g.add_edges(edge_list)
        g.es["weight"] = edge_weights
        g.es["rel_type"] = edge_rel_types

    print(f"igraph: {g.vcount()} vertices, {g.ecount()} edges")

    # Connectivity summary
    components = g.connected_components()
    giant = max(components.sizes())
    print(f"Connected components: {len(components.sizes())} (giant={giant})")

    return g


def run_leiden(g, resolution=1.0):
    partition = g.community_leiden(
        objective_function="modularity",
        weights="weight" if g.ecount() > 0 else None,
        resolution=resolution,
    )
    return partition


def summarize(partition, g, resolution, top_n_per_community=8):
    membership = partition.membership
    n_communities = len(set(membership))
    modularity = partition.modularity

    print(f"\n{'=' * 70}")
    print(f"Leiden Community Detection Results")
    print(f"{'=' * 70}")
    print(f"Resolution:   {resolution}")
    print(f"Modularity:   {modularity:.6f}")
    print(f"Communities:  {n_communities}")
    print(f"Vertices:     {g.vcount()}")
    print(f"Edges:        {g.ecount()}")

    communities = defaultdict(list)
    for idx, comm_id in enumerate(membership):
        communities[comm_id].append(idx)

    sorted_comms = sorted(communities.items(), key=lambda x: len(x[1]), reverse=True)

    # Size distribution
    sizes = [len(v) for v in communities.values()]
    print(f"\nCommunity size distribution:")
    size_buckets = defaultdict(int)
    for s in sizes:
        if s == 1:
            size_buckets["size=1 (isolated)"] += 1
        elif s <= 5:
            size_buckets["size=2-5"] += 1
        elif s <= 20:
            size_buckets["size=6-20"] += 1
        elif s <= 100:
            size_buckets["size=21-100"] += 1
        else:
            size_buckets["size>100"] += 1
    for bucket in ["size=1 (isolated)", "size=2-5", "size=6-20", "size=21-100", "size>100"]:
        if bucket in size_buckets:
            print(f"  {bucket}: {size_buckets[bucket]}")

    print(f"\n--- Top 25 Communities ---")
    for rank, (comm_id, member_idxs) in enumerate(sorted_comms[:25]):
        size = len(member_idxs)

        # Type breakdown
        type_counts = defaultdict(int)
        for idx in member_idxs:
            type_counts[g.vs[idx]["entity_type"]] += 1
        type_str = ", ".join(f"{t}:{c}" for t, c in sorted(type_counts.items(), key=lambda x: -x[1])[:5])

        # Internal edge density
        if size > 1:
            sub = g.subgraph(member_idxs)
            internal_edges = sub.ecount()
            max_possible = size * (size - 1) // 2
            density = internal_edges / max_possible if max_possible > 0 else 0
            density_str = f"edges={internal_edges} density={density:.4f}"
        else:
            density_str = ""

        # Representative members — pick the ones with highest degree within the community
        members = []
        if size > 1:
            sub = g.subgraph(member_idxs)
            degrees = sub.degree()
            # Sort by degree desc, pick top
            top_indices = sorted(range(len(member_idxs)), key=lambda i: degrees[i], reverse=True)
            for i in top_indices[:top_n_per_community]:
                orig_idx = member_idxs[i]
                members.append(f"{g.vs[orig_idx]['name']}({g.vs[orig_idx]['entity_type']})")
        else:
            members = [f"{g.vs[member_idxs[0]]['name']}({g.vs[member_idxs[0]]['entity_type']})"]

        member_str = " | ".join(members)
        if size > top_n_per_community:
            member_str += f" | ... +{size - top_n_per_community} more"

        print(f"\n  C{rank+1}: size={size} {type_str} {density_str}")
        print(f"    {member_str}")

    return n_communities, modularity, sorted_comms


async def main(resolution: float = 1.0):
    nodes, edges = await load_graph()
    g = build_igraph(nodes, edges)
    partition = run_leiden(g, resolution=resolution)
    summarize(partition, g, resolution)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Leiden community detection on entity graph")
    parser.add_argument("--resolution", type=float, default=1.0,
                        help="Resolution parameter (default 1.0, higher = smaller communities)")
    args = parser.parse_args()
    asyncio.run(main(resolution=args.resolution))
