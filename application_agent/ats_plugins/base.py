"""Base ATS plugin contract."""

from __future__ import annotations

from typing import Any


class BaseATSHandler:
    ats = "unknown"
    known_selectors: dict[str, tuple[str, ...]] = {}

    def detect(self, url: str, html: str) -> float:
        return 0.0

    def get_special_fields(self) -> dict[str, tuple[str, ...]]:
        return self.known_selectors

    def selectors_for(self, field_type: str) -> list[str]:
        return list(self.known_selectors.get(field_type, ()))

    def hint_for_candidate(self, candidate: Any) -> str:
        text = (getattr(candidate, "selector", "") + " " + getattr(candidate, "match_text", "")).lower()
        for field_type, selectors in self.known_selectors.items():
            if any(selector.lower().strip("#.[]='\"") in text for selector in selectors):
                return field_type
        return ""

    def handle_navigation(self, page, stats) -> bool:
        return False

    def validate(self, page) -> list[str]:
        return []
