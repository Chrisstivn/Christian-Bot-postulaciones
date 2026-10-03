from __future__ import annotations

from .base import BaseATSHandler


class SuccessFactorsHandler(BaseATSHandler):
    ats = "successfactors"
    known_selectors = {
        "first_name": ("input[name*='firstName' i]", "input[id*='firstName' i]", "input[autocomplete='given-name']"),
        "last_name": ("input[name*='lastName' i]", "input[id*='lastName' i]", "input[autocomplete='family-name']"),
        "email": ("input[name*='email' i]", "input[type='email']"),
        "phone": ("input[name*='phone' i]", "input[type='tel']"),
        "location": ("input[name*='city' i]", "input[name*='location' i]"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.96 if any(token in text for token in ("successfactors", "sapsf.", "sfcareer")) else 0.0
