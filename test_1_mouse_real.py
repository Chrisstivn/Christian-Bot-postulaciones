"""
test_1_mouse_real.py
MÉTODO 1: click con coordenadas REALES de mouse (page.mouse.click, no
el.click() de Playwright) + tipeo a nivel de PÁGINA (page.keyboard.type,
no locator.type()). La idea: sacar de la ecuación cualquier resolución
"inteligente" de Playwright y simular exactamente lo que hace un mouse y
un teclado físico.

Uso:
    python3 test_1_mouse_real.py "https://boards.greenhouse.io/tuempresa/jobs/123" "input#location-input" "Berlin, Germany"

El selector lo sacás así: click derecho sobre el campo Location en el
navegador -> Inspeccionar -> en el HTML resaltado, click derecho -> Copy ->
Copy selector.
"""
import sys
import time
from playwright.sync_api import sync_playwright

URL = sys.argv[1]
SELECTOR = sys.argv[2]
VALUE = sys.argv[3] if len(sys.argv) > 3 else "Berlin, Germany"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=50)
    page = browser.new_page()
    page.goto(URL, wait_until="networkidle", timeout=30000)

    input(
        "\n>>> Navegá manualmente (si hace falta) hasta que el campo Location "
        "sea visible en pantalla, después presioná ENTER acá en la terminal...\n"
    )

    loc = page.locator(SELECTOR).first
    box = loc.bounding_box()
    if not box:
        print("ERROR: no encontré ese selector visible en pantalla. Revisá el selector.")
        browser.close()
        sys.exit(1)

    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    print(f"Click real en coordenadas ({x:.0f}, {y:.0f})...")

    page.mouse.click(x, y)
    time.sleep(0.3)

    focused_matches = page.evaluate(
        "sel => document.activeElement === document.querySelector(sel)", SELECTOR
    )
    print(f"¿Quedó focused el elemento esperado? {focused_matches}")

    print(f"Tipeando '{VALUE}' letra por letra a nivel de página...")
    page.keyboard.type(VALUE, delay=120)
    time.sleep(1.5)

    final_value = page.evaluate("sel => document.querySelector(sel)?.value", SELECTOR)
    print(f"\nVALOR FINAL en el campo: '{final_value}'")
    print("(¿Aparecieron letras en pantalla mientras tipeaba? esa es la prueba real)")

    page.screenshot(path="test_1_resultado.png", full_page=False)
    print("Screenshot guardado en test_1_resultado.png")

    input("\nPresioná ENTER para cerrar el navegador...")
    browser.close()
