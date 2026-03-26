"""界隈間の重複分析"""
from dataclasses import dataclass, field
from itertools import combinations

from db.models import CommunityMember, FollowEdge, get_session, init_db
from db.ops import get_all_community_ids, get_community_member_ids


@dataclass
class OverlapResult:
    community_a: str
    community_b: str
    intersection_count: int
    union_count: int
    jaccard: float
    containment_a_in_b: float  # A の何% が B にもいるか
    containment_b_in_a: float  # B の何% が A にもいるか


@dataclass
class FollowAffinityResult:
    community_a: str
    community_b: str
    a_follows_b_count: int  # A のメンバーが B のメンバーをフォローしている数
    b_follows_a_count: int
    a_follows_b_ratio: float  # A→B のフォロー率
    b_follows_a_ratio: float
    affinity: float  # 双方向平均


def _load_member_sets(min_confidence: float = 0.5) -> dict[str, set[str]]:
    """SQLite直接でメンバーIDセットを取得"""
    import sqlite3

    from config.settings import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    member_sets: dict[str, set[str]] = {}
    for cid, uid in conn.execute(
        "SELECT community_id, user_id FROM community_members WHERE confidence >= ?",
        (min_confidence,),
    ).fetchall():
        member_sets.setdefault(cid, set()).add(uid)
    conn.close()
    return member_sets


def compute_pairwise_overlap(
    min_confidence: float = 0.5,
) -> list[OverlapResult]:
    """全界隈ペアのJaccard類似度 + 非対称重複を算出"""
    member_sets = _load_member_sets(min_confidence)
    community_ids = sorted(member_sets.keys())
    if len(community_ids) < 2:
        return []

    results = []
    for a, b in combinations(community_ids, 2):
        set_a = member_sets[a]
        set_b = member_sets[b]
        intersection = set_a & set_b
        union = set_a | set_b
        inter_count = len(intersection)
        if inter_count == 0:
            continue
        union_count = len(union)
        results.append(OverlapResult(
            community_a=a, community_b=b,
            intersection_count=inter_count, union_count=union_count,
            jaccard=inter_count / union_count if union_count > 0 else 0.0,
            containment_a_in_b=inter_count / len(set_a) if set_a else 0.0,
            containment_b_in_a=inter_count / len(set_b) if set_b else 0.0,
        ))
    results.sort(key=lambda r: (r.jaccard, r.intersection_count), reverse=True)
    return results


def build_overlap_matrix(
    min_confidence: float = 0.5,
) -> tuple[list[str], list[list[float]]]:
    """界隈×界隈のJaccard類似度マトリクスを構築"""
    member_sets = _load_member_sets(min_confidence)
    community_ids = sorted(member_sets.keys())
    n = len(community_ids)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            a = member_sets[community_ids[i]]
            b = member_sets[community_ids[j]]
            union = a | b
            jaccard = len(a & b) / len(union) if union else 0.0
            matrix[i][j] = jaccard
            matrix[j][i] = jaccard
    return community_ids, matrix


def compute_follow_affinity(min_confidence: float = 0.3) -> list[FollowAffinityResult]:
    """フォローグラフベースの界隈間親和度（SQLフィルタ + 純Python高速版）

    98Kのnanpa内部エッジをSQL段階で除外し、残りの小さいエッジセットで計算。
    """
    import sqlite3

    from config.settings import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))

    # 界隈メンバー数
    member_counts = {}
    for cid, cnt in conn.execute(
        "SELECT community_id, COUNT(*) FROM community_members WHERE confidence>=? GROUP BY community_id",
        (min_confidence,),
    ).fetchall():
        member_counts[cid] = cnt

    community_ids = sorted(member_counts.keys())
    if len(community_ids) < 2:
        conn.close()
        return []

    # user_id → community_ids（複数界隈所属対応）
    user_to_cids: dict[str, list[str]] = {}
    for cid in community_ids:
        for (uid,) in conn.execute(
            "SELECT user_id FROM community_members WHERE community_id=? AND confidence>=?",
            (cid, min_confidence),
        ).fetchall():
            user_to_cids.setdefault(uid, []).append(cid)

    # 「2+界隈に所属」または「nanpa以外の界隈に所属」のuser_idだけを関連ユーザーとする
    # nanpa-onlyのユーザー（98K+エッジの原因）を除外
    relevant_users = set()
    for uid, cids in user_to_cids.items():
        if len(cids) > 1 or cids[0] != "nanpa":
            relevant_users.add(uid)

    # フォローエッジをフィルタして読み込み（src/tgtの片方が関連ユーザー）
    all_edges = conn.execute("SELECT source_user_id, target_user_id FROM follow_edges").fetchall()
    conn.close()

    pair_counts: dict[tuple[str, str], int] = {}
    for src, tgt in all_edges:
        if src not in relevant_users:
            continue
        tgt_cids = user_to_cids.get(tgt)
        if not tgt_cids:
            continue
        src_cids = user_to_cids[src]
        for sc in src_cids:
            for tc in tgt_cids:
                if sc != tc:
                    pair_counts[(sc, tc)] = pair_counts.get((sc, tc), 0) + 1

    results = []
    for a, b in combinations(community_ids, 2):
        a_to_b = pair_counts.get((a, b), 0)
        b_to_a = pair_counts.get((b, a), 0)
        if a_to_b == 0 and b_to_a == 0:
            continue
        max_possible = max(member_counts.get(a, 1) * member_counts.get(b, 1), 1)
        a_ratio = a_to_b / max_possible
        b_ratio = b_to_a / max_possible
        affinity = (a_ratio + b_ratio) / 2
        results.append(FollowAffinityResult(
            community_a=a, community_b=b,
            a_follows_b_count=a_to_b, b_follows_a_count=b_to_a,
            a_follows_b_ratio=a_ratio, b_follows_a_ratio=b_ratio,
            affinity=affinity,
        ))

    results.sort(key=lambda r: r.affinity, reverse=True)
    return results


def build_affinity_matrix_from_results(
    affinities: list[FollowAffinityResult],
) -> tuple[list[str], list[list[float]]]:
    """既に計算済みの親和度結果からマトリクスを構築"""
    import sqlite3

    from config.settings import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    community_ids = sorted(r[0] for r in conn.execute("SELECT id FROM communities").fetchall())
    conn.close()

    idx = {cid: i for i, cid in enumerate(community_ids)}
    n = len(community_ids)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0

    for r in affinities:
        i = idx.get(r.community_a)
        j = idx.get(r.community_b)
        if i is not None and j is not None:
            matrix[i][j] = r.affinity
            matrix[j][i] = r.affinity

    return community_ids, matrix


def build_affinity_matrix(min_confidence: float = 0.3) -> tuple[list[str], list[list[float]]]:
    """フォローベース親和度マトリクスを構築"""
    affinities = compute_follow_affinity(min_confidence)
    return build_affinity_matrix_from_results(affinities)
