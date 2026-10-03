"""
Central configuration for the application automation agent.

The project intentionally avoids adding a heavy dependency just to read a
small config file. If PyYAML is installed we use it; otherwise a tiny parser
handles the simple nested key/value shape used by config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "confidence": {
        "auto_fill_threshold": 0.85,
        "fill_log_threshold": 0.60,
        "skip_threshold": 0.60,
    },
    "retries": {
        "max_retries": 8,
        "field_retries": 3,
        "recovery_retries": 2,
    },
    "ats_memory": {"enabled": True, "path": "work/ats_memory.json"},
    "metrics": {"enabled": True, "path": "work/metrics.jsonl"},
    "logging": {"enabled": True, "path": "work/application_logs.jsonl"},
    "screenshots": {"dir": "work/screenshots"},
    "manual_intervention": {"timeout_seconds": 900},
    "questions": {"use_contextual_classification": True},
}


def _coerce_scalar(value: str) -> Any:
    value = value.strip().strip('"').strip("'")
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _tiny_yaml_load(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            data[current_section] = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = _coerce_scalar(value)
        if raw_line.startswith(" ") and current_section:
            data.setdefault(current_section, {})[key] = value
        else:
            data[key] = value
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path or os.environ.get("APPLICATION_AGENT_CONFIG", "config.yaml"))
    loaded: dict[str, Any] = {}
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore

            loaded = yaml.safe_load(text) or {}
        except Exception:
            loaded = _tiny_yaml_load(text)
    config = _deep_merge(DEFAULT_CONFIG, loaded)

    config["confidence"]["auto_fill_threshold"] = float(
        os.environ.get("ATS_CONFIDENCE_AUTO_FILL", config["confidence"]["auto_fill_threshold"])
    )
    config["confidence"]["fill_log_threshold"] = float(
        os.environ.get("ATS_CONFIDENCE_FILL_LOG", config["confidence"]["fill_log_threshold"])
    )
    config["retries"]["max_retries"] = int(os.environ.get("ATS_MAX_RETRIES", config["retries"]["max_retries"]))
    config["retries"]["field_retries"] = int(os.environ.get("ATS_FIELD_RETRIES", config["retries"]["field_retries"]))
    return config


CONFIG = load_config()
