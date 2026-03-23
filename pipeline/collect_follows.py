"""フォローグラフ収集パイプライン（Stage 2: フォロー拡張）"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from communities import CommunityDef
from db.models import CommunityMember, FollowEdge, User, get_session, init_db
from db.ops import (
    add_community_member,
    add_follow_edge,
    get_community_member_ids,
    upsert_user,
)
from scraping.auth import create_worker_pool


def collect_follow_graph(community: CommunityDef, max_seeds: int = 10):
    """メンバーのフォローグラフを収集し、共通フォローでスコアリング"""
    print(f"\n{'='*50}")
    print(f"Follow graph: {community.name}")
    print(f"{'='*50}")

    init_db()
    session = get_session()
    workers = create_worker_pool()

    # フォロー取得対象: confidence >= 0.3 かつ following_count > 100 の上位メンバー
    rows = (
        session.query(User, CommunityMember.confidence)
        .join(CommunityMember, User.user_id == CommunityMember.user_id)
        .filter(CommunityMember.community_id == community.id)
        .filter(CommunityMember.confidence >= 0.3)
        .filter(User.following_count > 100)
        .order_by(User.followers_count.desc())
        .limit(max_seeds)
        .all()
    )

    seed_screen_names = [u.screen_name for u, _ in rows if u.screen_name]
    sn_to_id = {u.screen_name: u.user_id for u, _ in rows if u.screen_name}

    if not seed_screen_names:
        print("[SKIP] Following > 100 のメンバーがいません")
        session.close()
        return

    # 既にフォローグラフ取得済みをスキップ
    from sqlalchemy import func
    already_scraped = {
        r[0] for r in
        session.query(FollowEdge.source_user_id)
        .group_by(FollowEdge.source_user_id)
        .having(func.count() > 0)
        .all()
    }

    remaining = [sn for sn in seed_screen_names if sn_to_id.get(sn) not in already_scraped]
    print(f"[INFO] Seeds: {len(seed_screen_names)}, remaining: {len(remaining)}")

    if remaining:
        num_workers = min(len(workers), len(remaining))
        chunks = [[] for _ in range(num_workers)]
        for i, sn in enumerate(remaining):
            chunks[i % num_workers].append(sn)

        print(f"\n[Phase 1] {num_workers} workers fetching Following lists")
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {}
            for i in range(num_workers):
                if chunks[i]:
                    future = executor.submit(workers[i].process_following_batch, chunks[i])
                    futures[future] = i + 1

            for future in as_completed(futures):
                wid = futures[future]
                try:
                    results = future.result()
                    for source_sn, following_list in results.items():
                        source_id = sn_to_id.get(source_sn, source_sn)
                        for target_sn in following_list:
                            target_user = session.query(User).filter(User.screen_name == target_sn).first()
                            target_id = target_user.user_id if target_user else f"sn:{target_sn}"
                            if not target_user:
                                upsert_user(session, target_id, screen_name=target_sn)
                            add_follow_edge(session, source_id, target_id)
                        session.commit()
                    print(f"  [W{wid}] done: {len(results)} accounts")
                except Exception as e:
                    print(f"  [W{wid}] error: {e}")

    # --- Phase 2: スコアリング ---
    print(f"\n[Phase 2] Scoring by shared follows")
    min_shared = community.expansion.get("min_shared_follows", 3)
    max_members = community.expansion.get("max_members", 5000)

    # 全シードのfollowingを集計
    appearance_count = Counter()
    for sn in seed_screen_names:
        source_id = sn_to_id.get(sn)
        if not source_id:
            continue
        edges = session.query(FollowEdge).filter(FollowEdge.source_user_id == source_id).all()
        for edge in edges:
            appearance_count[edge.target_user_id] += 1

    existing_ids = get_community_member_ids(session, community.id)
    # min_shared を動的調整（シード少ない場合は2に下げる）
    effective_min = min(min_shared, max(2, len(seed_screen_names) // 3))
    candidates = {
        uid: count for uid, count in appearance_count.items()
        if count >= effective_min and uid not in existing_ids
    }

    sorted_candidates = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:max_members]
    print(f"  Candidates: {len(candidates)} (min_shared={effective_min})")

    added = 0
    for uid, shared in sorted_candidates:
        max_possible = len(seed_screen_names)
        confidence = min(shared / max(max_possible, 1) * 0.8, 0.9)
        add_community_member(
            session, community.id, uid,
            confidence=confidence,
            source="follow_expansion",
            shared_follows=shared,
        )
        added += 1

    session.commit()

    # スコア分布
    score_dist = Counter(candidates.values())
    for score in sorted(score_dist.keys(), reverse=True)[:10]:
        print(f"    {score}shared={score_dist[score]}", end=" ")
    if score_dist:
        print()

    print(f"\n[Done] +{added} members (shared >= {effective_min})")
    session.close()
