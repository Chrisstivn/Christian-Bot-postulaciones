"""
application_agent/memory.py
Persiste el historial de pasos del agente en Postgres -- tabla NUEVA y
aditiva (`agent_steps`), reusa la MISMA conexión (`get_conn`) que ya usa
queue_service.py. No toca `job_queue` para nada.
"""

from __future__ import annotations
import json
import logging

from queue_service import get_conn  # mismo DSN/pool que la cola existente

log = logging.getLogger("application_agent")


def ensure_schema() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_steps (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                application_id TEXT NOT NULL,
                step_number INT NOT NULL,
                page_state TEXT,
                action TEXT,
                confidence REAL,
                fields_json JSONB,
                outcome_json JSONB,
                screenshot_path TEXT,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_steps_app ON agent_steps(application_id);"
        )
    log.info("Schema de agent_steps verificado/creado.")


def log_step(
    application_id: str,
    step_number: int,
    decision,
    outcome: dict,
    screenshot_path: str = "",
    last_error: str = "",
) -> None:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_steps
                    (application_id, step_number, page_state, action, confidence,
                     fields_json, outcome_json, screenshot_path, last_error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    application_id, step_number, decision.page_state, decision.action,
                    decision.confidence,
                    json.dumps([f.model_dump() for f in decision.fields]),
                    json.dumps(outcome),
                    screenshot_path, last_error,
                ),
            )
    except Exception as e:
        # La memoria es diagnóstico, no crítica -- si Postgres falla acá,
        # NO debe tumbar el intento de llenado del formulario.
        log.warning("No se pudo loguear step del agente en Postgres: %s", e)
