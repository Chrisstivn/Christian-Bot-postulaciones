from playwright.sync_api import sync_playwright
import time
import os

print("DISPLAY:", os.environ.get("DISPLAY"))
print("WAYLAND:", os.environ.get("WAYLAND_DISPLAY"))

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=[
            "--disable-gpu",
            "--no-sandbox",
            "--window-size=1366,900"
        ]
    )

    page = browser.new_page()
    page.goto("https://google.com")

    print("Browser abierto")
    input("Presiona ENTER para cerrar...")
