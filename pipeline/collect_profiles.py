"""プロフィール一括収集パイプライン（Stage 3）"""
from datetime import datetime, timezone

from db.models import User, get_session, init_db
from db.ops import upsert_user
from scraping.auth import create_worker_pool
from scraping.rate_limiter import pick_best_worker


def collect_profiles(community_id: str = None):
    """未取得プロフィールを一括収集"""
    init_db()
    session = get_session()
    workers = create_worker_pool()

    # プロフィール未取得のユーザーを抽出
    query = session.query(User).filter(User.last_scraped == None)
    if community_id:
        from db.models import CommunityMember
        subq = session.query(CommunityMember.user_id).filter(
            CommunityMember.community_id == community_id
        ).subquery()
        query = query.filter(User.user_id.in_(subq))

    users_to_fetch = query.all()
    # sn:プレフィックスのユーザー（screen_nameから仮IDで作成されたもの）のみ対象
    users_to_fetch = [u for u in users_to_fetch if u.user_id.startswith("sn:")]

    print(f"[INFO] プロフィール未取得: {len(users_to_fetch)} ユーザー")

    if not users_to_fetch:
        print("[INFO] 全ユーザーのプロフィール取得済み")
        session.close()
        return

    api = pick_best_worker(workers, "UserByScreenName")
    collected = 0

    for i, user in enumerate(users_to_fetch):
        screen_name = user.screen_name
        if not screen_name:
            continue

        user_data = api.get_user(screen_name)
        if not user_data:
            continue

        real_id = user_data["rest_id"]

        # 仮ID → 実IDへの移行
        if user.user_id != real_id:
            # 実IDのユーザーが既に存在するか確認
            existing = session.get(User, real_id)
            if not existing:
                user.user_id = real_id
            # 存在する場合はスキップ（重複）

        user.screen_name = user_data["screen_name"]
        user.display_name = user_data["name"]
        user.bio = user_data["description"]
        user.followers_count = user_data["followers_count"]
        user.following_count = user_data["following_count"]
        user.tweet_count = user_data["tweet_count"]
        user.profile_image = user_data["profile_image_url"]
        user.last_scraped = datetime.now(timezone.utc)
        collected += 1

        if (i + 1) % 50 == 0:
            session.commit()
            print(f"  [PROGRESS] {i + 1}/{len(users_to_fetch)} ({collected} 件取得)")

            # レート制限チェック、ワーカー切り替え
            remaining = api.get_rate_remaining("UserByScreenName")
            if remaining is not None and remaining < 5:
                api = pick_best_worker(workers, "UserByScreenName")

    session.commit()
    session.close()
    print(f"\n[完了] {collected} プロフィール取得")
