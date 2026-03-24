"""界隈横断コンテンツスカウト

界隈Aのトップインフルエンサーの最新ツイートを取得し、
界隈Bへの翻案アイデアを提案するCLIツール。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.overlap import compute_follow_affinity
from db.models import Community, CommunityMember, User, get_session, init_db


def get_top_influencers(community_id: str, limit: int = 10) -> list[dict]:
    """界隈のトップインフルエンサーを取得"""
    init_db()
    session = get_session()
    rows = (
        session.query(User)
        .join(CommunityMember, User.user_id == CommunityMember.user_id)
        .filter(CommunityMember.community_id == community_id)
        .filter(CommunityMember.confidence >= 0.5)
        .order_by(User.followers_count.desc())
        .limit(limit)
        .all()
    )
    result = [
        {
            "screen_name": u.screen_name,
            "display_name": u.display_name,
            "followers_count": u.followers_count,
            "bio": (u.bio or "")[:100],
        }
        for u in rows if u.screen_name
    ]
    session.close()
    return result


def suggest_cross_community_pairs() -> list[dict]:
    """親和度の高い界隈ペアを提案"""
    affinities = compute_follow_affinity(0.5)
    init_db()
    session = get_session()
    names = {}
    for c in session.query(Community).all():
        names[c.id] = c.name
    session.close()

    pairs = []
    for a in affinities[:20]:
        pairs.append({
            "source": a.community_a,
            "source_name": names.get(a.community_a, a.community_a),
            "target": a.community_b,
            "target_name": names.get(a.community_b, a.community_b),
            "affinity": a.affinity,
            "a_to_b": a.a_follows_b_count,
            "b_to_a": a.b_follows_a_count,
        })
    return pairs


def scout(source_community: str, target_community: str):
    """界隈Aのインフルエンサーを分析し、界隈Bへの翻案を提案"""
    init_db()
    session = get_session()

    source = session.get(Community, source_community)
    target = session.get(Community, target_community)
    if not source or not target:
        print(f"界隈が見つかりません: {source_community}, {target_community}")
        session.close()
        return

    print(f"\n{'='*60}")
    print(f"コンテンツスカウト: {source.name} -> {target.name}")
    print(f"{'='*60}")

    session.close()

    # ソース界隈のトップインフルエンサー
    influencers = get_top_influencers(source_community, 10)
    print(f"\n[{source.name}] トップインフルエンサー:")
    for i, inf in enumerate(influencers, 1):
        print(f"  {i}. @{inf['screen_name']} ({inf['followers_count']:,} FL)")
        print(f"     bio: {inf['bio']}")

    # ターゲット界隈のインフルエンサー
    target_inf = get_top_influencers(target_community, 5)
    print(f"\n[{target.name}] 参考インフルエンサー:")
    for inf in target_inf[:5]:
        print(f"  @{inf['screen_name']} ({inf['followers_count']:,} FL) - {inf['bio'][:60]}")

    print(f"\n[ヒント] @ユーザー名 でx.comを確認し、バズツイートの切り口を{target.name}向けに翻案してください")
    print(f"  例: x.com/{influencers[0]['screen_name']}" if influencers else "")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="界隈横断コンテンツスカウト")
    sub = parser.add_subparsers(dest="command")

    # pairs: 親和度の高いペアを表示
    sub.add_parser("pairs", help="親和度の高い界隈ペアを表示")

    # scout: 特定ペアのインフルエンサーを表示
    scout_p = sub.add_parser("scout", help="界隈AのインフルエンサーをBに翻案")
    scout_p.add_argument("source", help="ソース界隈ID")
    scout_p.add_argument("target", help="ターゲット界隈ID")

    # influencers: 特定界隈のインフルエンサーを表示
    inf_p = sub.add_parser("influencers", help="界隈のインフルエンサーを表示")
    inf_p.add_argument("community", help="界隈ID")

    args = parser.parse_args()

    if args.command == "pairs":
        pairs = suggest_cross_community_pairs()
        print(f"\n{'='*60}")
        print("界隈間フォロー親和度 TOP20")
        print(f"{'='*60}\n")
        for p in pairs:
            direction = ""
            if p["a_to_b"] > p["b_to_a"]:
                direction = f"{p['source_name']}->{p['target_name']}"
            elif p["b_to_a"] > p["a_to_b"]:
                direction = f"{p['target_name']}->{p['source_name']}"
            else:
                direction = "双方向"
            print(f"  {p['source_name']:15s} x {p['target_name']:15s} | 親和度={p['affinity']:.6f} | {direction} ({p['a_to_b']}<->{p['b_to_a']})")

    elif args.command == "scout":
        scout(args.source, args.target)

    elif args.command == "influencers":
        influencers = get_top_influencers(args.community, 20)
        for i, inf in enumerate(influencers, 1):
            print(f"  {i:2d}. @{inf['screen_name']:20s} ({inf['followers_count']:>10,} FL) {inf['bio']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
