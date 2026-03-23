"""sokusuu-rankingの既存データをインポート"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.models import init_db, get_session
from db.ops import add_community_member, add_follow_edge, upsert_community, upsert_user

SOKUSUU_DIR = Path(__file__).resolve().parent.parent / "sokusuu-ranking" / "data"


def import_sokusuu_data():
    init_db()
    session = get_session()

    # ナンパ界隈のcommunityを確認/作成
    upsert_community(session, "nanpa", name="ナンパ界隈", description="sokusuu-rankingからインポート")
    session.commit()

    # 1. sokusuu_accounts.json からユーザープロフィールをインポート
    accounts_path = SOKUSUU_DIR / "sokusuu_accounts.json"
    if accounts_path.exists():
        with open(accounts_path, "r", encoding="utf-8") as f:
            accounts = json.load(f)
        print(f"[INFO] sokusuu_accounts.json: {len(accounts)} accounts")

        imported = 0
        for acc in accounts:
            username = acc.get("username", "")
            if not username:
                continue
            user_id = f"sn:{username}"  # screen_name based ID
            upsert_user(session, user_id,
                        screen_name=username,
                        display_name=acc.get("display_name", ""),
                        bio=acc.get("bio", ""),
                        followers_count=acc.get("followers_count", 0),
                        profile_image=acc.get("profile_image_url", ""))
            add_community_member(session, "nanpa", user_id,
                                 confidence=0.8, source="import_sokusuu")
            imported += 1

        session.commit()
        print(f"  -> {imported} users imported as nanpa members")

    # 2. follow_graph.json からフォローエッジをインポート
    graph_path = SOKUSUU_DIR / "follow_graph.json"
    if graph_path.exists():
        with open(graph_path, "r", encoding="utf-8") as f:
            graph = json.load(f)
        print(f"[INFO] follow_graph.json: {len(graph)} source accounts")

        edges = 0
        for source_sn, following_list in graph.items():
            source_id = f"sn:{source_sn}"
            # source userが存在しなければ作成
            upsert_user(session, source_id, screen_name=source_sn)
            add_community_member(session, "nanpa", source_id,
                                 confidence=0.6, source="import_graph")

            for target_sn in following_list:
                target_id = f"sn:{target_sn}"
                upsert_user(session, target_id, screen_name=target_sn)
                add_follow_edge(session, source_id, target_id)
                edges += 1

            if edges % 5000 == 0:
                session.commit()
                print(f"  [PROGRESS] {edges} edges...")

        session.commit()
        print(f"  -> {edges} follow edges imported")

    # 3. 共通フォローでスコアリング（nanpa界隈を拡張）
    print("\n[Scoring] Computing shared follows for nanpa expansion...")
    from db.models import FollowEdge, CommunityMember

    # 既存のnanpaメンバー（confidence >= 0.6）のfollowingを集計
    nanpa_members = (
        session.query(CommunityMember)
        .filter(CommunityMember.community_id == "nanpa")
        .filter(CommunityMember.confidence >= 0.6)
        .all()
    )
    member_ids = {m.user_id for m in nanpa_members}
    print(f"  Nanpa seed members: {len(member_ids)}")

    appearance_count = Counter()
    for mid in member_ids:
        edges_q = session.query(FollowEdge).filter(FollowEdge.source_user_id == mid).all()
        for e in edges_q:
            if e.target_user_id not in member_ids:
                appearance_count[e.target_user_id] += 1

    min_shared = 5  # sokusuu data is dense, use higher threshold
    candidates = {uid: c for uid, c in appearance_count.items() if c >= min_shared}
    print(f"  Candidates with shared >= {min_shared}: {len(candidates)}")

    added = 0
    for uid, shared in sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:5000]:
        confidence = min(shared / max(len(member_ids), 1) * 0.8, 0.9)
        add_community_member(session, "nanpa", uid,
                             confidence=confidence, source="import_expansion",
                             shared_follows=shared)
        added += 1

    session.commit()
    session.close()
    print(f"  -> {added} additional members via shared follows")
    print("\n[Done] sokusuu-ranking import complete")


if __name__ == "__main__":
    import_sokusuu_data()
