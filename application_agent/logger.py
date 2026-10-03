"""Structured JSONL logger for each application run."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import CONFIG


class ApplicationLogger:
    def __init__(self, application_id: str, company: str = "", role: str = "", ats: str = "unknown"):
        self.application_id = application_id
        self.company = company
        self.role = role
        self.ats = ats
        self.path = Path(CONFIG["logging"]["path"])
        self.enabled = bool(CONFIG["logging"]["enabled"])
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, name: str, **payload: Any) -> None:
        if not self.enabled:
            return
        row = {
            "ts": time.time(),
            "application_id": self.application_id,
            "company": self.company,
            "role": self.role,
            "ATS": self.ats,
            "event": name,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
