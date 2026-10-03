"""
application_agent
Punto de entrada único: run_step(page, ...). form_filler.py llama esto
SOLO cuando su propia lógica determinística se queda atascada (no
encuentra next/submit, o quedan campos requeridos sin llenar). No abre
browser propio -- opera sobre el `page` que form_filler ya tiene abierto.
"""

from __future__ import annotations
from pathlib import Path

from playwright.sync_api import Page

SCREENSHOT_DIR = Path("work/screenshots/agent")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_schema_ready = False


def _ensure_schema_once() -> None:
    global _schema_ready
    if not _schema_ready:
        try:
            from . import memory

            memory.ensure_schema()
        except Exception:
            pass  # ver memory.log_step: la memoria nunca debe bloquear el flujo
        _schema_ready = True


def run_step(
    page: Page,
    application_id: str,
    step_number: int,
    job_context: str,
    cv_summary: str,
) -> dict:
    """Un (1) ciclo observar -> decidir -> actuar. Devuelve el mismo
    'outcome' de actions.execute(), más el page_state detectado, para que
    form_filler.py decida si reintentar, seguir, o marcar manual_required."""
    from . import actions, browser, memory, vision

    _ensure_schema_once()

    screenshot_path = str(SCREENSHOT_DIR / f"{application_id}_step{step_number}.png")
    browser.take_focused_screenshot(page, screenshot_path)
    dom_snapshot = browser.get_interactive_snapshot(page)

    decision = vision.analyze_step(screenshot_path, dom_snapshot, job_context, cv_summary)
    outcome = actions.execute(page, decision)

    memory.log_step(
        application_id, step_number, decision, outcome,
        screenshot_path=screenshot_path,
    )

    return {
        "page_state": decision.page_state,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        **outcome,
    }
