"""Twitter GraphQL APIクライアント（sokusuu-rankingから移植・リファクタ）"""
import json
import time
from typing import Optional

import requests

from config.settings import (
    BEARER_TOKEN,
    GRAPHQL_FOLLOWING,
    GRAPHQL_SEARCH_TIMELINE,
    GRAPHQL_USER_BY_SCREEN_NAME,
    TIMELINE_FEATURES,
    USER_AGENT,
    USER_FEATURES,
)


class TwitterGraphQL:
    def __init__(self, cookie_file: str, worker_id: int = 0):
        cookies = json.load(open(cookie_file))
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        ct0 = cookie_dict["ct0"]
        cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)

        self.session = requests.Session()
        self.session.headers.update({
            "authorization": BEARER_TOKEN,
            "x-csrf-token": ct0,
            "cookie": cookie_str,
            "user-agent": USER_AGENT,
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
        })
        self.worker_id = worker_id
        self.rate_limits: dict[str, dict] = {}  # endpoint -> {remaining, reset}

    def _update_rate_limit(self, endpoint: str, resp: requests.Response):
        remaining = resp.headers.get("x-rate-limit-remaining")
        reset = resp.headers.get("x-rate-limit-reset")
        if remaining is not None:
            self.rate_limits[endpoint] = {
                "remaining": int(remaining),
                "reset": int(reset) if reset else 0,
            }

    def _wait_if_rate_limited(self, endpoint: str, resp: requests.Response) -> bool:
        self._update_rate_limit(endpoint, resp)
        if resp.status_code == 429:
            reset = resp.headers.get("x-rate-limit-reset")
            wait = max(int(reset) - int(time.time()), 5) if reset else 60
            print(f"  [W{self.worker_id} RATE LIMIT] {endpoint}: {wait}秒待機...")
            time.sleep(wait)
            return True
        return False

    def get_rate_remaining(self, endpoint: str) -> Optional[int]:
        info = self.rate_limits.get(endpoint)
        return info["remaining"] if info else None

    def get_user(self, screen_name: str) -> Optional[dict]:
        """ユーザープロフィールを取得"""
        variables = json.dumps({
            "screen_name": screen_name,
            "withSafetyModeUserFields": True,
        })
        for attempt in range(3):
            try:
                resp = self.session.get(
                    GRAPHQL_USER_BY_SCREEN_NAME,
                    params={"variables": variables, "features": USER_FEATURES},
                    timeout=15,
                )
                if self._wait_if_rate_limited("UserByScreenName", resp):
                    continue
                if resp.status_code != 200:
                    return None

                data = resp.json()
                user = data.get("data", {}).get("user", {}).get("result", {})
                if not user or user.get("__typename") == "UserUnavailable":
                    return None

                legacy = user.get("legacy", {})
                img = legacy.get("profile_image_url_https", "")
                img = img.replace("_normal.", "_400x400.")

                return {
                    "rest_id": user.get("rest_id", ""),
                    "screen_name": legacy.get("screen_name", screen_name),
                    "name": legacy.get("name", screen_name),
                    "description": legacy.get("description", ""),
                    "followers_count": legacy.get("followers_count", 0),
                    "following_count": legacy.get("friends_count", 0),
                    "tweet_count": legacy.get("statuses_count", 0),
                    "created_at": legacy.get("created_at", ""),
                    "profile_image_url": img,
                }
            except requests.exceptions.RequestException as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                print(f"  [W{self.worker_id} ERROR] @{screen_name}: {e}")
                return None
        return None

    def get_user_id(self, screen_name: str) -> Optional[str]:
        """screen_name → rest_id"""
        user = self.get_user(screen_name)
        return user["rest_id"] if user else None

    def get_following(self, user_id: str, max_pages: int = 10) -> list[str]:
        """Followingリスト取得（screen_nameのリスト）"""
        all_users = []
        cursor = None
        for _ in range(max_pages):
            variables = {"userId": user_id, "count": 200, "includePromotedContent": False}
            if cursor:
                variables["cursor"] = cursor

            for attempt in range(3):
                try:
                    resp = self.session.get(
                        GRAPHQL_FOLLOWING,
                        params={"variables": json.dumps(variables), "features": TIMELINE_FEATURES},
                        timeout=15,
                    )
                    if self._wait_if_rate_limited("Following", resp):
                        continue
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(2)
                    else:
                        return all_users

            if resp.status_code != 200:
                break

            instructions = (
                resp.json().get("data", {}).get("user", {}).get("result", {})
                .get("timeline", {}).get("timeline", {}).get("instructions", [])
            )
            new_cursor = None
            found = 0
            for inst in instructions:
                for entry in inst.get("entries", []):
                    content = entry.get("content", {})
                    ur = content.get("itemContent", {}).get("user_results", {}).get("result", {})
                    if ur:
                        sn = ur.get("legacy", {}).get("screen_name", "")
                        if sn:
                            all_users.append(sn)
                            found += 1
                    if content.get("cursorType") == "Bottom":
                        new_cursor = content.get("value")
            if not new_cursor or found == 0:
                break
            cursor = new_cursor
        return all_users

    def search_users(self, query: str, max_pages: int = 5) -> list[dict]:
        """ツイート検索でユーザーを発見（SearchTimelineエンドポイント）"""
        all_users = []
        seen_ids = set()
        cursor = None

        for _ in range(max_pages):
            variables = {
                "rawQuery": query,
                "count": 20,
                "querySource": "typed_query",
                "product": "People",
            }
            if cursor:
                variables["cursor"] = cursor

            for attempt in range(3):
                try:
                    resp = self.session.get(
                        GRAPHQL_SEARCH_TIMELINE,
                        params={
                            "variables": json.dumps(variables),
                            "features": TIMELINE_FEATURES,
                        },
                        timeout=15,
                    )
                    if self._wait_if_rate_limited("SearchTimeline", resp):
                        continue
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(2)
                    else:
                        return all_users

            if resp.status_code != 200:
                break

            data = resp.json()
            instructions = (
                data.get("data", {}).get("search_by_raw_query", {})
                .get("search_timeline", {}).get("timeline", {})
                .get("instructions", [])
            )

            new_cursor = None
            found = 0
            for inst in instructions:
                for entry in inst.get("entries", []):
                    content = entry.get("content", {})
                    ur = content.get("itemContent", {}).get("user_results", {}).get("result", {})
                    if ur and ur.get("rest_id") and ur["rest_id"] not in seen_ids:
                        legacy = ur.get("legacy", {})
                        seen_ids.add(ur["rest_id"])
                        all_users.append({
                            "rest_id": ur["rest_id"],
                            "screen_name": legacy.get("screen_name", ""),
                            "name": legacy.get("name", ""),
                            "description": legacy.get("description", ""),
                            "followers_count": legacy.get("followers_count", 0),
                            "following_count": legacy.get("friends_count", 0),
                            "tweet_count": legacy.get("statuses_count", 0),
                            "profile_image_url": legacy.get("profile_image_url_https", ""),
                        })
                        found += 1
                    if content.get("cursorType") == "Bottom":
                        new_cursor = content.get("value")
            if not new_cursor or found == 0:
                break
            cursor = new_cursor
            time.sleep(1)  # 検索は控えめに

        return all_users

    def process_following_batch(self, usernames: list[str]) -> dict[str, list[str]]:
        """一括: screen_name → Following取得"""
        results = {}
        for u in usernames:
            uid = self.get_user_id(u)
            if not uid:
                results[u] = []
                continue
            following = self.get_following(uid)
            results[u] = following
            print(f"  [W{self.worker_id}] @{u}: {len(following)} following")
        return results
