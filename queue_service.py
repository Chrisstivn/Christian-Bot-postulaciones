"""
queue_service.py
Cola de jobs en Postgres. Reemplaza el flujo "un webhook = una URL" por
algo más robusto: n8n mete N URLs a la cola de una sola vez (ej. después
de /linkedin-search) y un segundo flujo las va sacando de a una, dejando
trazabilidad de qué pasó con cada una (done / discarded / failed) sin
depender de que n8n mantenga estado.

Tabla (ya la creaste a mano, esto solo la formaliza con IF NOT EXISTS
para que levantar el backend en otra máquina también funcione):

    job_queue(
        id UUID PRIMARY KEY,
        job_url TEXT UNIQUE,
        status TEXT,              -- pending | processing | done | discarded | failed
        company TEXT,
        job_title TEXT,
        language TEXT,
        german_required BOOLEAN,
        job_text TEXT,
        retry_count INT,
        last_error TEXT,
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ
    )
"""

import os
import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

log = logging.getLogger("queue_service")

# Database credentials are supplied through environment variables.
JOBQUEUE_DSN = os.environ.get(
    "JOBQUEUE_DSN",
    "",
)

MAX_RETRIES = int(os.environ.get("JOBQUEUE_MAX_RETRIES", "3"))


@contextmanager
def get_conn():
    conn = psycopg2.connect(JOBQUEUE_DSN)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS job_queue (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                job_url TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                company TEXT,
                job_title TEXT,
                language TEXT,
                german_required BOOLEAN DEFAULT FALSE,
                job_text TEXT,
                work_format TEXT,
                baseline_level TEXT,
                upgrade_reason TEXT,
                rejected_reason TEXT,
                baseline_comparison JSONB,
                candidate_fit_decision TEXT,
                candidate_fit_level TEXT,
                candidate_fit_reason TEXT,
                retry_count INT NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS work_format TEXT;")
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS baseline_level TEXT;")
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS upgrade_reason TEXT;")
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS rejected_reason TEXT;")
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS baseline_comparison JSONB;")
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS candidate_fit_decision TEXT;")
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS candidate_fit_level TEXT;")
        cur.execute("ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS candidate_fit_reason TEXT;")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue(status);"
        )
    log.info("Schema de job_queue verificado/creado.")


