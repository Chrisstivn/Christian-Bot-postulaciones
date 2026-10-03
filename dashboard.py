"""Local Streamlit dashboard for the job application bot.

Run:
    streamlit run dashboard.py

The module is import-safe even when Streamlit is not installed, which keeps
backend smoke tests independent from dashboard dependencies.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

METRICS_PATH = Path("work/metrics.jsonl")
LOGS_PATH = Path("work/application_logs.jsonl")
MEMORY_PATH = Path("work/ats_memory.json")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def metric_count(rows: list[dict[str, Any]], event: str) -> int:
    return sum(1 for row in rows if row.get("event") == event)


def ats_success(metrics: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    started = Counter(row.get("ATS", "unknown") for row in metrics if row.get("event") == "application_started")
    submitted = Counter(row.get("ATS", "unknown") for row in metrics if row.get("event") == "application_submitted")
    return {
        ats: {
            "started": count,
            "submitted": submitted.get(ats, 0),
            "success_rate": round(submitted.get(ats, 0) / count, 3) if count else 0,
        }
        for ats, count in started.items()
    }


def most_problematic_fields(logs: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counter = Counter()
    for row in logs:
        if row.get("event") in ("field_skipped", "field_recovery_failed", "validation_errors"):
            decision = row.get("decision") or {}
            field = decision.get("field_type") or row.get("event")
            counter[field] += 1
    return counter.most_common(15)


def app_rows(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    apps: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in logs:
        app_id = row.get("application_id", "")
        if not app_id:
            continue
        apps[app_id].update(
            {
                "application_id": app_id,
                "company": row.get("company", apps[app_id].get("company", "")),
                "role": row.get("role", apps[app_id].get("role", "")),
                "ATS": row.get("ATS", apps[app_id].get("ATS", "")),
                "last_event": row.get("event"),
            }
        )
    return list(apps.values())


def memory_rows(memory: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for ats, fields in memory.items():
        for field_type, entry in fields.items():
            rows.append(
                {
                    "ATS": ats,
                    "field_type": field_type,
                    "selector": entry.get("selector", ""),
                    "success": entry.get("success", 0),
                    "failure": entry.get("failure", 0),
                }
            )
    return rows


def main() -> None:
    try:
        import streamlit as st
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Streamlit is not installed. Run: venv/bin/pip install -r requirements.txt"
        ) from exc

    st.set_page_config(page_title="Job Application Bot Dashboard", layout="wide")
    st.title("Job Application Bot Dashboard")

    metrics = read_jsonl(METRICS_PATH)
    logs = read_jsonl(LOGS_PATH)
    memory = read_json(MEMORY_PATH)

    if not metrics and not logs:
        st.info("No metrics/logs found yet. Run a dry-run application first to populate work/*.jsonl.")

    total_started = metric_count(metrics, "application_started")
    total_submitted = metric_count(metrics, "application_submitted")
    failed = metric_count(metrics, "application_failed")
    captcha = metric_count(metrics, "captcha")
    login = metric_count(metrics, "login")
    manual = captcha + login + metric_count(metrics, "manual_intervention")
    durations = [float(row.get("duration", 0)) for row in metrics if row.get("event") == "application_submitted" and row.get("duration")]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else 0
    error_rate = round(failed / total_started, 3) if total_started else 0

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Started", total_started)
    col2.metric("Submitted", total_submitted)
    col3.metric("Failed", failed)
    col4.metric("Manual", manual)
    col5.metric("Avg time", f"{avg_duration}s")
    col6.metric("Error rate", error_rate)

    st.subheader("ATS Analytics")
    ats_table = [{"ATS": ats, **values} for ats, values in ats_success(metrics).items()]
    st.dataframe(ats_table, use_container_width=True)

    st.subheader("Applications")
    st.dataframe(app_rows(logs), use_container_width=True)

    st.subheader("Performance")
    st.dataframe(
        [{"field": field, "count": count} for field, count in most_problematic_fields(logs)],
        use_container_width=True,
    )

    st.subheader("ATS Memory")
    st.dataframe(memory_rows(memory), use_container_width=True)

    st.subheader("Job Analytics")
    roles = Counter(row.get("role", "") for row in logs if row.get("role"))
    companies = Counter(row.get("company", "") for row in logs if row.get("company"))
    left, right = st.columns(2)
    left.write("Roles applied")
    left.dataframe([{"role": k, "count": v} for k, v in roles.most_common(20)], use_container_width=True)
    right.write("Companies")
    right.dataframe([{"company": k, "count": v} for k, v in companies.most_common(20)], use_container_width=True)

    st.subheader("CV Analytics")
    st.info("Outcome tracking is not present yet. Add interview/outcome events later to compare CV versions.")


if __name__ == "__main__":
    main()
