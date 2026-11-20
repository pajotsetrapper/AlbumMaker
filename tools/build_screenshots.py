#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Take the template screenshots shown on the product page (docs/index.html):

    pip install playwright && python -m playwright install chromium     # once
    python tools/build_screenshots.py              # needs internet, for the map tiles
    python tools/build_screenshots.py --offline    # no map tiles: routes on a plain map
    python tools/build_screenshots.py journal      # only some templates

It serves the sample sites in docs (docs/demo, docs/demo-journal, ...; see
tools/build_demo.py) on this computer, opens each one in a 1440 pixel wide
browser and saves the trip homepage and the second day's page as

    docs/assets/template-<name>-home.webp
    docs/assets/template-<name>-day.webp

The classic template's screenshots (trip-library.png, trip-day.png) are made by
hand and not touched. Run tools/build_demo.py first if the sample sites changed.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import io
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from albummaker.site_templates import template_names  # noqa: E402

DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
WIDTH = 1440
MAX_HOME, MAX_DAY = 1400, 1600      # pixels tall, like the classic screenshots
DAY_PAGE = "days/2025-05-15.html"


def sample_folder(template: str) -> str:
    return "demo" if template == "classic" else f"demo-{template}"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve() -> http.server.ThreadingHTTPServer:
    handler = functools.partial(_Quiet, directory=str(DOCS))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def settle(page) -> None:
    """Let the page finish: scroll through it so reveal-on-scroll parts show, then back to the top."""
    page.wait_for_load_state("load")
    page.wait_for_timeout(1200)
    height = page.evaluate("document.documentElement.scrollHeight")
    for y in range(0, height, 500):
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(250)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(800)


def save(png: bytes, target: Path, max_height: int) -> None:
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = image.crop((0, 0, image.width, min(image.height, max_height)))
    image.save(target, "WEBP", quality=82, method=6)
    print(f"{target.relative_to(ROOT)}  {image.width}x{image.height}  {target.stat().st_size // 1024} KB")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Take the template screenshots for the product page.")
    parser.add_argument("templates", nargs="*", metavar="TEMPLATE", help="default: journal, scrapbook, cinema")
    parser.add_argument("--offline", action="store_true",
                        help="do not load anything from the internet (the maps then have no background tiles)")
    args = parser.parse_args(argv)
    wanted = args.templates or [name for name in template_names() if name != "classic"]
    unknown = [name for name in wanted if name not in template_names()]
    if unknown:
        parser.error(f"unknown template: {', '.join(unknown)}")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("This needs Playwright: pip install playwright && python -m playwright install chromium",
              file=sys.stderr)
        return 1

    server = serve()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ASSETS.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name in wanted:
            folder = sample_folder(name)
            if not (DOCS / folder / "index.html").is_file():
                print(f"docs/{folder} is missing; run tools/build_demo.py first.", file=sys.stderr)
                return 1
            context = browser.new_context(viewport={"width": WIDTH, "height": 900})
            page = context.new_page()
            if args.offline:
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base)
                           else route.abort())
            for kind, path, limit in (("home", "index.html", MAX_HOME), ("day", DAY_PAGE, MAX_DAY)):
                page.goto(f"{base}/{folder}/{path}")
                settle(page)
                save(page.screenshot(full_page=True), ASSETS / f"template-{name}-{kind}.webp", limit)
            context.close()
        browser.close()
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
