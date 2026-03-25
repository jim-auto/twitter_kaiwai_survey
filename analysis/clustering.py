"""フォロー親和度ベースの界隈クラスタ分析"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from itertools import combinations

from analysis.overlap import FollowAffinityResult, compute_follow_affinity
from config.settings import DB_PATH


@dataclass
class ClusterEdge:
    community_a: str
    community_b: str
    affinity: float
    a_follows_b_count: int
    b_follows_a_count: int


@dataclass
class ClusterRepresentative:
    user_id: str
    screen_name: str
    display_name: str
    followers_count: int
    community_ids: list[str]


@dataclass
class ClusterResult:
    cluster_id: int
    communities: list[str]
    internal_weight_sum: float
    strongest_edges: list[ClusterEdge] = field(default_factory=list)
    representative_accounts: list[ClusterRepresentative] = field(default_factory=list)


@dataclass
class ClusterAnalysisResult:
    method: str
    min_confidence: float
    min_edge_ratio: float
    min_edge_weight: float
    max_affinity: float
    isolated_communities: list[str]
    clusters: list[ClusterResult]


def _load_all_community_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT id FROM communities ORDER BY id").fetchall()
    return [row[0] for row in rows]


def _load_cluster_representatives(
    conn: sqlite3.Connection,
    community_ids: list[str],
    min_confidence: float,
    limit: int = 6,
) -> list[ClusterRepresentative]:
    if not community_ids:
        return []

    placeholders = ",".join("?" for _ in community_ids)
    rows = conn.execute(
        f"""
        SELECT cm.user_id,
               cm.community_id,
               COALESCE(u.screen_name, cm.user_id) AS screen_name,
               COALESCE(u.display_name, '') AS display_name,
               COALESCE(u.followers_count, 0) AS followers_count
        FROM community_members cm
        LEFT JOIN users u ON cm.user_id = u.user_id
        WHERE cm.confidence >= ?
          AND cm.community_id IN ({placeholders})
        """,
        (min_confidence, *community_ids),
    ).fetchall()

    candidate_map: dict[str, dict[str, object]] = {}
    for user_id, community_id, screen_name, display_name, followers_count in rows:
        candidate = candidate_map.setdefault(user_id, {
            "user_id": user_id,
            "screen_name": screen_name or user_id,
            "display_name": display_name or "",
            "followers_count": int(followers_count or 0),
            "community_ids": set(),
        })
        candidate["community_ids"].add(community_id)
        candidate["followers_count"] = max(candidate["followers_count"], int(followers_count or 0))

    candidates = list(candidate_map.values())
    selected: list[dict[str, object]] = []
    covered: set[str] = set()
    target_communities = set(community_ids)
    remaining = candidates.copy()

    while remaining and len(selected) < limit:
        remaining.sort(
            key=lambda item: (
                len(item["community_ids"] - covered),
                len(item["community_ids"]),
                item["followers_count"],
                item["screen_name"],
            ),
            reverse=True,
        )
        best = remaining.pop(0)
        new_coverage = best["community_ids"] - covered
        if not selected or new_coverage or len(selected) < min(limit, len(target_communities)):
            selected.append(best)
            covered |= best["community_ids"]
        if covered >= target_communities and len(selected) >= min(limit, len(target_communities)):
            break

    if len(selected) < limit:
        leftovers = [item for item in candidates if item not in selected]
        leftovers.sort(
            key=lambda item: (
                len(item["community_ids"]),
                item["followers_count"],
                item["screen_name"],
            ),
            reverse=True,
        )
        selected.extend(leftovers[:limit - len(selected)])

    return [
        ClusterRepresentative(
            user_id=str(item["user_id"]),
            screen_name=str(item["screen_name"]),
            display_name=str(item["display_name"]),
            followers_count=int(item["followers_count"]),
            community_ids=sorted(item["community_ids"]),
        )
        for item in selected
    ]


def detect_affinity_clusters(
    min_confidence: float = 0.5,
    min_edge_ratio: float = 0.07,
) -> ClusterAnalysisResult:
    """親和度グラフから界隈クラスタを抽出する。

    親和度最大値に対する相対しきい値で弱すぎるエッジを落とし、
    孤立ノードはクラスタリング対象から外して singleton として扱う。
    """
    try:
        import networkx as nx
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("networkx is required for clustering analysis") from exc

    conn = sqlite3.connect(str(DB_PATH))
    community_ids = _load_all_community_ids(conn)
    affinities = compute_follow_affinity(min_confidence)
    max_affinity = max((row.affinity for row in affinities), default=0.0)
    min_edge_weight = max_affinity * min_edge_ratio if max_affinity else 0.0

    graph = nx.Graph()
    graph.add_nodes_from(community_ids)

    edge_lookup: dict[frozenset[str], FollowAffinityResult] = {}
    for row in affinities:
        if row.affinity < min_edge_weight:
            continue
        graph.add_edge(
            row.community_a,
            row.community_b,
            weight=row.affinity,
        )
        edge_lookup[frozenset((row.community_a, row.community_b))] = row

    isolated = sorted(nx.isolates(graph))
    core_graph = graph.copy()
    core_graph.remove_nodes_from(isolated)

    clusters: list[ClusterResult] = []
    method = "none"
    if core_graph.number_of_edges() > 0:
        try:
            raw_clusters = nx.algorithms.community.louvain_communities(
                core_graph,
                weight="weight",
                seed=42,
            )
            method = "louvain"
        except AttributeError:
            raw_clusters = nx.algorithms.community.greedy_modularity_communities(
                core_graph,
                weight="weight",
            )
            method = "greedy_modularity"

        sorted_clusters = sorted(
            (sorted(cluster) for cluster in raw_clusters),
            key=lambda cluster: (-len(cluster), cluster),
        )
        for index, community_list in enumerate(sorted_clusters, start=1):
            cluster_edges: list[ClusterEdge] = []
            for cid_a, cid_b in combinations(community_list, 2):
                row = edge_lookup.get(frozenset((cid_a, cid_b)))
                if row is None:
                    continue
                cluster_edges.append(ClusterEdge(
                    community_a=row.community_a,
                    community_b=row.community_b,
                    affinity=row.affinity,
                    a_follows_b_count=row.a_follows_b_count,
                    b_follows_a_count=row.b_follows_a_count,
                ))

            cluster_edges.sort(key=lambda edge: edge.affinity, reverse=True)
            representatives = _load_cluster_representatives(
                conn,
                community_list,
                min_confidence=min_confidence,
            )
            clusters.append(ClusterResult(
                cluster_id=index,
                communities=community_list,
                internal_weight_sum=sum(edge.affinity for edge in cluster_edges),
                strongest_edges=cluster_edges[:5],
                representative_accounts=representatives,
            ))

    result = ClusterAnalysisResult(
        method=method,
        min_confidence=min_confidence,
        min_edge_ratio=min_edge_ratio,
        min_edge_weight=min_edge_weight,
        max_affinity=max_affinity,
        isolated_communities=isolated,
        clusters=clusters,
    )
    conn.close()
    return result
