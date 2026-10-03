from __future__ import annotations

from .base import BaseATSHandler


class TeamtailorHandler(BaseATSHandler):
    ats = "teamtailor"
    known_selectors = {
        "first_name": ("input[name*='first_name' i]", "input[name*='firstName' i]", "input[autocomplete='given-name']"),
        "last_name": ("input[name*='last_name' i]", "input[name*='lastName' i]", "input[autocomplete='family-name']"),
        "email": ("input[name*='email' i]", "input[type='email']"),
        "phone": ("input[name*='phone' i]", "input[type='tel']"),
        "linkedin": ("input[name*='linkedin' i]", "input[placeholder*='LinkedIn' i]"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.96 if "teamtailor" in text else 0.0
