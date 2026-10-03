"""Recovery helpers for failed field interactions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import time

from .config import CONFIG


@dataclass
class RecoveryResult:
    ok: bool
    strategy: str
    attempts: int
    errors: list[str] = field(default_factory=list)
    screenshot: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strategy": self.strategy,
            "attempts": self.attempts,
            "errors": self.errors,
            "screenshot": self.screenshot,
        }


def safe_screenshot(page, application_id: str, suffix: str) -> str:
    directory = Path(CONFIG["screenshots"]["dir"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{application_id}_{suffix}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return ""


def run_with_recovery(
    page,
    application_id: str,
    primary: Callable[[], bool],
    alternatives: list[tuple[str, Callable[[], bool]]] | None = None,
    field_name: str = "field",
) -> RecoveryResult:
    errors: list[str] = []
    attempts = 0
    max_attempts = int(CONFIG["retries"]["recovery_retries"]) + 1
    strategies = [("primary", primary)] + list(alternatives or [])

    for strategy, action in strategies[:max_attempts]:
        attempts += 1
        try:
            if action():
                return RecoveryResult(True, strategy, attempts, errors)
        except Exception as exc:
            errors.append(f"{strategy}:{field_name}:{exc}")
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)

    screenshot = safe_screenshot(page, application_id, f"recovery_{field_name}")
    return RecoveryResult(False, "human_intervention", attempts, errors, screenshot)
