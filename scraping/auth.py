"""Cookie管理・ワーカープール"""
import os
from pathlib import Path

from config.settings import COOKIE_DIR
from scraping.graphql_client import TwitterGraphQL


def discover_cookie_files() -> list[Path]:
    """利用可能なCookieファイルを検出"""
    files = []
    # sokusuu-ranking のCookieも参照
    sokusuu_data = Path(__file__).resolve().parent.parent.parent / "sokusuu-ranking" / "data"

    for search_dir in [COOKIE_DIR, sokusuu_data]:
        if not search_dir.exists():
            continue
        for f in sorted(search_dir.glob(".twitter_cookies*.json")):
            if f.stat().st_size > 10:
                files.append(f)

    return files


def create_worker_pool() -> list[TwitterGraphQL]:
    """利用可能な全ワーカーを生成"""
    cookie_files = discover_cookie_files()
    if not cookie_files:
        raise RuntimeError(
            f"Cookieファイルが見つかりません。{COOKIE_DIR} または sokusuu-ranking/data/ にCookieを配置してください"
        )

    workers = []
    for i, cf in enumerate(cookie_files):
        try:
            worker = TwitterGraphQL(str(cf), worker_id=i + 1)
            workers.append(worker)
            print(f"  [Worker {i + 1}] {cf.name} OK")
        except Exception as e:
            print(f"  [Worker {i + 1}] {cf.name} FAIL ({e})")

    if not workers:
        raise RuntimeError("有効なワーカーがありません")

    print(f"  → {len(workers)} ワーカー利用可能\n")
    return workers
