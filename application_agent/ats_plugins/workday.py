from __future__ import annotations

from .base import BaseATSHandler


class WorkdayHandler(BaseATSHandler):
    ats = "workday"
    known_selectors = {
        "email": ("input[data-automation-id*='email']", "input[type='email']"),
        "phone": ("input[data-automation-id*='phone']", "input[type='tel']"),
        "resume": ("input[type='file']",),
        "location": ("input[data-automation-id*='location']",),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.96 if "myworkdayjobs.com" in text or "workday" in text else 0.0
