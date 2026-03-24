"""界隈メンバー発見パイプライン（Stage 1: シード解決 + 検索）"""
import re

from communities import CommunityDef
from db.models import get_session, init_db
from db.ops import add_community_member, upsert_community, upsert_user
from scraping.auth import create_worker_pool
from scraping.rate_limiter import pick_best_worker


def _matches_bio_patterns(bio: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if re.search(pat, bio, re.IGNORECASE):
            return True
    return False


def _matches_exclude_patterns(bio: str, screen_name: str, patterns: list[str]) -> bool:
    text = f"{bio} {screen_name}"
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def discover_community(community: CommunityDef):
    """界隈のメンバーを発見してDBに登録"""
    print(f"\n{'='*50}")
    print(f"界隈発見: {community.name} ({community.id})")
    print(f"{'='*50}")

    init_db()
    session = get_session()

    # 界隈をDBに登録
    upsert_community(
        session, community.id,
        name=community.name,
        description=community.description,
        config_path=str(community.id) + ".yaml",
    )
    session.commit()

    # ワーカー準備
    workers = create_worker_pool()
    api = workers[0]

    # --- Stage 1a: シード解決 ---
    print(f"\n[Stage 1a] シード解決 ({len(community.seeds)} アカウント)")
    seed_user_ids = []

    for seed in community.seeds:
        username = seed["username"]
        user_data = api.get_user(username)
        if not user_data:
            print(f"  [SKIP] @{username}: 取得失敗")
            continue

        user_id = user_data["rest_id"]
        upsert_user(session, user_id, **{
            "screen_name": user_data["screen_name"],
            "display_name": user_data["name"],
            "bio": user_data["description"],
            "followers_count": user_data["followers_count"],
            "following_count": user_data["following_count"],
            "tweet_count": user_data["tweet_count"],
            "profile_image": user_data["profile_image_url"],
        })
        add_community_member(
            session, community.id, user_id,
            confidence=1.0, source="seed",
            bio_match=_matches_bio_patterns(user_data["description"], community.bio_patterns),
        )
        seed_user_ids.append(user_id)
        print(f"  [OK] @{username} (id={user_id}, followers={user_data['followers_count']})")

    session.commit()
    print(f"  → {len(seed_user_ids)} シード解決完了")

    # --- Stage 1b: キーワード/ハッシュタグ検索 ---
    queries = community.hashtags + community.keywords
    if queries:
        print(f"\n[Stage 1b] 検索発見 ({len(queries)} クエリ)")
        search_api = pick_best_worker(workers, "SearchTimeline")
        found_count = 0

        for query in queries:
            print(f"  検索: {query}")
            users = search_api.search_users(query, max_pages=3)

            for u in users:
                if _matches_exclude_patterns(u.get("description", ""), u.get("screen_name", ""), community.exclude_patterns):
                    continue

                bio_match = _matches_bio_patterns(u.get("description", ""), community.bio_patterns)
                confidence = 0.5 if bio_match else 0.3

                upsert_user(session, u["rest_id"], **{
                    "screen_name": u["screen_name"],
                    "display_name": u["name"],
                    "bio": u["description"],
                    "followers_count": u["followers_count"],
                    "following_count": u["following_count"],
                    "tweet_count": u["tweet_count"],
                    "profile_image": u.get("profile_image_url", ""),
                })
                add_community_member(
                    session, community.id, u["rest_id"],
                    confidence=confidence, source="search",
                    bio_match=bio_match,
                )
                found_count += 1

            session.commit()
            print(f"    → {len(users)} ユーザー発見")

        print(f"  → 検索合計: {found_count} ユーザー")

    # --- Stage 1c: Playwright検索（大量発見） ---
    pw_queries = community.hashtags[:3]  # 上位3つのハッシュタグ
    if pw_queries:
        try:
            from scraping.playwright_search import search_users_sync
            print(f"\n[Stage 1c] Playwright検索 ({len(pw_queries)} クエリ)")
            pw_count = 0

            for query in pw_queries:
                print(f"  PW検索: {query}")
                pw_users = search_users_sync(query, max_scroll=8)

                for u in pw_users:
                    sn = u.get("screen_name", "")
                    bio = u.get("bio", "")
                    if not sn:
                        continue
                    if _matches_exclude_patterns(bio, sn, community.exclude_patterns):
                        continue

                    bio_match = _matches_bio_patterns(bio, community.bio_patterns)
                    confidence = 0.5 if bio_match else 0.3
                    user_id = f"sn:{sn}"
                    upsert_user(session, user_id, screen_name=sn, bio=bio)
                    add_community_member(
                        session, community.id, user_id,
                        confidence=confidence, source="pw_search",
                        bio_match=bio_match,
                    )
                    pw_count += 1

                session.commit()
                print(f"    → {len(pw_users)} ユーザー発見")

            print(f"  → PW検索合計: {pw_count} ユーザー")
        except ImportError:
            print("  [SKIP] playwright未インストール")
        except Exception as e:
            print(f"  [WARN] Playwright検索失敗: {e}")

    session.close()
    print(f"\n[完了] {community.name} のシード解決 + 検索発見が完了しました")
    return seed_user_ids