def enqueue_urls(urls: list[str]) -> dict:
    """
    Inserta URLs nuevas como 'pending'. Las que ya existen (mismo job_url)
    se ignoran gracias al UNIQUE + ON CONFLICT -- así podés correr
    /linkedin-search todos los días sin duplicar la misma oferta en la cola.
    """
    inserted = 0
    skipped = 0
    with get_conn() as conn, conn.cursor() as cur:
        for url in urls:
            cur.execute(
                """
                INSERT INTO job_queue (job_url)
                VALUES (%s)
                ON CONFLICT (job_url) DO NOTHING
                """,
                (url,),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
    return {"inserted": inserted, "skipped_duplicates": skipped, "total": len(urls)}


def enqueue_triage_urls(urls: list[str]) -> dict:
    """Persist newly discovered URLs specifically for triage.

    Uses triage_pending instead of pending so /process-next (the application/PDF
    queue) can never claim a job that has not passed language/German triage yet.
    Existing URLs are never modified.
    """
    inserted = 0
    skipped = 0
    with get_conn() as conn, conn.cursor() as cur:
        for url in urls:
            cur.execute(
                """
                INSERT INTO job_queue (job_url, status)
                VALUES (%s, 'triage_pending')
                ON CONFLICT (job_url) DO NOTHING
                """,
                (url,),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
    return {"inserted": inserted, "skipped_duplicates": skipped, "total": len(urls)}


def list_ready_for_review() -> list[dict]:
    """Return already-triaged jobs for DB -> Sheet reconciliation.

    Read-only: no scraping, Gemini calls, or status changes.
    """
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            """
            SELECT
                job_url, company, job_title, language, job_text, work_format,
                baseline_level, upgrade_reason, rejected_reason,
                baseline_comparison, candidate_fit_decision,
                candidate_fit_level, candidate_fit_reason,
                last_error, updated_at
            FROM job_queue
            WHERE status = 'ready_for_review'
            ORDER BY updated_at ASC
            """
        )
        return [dict(row) for row in cur.fetchall()]


def list_failed(limit: int = 200) -> list[dict]:
    """Return failed queue rows so they can be explicitly retried on demand."""
    safe_limit = max(1, min(int(limit or 200), 1000))
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            """
            SELECT id, job_url, last_error, retry_count, updated_at
            FROM job_queue
            WHERE status = 'failed'
            ORDER BY updated_at ASC
            LIMIT %s
            """,
            (safe_limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def get_by_url(job_url: str) -> dict | None:
    """
    Dedup: si esta URL ya está en la tabla (sea cual sea su status), la
    devuelve. /triage-job la usa para NUNCA volver a scrapear/analizar
    con Gemini una oferta que ya vimos antes.
    """
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute("SELECT * FROM job_queue WHERE job_url = %s", (job_url,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_triage_result(job_url: str, status: str, **fields) -> None:
    """
    Inserta la fila si es la primera vez que vemos esta URL, o actualiza
    sus campos + status si ya existía (ej. reintentando un 'failed').
    Usado por /triage-job para dejar registro de duplicate/discarded/ready.
    """
    columns = ["status"] + list(fields.keys())
    values = [status] + list(fields.values())
    db_values = [
        psycopg2.extras.Json(value) if isinstance(value, (dict, list)) else value
        for value in values
    ]
    insert_cols = ", ".join(["job_url"] + columns)
    insert_placeholders = ", ".join(["%s"] * (len(columns) + 1))
    set_clause = ", ".join(f"{c} = %s" for c in columns) + ", updated_at = now()"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO job_queue ({insert_cols})
            VALUES ({insert_placeholders})
            ON CONFLICT (job_url) DO UPDATE SET {set_clause}
            """,
            [job_url] + db_values + db_values,
        )


def claim_next_pending() -> dict | None:
    """
    Toma el próximo job 'pending' y lo marca 'processing' de forma atómica.
    FOR UPDATE SKIP LOCKED evita que dos llamadas simultáneas a
    /process-next agarren la misma fila (importante si en algún momento
    corrés varios workers en paralelo).
    """
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            """
            SELECT id, job_url FROM job_queue
            WHERE status = 'pending'
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            return None

        cur.execute(
            "UPDATE job_queue SET status = 'processing', updated_at = now() WHERE id = %s",
            (row["id"],),
        )
        return dict(row)


def mark_done(job_id, company=None, job_title=None, language=None, german_required=None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE job_queue
            SET status = 'done', company = %s, job_title = %s,
                language = %s, german_required = %s, updated_at = now()
            WHERE id = %s
            """,
            (company, job_title, language, german_required, job_id),
        )


def mark_discarded(job_id, reason: str, language=None, company=None, job_title=None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE job_queue
            SET status = 'discarded', last_error = %s, language = %s,
                company = %s, job_title = %s, updated_at = now()
            WHERE id = %s
            """,
            (reason, language, company, job_title, job_id),
        )


def mark_failed(job_id, error: str) -> None:
    """
    Si ya se reintentó MAX_RETRIES veces, la deja en 'failed' definitivo.
    Si no, la vuelve a 'pending' para que /process-next la reintente en la
    próxima corrida.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT retry_count FROM job_queue WHERE id = %s", (job_id,))
        row = cur.fetchone()
        retry_count = (row[0] if row else 0) + 1
        new_status = "failed" if retry_count >= MAX_RETRIES else "pending"
        cur.execute(
            """
            UPDATE job_queue
            SET status = %s, retry_count = %s, last_error = %s, updated_at = now()
            WHERE id = %s
            """,
            (new_status, retry_count, str(error)[:2000], job_id),
        )


def queue_stats() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM job_queue GROUP BY status;")
        rows = cur.fetchall()
    return {status: count for status, count in rows}
