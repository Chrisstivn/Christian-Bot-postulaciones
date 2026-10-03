"""Lightweight JSONL metrics store."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import CONFIG


class MetricsTracker:
    def __init__(self, path: str | Path | None = None, enabled: bool | None = None):
        config = CONFIG["metrics"]
        self.path = Path(path or config["path"])
        self.enabled = bool(config["enabled"] if enabled is None else enabled)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **payload: Any) -> None:
        if not self.enabled:
            return
        row = {"ts": time.time(), "event": event, **payload}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def summary(self) -> dict[str, Any]:
        counters = {
            "total_jobs_scraped": 0,
            "total_applications_started": 0,
            "total_submitted": 0,
            "failed_applications": 0,
            "captcha_events": 0,
            "login_events": 0,
            "manual_interventions": 0,
        }
        durations: list[float] = []
        if not self.path.exists():
            return {**counters, "average_completion_time": 0, "success_rate": 0}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            event = row.get("event")
            if event == "job_scraped":
                counters["total_jobs_scraped"] += 1
            elif event == "application_started":
                counters["total_applications_started"] += 1
            elif event == "application_submitted":
                counters["total_submitted"] += 1
                if row.get("duration"):
                    durations.append(float(row["duration"]))
            elif event == "application_failed":
                counters["failed_applications"] += 1
            elif event == "captcha":
                counters["captcha_events"] += 1
                counters["manual_interventions"] += 1
            elif event == "login":
                counters["login_events"] += 1
                counters["manual_interventions"] += 1
            elif event == "manual_intervention":
                counters["manual_interventions"] += 1
        started = counters["total_applications_started"]
        success_rate = counters["total_submitted"] / started if started else 0
        avg = sum(durations) / len(durations) if durations else 0
        return {**counters, "average_completion_time": round(avg, 2), "success_rate": round(success_rate, 3)}


metrics = MetricsTracker()
