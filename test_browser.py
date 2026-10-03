from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=[
            "--start-maximized",
            "--window-size=1366,900"
        ]
    )

    page = browser.new_page()
    page.goto("https://google.com")

    time.sleep(30)
