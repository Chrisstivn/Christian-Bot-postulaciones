from __future__ import annotations

from .base import BaseATSHandler


class AshbyHandler(BaseATSHandler):
    ats = "ashby"
    known_selectors = {
        "first_name": ("input[name='firstName']",),
        "last_name": ("input[name='lastName']",),
        "email": ("input[name='email']", "input[type='email']"),
        "phone": ("input[name='phone']",),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        return 0.96 if "ashby" in f"{url} {html}".lower() else 0.0
