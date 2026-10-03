"""
application_agent/browser.py
Wrappers sobre el `Page` de Playwright que YA abrió form_filler.py -- este
módulo NUNCA abre su propio browser ni su propia pestaña. Solo extrae, de
la página existente, lo mínimo necesario para que el LLM decida algo:

  - Screenshot (downscaled/recortado, no full_page en cada paso).
  - Snapshot del DOM filtrado a SOLO elementos interactivos visibles
    (input/select/textarea/button/a), con su label, tipo, y si están en
    estado de error (aria-invalid / clase "error" / mensaje adyacente).

Esto es lo que mantiene el costo en tokens bajo: nunca mandamos el HTML
completo de la página, solo esta lista compacta.
"""

from __future__ import annotations
from typing import Any

from playwright.sync_api import Page

MAX_ELEMENTS = 60  # tope duro -- evita mandar formularios gigantes enteros


def _label_for(page: Page, el) -> str:
    try:
        el_id = el.get_attribute("id")
        if el_id:
            lbl = page.query_selector(f"label[for='{el_id}']")
            if lbl:
                return lbl.inner_text().strip()
        lbl = el.query_selector("xpath=ancestor::label[1]")
        if lbl:
            return lbl.inner_text().strip()
        return (el.get_attribute("placeholder") or el.get_attribute("aria-label") or "").strip()
    except Exception:
        return ""


def _build_selector(el, tag: str) -> str:
    """Selector estable y corto para que el LLM lo devuelva tal cual y
    nosotros lo reusemos en actions.py sin ambigüedad."""
    try:
        el_id = el.get_attribute("id")
        if el_id:
            return f"#{el_id}"
        name = el.get_attribute("name")
        if name:
            return f"{tag}[name='{name}']"
    except Exception:
        pass
    return ""  # sin selector estable -> actions.py lo descarta


def _is_invalid(el) -> bool:
    try:
        if (el.get_attribute("aria-invalid") or "").lower() == "true":
            return True
        cls = (el.get_attribute("class") or "").lower()
        return "error" in cls or "invalid" in cls
    except Exception:
        return False


def get_interactive_snapshot(page: Page) -> list[dict[str, Any]]:
    """Devuelve una lista compacta de elementos interactivos visibles:
    [{tag, type, selector, label, value, required, invalid}, ...]
    Cortada a MAX_ELEMENTS para no reventar el presupuesto de tokens en
    formularios enormes (ej. Workday con 80+ campos)."""
    snapshot = []
    selectors = "input, select, textarea, button, a[href]"
    for el in page.query_selector_all(selectors):
        if len(snapshot) >= MAX_ELEMENTS:
            break
        try:
            if not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            entry = {
                "tag": tag,
                "type": (el.get_attribute("type") or "").lower(),
                "selector": _build_selector(el, tag),
                "label": _label_for(page, el) or (el.inner_text().strip()[:60] if tag in ("button", "a") else ""),
                "value": (el.input_value() if tag in ("input", "textarea", "select") else "") or "",
                "required": el.get_attribute("required") is not None,
                "invalid": _is_invalid(el),
            }
            if not entry["selector"] and tag not in ("button", "a"):
                continue  # sin id/name no podemos referenciarlo de forma segura
            snapshot.append(entry)
        except Exception:
            continue
    return snapshot


def take_focused_screenshot(page: Page, path: str) -> str:
    """Screenshot del viewport actual (NO full_page): más barato en tokens
    y basta para que el LLM vea el estado visual del paso actual."""
    page.screenshot(path=path, full_page=False)
    return path
