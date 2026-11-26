"""Capture the README screenshots of the surf dashboard from a running Home Assistant.

Renders one dashboard view in headless Chrome, in light and dark mode, at phone
width, and saves each as a PNG. It signs in with a long-lived access token
(Profile -> Security -> Long-lived access tokens), passed through the
environment so it never lands in shell history or the repo:

    pip install playwright
    export HASS_SERVER=http://homeassistant.local:8123
    export HASS_TOKEN=...
    python scripts/screenshot.py --view lovelace/surf --out docs/images

Uses the installed Google Chrome (`--channel chrome`), so no browser download
is needed; pass `--channel chromium` after `playwright install chromium` to use
Playwright's own build instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# The cards are in a sections view; capturing that element leaves out the
# top bar but keeps the view's background, which the translucent cards need.
VIEW_SELECTOR = "hui-sections-view"


def _tokens(server: str, token: str) -> str:
    # The shape the frontend keeps in localStorage after a normal login. A
    # long-lived token has no refresh token, so expiry is pushed far out.
    return json.dumps(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 1800,
            "hassUrl": server,
            "clientId": f"{server}/",
            "expires": int((time.time() + 365 * 86400) * 1000),
            "refresh_token": "",
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--view", default="lovelace/surf", help="dashboard path, e.g. lovelace/surf")
    parser.add_argument("--out", default="docs/images", type=Path)
    parser.add_argument("--width", default=430, type=int, help="viewport width in CSS px")
    parser.add_argument("--scale", default=2, type=int, help="device pixel ratio")
    parser.add_argument("--wait", default=4000, type=int, help="ms to let cards render")
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args()

    server = os.environ.get("HASS_SERVER", "").rstrip("/")
    token = os.environ.get("HASS_TOKEN", "")
    if not server or not token:
        print("Set HASS_SERVER and HASS_TOKEN.", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel)
        for mode in ("light", "dark"):
            ctx = browser.new_context(
                viewport={"width": args.width, "height": 900},
                device_scale_factor=args.scale,
                color_scheme=mode,
            )
            theme = json.dumps({"theme": "default", "dark": mode == "dark"})
            ctx.add_init_script(
                f"localStorage.setItem('hassTokens', {json.dumps(_tokens(server, token))});"
                f"localStorage.setItem('selectedTheme', {json.dumps(theme)});"
            )
            page = ctx.new_page()
            page.goto(f"{server}/{args.view.lstrip('/')}")
            view = page.locator(VIEW_SELECTOR).first
            view.wait_for(state="visible", timeout=30000)
            page.wait_for_timeout(args.wait)
            dest = args.out / f"dashboard-{mode}.png"
            view.screenshot(path=str(dest))
            print(dest)
            ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
