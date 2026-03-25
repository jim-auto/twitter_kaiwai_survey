"""プロフィール一括取得パイプライン (stage 3)."""
from datetime import datetime, timezone

from db.models import User, get_session, init_db
from db.ops import ensure_user_rows_for_references, merge_user_ids, upsert_user
from scraping.auth import create_worker_pool
from scraping.rate_limiter import pick_best_worker


def _base_profile_query(session, community_id: str | None = None):
    query = session.query(User).filter(User.last_scraped == None)  # noqa: E711
    if community_id:
        from db.models import CommunityMember

        subq = session.query(CommunityMember.user_id).filter(
            CommunityMember.community_id == community_id
        ).subquery()
        query = query.filter(User.user_id.in_(subq))
    return query


def _resolve_missing_screen_names(session, community_id: str | None = None) -> tuple[int, int]:
    unresolved_users = (
        _base_profile_query(session, community_id)
        .filter(User.screen_name == None)  # noqa: E711
        .all()
    )
    if not unresolved_users:
        return 0, 0

    try:
        from scraping.playwright_search import resolve_screen_names_sync
    except Exception as e:
        print(f"[WARN] playwright resolver unavailable: {e}")
        return 0, len(unresolved_users)

    user_ids = [u.user_id for u in unresolved_users if u.user_id and not u.user_id.startswith("sn:")]
    if not user_ids:
        return 0, len(unresolved_users)

    print(f"[INFO] rest_id redirect解決: {len(user_ids)} ユーザー")
    try:
        resolved = resolve_screen_names_sync(user_ids)
    except Exception as e:
        print(f"[WARN] playwright resolver failed: {e}")
        return 0, len(unresolved_users)

    resolved_count = 0
    for user_id, screen_name in resolved.items():
        if not screen_name:
            continue
        upsert_user(session, user_id, screen_name=screen_name, touch_last_scraped=False)
        resolved_count += 1

    if resolved_count:
        session.commit()
    return resolved_count, len(unresolved_users) - resolved_count


def _canonicalize_sn_placeholders(session, community_id: str | None = None) -> int:
    placeholder_users = (
        _base_profile_query(session, community_id)
        .filter(User.screen_name == None)  # noqa: E711
        .filter(User.user_id.like("sn:%"))
        .all()
    )
    if not placeholder_users:
        return 0

    merged = 0
    for user in placeholder_users:
        inferred_screen_name = user.user_id[3:]
        if not inferred_screen_name:
            continue
        upsert_user(
            session,
            user.user_id,
            screen_name=inferred_screen_name,
            touch_last_scraped=False,
        )
        merged += 1

    if merged:
        session.commit()
    return merged


def collect_profiles(community_id: str = None):
    """未取得プロフィールを一括取得する。"""
    init_db()
    session = get_session()
    workers = create_worker_pool()

    created = ensure_user_rows_for_references(session, community_id=community_id)
    if created:
        session.commit()
        print(f"[INFO] placeholder users created: {created}")

    merged_placeholders = _canonicalize_sn_placeholders(session, community_id)
    if merged_placeholders:
        print(f"[INFO] sn:* placeholder canonicalized: {merged_placeholders}")

    resolved, unresolved = _resolve_missing_screen_names(session, community_id)
    if resolved:
        print(f"[INFO] rest_id -> screen_name resolved: {resolved}")
    if unresolved:
        print(f"[WARN] screen_name unresolved after redirect: {unresolved}")

    query = _base_profile_query(session, community_id)
    users_to_fetch = query.filter(User.screen_name != None).all()  # noqa: E711
    unresolved = query.filter(User.screen_name == None).count()  # noqa: E711

    print(f"[INFO] プロフィール未取得: {len(users_to_fetch)} ユーザー")
    if unresolved:
        print(f"[WARN] screen_name 不明で取得不能: {unresolved} ユーザー")

    if not users_to_fetch:
        print("[INFO] 全ユーザーのプロフィール取得済み")
        session.close()
        return

    api = pick_best_worker(workers, "UserByScreenName")
    collected = 0

    for i, user in enumerate(users_to_fetch):
        current_user_id = user.user_id
        screen_name = user.screen_name
        if not screen_name:
            continue

        user_data = api.get_user(screen_name)
        if not user_data:
            continue

        real_id = user_data["rest_id"]
        updates = {
            "screen_name": user_data["screen_name"],
            "display_name": user_data["name"],
            "bio": user_data["description"],
            "followers_count": user_data["followers_count"],
            "following_count": user_data["following_count"],
            "tweet_count": user_data["tweet_count"],
            "profile_image": user_data["profile_image_url"],
        }
        if current_user_id != real_id:
            merge_user_ids(session, current_user_id, real_id, **updates)
        else:
            upsert_user(session, real_id, **updates)
        fetched_user = session.get(User, real_id)
        if fetched_user:
            fetched_user.last_scraped = datetime.now(timezone.utc)
        collected += 1

        if (i + 1) % 50 == 0:
            session.commit()
            print(f"  [PROGRESS] {i + 1}/{len(users_to_fetch)} ({collected} 件取得)")

            remaining = api.get_rate_remaining("UserByScreenName")
            if remaining is not None and remaining < 5:
                api = pick_best_worker(workers, "UserByScreenName")

    session.commit()
    session.close()
    print(f"\n[完了] {collected} プロフィール取得")
