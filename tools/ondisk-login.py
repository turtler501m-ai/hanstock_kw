"""Log in to Ondisk with Playwright.

Credentials are read from environment variables:
  ONDISK_ID
  ONDISK_PASSWORD

The resulting browser storage state is written under .runtime/ by default.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_URL = "https://ondisk.co.kr/index.php"
DEFAULT_STATE_PATH = ".runtime/ondisk/storage_state.json"
DEFAULT_WHALE_PROFILE_PATH = ".runtime/ondisk/whale-profile"
DEFAULT_WHALE_PATHS = [
    r"C:\Program Files\Naver\Naver Whale\Application\whale.exe",
    r"C:\Program Files (x86)\Naver\Naver Whale\Application\whale.exe",
]


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        print(
            "Playwright is not installed.\n"
            "Install it with:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return sync_playwright, PlaywrightTimeoutError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Log in to ondisk.co.kr.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"login page URL, default: {DEFAULT_URL}")
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE_PATH,
        help=f"path to save Playwright storage state, default: {DEFAULT_STATE_PATH}",
    )
    parser.add_argument("--show", action="store_true", help="run with a visible browser window")
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="keep the browser open after login, useful for captcha or first-run checks",
    )
    parser.add_argument(
        "--save-id",
        action="store_true",
        help="check Ondisk's 'save ID' option before logging in",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "whale"],
        default="chromium",
        help="browser executable to use, default: chromium",
    )
    parser.add_argument(
        "--browser-path",
        help="custom Chromium-compatible browser executable path, overrides --browser",
    )
    parser.add_argument(
        "--whale-profile",
        default=DEFAULT_WHALE_PROFILE_PATH,
        help=f"Whale user data directory for CDP mode, default: {DEFAULT_WHALE_PROFILE_PATH}",
    )
    parser.add_argument("--cdp-port", type=int, default=9223, help="local CDP port for Whale mode")
    parser.add_argument("--timeout-ms", type=int, default=30000, help="page action timeout")
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        raise SystemExit(2)
    return value


def first_visible_locator(page, selectors: list[str], timeout_ms: int):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:
            continue
    raise RuntimeError(f"Could not find a visible element from selectors: {selectors}")


def looks_logged_in(page) -> bool:
    login_id_count = page.locator("input[name='mb_id'], #mb_id").count()
    login_password_count = page.locator("input[name='mb_pw'], input[type='password']").count()
    logout_count = page.get_by_text("로그아웃", exact=False).count()
    return logout_count > 0 or (login_id_count == 0 and login_password_count == 0)


def resolve_browser_path(args: argparse.Namespace) -> str | None:
    if args.browser_path:
        browser_path = Path(args.browser_path)
        if not browser_path.is_file():
            print(f"Browser executable was not found: {browser_path}", file=sys.stderr)
            raise SystemExit(2)
        return str(browser_path)

    if args.browser != "whale":
        return None

    for candidate in DEFAULT_WHALE_PATHS:
        if Path(candidate).is_file():
            return candidate

    print(
        "Naver Whale executable was not found. "
        "Use --browser-path to provide the whale.exe path.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def wait_for_cdp(port: int, timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    url = f"http://127.0.0.1:{port}/json/version"

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5):
                return
        except Exception:
            time.sleep(0.2)

    raise RuntimeError(f"Whale CDP endpoint did not start on port {port}")


def cdp_endpoint_is_open(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5):
            return True
    except Exception:
        return False


def open_browser(playwright, args: argparse.Namespace, browser_path: str | None):
    if args.browser != "whale":
        return playwright.chromium.launch(headless=not args.show), None

    if not browser_path:
        raise RuntimeError("Whale browser path was not resolved")

    whale_process = None
    if not cdp_endpoint_is_open(args.cdp_port):
        profile_path = Path(args.whale_profile)
        profile_path.mkdir(parents=True, exist_ok=True)
        whale_process = subprocess.Popen(
            [
                browser_path,
                f"--remote-debugging-port={args.cdp_port}",
                f"--user-data-dir={profile_path.resolve()}",
                "--no-first-run",
                "--new-window",
                "about:blank",
            ]
        )
        wait_for_cdp(args.cdp_port, args.timeout_ms)

    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.cdp_port}")
    return browser, whale_process


def login(args: argparse.Namespace) -> int:
    load_dotenv()
    ondisk_id = require_env("ONDISK_ID")
    ondisk_password = require_env("ONDISK_PASSWORD")
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    sync_playwright, PlaywrightTimeoutError = import_playwright()
    browser_path = resolve_browser_path(args)

    with sync_playwright() as playwright:
        browser, whale_process = open_browser(playwright, args, browser_path)
        if args.browser == "whale":
            context = browser.contexts[0] if browser.contexts else browser.new_context(locale="ko-KR")
            page = context.pages[-1] if context.pages else context.new_page()
        else:
            context = browser.new_context(locale="ko-KR")
            page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        try:
            page.goto(args.url, wait_until="domcontentloaded")

            user_id = first_visible_locator(
                page,
                ["input[name='mb_id']", "#mb_id", "form[name='loginFrm'] input[type='text']"],
                args.timeout_ms,
            )
            password = first_visible_locator(
                page,
                ["input[name='mb_pw']", "form[name='loginFrm'] input[type='password']", "input[type='password']"],
                args.timeout_ms,
            )

            user_id.fill(ondisk_id)
            password.fill(ondisk_password)

            if args.save_id:
                save_id = page.locator("#log_save, input[name='log_save']").first
                if save_id.count() > 0 and not save_id.is_checked():
                    save_id.check(force=True)

            submit = page.locator("form[name='loginFrm'] input[type='image'], form[name='loginFrm'] button").first
            if submit.count() > 0:
                submit.click()
            else:
                password.press("Enter")

            try:
                page.wait_for_load_state("networkidle", timeout=args.timeout_ms)
            except PlaywrightTimeoutError:
                pass

            if not looks_logged_in(page):
                print(
                    "Login was submitted, but the page still looks unauthenticated. "
                    "Check credentials, captcha, or site-side verification.",
                    file=sys.stderr,
                )
                if args.keep_open:
                    input("Press Enter to close the browser...")
                return 1

            context.storage_state(path=str(state_path))
            print(f"Login succeeded. Storage state saved to: {state_path}")

            if args.keep_open:
                input("Press Enter to close the browser...")
            return 0
        finally:
            context.close()
            browser.close()
            if whale_process and whale_process.poll() is None:
                whale_process.terminate()


def main() -> int:
    return login(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
