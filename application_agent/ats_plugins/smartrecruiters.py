from __future__ import annotations

from .base import BaseATSHandler


class SmartRecruitersHandler(BaseATSHandler):
    ats = "smartrecruiters"
    known_selectors = {
        "email": ("input[type='email']", "input[name*='email']"),
        "phone": ("input[type='tel']", "input[name*='phone']"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        return 0.95 if "smartrecruiters" in f"{url} {html}".lower() else 0.0
