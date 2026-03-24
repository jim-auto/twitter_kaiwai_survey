"""Playwright経由のTwitter検索（typeaheadの10件制限を回避）"""
import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from config.settings import COOKIE_DIR, USER_AGENT

BLOCK_TYPES = {"image", "stylesheet", "font", "media"}


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


if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "#筋トレ"
    print(f"Searching: {query}")
    results = search_users_sync(query, max_scroll=5)
    print(f"Found: {len(results)} users")
    for u in results[:10]:
        print(f"  @{u['screen_name']}: {u['bio'][:60]}")
