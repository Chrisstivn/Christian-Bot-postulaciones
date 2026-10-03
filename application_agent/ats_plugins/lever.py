from __future__ import annotations

from .base import BaseATSHandler


class LeverHandler(BaseATSHandler):
    ats = "lever"
    known_selectors = {
        "first_name": ("input[name='name']", "input[placeholder*='First']"),
        "last_name": ("input[name='name']", "input[placeholder*='Last']"),
        "email": ("input[name='email']", "input[type='email']"),
        "phone": ("input[name='phone']", "input[type='tel']"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        return 0.95 if "jobs.lever.co" in f"{url} {html}".lower() else 0.0
