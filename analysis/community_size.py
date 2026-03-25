"""界隈の規模メトリクス算出（SQLite直接版）"""
import sqlite3
from dataclasses import dataclass
from statistics import median

from config.settings import DB_PATH


@dataclass
class SizeMetrics:
    community_id: str
    community_name: str
    member_count: int
    active_member_count: int
    total_followers_reach: int
    median_followers: int
    influencer_count: int  # followers >= 5000
    top_influencers: list[dict]


def compute_all_sizes(min_confidence: float = 0.5) -> list[SizeMetrics]:
    """全界隈の規模を一括算出（SQLite直接、高速）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 界隈名取得
    names = {}
    for r in conn.execute("SELECT id, name FROM communities"):
        names[r["id"]] = r["name"]

    results = []
    for cid in sorted(names.keys()):
        # メンバー + ユーザー情報をJOIN
        rows = conn.execute("""
            SELECT cm.user_id,
                   u.screen_name, u.display_name, u.bio,
                   COALESCE(u.followers_count, 0) as followers_count,
                   COALESCE(u.tweet_count, 0) as tweet_count
            FROM community_members cm
            LEFT JOIN users u ON cm.user_id = u.user_id
            WHERE cm.community_id = ? AND cm.confidence >= ?
            ORDER BY u.followers_count DESC
        """, (cid, min_confidence)).fetchall()

        if not rows:
            results.append(SizeMetrics(
                community_id=cid, community_name=names[cid],
                member_count=0, active_member_count=0,
                total_followers_reach=0, median_followers=0,
                influencer_count=0, top_influencers=[],
            ))
            continue

        followers_list = [r["followers_count"] for r in rows]
        active_count = sum(1 for r in rows if r["tweet_count"] > 0)
        influencer_count = sum(1 for f in followers_list if f >= 5000)

        top = [
            {
                "screen_name": r["screen_name"] or "",
                "display_name": r["display_name"] or "",
                "followers_count": r["followers_count"],
                "bio": (r["bio"] or "")[:100],
            }
            for r in rows[:10]
        ]

        results.append(SizeMetrics(
            community_id=cid,
            community_name=names[cid],
            member_count=len(rows),
            active_member_count=active_count,
            total_followers_reach=sum(followers_list),
            median_followers=int(median(followers_list)) if followers_list else 0,
            influencer_count=influencer_count,
            top_influencers=top,
        ))

    conn.close()
    results.sort(key=lambda m: m.member_count, reverse=True)
    return results
