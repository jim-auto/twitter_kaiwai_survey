"""Bridge account analysis for cross-community and cross-cluster discovery."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from analysis.clustering import ClusterAnalysisResult, detect_affinity_clusters
from config.settings import DB_PATH


@dataclass
class MemberBridgeAccount:
    user_id: str
    screen_name: str
    display_name: str
    followers_count: int
    community_ids: list[str]
    cluster_labels: list[str]
    community_count: int
    cluster_count: int
    bridge_score: float


@dataclass
class AttentionBridgeAccount:
    user_id: str
    screen_name: str
    display_name: str
    followers_count: int
    source_community_ids: list[str]
    cluster_labels: list[str]
    source_community_count: int
    cluster_count: int
    follow_edge_count: int
    bridge_score: float
    account_category: str
    classification_reasons: list[str] = field(default_factory=list)


@dataclass
class ClusterBridgePair:
    cluster_a: str
    cluster_b: str
    account_count: int
    top_accounts: list[AttentionBridgeAccount] = field(default_factory=list)


@dataclass
class AttentionBridgeView:
    view_id: str
    label: str
    description: str
    excluded_community_ids: list[str]
    exclude_existing_members: bool
    attention_hub_count: int
    cross_cluster_attention_hub_count: int
    community_seed_count: int
    generic_hub_count: int
    category_counts: dict[str, int]
    attention_hubs: list[AttentionBridgeAccount]
    cross_cluster_attention_hubs: list[AttentionBridgeAccount]
    cluster_pairs: list[ClusterBridgePair]


@dataclass
class BridgeAccountAnalysisResult:
    min_confidence: float
    member_bridge_account_count: int
    cross_cluster_member_bridge_count: int
    member_bridges: list[MemberBridgeAccount]
    cross_cluster_member_bridges: list[MemberBridgeAccount]
    attention_hub_count: int
    cross_cluster_attention_hub_count: int
    attention_hubs: list[AttentionBridgeAccount]
    cross_cluster_attention_hubs: list[AttentionBridgeAccount]
    cluster_pairs: list[ClusterBridgePair]
    all_view: AttentionBridgeView
    no_nanpa_view: AttentionBridgeView
    frontier_view: AttentionBridgeView
    frontier_seed_view: AttentionBridgeView


OFFICIAL_TOKENS = (
    "official",
    "公式",
    "運営",
    "広報",
)

MEDIA_TOKENS = (
    "news",
    "press",
    "media",
    "magazine",
    "journal",
    "wiki",
    "topics",
    "速報",
    "ニュース",
    "新聞",
    "編集部",
    "メディア",
)


def _chunked(values: list[str], size: int = 900):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _load_community_names(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT id, name FROM communities ORDER BY id").fetchall()
    return {row[0]: row[1] for row in rows}


def _load_members(
    conn: sqlite3.Connection,
    min_confidence: float,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    rows = conn.execute(
        """
        SELECT community_id, user_id
        FROM community_members
        WHERE confidence >= ?
        """,
        (min_confidence,),
    ).fetchall()

    community_to_users: dict[str, set[str]] = defaultdict(set)
    user_to_communities: dict[str, set[str]] = defaultdict(set)
    for community_id, user_id in rows:
        community_to_users[community_id].add(user_id)
        user_to_communities[user_id].add(community_id)
    return community_to_users, user_to_communities


def _load_user_map(
    conn: sqlite3.Connection,
    user_ids: set[str],
) -> dict[str, dict[str, object]]:
    if not user_ids:
        return {}

    user_map: dict[str, dict[str, object]] = {}
    user_id_list = sorted(user_ids)
    for chunk in _chunked(user_id_list):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT user_id,
                   screen_name,
                   COALESCE(display_name, '') AS display_name,
                   COALESCE(bio, '') AS bio,
                   COALESCE(followers_count, 0) AS followers_count
            FROM users
            WHERE user_id IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for user_id, screen_name, display_name, bio, followers_count in rows:
            user_map[user_id] = {
                "user_id": user_id,
                "screen_name": screen_name,
                "display_name": display_name or "",
                "bio": bio or "",
                "followers_count": int(followers_count or 0),
            }
    return user_map


def _screen_name_for(user_id: str, user_map: dict[str, dict[str, object]]) -> str:
    record = user_map.get(user_id)
    if record and record.get("screen_name"):
        return str(record["screen_name"])
    if user_id.startswith("sn:"):
        return user_id[3:]
    return user_id


def _display_name_for(user_id: str, user_map: dict[str, dict[str, object]]) -> str:
    record = user_map.get(user_id)
    if record and record.get("display_name"):
        return str(record["display_name"])
    return ""


def _followers_for(user_id: str, user_map: dict[str, dict[str, object]]) -> int:
    record = user_map.get(user_id)
    if record:
        return int(record.get("followers_count", 0) or 0)
    return 0


def _bio_for(user_id: str, user_map: dict[str, dict[str, object]]) -> str:
    record = user_map.get(user_id)
    if record and record.get("bio"):
        return str(record["bio"])
    return ""


def _normalize_account_text(
    *,
    screen_name: str,
    display_name: str,
    bio: str,
) -> str:
    return " ".join(part for part in (screen_name, display_name, bio) if part).lower()


def _classify_attention_account(
    *,
    screen_name: str,
    display_name: str,
    bio: str,
    followers_count: int,
    source_community_count: int,
    cluster_count: int,
) -> tuple[str, list[str]]:
    normalized = _normalize_account_text(
        screen_name=screen_name,
        display_name=display_name,
        bio=bio,
    )
    reasons: list[str] = []

    if any(token in normalized for token in OFFICIAL_TOKENS):
        reasons.append("official_token")
    if any(token in normalized for token in MEDIA_TOKENS):
        reasons.append("media_token")

    if "media_token" in reasons:
        return "media_hub", reasons
    if "official_token" in reasons:
        return "official_hub", reasons
    if followers_count >= 1_000_000 and source_community_count >= 4 and cluster_count >= 2:
        reasons.append("large_cross_cluster_following")
        return "celebrity_hub", reasons
    return "community_seed", reasons


def _build_cluster_maps(
    community_names: dict[str, str],
    cluster_analysis: ClusterAnalysisResult,
) -> tuple[dict[str, str], dict[str, int]]:
    community_to_label: dict[str, str] = {}
    label_order: dict[str, int] = {}

    for order, cluster in enumerate(cluster_analysis.clusters, start=1):
        label = f"Cluster {cluster.cluster_id}"
        label_order[label] = order
        for community_id in cluster.communities:
            community_to_label[community_id] = label

    isolated_offset = len(label_order) + 1
    for index, community_id in enumerate(cluster_analysis.isolated_communities):
        label = f"Isolated / {community_names.get(community_id, community_id)}"
        label_order[label] = isolated_offset + index
        community_to_label[community_id] = label

    return community_to_label, label_order


def _score_member_bridge(
    community_count: int,
    cluster_count: int,
    followers_count: int,
) -> float:
    return round(
        (community_count * 3.5)
        + (cluster_count * 2.5)
        + (math.log10(followers_count + 10) * 1.8),
        3,
    )


def _score_attention_bridge(
    source_community_count: int,
    cluster_count: int,
    follow_edge_count: int,
    followers_count: int,
) -> float:
    return round(
        (source_community_count * 4.0)
        + (cluster_count * 6.0)
        + (math.log1p(follow_edge_count) * 3.0)
        + (math.log10(followers_count + 10) * 2.0),
        3,
    )


def _build_member_bridges(
    user_to_communities: dict[str, set[str]],
    user_map: dict[str, dict[str, object]],
    community_to_label: dict[str, str],
    label_order: dict[str, int],
) -> tuple[list[MemberBridgeAccount], list[MemberBridgeAccount]]:
    rows: list[MemberBridgeAccount] = []
    for user_id, communities in user_to_communities.items():
        if len(communities) < 2:
            continue

        sorted_communities = sorted(communities)
        cluster_labels = sorted(
            {community_to_label[community_id] for community_id in sorted_communities},
            key=lambda label: (label_order.get(label, 9999), label),
        )
        followers_count = _followers_for(user_id, user_map)
        rows.append(MemberBridgeAccount(
            user_id=user_id,
            screen_name=_screen_name_for(user_id, user_map),
            display_name=_display_name_for(user_id, user_map),
            followers_count=followers_count,
            community_ids=sorted_communities,
            cluster_labels=cluster_labels,
            community_count=len(sorted_communities),
            cluster_count=len(cluster_labels),
            bridge_score=_score_member_bridge(
                community_count=len(sorted_communities),
                cluster_count=len(cluster_labels),
                followers_count=followers_count,
            ),
        ))

    rows.sort(
        key=lambda row: (
            row.cluster_count,
            row.community_count,
            row.bridge_score,
            row.followers_count,
            row.screen_name,
        ),
        reverse=True,
    )
    cross_cluster_rows = [row for row in rows if row.cluster_count >= 2]
    return rows, cross_cluster_rows


def _load_attention_sources(
    conn: sqlite3.Connection,
    user_to_communities: dict[str, set[str]],
) -> dict[str, dict[str, object]]:
    target_map: dict[str, dict[str, object]] = {}

    for source_user_id, target_user_id in conn.execute(
        "SELECT source_user_id, target_user_id FROM follow_edges"
    ):
        source_communities = user_to_communities.get(source_user_id)
        if not source_communities:
            continue

        record = target_map.setdefault(target_user_id, {
            "user_id": target_user_id,
            "source_community_ids": set(),
            "follow_edge_count": 0,
        })
        record["source_community_ids"].update(source_communities)
        record["follow_edge_count"] += 1

    return target_map


def _materialize_attention_view(
    *,
    view_id: str,
    label: str,
    description: str,
    raw_targets: dict[str, dict[str, object]],
    user_map: dict[str, dict[str, object]],
    community_to_label: dict[str, str],
    label_order: dict[str, int],
    excluded_community_ids: set[str] | None = None,
    existing_member_user_ids: set[str] | None = None,
    exclude_existing_members: bool = False,
    min_source_community_count: int = 2,
    max_source_community_count: int | None = None,
    min_cluster_count: int = 1,
    max_cluster_count: int | None = None,
    min_follow_edge_count: int = 1,
) -> AttentionBridgeView:
    excluded_community_ids = excluded_community_ids or set()
    existing_member_user_ids = existing_member_user_ids or set()

    rows: list[AttentionBridgeAccount] = []
    for user_id, raw in raw_targets.items():
        if exclude_existing_members and user_id in existing_member_user_ids:
            continue

        source_community_ids = sorted(
            community_id
            for community_id in raw["source_community_ids"]
            if community_id not in excluded_community_ids
        )
        source_community_count = len(source_community_ids)
        if source_community_count < min_source_community_count:
            continue
        if max_source_community_count is not None and source_community_count > max_source_community_count:
            continue

        cluster_labels = sorted(
            {community_to_label[community_id] for community_id in source_community_ids},
            key=lambda label: (label_order.get(label, 9999), label),
        )
        cluster_count = len(cluster_labels)
        if cluster_count < min_cluster_count:
            continue
        if max_cluster_count is not None and cluster_count > max_cluster_count:
            continue

        follow_edge_count = int(raw["follow_edge_count"])
        if follow_edge_count < min_follow_edge_count:
            continue

        followers_count = _followers_for(user_id, user_map)
        screen_name = _screen_name_for(user_id, user_map)
        display_name = _display_name_for(user_id, user_map)
        account_category, classification_reasons = _classify_attention_account(
            screen_name=screen_name,
            display_name=display_name,
            bio=_bio_for(user_id, user_map),
            followers_count=followers_count,
            source_community_count=source_community_count,
            cluster_count=cluster_count,
        )
        rows.append(AttentionBridgeAccount(
            user_id=user_id,
            screen_name=screen_name,
            display_name=display_name,
            followers_count=followers_count,
            source_community_ids=source_community_ids,
            cluster_labels=cluster_labels,
            source_community_count=source_community_count,
            cluster_count=cluster_count,
            follow_edge_count=follow_edge_count,
            bridge_score=_score_attention_bridge(
                source_community_count=source_community_count,
                cluster_count=cluster_count,
                follow_edge_count=follow_edge_count,
                followers_count=followers_count,
            ),
            account_category=account_category,
            classification_reasons=classification_reasons,
        ))

    rows.sort(
        key=lambda row: (
            row.cluster_count,
            row.source_community_count,
            row.account_category == "community_seed",
            row.bridge_score,
            row.follow_edge_count,
            row.followers_count,
            row.screen_name,
        ),
        reverse=True,
    )
    cross_cluster_rows = [row for row in rows if row.cluster_count >= 2]
    category_counts = Counter(row.account_category for row in rows)

    pair_map: dict[tuple[str, str], list[AttentionBridgeAccount]] = defaultdict(list)
    for row in cross_cluster_rows:
        for cluster_a, cluster_b in combinations(row.cluster_labels, 2):
            pair_map[(cluster_a, cluster_b)].append(row)

    cluster_pairs: list[ClusterBridgePair] = []
    for (cluster_a, cluster_b), accounts in pair_map.items():
        cluster_pairs.append(ClusterBridgePair(
            cluster_a=cluster_a,
            cluster_b=cluster_b,
            account_count=len(accounts),
            top_accounts=sorted(
                accounts,
                key=lambda row: (
                    row.bridge_score,
                    row.follow_edge_count,
                    row.followers_count,
                    row.screen_name,
                ),
                reverse=True,
            )[:8],
        ))

    cluster_pairs.sort(
        key=lambda pair: (
            pair.account_count,
            pair.cluster_a,
            pair.cluster_b,
        ),
        reverse=True,
    )

    return AttentionBridgeView(
        view_id=view_id,
        label=label,
        description=description,
        excluded_community_ids=sorted(excluded_community_ids),
        exclude_existing_members=exclude_existing_members,
        attention_hub_count=len(rows),
        cross_cluster_attention_hub_count=len(cross_cluster_rows),
        community_seed_count=category_counts.get("community_seed", 0),
        generic_hub_count=sum(
            count for category, count in category_counts.items()
            if category != "community_seed"
        ),
        category_counts=dict(sorted(category_counts.items())),
        attention_hubs=rows,
        cross_cluster_attention_hubs=cross_cluster_rows,
        cluster_pairs=cluster_pairs,
    )


def detect_bridge_accounts(
    min_confidence: float = 0.5,
    cluster_analysis: ClusterAnalysisResult | None = None,
) -> BridgeAccountAnalysisResult:
    """Detect cross-community member bridges and shared-attention hubs."""

    if cluster_analysis is None:
        cluster_analysis = detect_affinity_clusters(min_confidence=min_confidence)

    conn = sqlite3.connect(str(DB_PATH))
    community_names = _load_community_names(conn)
    _, user_to_communities = _load_members(conn, min_confidence)
    member_user_ids = set(user_to_communities)
    raw_targets = _load_attention_sources(conn, user_to_communities)
    target_user_ids = set(raw_targets)
    user_map = _load_user_map(conn, member_user_ids | target_user_ids)
    conn.close()

    community_to_label, label_order = _build_cluster_maps(community_names, cluster_analysis)
    member_bridges, cross_cluster_member_bridges = _build_member_bridges(
        user_to_communities=user_to_communities,
        user_map=user_map,
        community_to_label=community_to_label,
        label_order=label_order,
    )

    all_view = _materialize_attention_view(
        view_id="all",
        label="All Shared-Attention Hubs",
        description="Accounts followed by multiple current communities.",
        raw_targets=raw_targets,
        user_map=user_map,
        community_to_label=community_to_label,
        label_order=label_order,
    )
    no_nanpa_view = _materialize_attention_view(
        view_id="no_nanpa",
        label="No-Nanpa Shared-Attention Hubs",
        description="Same hub ranking after removing nanpa as a source community.",
        raw_targets=raw_targets,
        user_map=user_map,
        community_to_label=community_to_label,
        label_order=label_order,
        excluded_community_ids={"nanpa"},
    )
    frontier_view = _materialize_attention_view(
        view_id="frontier",
        label="Frontier Candidates",
        description=(
            "Accounts shared by multiple non-nanpa communities but not yet current "
            "high-confidence members."
        ),
        raw_targets=raw_targets,
        user_map=user_map,
        community_to_label=community_to_label,
        label_order=label_order,
        excluded_community_ids={"nanpa"},
        existing_member_user_ids=member_user_ids,
        exclude_existing_members=True,
    )
    frontier_seed_view = _materialize_attention_view(
        view_id="frontier_seed",
        label="Frontier Seed Candidates",
        description=(
            "More specific frontier candidates: shared by 3-5 non-nanpa communities, "
            "spanning 2-3 clusters, and backed by at least 4 follow edges."
        ),
        raw_targets=raw_targets,
        user_map=user_map,
        community_to_label=community_to_label,
        label_order=label_order,
        excluded_community_ids={"nanpa"},
        existing_member_user_ids=member_user_ids,
        exclude_existing_members=True,
        min_source_community_count=3,
        max_source_community_count=5,
        min_cluster_count=2,
        max_cluster_count=3,
        min_follow_edge_count=4,
    )

    return BridgeAccountAnalysisResult(
        min_confidence=min_confidence,
        member_bridge_account_count=len(member_bridges),
        cross_cluster_member_bridge_count=len(cross_cluster_member_bridges),
        member_bridges=member_bridges,
        cross_cluster_member_bridges=cross_cluster_member_bridges,
        attention_hub_count=all_view.attention_hub_count,
        cross_cluster_attention_hub_count=all_view.cross_cluster_attention_hub_count,
        attention_hubs=all_view.attention_hubs,
        cross_cluster_attention_hubs=all_view.cross_cluster_attention_hubs,
        cluster_pairs=all_view.cluster_pairs,
        all_view=all_view,
        no_nanpa_view=no_nanpa_view,
        frontier_view=frontier_view,
        frontier_seed_view=frontier_seed_view,
    )
