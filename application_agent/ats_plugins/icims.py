from __future__ import annotations

from .base import BaseATSHandler


class ICIMSHandler(BaseATSHandler):
    ats = "icims"
    known_selectors = {
        "email": ("input[type='email']", "input[name*='email']"),
        "phone": ("input[type='tel']", "input[name*='phone']"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.95 if "icims" in text else 0.0
