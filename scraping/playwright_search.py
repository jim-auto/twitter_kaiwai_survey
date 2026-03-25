"""Playwright経由のTwitter検索（typeaheadの10件制限を回避）"""
import asyncio
import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from config.settings import COOKIE_DIR, USER_AGENT

BLOCK_TYPES = {"image", "stylesheet", "font", "media"}
RESERVED_PROFILE_PATHS = {
    "account", "compose", "explore", "home", "i", "intent",
    "login", "messages", "notifications", "privacy", "search",
    "settings", "tos",
}


def _find_cookie_file() -> Path:
    """利用可能なCookieファイルを探す"""
    sokusuu_data = Path(__file__).resolve().parent.parent.parent / "sokusuu-ranking" / "data"
    for search_dir in [COOKIE_DIR, sokusuu_data]:
        if not search_dir.exists():
            continue
        for f in sorted(search_dir.glob(".twitter_cookies*.json")):
            if f.stat().st_size > 10:
                return f
    raise RuntimeError("Cookieファイルが見つかりません")


def _load_cookies_for_playwright(cookie_file: Path) -> list[dict]:
    raw = json.load(open(cookie_file))
    cookies = []
    for c in raw:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".x.com"),
            "path": c.get("path", "/"),
        }
        if c.get("secure"):
            cookie["secure"] = True
        if c.get("httpOnly"):
            cookie["httpOnly"] = True
        cookies.append(cookie)
    return cookies


async def _block_resources(route):
    if route.request.resource_type in BLOCK_TYPES:
        await route.abort()
    else:
        await route.continue_()


def _extract_screen_name_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.netloc not in {"x.com", "www.x.com"}:
        return None

    path = parsed.path.strip("/")
    if not path or "/" in path or path in RESERVED_PROFILE_PATHS:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", path):
        return None
    return path


async def _resolve_screen_name_from_page(page, user_id: str, timeout_ms: int) -> Optional[str]:
    try:
        await page.goto(
            f"https://x.com/i/user/{user_id}",
            wait_until="commit",
            timeout=min(timeout_ms, 10000),
        )
    except Exception:
        pass

    checks = max(10, timeout_ms // 500)
    for _ in range(checks):
        screen_name = _extract_screen_name_from_url(page.url)
        if screen_name:
            return screen_name
        try:
            await page.wait_for_timeout(500)
        except Exception:
            break

    try:
        title = await page.title()
    except Exception:
        title = ""
    match = re.search(r"@([A-Za-z0-9_]{1,15})", title)
    if match:
        return match.group(1)

    return None


async def search_users_playwright(query: str, max_scroll: int = 10) -> list[dict]:
    """Playwrightでx.comの検索ページからユーザーを取得"""
    from playwright.async_api import async_playwright

    cookie_file = _find_cookie_file()
    cookies = _load_cookies_for_playwright(cookie_file)

    users = []
    seen = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT + " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        await context.add_cookies(cookies)
        page = await context.new_page()
        await page.route("**/*", _block_resources)

        # People検索ページ
        search_url = f"https://x.com/search?q={query}&src=typed_query&f=people"
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector('[data-testid="UserCell"], [data-testid="empty_state_header_text"]', timeout=8000)
        except Exception as e:
            print(f"  [PW] Page load failed: {e}")
            await browser.close()
            return users

        for scroll in range(max_scroll):
            # ユーザーセルを取得
            cells = page.locator('[data-testid="UserCell"]')
            count = await cells.count()

            for i in range(count):
                try:
                    cell = cells.nth(i)
                    # username取得
                    links = cell.locator('a[role="link"]')
                    username = None
                    for j in range(await links.count()):
                        href = await links.nth(j).get_attribute("href") or ""
                        if href.startswith("/") and "/" not in href[1:]:
                            username = href[1:]
                            break

                    if not username or username in seen:
                        continue
                    seen.add(username)

                    # bio取得
                    bio = ""
                    bio_el = cell.locator('[data-testid="UserDescription"] span, [dir="auto"] span')
                    if await bio_el.count() > 0:
                        bio = await bio_el.first.text_content() or ""

                    users.append({
                        "screen_name": username,
                        "bio": bio,
                    })
                except Exception:
                    continue

            # スクロール
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1500)

            # 新しいユーザーが出なければ終了
            new_count = await cells.count()
            if new_count == count and scroll > 2:
                break

        await browser.close()

    return users


def search_users_sync(query: str, max_scroll: int = 10) -> list[dict]:
    """同期版ラッパー"""
    return asyncio.run(search_users_playwright(query, max_scroll))


async def resolve_screen_names_playwright(
    user_ids: list[str],
    concurrency: int = 4,
    timeout_ms: int = 30000,
) -> dict[str, str]:
    """rest_id から profile redirect を辿って screen_name を解決する。"""
    from playwright.async_api import async_playwright

    if not user_ids:
        return {}

    cookie_file = _find_cookie_file()
    cookies = _load_cookies_for_playwright(cookie_file)
    results: dict[str, str] = {}
    queue: asyncio.Queue[str] = asyncio.Queue()

    for user_id in user_ids:
        queue.put_nowait(user_id)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT + " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        await context.add_cookies(cookies)

        async def worker(worker_idx: int):
            page = await context.new_page()
            await page.route("**/*", _block_resources)
            processed = 0
            try:
                while True:
                    try:
                        user_id = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    screen_name = await _resolve_screen_name_from_page(page, user_id, timeout_ms)
                    if screen_name:
                        results[user_id] = screen_name
                    processed += 1
                    if processed % 20 == 0:
                        print(f"  [PW{worker_idx}] {processed} redirect attempts")
            finally:
                await page.close()

        worker_count = max(1, min(concurrency, len(user_ids)))
        await asyncio.gather(*(worker(i + 1) for i in range(worker_count)))
        await context.close()
        await browser.close()

    return results


def resolve_screen_names_sync(
    user_ids: list[str],
    concurrency: int = 4,
    timeout_ms: int = 30000,
) -> dict[str, str]:
    """sync wrapper for rest_id -> screen_name resolution."""
    return asyncio.run(resolve_screen_names_playwright(user_ids, concurrency, timeout_ms))


if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "#筋トレ"
    print(f"Searching: {query}")
    results = search_users_sync(query, max_scroll=5)
    print(f"Found: {len(results)} users")
    for u in results[:10]:
        print(f"  @{u['screen_name']}: {u['bio'][:60]}")
