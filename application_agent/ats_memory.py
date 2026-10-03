"""Small JSON learning layer keyed by ATS and canonical field type."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CONFIG


@dataclass
class MemoryEntry:
    selector: str
    field_type: str
    success: int = 0
    failure: int = 0
    timestamp: float = 0.0
    ats: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "field_type": self.field_type,
            "success": self.success,
            "failure": self.failure,
            "timestamp": self.timestamp or time.time(),
            "ATS": self.ats,
        }


class ATSMemory:
    def __init__(self, path: str | Path | None = None, enabled: bool | None = None):
        config = CONFIG["ats_memory"]
        self.path = Path(path or config["path"])
        self.enabled = bool(config["enabled"] if enabled is None else enabled)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, dict[str, Any]]] = self._load()

    def _load(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self.enabled or not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self) -> None:
        if not self.enabled:
            return
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def get(self, ats: str, field_type: str) -> MemoryEntry | None:
        raw = self._data.get(ats, {}).get(field_type)
        if not raw:
            return None
        return MemoryEntry(
            selector=raw.get("selector", ""),
            field_type=raw.get("field_type", field_type),
            success=int(raw.get("success", 0)),
            failure=int(raw.get("failure", 0)),
            timestamp=float(raw.get("timestamp", 0.0)),
            ats=raw.get("ATS", ats),
        )

    def selectors_for(self, ats: str, field_type: str) -> list[str]:
        entry = self.get(ats, field_type)
        return [entry.selector] if entry and entry.selector else []

    def record_success(self, ats: str, field_type: str, selector: str) -> None:
        if not selector:
            return
        section = self._data.setdefault(ats, {})
        raw = section.setdefault(field_type, {"selector": selector, "field_type": field_type, "success": 0, "failure": 0})
        raw["selector"] = selector
        raw["field_type"] = field_type
        raw["success"] = int(raw.get("success", 0)) + 1
        raw["timestamp"] = time.time()
        raw["ATS"] = ats
        self._save()

    def record_failure(self, ats: str, field_type: str, selector: str) -> None:
        if not selector:
            return
        section = self._data.setdefault(ats, {})
        raw = section.setdefault(field_type, {"selector": selector, "field_type": field_type, "success": 0, "failure": 0})
        raw["failure"] = int(raw.get("failure", 0)) + 1
        raw["timestamp"] = time.time()
        raw["ATS"] = ats
        self._save()
