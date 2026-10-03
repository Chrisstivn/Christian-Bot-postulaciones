from __future__ import annotations

from .base import BaseATSHandler


class AmazonJobsHandler(BaseATSHandler):
    ats = "amazonjobs"
    known_selectors = {
        "first_name": ("input[name*='firstName' i]", "input[id*='firstName' i]", "input[autocomplete='given-name']"),
        "last_name": ("input[name*='lastName' i]", "input[id*='lastName' i]", "input[autocomplete='family-name']"),
        "email": ("input[name*='email' i]", "input[type='email']"),
        "phone": ("input[name*='phone' i]", "input[type='tel']"),
        "location": ("input[name*='location' i]", "input[name*='city' i]"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.97 if "amazon.jobs" in text else 0.0
