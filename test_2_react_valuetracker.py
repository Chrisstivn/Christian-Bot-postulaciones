"""
test_2_react_valuetracker.py
MÉTODO 2: setter nativo del prototipo de HTMLInputElement (lo que ya se
había probado antes) PERO además resetea el `_valueTracker` interno que
React le pega a cada input controlado.

Por qué esto es distinto y puede ser la pieza que faltaba: React no lee el
DOM para saber si un input "cambió" -- guarda el último valor que ÉL puso
en un objeto oculto `elemento._valueTracker`. Si vos cambiás el `.value`
del input por afuera (aunque sea con el setter nativo + dispatchEvent
correcto) SIN tocar ese tracker, React compara su propio valor cacheado
contra sí mismo, ve que "no cambió" según su registro interno, e ignora tu
cambio o lo pisa de vuelta en el siguiente render. Este es un bug MUY
conocido de testing con inputs controlados por React (por eso
testing-library y herramientas similares tienen este mismo parche).

Uso:
    python3 test_2_react_valuetracker.py "https://boards.greenhouse.io/tuempresa/jobs/123" "input#location-input" "Berlin, Germany"
"""
import sys
import time
from playwright.sync_api import sync_playwright

URL = sys.argv[1]
SELECTOR = sys.argv[2]
VALUE = sys.argv[3] if len(sys.argv) > 3 else "Berlin, Germany"

SET_VALUE_AND_RESET_TRACKER_JS = """
(el, value) => {
    const prototype = Object.getPrototypeOf(el);
    const nativeSetter = Object.getOwnPropertyDescriptor(prototype, 'value').set;

    // LA PARTE QUE FALTABA: resetear el tracker interno de React ANTES
    // de setear el valor, para que React piense que el valor "anterior"
    // era distinto y SÍ note el cambio.
    const tracker = el._valueTracker;
    if (tracker) {
        tracker.setValue('');
    }

    nativeSetter.call(el, value);

    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));

    return { hadTracker: !!tracker, valueAfter: el.value };
}
"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=50)
    page = browser.new_page()
    page.goto(URL, wait_until="networkidle", timeout=30000)

    input(
        "\n>>> Navegá manualmente (si hace falta) hasta que el campo Location "
        "sea visible en pantalla, después presioná ENTER acá en la terminal...\n"
    )

    loc = page.locator(SELECTOR).first
    try:
        loc.click(timeout=3000)
    except Exception as e:
        print(f"Aviso: el click previo falló ({e}), sigo igual -- este método no depende del focus real.")

    result = loc.evaluate(SET_VALUE_AND_RESET_TRACKER_JS, VALUE)
    print(f"¿El input tenía un _valueTracker de React? {result['hadTracker']}")
    print(f"Valor del input inmediatamente después: '{result['valueAfter']}'")

    time.sleep(1.5)  # dar tiempo a que React re-renderice y/o dispare el autocomplete

    final_value = page.evaluate("sel => document.querySelector(sel)?.value", SELECTOR)
    print(f"\nVALOR FINAL en el campo (después de esperar): '{final_value}'")
    if final_value != VALUE:
        print("¡OJO! El valor cambió o se vació después de esperar -- React lo pisó igual.")
    else:
        print("El valor se mantuvo -- buena señal.")

    page.screenshot(path="test_2_resultado.png", full_page=False)
    print("Screenshot guardado en test_2_resultado.png")

    input("\nPresioná ENTER para cerrar el navegador...")
    browser.close()
