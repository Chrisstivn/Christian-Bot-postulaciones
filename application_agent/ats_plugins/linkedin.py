from __future__ import annotations

from .base import BaseATSHandler


class LinkedInHandler(BaseATSHandler):
    ats = "linkedin"
    known_selectors = {
        "first_name": ("input[aria-label*='First name' i]", "input[name*='firstName' i]"),
        "last_name": ("input[aria-label*='Last name' i]", "input[name*='lastName' i]"),
        "email": ("input[aria-label*='Email' i]", "input[type='email']"),
        "phone": ("input[aria-label*='Phone' i]", "input[type='tel']"),
        "location": ("input[aria-label*='City' i]", "input[aria-label*='Location' i]"),
        "resume": ("input[type='file']",),
    }

    def detect(self, url: str, html: str) -> float:
        text = f"{url} {html}".lower()
        return 0.96 if "linkedin.com/jobs" in text or "easy apply" in text else 0.0
