"""フォローグラフ収集パイプライン（Stage 2: フォロー拡張）"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from communities import CommunityDef
from db.models import get_session, init_db
from db.ops import (
    add_community_member,
    add_follow_edge,
    get_community_member_ids,
    upsert_user,
)
from scraping.auth import create_worker_pool


def collect_follow_graph(community: CommunityDef):
    """シードのフォローグラフを収集し、共通フォローでスコアリング"""
    print(f"\n{'='*50}")
    print(f"フォローグラフ収集: {community.name}")
    print(f"{'='*50}")

    init_db()
    session = get_session()
    workers = create_worker_pool()

    # シードメンバーを取得
    member_ids = get_community_member_ids(session, community.id, min_confidence=0.8)
    seed_screen_names = []

    for uid in member_ids:
        from db.models import User
        user = session.get(User, uid)
        if user and user.screen_name:
            seed_screen_names.append(user.screen_name)

    if not seed_screen_names:
        print("[ERROR] シードが見つかりません。先にdiscoverを実行してください")
        session.close()
        return

    # 既にフォローグラフ取得済みのユーザーをスキップ
    from sqlalchemy import func
    from db.models import FollowEdge
    already_scraped = {
        r[0] for r in
        session.query(FollowEdge.source_user_id)
        .group_by(FollowEdge.source_user_id)
        .having(func.count() > 0)
        .all()
    }
    # screen_name → user_id マッピング
    from db.models import User
    sn_to_id = {}
    for sn in seed_screen_names:
        user = session.query(User).filter(User.screen_name == sn).first()
        if user:
            sn_to_id[sn] = user.user_id

    remaining = [sn for sn in seed_screen_names if sn_to_id.get(sn) not in already_scraped]
    print(f"[INFO] シード: {len(seed_screen_names)}, 未取得: {len(remaining)}")

    if not remaining:
        print("[INFO] 全シードのフォローグラフ取得済み。スコアリングへ進みます。")
    else:
        # マルチワーカー並列でFollowing取得
        num_workers = min(len(workers), len(remaining))
        chunks = [[] for _ in range(num_workers)]
        for i, sn in enumerate(remaining):
            chunks[i % num_workers].append(sn)

        print(f"\n[Phase 1] {num_workers}ワーカー並列でFollowing取得")
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {}
            for i in range(num_workers):
                future = executor.submit(workers[i].process_following_batch, chunks[i])
                futures[future] = i + 1

            for future in as_completed(futures):
                wid = futures[future]
                try:
                    results = future.result()
                    # DBにフォローエッジを保存
                    for source_sn, following_list in results.items():
                        source_id = sn_to_id.get(source_sn, source_sn)
                        for target_sn in following_list:
                            # target_snのuser_idが不明な場合はscreen_nameを仮IDとして使用
                            target_user = session.query(User).filter(User.screen_name == target_sn).first()
                            target_id = target_user.user_id if target_user else f"sn:{target_sn}"
                            if not target_user:
                                upsert_user(session, target_id, screen_name=target_sn)
                            add_follow_edge(session, source_id, target_id)
                        session.commit()
                    print(f"  [W{wid}] 完了: {len(results)} アカウント分")
                except Exception as e:
                    print(f"  [W{wid}] エラー: {e}")

    # --- Phase 2: スコアリング ---
    print(f"\n[Phase 2] 共通フォローでスコアリング")
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

    # 既存メンバーを除外
    existing_ids = get_community_member_ids(session, community.id)
    candidates = {
        uid: count for uid, count in appearance_count.items()
        if count >= min_shared and uid not in existing_ids
    }

    # 上位からメンバー登録
    sorted_candidates = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:max_members]
    print(f"  候補: {len(candidates)}, 登録上限: {max_members}")

    added = 0
    for uid, shared in sorted_candidates:
        # confidenceは共通フォロー数に基づいて算出
        max_possible = len(seed_screen_names)
        confidence = min(shared / max(max_possible, 1) * 0.8, 0.9)  # 最大0.9（seedの1.0と区別）

        add_community_member(
            session, community.id, uid,
            confidence=confidence,
            source="follow_expansion",
            shared_follows=shared,
        )
        added += 1

    session.commit()

    # スコア分布表示
    score_dist = Counter(candidates.values())
    for score in sorted(score_dist.keys(), reverse=True)[:10]:
        print(f"    {score}共通={score_dist[score]}件", end=" ")
    print()

    print(f"\n[完了] {added} メンバー追加（共通フォロー >= {min_shared}）")
    session.close()
