"""Regenerate the README screenshots.

Not part of the test suite and not in requirements-dev: it needs a real browser,
which is a 150 MB download nobody should pay for to run the tests.

    pip install playwright && playwright install chromium
    python scripts/screenshots.py

Both servers must already be running. Shots that need verified fares will spend
live requests; the rest run at quick depth and cost nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs"
APP = "http://localhost:3000"
WIDTH, HEIGHT = 1500, 1460


def wait_for_scan(page: Page) -> None:
    """Wait for the scan to finish, by watching the button rather than the results.

    Waiting for "Best options" only works on a first search: on a re-scan that
    text is already on the page, so the wait returns instantly and the shot
    catches the previous results under the new filters.
    """
    page.wait_for_timeout(700)  # let the button flip to "Scanning…"
    page.wait_for_selector("button:text-is('Scan fares')", timeout=180_000)
    page.wait_for_timeout(1_500)


def scan(page: Page, *, destination: str, depth: str, wants_return: bool = False,
         min_nights: str = "", max_nights: str = "") -> None:
    page.locator("input.uppercase").nth(1).fill(destination)

    checkbox = page.locator("input[type=checkbox]").first
    if checkbox.is_checked() != wants_return:
        checkbox.click()
        page.wait_for_timeout(300)

    page.locator('select:has(option[value="quick"])').select_option(depth)

    if wants_return and min_nights:
        nights = page.locator("input[type=number]")
        nights.nth(0).fill(min_nights)
        nights.nth(1).fill(max_nights)

    page.wait_for_timeout(400)
    page.get_by_role("button", name="Scan fares").click()
    wait_for_scan(page)


def shoot(page: Page, name: str, *, full: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    page.screenshot(path=str(path), full_page=full)
    kb = path.stat().st_size // 1024
    print(f"  {name}  {kb} KB")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=2,
            color_scheme="dark",
        )
        page.goto(APP, wait_until="networkidle")

        # Verified fares, aircraft and sellers — costs live requests.
        print("search.png — one-way, standard depth (live pricing)")
        scan(page, destination="DXB", depth="standard")
        shoot(page, "search.png")

        # The avgeek filter, reusing the quotes the previous scan just cached.
        print("aircraft.png — widebody only (cached quotes)")
        page.locator("button:text-is('Widebody')").click()
        page.wait_for_timeout(300)
        page.get_by_role("button", name="Scan fares").click()
        wait_for_scan(page)
        shoot(page, "aircraft.png")

        # The two-axis heatmap. Quick depth, so free.
        print("matrix.png — departure x return heatmap (free)")
        page.goto(APP, wait_until="networkidle")
        scan(
            page,
            destination="DXB",
            depth="quick",
            wants_return=True,
            min_nights="20",
            max_nights="45",
        )
        page.locator("table").scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        shoot(page, "matrix.png")

        browser.close()
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
