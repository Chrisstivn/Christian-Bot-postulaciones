from __future__ import annotations

from .base import BaseATSHandler


class JoinHandler(BaseATSHandler):
    ats = "join"
    known_selectors = {
        "first_name": ("input[name*='firstName' i]", "input[name*='first_name' i]", "input[autocomplete='given-name']"),
        "last_name": ("input[name*='lastName' i]", "input[name*='last_name' i]", "input[autocomplete='family-name']"),
        "email": ("input[name*='email' i]", "input[type='email']"),
        "phone": ("input[name*='phone' i]", "input[type='tel']"),
        "linkedin": ("input[name*='linkedin' i]",),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        return 0.96 if "join.com" in f"{url} {html}".lower() else 0.0
