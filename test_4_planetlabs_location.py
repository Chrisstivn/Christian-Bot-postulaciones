"""
test_4_planetlabs_location.py
Script afilado a la estructura REAL de este formulario específico
(Planet Labs, Greenhouse "Job Board 2.0", combobox tipo react-select
conectado a un proxy de geocoding "pelias"). El selector NO es una
adivinanza -- viene del HTML real que mandaste:

    <input ... id="candidate-location" role="combobox"
           aria-autocomplete="list" ...>

(el otro <input required tabindex="-1" aria-hidden="true"
class="remix-...-requiredInput"> que está al lado es un decoy conocido de
react-select para validación HTML5 nativa -- este script lo ignora a
propósito, igual que ya hace form_engine.py).

Corre las 4 variantes UNA DESPUÉS DE OTRA contra el mismo campo (recargando
la página entre cada una) e imprime, para cada una, si aparecieron letras
Y si se disparó la llamada de red al proxy de geocoding. Así identificamos
en un solo run cuál mecanismo es el que falta.

Uso:
    python3 test_4_planetlabs_location.py "https://job-boards.greenhouse.io/planetlabs/jobs/7993116"
"""
import sys
import time
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "https://job-boards.greenhouse.io/planetlabs/jobs/7993116"
SELECTOR = "#candidate-location"
VALUE = "Berlin"


def _wait_geocode_and(page, action):
    """Ejecuta `action` mientras espera (sin bloquear si no llega) una
    respuesta de red al proxy de geocoding, para saber si el tipeo
    realmente disparó la búsqueda real."""
    got_response = {"value": False}

    def _on_response(resp):
        if "geocod" in resp.url.lower() or "pelias" in resp.url.lower() or "autocomplete" in resp.url.lower():
            got_response["value"] = True
            print(f"    -> Llamada de red detectada: {resp.url} (status {resp.status})")

    page.on("response", _on_response)
    action()
    time.sleep(2.5)
    page.remove_listener("response", _on_response)
    return got_response["value"]


def _report(page, label):
    value = page.evaluate(f"document.querySelector('{SELECTOR}')?.value")
    expanded = page.evaluate(f"document.querySelector('{SELECTOR}')?.getAttribute('aria-expanded')")
    print(f"  RESULTADO [{label}]: value='{value}'  aria-expanded='{expanded}'")
    return value


def variant_1_click_and_type(page):
    print("\n=== Variante 1: click normal + page.keyboard.type (letra por letra) ===")
    page.click(SELECTOR, timeout=5000)
    fired = _wait_geocode_and(page, lambda: page.keyboard.type(VALUE, delay=120))
    _report(page, "v1")
    print(f"  ¿Se disparó la llamada de geocoding? {fired}")


def variant_2_fill_then_dispatch(page):
    print("\n=== Variante 2: .fill() nativo de Playwright + dispatch input/change ===")
    page.fill(SELECTOR, VALUE, timeout=5000)
    page.eval_on_selector(
        SELECTOR,
        "el => { el.dispatchEvent(new Event('input', {bubbles: true})); "
        "el.dispatchEvent(new Event('change', {bubbles: true})); }",
    )
    time.sleep(2.5)
    _report(page, "v2")


def variant_3_native_setter_valuetracker(page):
    print("\n=== Variante 3: setter nativo + reset de _valueTracker de React ===")
    result = page.eval_on_selector(
        SELECTOR,
        """(el, value) => {
            const proto = Object.getPrototypeOf(el);
            const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            const tracker = el._valueTracker;
            if (tracker) tracker.setValue('');
            setter.call(el, value);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return { hadTracker: !!tracker };
        }""",
        VALUE,
    )
    print(f"  ¿Tenía _valueTracker de React? {result['hadTracker']}")
    time.sleep(2.5)
    _report(page, "v3")


def variant_4_keydown_per_char_with_focus_check(page):
    print("\n=== Variante 4: keydown+input+keyup manual por cada letra, verificando foco ===")
    page.click(SELECTOR, timeout=5000)
    still_focused = page.evaluate(f"document.activeElement === document.querySelector('{SELECTOR}')")
    print(f"  ¿Quedó focused tras el click? {still_focused}")

    def _type_manual():
        for ch in VALUE:
            page.eval_on_selector(
                SELECTOR,
                """(el, ch) => {
                    const proto = Object.getPrototypeOf(el);
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    const tracker = el._valueTracker;
                    const newValue = el.value + ch;
                    if (tracker) tracker.setValue(el.value);
                    setter.call(el, newValue);
                    el.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: ch, inputType: 'insertText' }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
                }""",
                ch,
            )
            time.sleep(0.12)

    fired = _wait_geocode_and(page, _type_manual)
    _report(page, "v4")
    print(f"  ¿Se disparó la llamada de geocoding? {fired}")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=30)

    variants = [
        variant_1_click_and_type,
        variant_2_fill_then_dispatch,
        variant_3_native_setter_valuetracker,
        variant_4_keydown_per_char_with_focus_check,
    ]

    for variant in variants:
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        try:
            page.wait_for_selector(SELECTOR, timeout=10000)
        except Exception:
            print(f"No encontré {SELECTOR} en la página -- puede que Greenhouse haya cambiado el form. Saltando variante.")
            page.close()
            continue
        try:
            variant(page)
        except Exception as e:
            print(f"  ERROR en esta variante: {e}")
        page.screenshot(path=f"resultado_{variant.__name__}.png")
        page.close()

    print("\nListo. Revisá los screenshots resultado_variant_*.png y decime cuál mostró texto real en el campo.")
    browser.close()
