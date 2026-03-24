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


def compute_pairwise_overlap(
    min_confidence: float = 0.5,
) -> list[OverlapResult]:
    """全界隈ペアのJaccard類似度 + 非対称重複を算出"""
    init_db()
    session = get_session()

    community_ids = get_all_community_ids(session)
    if len(community_ids) < 2:
        print("[INFO] 2つ以上の界隈が必要です")
        session.close()
        return []

    # 各界隈のメンバーIDセットをキャッシュ
    member_sets: dict[str, set[str]] = {}
    for cid in community_ids:
        member_sets[cid] = get_community_member_ids(session, cid, min_confidence)

    session.close()

    results = []
    for a, b in combinations(community_ids, 2):
        set_a = member_sets[a]
        set_b = member_sets[b]
        intersection = set_a & set_b
        union = set_a | set_b

        inter_count = len(intersection)
        union_count = len(union)

        results.append(OverlapResult(
            community_a=a,
            community_b=b,
            intersection_count=inter_count,
            union_count=union_count,
            jaccard=inter_count / union_count if union_count > 0 else 0.0,
            containment_a_in_b=inter_count / len(set_a) if set_a else 0.0,
            containment_b_in_a=inter_count / len(set_b) if set_b else 0.0,
        ))

    results.sort(key=lambda r: r.jaccard, reverse=True)
    return results


def build_overlap_matrix(
    min_confidence: float = 0.5,
) -> tuple[list[str], list[list[float]]]:
    """界隈×界隈のJaccard類似度マトリクスを構築"""
    init_db()
    session = get_session()
    community_ids = sorted(get_all_community_ids(session))

    member_sets: dict[str, set[str]] = {}
    for cid in community_ids:
        member_sets[cid] = get_community_member_ids(session, cid, min_confidence)
    session.close()

    n = len(community_ids)
    matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        matrix[i][i] = 1.0  # 自分自身との重複は1.0
        for j in range(i + 1, n):
            a = member_sets[community_ids[i]]
            b = member_sets[community_ids[j]]
            union = a | b
            jaccard = len(a & b) / len(union) if union else 0.0
            matrix[i][j] = jaccard
            matrix[j][i] = jaccard

    return community_ids, matrix


def compute_follow_affinity(min_confidence: float = 0.3) -> list[FollowAffinityResult]:
    """フォローグラフベースの界隈間親和度を算出（メモリ最適化版）

    nanpa界隈のような巨大界隈を除外してnanpa以外の界隈間で高速計算。
    nanpaは独自のfollow_graphデータが膨大なため、個別にサンプリングで処理。
    """
    import sqlite3
    from config.settings import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))

    # 界隈メンバー数
    member_counts = {}
    for cid, cnt in conn.execute(
        "SELECT community_id, COUNT(*) FROM community_members WHERE confidence>=? GROUP BY community_id",
        (min_confidence,)
    ).fetchall():
        member_counts[cid] = cnt

    community_ids = sorted(member_counts.keys())
    if len(community_ids) < 2:
        conn.close()
        return []

    # user_id → community_ids マッピング（メンバーが少ない界隈のみ高速処理）
    user_to_cids: dict[str, list[str]] = {}
    for cid in community_ids:
        rows = conn.execute(
            "SELECT user_id FROM community_members WHERE community_id=? AND confidence>=?",
            (cid, min_confidence)
        ).fetchall()
        for (uid,) in rows:
            user_to_cids.setdefault(uid, []).append(cid)

    # フォローエッジを一括メモリロード
    all_edges = conn.execute("SELECT source_user_id, target_user_id FROM follow_edges").fetchall()
    conn.close()

    pair_counts: dict[tuple[str, str], int] = {}
    for src, tgt in all_edges:
        src_cids = user_to_cids.get(src)
        if not src_cids:
            continue
        tgt_cids = user_to_cids.get(tgt)
        if not tgt_cids:
            continue
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


def build_affinity_matrix(min_confidence: float = 0.3) -> tuple[list[str], list[list[float]]]:
    """フォローベース親和度マトリクスを構築"""
    affinities = compute_follow_affinity(min_confidence)
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
