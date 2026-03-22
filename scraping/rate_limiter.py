"""レート制限管理"""
import time
from typing import Optional

from scraping.graphql_client import TwitterGraphQL


def pick_best_worker(workers: list[TwitterGraphQL], endpoint: str) -> TwitterGraphQL:
    """レート制限の余裕が最も大きいワーカーを返す"""
    best = workers[0]
    best_remaining = -1

    for w in workers:
        remaining = w.get_rate_remaining(endpoint)
        if remaining is None:
            return w  # まだ使ってないワーカーを優先
        if remaining > best_remaining:
            best = w
            best_remaining = remaining

    # 全ワーカーが枯渇していたらリセット待ち
    if best_remaining is not None and best_remaining <= 1:
        info = best.rate_limits.get(endpoint, {})
        reset = info.get("reset", 0)
        wait = max(reset - int(time.time()), 5)
        if wait > 0 and wait < 900:  # 15分以内
            print(f"  [RATE] 全ワーカー枯渇、{wait}秒待機...")
            time.sleep(wait)

    return best
