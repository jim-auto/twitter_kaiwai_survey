"""ユーザー参照の補修と seed 再解決"""

from __future__ import annotations

import re

from sqlalchemy import text

from communities import load_all_communities
from db.models import get_session, init_db
from db.ops import add_community_member, ensure_user_rows_for_references, upsert_user
from pipeline.collect_profiles import collect_profiles
from scraping.auth import create_worker_pool


def _count_missing_refs(session, community_id: str | None = None) -> dict[str, int]:
    member_sql = """
        SELECT COUNT(*)
        FROM community_members cm
        LEFT JOIN users u ON u.user_id = cm.user_id
        WHERE u.user_id IS NULL
    """
    follow_source_sql = """
        SELECT COUNT(*)
        FROM follow_edges fe
        LEFT JOIN users u ON u.user_id = fe.source_user_id
        WHERE u.user_id IS NULL
    """
    follow_target_sql = """
        SELECT COUNT(*)
        FROM follow_edges fe
        LEFT JOIN users u ON u.user_id = fe.target_user_id
        WHERE u.user_id IS NULL
    """

    params: dict[str, object] = {}
    if community_id:
        member_sql += " AND cm.community_id = :community_id"
        params = {"community_id": community_id}

    return {
        "community_members": session.execute(text(member_sql), params).scalar() or 0,
        "follow_edges_source": session.execute(text(follow_source_sql)).scalar() or 0,
        "follow_edges_target": session.execute(text(follow_target_sql)).scalar() or 0,
    }


def _matches_bio_patterns(bio: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if re.search(pattern, bio or "", re.IGNORECASE):
            return True
    return False


def repair_users(community_id: str | None = None, rerun_seeds: bool = True):
    """欠損 user 行を補完し、seed を再解決してプロフィール収集をやり直す。"""
    init_db()
    session = get_session()

    before = _count_missing_refs(session, community_id)
    print("[BEFORE]", before)

    created = ensure_user_rows_for_references(session, community_id=community_id)
    session.commit()
    print(f"[STEP 1] placeholder users created: {created}")

    if rerun_seeds:
        defs = [d for d in load_all_communities() if not community_id or d.id == community_id]
        workers = create_worker_pool()
        api = workers[0]
        repaired = 0

        print(f"[STEP 2] seed re-resolution: {len(defs)} communities")
        for community in defs:
            for seed in community.seeds:
                username = seed["username"]
                user_data = api.get_user(username)
                if not user_data:
                    print(f"  [SKIP] @{username}: fetch failed")
                    continue
                canonical_user = upsert_user(session, user_data["rest_id"], **{
                    "screen_name": user_data["screen_name"],
                    "display_name": user_data["name"],
                    "bio": user_data["description"],
                    "followers_count": user_data["followers_count"],
                    "following_count": user_data["following_count"],
                    "tweet_count": user_data["tweet_count"],
                    "profile_image": user_data["profile_image_url"],
                })
                add_community_member(
                    session,
                    community.id,
                    canonical_user.user_id,
                    confidence=1.0,
                    source="seed",
                    bio_match=_matches_bio_patterns(user_data["description"], community.bio_patterns),
                )
                repaired += 1
            session.commit()
        print(f"[STEP 2] seed profiles repaired: {repaired}")

    after_seed = _count_missing_refs(session, community_id)
    session.close()
    print("[AFTER SEEDS]", after_seed)

    print("[STEP 3] collect profiles")
    collect_profiles(community_id)

    session = get_session()
    after = _count_missing_refs(session, community_id)
    unresolved = session.execute(
        text("SELECT COUNT(*) FROM users WHERE last_scraped IS NULL AND screen_name IS NULL")
    ).scalar() or 0
    session.close()

    print("[AFTER]", after)
    print(f"[UNRESOLVED] screen_name unknown users: {unresolved}")
