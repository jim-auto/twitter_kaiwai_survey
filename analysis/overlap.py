"""界隈間の重複分析"""
from dataclasses import dataclass
from itertools import combinations

from db.models import get_session, init_db
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
