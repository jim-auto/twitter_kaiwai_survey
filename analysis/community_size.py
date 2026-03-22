"""界隈の規模メトリクス算出"""
from dataclasses import dataclass
from statistics import median

from sqlalchemy import func

from db.models import CommunityMember, User, get_session, init_db


@dataclass
class SizeMetrics:
    community_id: str
    community_name: str
    member_count: int
    active_member_count: int  # tweet_count > 0
    total_followers_reach: int
    median_followers: int
    influencer_count: int  # followers >= 5000
    top_influencers: list[dict]


def compute_size(community_id: str, min_confidence: float = 0.5) -> SizeMetrics:
    """界隈の規模メトリクスを算出"""
    init_db()
    session = get_session()

    from db.models import Community
    community = session.get(Community, community_id)
    if not community:
        raise ValueError(f"界隈 '{community_id}' が見つかりません")

    # メンバー + ユーザー情報をJOIN
    rows = (
        session.query(User, CommunityMember.confidence)
        .join(CommunityMember, User.user_id == CommunityMember.user_id)
        .filter(CommunityMember.community_id == community_id)
        .filter(CommunityMember.confidence >= min_confidence)
        .all()
    )

    if not rows:
        session.close()
        return SizeMetrics(
            community_id=community_id, community_name=community.name,
            member_count=0, active_member_count=0,
            total_followers_reach=0, median_followers=0,
            influencer_count=0, top_influencers=[],
        )

    followers_list = [u.followers_count or 0 for u, _ in rows]
    active_count = sum(1 for u, _ in rows if (u.tweet_count or 0) > 0)
    influencer_count = sum(1 for f in followers_list if f >= 5000)

    # トップインフルエンサー
    sorted_users = sorted(rows, key=lambda x: x[0].followers_count or 0, reverse=True)
    top = [
        {
            "screen_name": u.screen_name,
            "display_name": u.display_name,
            "followers_count": u.followers_count,
            "bio": (u.bio or "")[:100],
        }
        for u, _ in sorted_users[:10]
    ]

    metrics = SizeMetrics(
        community_id=community_id,
        community_name=community.name,
        member_count=len(rows),
        active_member_count=active_count,
        total_followers_reach=sum(followers_list),
        median_followers=int(median(followers_list)) if followers_list else 0,
        influencer_count=influencer_count,
        top_influencers=top,
    )

    session.close()
    return metrics


def compute_all_sizes(min_confidence: float = 0.5) -> list[SizeMetrics]:
    """全界隈の規模を算出"""
    from db.ops import get_all_community_ids
    init_db()
    session = get_session()
    community_ids = get_all_community_ids(session)
    session.close()

    results = []
    for cid in community_ids:
        metrics = compute_size(cid, min_confidence)
        results.append(metrics)

    results.sort(key=lambda m: m.member_count, reverse=True)
    return results
