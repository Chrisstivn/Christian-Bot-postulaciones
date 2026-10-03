from __future__ import annotations

from .base import BaseATSHandler


class PersonioHandler(BaseATSHandler):
    ats = "personio"
    known_selectors = {
        "first_name": ("input[name='first_name']", "input[name*='firstName' i]", "input[id*='first_name' i]"),
        "last_name": ("input[name='last_name']", "input[name*='lastName' i]", "input[id*='last_name' i]"),
        "email": ("input[name='email']", "input[type='email']"),
        "phone": ("input[name*='phone' i]", "input[type='tel']"),
        "location": ("input[name*='location' i]", "input[name*='city' i]"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.96 if "personio" in text else 0.0
