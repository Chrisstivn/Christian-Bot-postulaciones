from __future__ import annotations

from .base import BaseATSHandler


class GreenhouseHandler(BaseATSHandler):
    ats = "greenhouse"
    known_selectors = {
        "first_name": ("input[name='job_application[first_name]']", "#first_name"),
        "last_name": ("input[name='job_application[last_name]']", "#last_name"),
        "email": ("input[name='job_application[email]']", "#email"),
        "phone": ("input[name='job_application[phone]']", "#phone"),
        "resume": ("input[type='file'][name*='resume']", "input[type='file']"),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.96 if "greenhouse" in text or "grnh.se" in text else 0.0
