"""
test_3_tab_navigation.py
MÉTODO 3: en vez de hacer click (real o programático) sobre el campo, lo
enfoca navegando con TAB desde un campo anterior -- exactamente como lo
haría alguien navegando el formulario con el teclado. Algunos widgets
(vistos en formularios con autocomplete armado con librerías tipo
downshift/react-aria) solo "arman" sus listeners de teclado cuando detectan
un focus-in real por navegación, y ese caso especial es distinto tanto de
un click de mouse (real o simulado) como de un .focus() por JavaScript --
por eso este método es genuinamente distinto a los otros dos.

Además, después de cada letra verifica que el foco SIGA en el campo
correcto (por si el propio widget lo saca del foco a mitad de tipeo, otra
causa posible de "no aparece nada en pantalla").

Uso:
    python3 test_3_tab_navigation.py "https://boards.greenhouse.io/tuempresa/jobs/123" "input#location-input" "Berlin, Germany"

IMPORTANTE: para este método hace falta que primero hagas UN click manual vos
mismo en el navegador que se abre, en el campo JUSTO ANTERIOR al de
Location (ej. "Last Name"), antes de presionar ENTER en la terminal --
así el script empieza el Tab desde un punto conocido.
"""
import sys
import time
from playwright.sync_api import sync_playwright

URL = sys.argv[1]
SELECTOR = sys.argv[2]
VALUE = sys.argv[3] if len(sys.argv) > 3 else "Berlin, Germany"
MAX_TABS = 15  # cuántos Tabs probamos como máximo para llegar al campo

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=50)
    page = browser.new_page()
    page.goto(URL, wait_until="networkidle", timeout=30000)

    input(
        "\n>>> Navegá manualmente hasta el formulario, y hacé CLICK vos mismo "
        "(con el mouse) en el campo JUSTO ANTERIOR al de Location (ej. 'Last "
        "Name' o el que sea). Después volvé acá y presioná ENTER...\n"
    )

    found = False
    for i in range(MAX_TABS):
        page.keyboard.press("Tab")
        time.sleep(0.15)
        matches = page.evaluate(
            "sel => document.activeElement === document.querySelector(sel)", SELECTOR
        )
        active_desc = page.evaluate(
            "() => { const e = document.activeElement; "
            "return e ? (e.tagName + '#' + (e.id||'') + '.' + (e.className||'').toString().slice(0,40)) : 'ninguno'; }"
        )
        print(f"Tab #{i+1}: elemento focused ahora = {active_desc}  (¿es el nuestro? {matches})")
        if matches:
            found = True
            break

    if not found:
        print(f"\nDespués de {MAX_TABS} Tabs no llegué al selector indicado. "
              "Puede que el campo no sea alcanzable por Tab (tabindex=-1) o que "
              "hayas empezado el Tab desde un lugar distinto al esperado.")
        browser.close()
        sys.exit(1)

    print(f"\nLlegué al campo correcto navegando con Tab. Tipeando '{VALUE}'...")
    for ch in VALUE:
        page.keyboard.type(ch, delay=90)
        still_focused = page.evaluate(
            "sel => document.activeElement === document.querySelector(sel)", SELECTOR
        )
        if not still_focused:
            print(f"¡El foco se salió del campo a mitad de tipeo, justo después de escribir '{ch}'!")
            break

    time.sleep(1.5)
    final_value = page.evaluate("sel => document.querySelector(sel)?.value", SELECTOR)
    print(f"\nVALOR FINAL en el campo: '{final_value}'")

    page.screenshot(path="test_3_resultado.png", full_page=False)
    print("Screenshot guardado en test_3_resultado.png")

    input("\nPresioná ENTER para cerrar el navegador...")
    browser.close()
