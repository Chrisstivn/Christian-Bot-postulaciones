"""
application_agent/actions.py
Ejecuta SOLO la acción validada por Pydantic (models.AgentDecision) sobre
el `page` real que ya tenía abierto form_filler.py.

Regla dura no-negociable: este módulo JAMÁS hace click en nada que se
parezca a un botón de submit final. Eso lo sigue decidiendo
SUBMIT_APPLICATIONS en form_filler.py, como hoy.
"""

from __future__ import annotations
import logging

from playwright.sync_api import Page
from models import AgentDecision

log = logging.getLogger("application_agent")

# Si el selector devuelto por el LLM cae en cualquiera de estos textos,
# lo bloqueamos aunque el LLM lo haya marcado como "click_next" -- defensa
# en profundidad además de la regla ya puesta en el prompt.
_SUBMIT_LIKE_WORDS = ("submit", "enviar", "postular", "send application")


def _looks_like_submit(page: Page, selector: str) -> bool:
    try:
        el = page.query_selector(selector)
        if not el:
            return False
        text = (el.inner_text() or "").lower()
        return any(w in text for w in _SUBMIT_LIKE_WORDS)
    except Exception:
        return False


def execute(page: Page, decision: AgentDecision) -> dict:
    """Devuelve un resumen de qué se ejecutó y qué falló, para loguear en
    memory.py. Nunca lanza excepción hacia afuera."""
    outcome = {"filled": [], "failed": [], "clicked_next": False, "blocked_submit": False}

    if decision.action == "fill_fields":
        for field in decision.fields:
            try:
                el = page.query_selector(field.selector)
                if not el or not el.is_visible():
                    outcome["failed"].append(field.name)
                    continue

                if field.field_kind == "select":
                    try:
                        el.select_option(label=field.value)
                    except Exception:
                        el.select_option(value=field.value)
                elif field.field_kind in ("checkbox", "radio"):
                    if field.value.lower() in ("true", "1", "yes", "on"):
                        el.check()
                elif field.field_kind == "file":
                    el.set_input_files(field.value)
                else:
                    el.fill(field.value)
                    # Heurística para campos tipo autocomplete (ej. "Location
                    # (City)" que exige SELECCIONAR una sugerencia, no solo
                    # escribir texto -- esto es lo que falló en el caso real
                    # de Talon.One/Greenhouse). Inofensivo si el campo no es
                    # un autocomplete: si no aparece dropdown, esto no hace
                    # nada visible.
                    try:
                        el.press("ArrowDown")
                        el.press("Enter")
                    except Exception:
                        pass
                outcome["filled"].append(field.name)
            except Exception as e:
                log.warning("Agent no pudo llenar %s (%s): %s", field.name, field.selector, e)
                outcome["failed"].append(field.name)

    elif decision.action == "click_next":
        selector = decision.next_button_selector
        if not selector:
            return outcome
        if _looks_like_submit(page, selector):
            outcome["blocked_submit"] = True
            log.info("Agent intentó click en algo parecido a submit -- bloqueado: %s", selector)
            return outcome
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(1000)
                outcome["clicked_next"] = True
        except Exception as e:
            log.warning("Agent no pudo hacer click en next (%s): %s", selector, e)

    # "wait" y "none" no requieren acción -- el llamador decide qué hacer después.
    return outcome
