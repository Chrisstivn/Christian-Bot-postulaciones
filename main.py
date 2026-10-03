"""
main.py
Backend Python (FastAPI) que n8n llama vía HTTP Request.
"""

import os
import re
import uuid
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from models import (
    ScrapeInput,
    CreatePdfInput,
    AutofillInput,
    ApplicationResult,
    QuestionAnswer,
    LinkedInSearchInput,
    EnqueueJobsInput,
    TriageInput,
)
import gemini_service
import docx_adapter
import cv_date_guard
import pdf_generator
import neo4j_service
import queue_service
import job_quality
import search_state
import excel_output
import scraper
import form_filler
from application_agent.metrics import metrics
from candidate_bible import load_candidate_bible

MIN_JOB_TEXT_CHARS = 400

def _is_linkedin_url(url: str) -> bool:
    return "linkedin.com" in url.lower()


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("job-app-backend")

app = FastAPI(title="Job Application Automation Backend")
app.include_router(excel_output.router)


@app.on_event("startup")
def initialize_output_excel():
    excel_output.ensure_workbook()

CV_MAESTRO_DOCX = os.environ.get("CV_MAESTRO_DOCX", "Christian_CV.docx")
CV_MAESTRO_TXT_CACHE = Path("cv_maestro_cache.txt")

REAL_CURRENT_COMPANY = os.environ.get("CV_CURRENT_COMPANY", "")
REAL_CURRENT_DATES = os.environ.get("CV_CURRENT_DATES", "")

WORK_DIR = Path("work")
WORK_DIR.mkdir(exist_ok=True)


def _load_cv_maestro_text() -> str:
    if CV_MAESTRO_TXT_CACHE.exists():
        return CV_MAESTRO_TXT_CACHE.read_text(encoding="utf-8")

    from docx import Document
    doc = Document(CV_MAESTRO_DOCX)
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    CV_MAESTRO_TXT_CACHE.write_text(text, encoding="utf-8")
    return text


def _resolve_job_text(url: str, job_text: str, source: str) -> str:
    job_text = job_text or ""
    if len(job_text.strip()) >= MIN_JOB_TEXT_CHARS:
        return job_text

    log.warning("job_text corto -> re-scraping con Playwright")
    try:
        scraped = scraper.scrape_job_posting(url)
        resolved = scraped["text"] if isinstance(scraped, dict) else scraped
        metrics.record("job_scraped", url=url, source=source)
        return resolved
    except Exception as e:
        raise HTTPException(422, f"No se pudo scrapear la oferta: {e}")


def _extract_job_info_or_discard(url: str, job_text: str):
    try:
        job_info = gemini_service.extract_job_info(job_text)
    except Exception as e:
        raise HTTPException(422, f"Fallo extrayendo info: {e}")

    if not job_info.company or not job_info.job_title:
        raise HTTPException(422, "Faltan campos obligatorios de Gemini")

    return job_info, None


def _build_application_pdf_artifacts(payload: ScrapeInput, app_id: str) -> dict:
    if not (payload.url or "").strip():
        raise HTTPException(
            422,
            "URL vacía: la fila READY debe tener real_apply_url o Link antes de crear el PDF.",
        )

    job_text = _resolve_job_text(payload.url.strip(), payload.job_text, "create_application_pdf")
    # PDF-only path: company/title/location are needed, but application
    # questions are not. Keep Gemini reading the full description while avoiding
    # extraction of unused responsibilities/requirements.
    try:
        job_info = gemini_service.extract_job_info(job_text, include_questions=False)
    except Exception as e:
        raise HTTPException(422, f"Fallo extrayendo info: {e}")

    if not job_info.company or not job_info.job_title:
        raise HTTPException(422, "Faltan campos obligatorios de Gemini")

    cv_maestro_text = _load_cv_maestro_text()

    try:
        candidate_bible = load_candidate_bible()
        adaptation = gemini_service.adapt_cv(cv_maestro_text, job_text, candidate_bible=candidate_bible)
        gemini_service.verify_cv_adaptation_safety(
            adaptation,
            REAL_CURRENT_COMPANY,
            REAL_CURRENT_DATES
        )
    except Exception as e:
        raise HTTPException(422, f"Fallo adaptación CV: {e}")

    adapted_docx_path = WORK_DIR / f"CV_adapted_{app_id}.docx"
    docx_adapter.apply_cv_adaptation(
        CV_MAESTRO_DOCX,
        str(adapted_docx_path),
        adaptation.model_dump()
    )
    # Change only the canonical end date in the generated DOCX XML.
    # No python-docx reopen/save: historical fonts/layout stay exactly as produced.
    cv_date_guard.enforce_real_current_date(str(adapted_docx_path))
    # PDF-only workflow: these fields are not rendered by pdf_generator.
    # Do not spend Gemini calls generating autofill/application answers.
    responses: list[QuestionAnswer] = []
    motivation_answer = ""
    experience_answer = ""

    pdf_name = pdf_generator.build_pdf_name(job_info.company, job_info.job_title)

    try:
        final_pdf_path = pdf_generator.build_final_pdf(
            adapted_docx_path=str(adapted_docx_path),
            company=job_info.company,
            job_title=job_info.job_title,
            motivation_answer=motivation_answer,
            experience_answer=experience_answer,
            responses=responses,
            pdf_name=pdf_name,
        )
    except Exception as e:
        raise HTTPException(500, f"Fallo PDF: {e}")

    # Source of truth for Sheet/download metadata: the file that was actually
    # written to output_pdfs. This prevents n8n/Sheet metadata from ever
    # drifting from the real filename (including the parentheses around the
    # job-title initials).
    pdf_name = Path(final_pdf_path).name

    result = ApplicationResult(
        ID=app_id,
        company=job_info.company,
        job_title=job_info.job_title,
        german_required=job_info.german_required,
        cv_profile=adaptation.nuevo_perfil,
        last_position=adaptation.nuevo_cargo_actual,
        responses=responses,
        motivation_answer=motivation_answer,
        experience_answer=experience_answer,
        pdf_name=pdf_name,
        source_url=payload.url,
        from_sheet=payload.from_sheet,
    )

    try:
        neo4j_service.ingest_application(result)
    except Exception as e:
        log.error("Neo4j error: %s", e)

    # Salary is application/autofill metadata, not part of the CV PDF.
    expected_salary = None
    candidate_context = candidate_bible.to_gemini_context()

    result_dict = result.model_dump()
    result_dict.update(
        {
            "status": "pdf_ready",
            "application_id": app_id,
            "pdf_path": final_pdf_path,
            "docx_path": str(adapted_docx_path),
            "expected_salary": expected_salary,
            "job_text": job_text,
            "job_context": f"{job_text}\n\n{candidate_context}",
            "company": job_info.company,
            "job_title": job_info.job_title,
        }
    )
    return result_dict


def _answer_missing_autofill_context(payload: AutofillInput, cv_maestro_text: str) -> dict:
    company = payload.company
    job_title = payload.job_title
    responses = list(payload.responses)
    motivation_answer = payload.motivation_answer
    experience_answer = payload.experience_answer
    job_text = payload.job_text or ""

    if job_text and (not company or not job_title):
        try:
            job_info = gemini_service.extract_job_info(job_text, include_questions=False)
        except Exception as e:
            raise HTTPException(422, f"Fallo extrayendo info para autofill: {e}")
        if not job_info.company or not job_info.job_title:
            raise HTTPException(422, "Faltan company/job_title para autofill.")
        company = company or job_info.company
        job_title = job_title or job_info.job_title

    return {
        "company": company,
        "job_title": job_title,
        "responses": responses,
        "motivation_answer": motivation_answer,
        "experience_answer": experience_answer,
    }


def _resolve_existing_pdf_path(payload: AutofillInput) -> str:
    if payload.pdf_path:
        pdf_path = Path(payload.pdf_path)
    elif payload.pdf_name:
        pdf_path = pdf_generator.OUTPUT_DIR / payload.pdf_name
    else:
        raise HTTPException(422, "Debes enviar pdf_path o pdf_name para ejecutar solo autofill.")

    if not pdf_path.exists():
        raise HTTPException(404, f"PDF no encontrado: {pdf_path}")
    return str(pdf_path)


# Mapea los estados internos del motor a las 3 opciones simples que pediste
# para la columna "Status" del Google Sheet.
_SHEET_STATUS_MAP = {
    "dry_run": "Completed",
    "submitted": "Completed",
    "manual_required": "Pending Review",
    "manual_required_captcha": "Pending Review",
    "manual_required_login": "Pending Review",
    "manual_required_linkedin": "Pending Review",
    "error": "Error",
}


def _build_sheet_fields(autofill_result: dict, personal_info: dict) -> dict:
    """Arma los 3 campos que pediste para el log de Google Sheets, a partir
    de lo que YA devuelve fill_and_submit_application -- sin duplicar
    lógica en un Code node de n8n. `engine` (cuando existe) trae las
    estadísticas reales del motor (ver EngineStats.as_dict() en
    form_engine.py): de ahí sale qué campos quedaron sin completar."""
    raw_status = autofill_result.get("status", "unknown")
    sheet_status = _SHEET_STATUS_MAP.get(raw_status, "Pending Review")

    notes = [autofill_result.get("detail", "")]
    engine_stats = autofill_result.get("engine") or {}
    skipped = engine_stats.get("skipped_fields") or []
    errors = engine_stats.get("errors") or []
    if skipped:
        notes.append("Campos sin completar: " + ", ".join(str(s) for s in skipped[:8]))
    if errors:
        notes.append("Errores del motor: " + " | ".join(str(e) for e in errors[:5]))

    return {
        "application_status": sheet_status,
        "error_notes": " -- ".join(n for n in notes if n).strip(),
        "bookmarklet": form_filler.build_bookmarklet(personal_info),
    }


def _run_autofill_only(payload: AutofillInput) -> dict:
    app_id = payload.application_id or str(uuid.uuid4())
    pdf_path = _resolve_existing_pdf_path(payload)
    cv_maestro_text = _load_cv_maestro_text()
    context = _answer_missing_autofill_context(payload, cv_maestro_text)
    job_context = payload.job_context
    if not job_context:
        bible_context = load_candidate_bible().to_gemini_context()
        job_context = f"{payload.job_text}\n\n{bible_context}".strip()

    expected_salary = payload.expected_salary
    if expected_salary is None:
        try:
            expected_salary = int(
                load_candidate_bible().get_path(
                    "preferences.minimum_salary_eur",
                    55000,
                )
            )
        except Exception:
            expected_salary = 55000

    autofill_result = form_filler.fill_and_submit_application(
        url=payload.url,
        responses=context["responses"],
        motivation_answer=context["motivation_answer"],
        experience_answer=context["experience_answer"],
        adapted_pdf_path=pdf_path,
        application_id=app_id,
        expected_salary=expected_salary,
        cv_maestro_text=cv_maestro_text,
        job_context=job_context,
        company=context["company"],
        job_title=context["job_title"],
    )

    sheet_personal_info = dict(form_filler.PERSONAL_INFO)
    sheet_personal_info["expected_salary"] = str(expected_salary or 55000)
    autofill_result.update(_build_sheet_fields(autofill_result, sheet_personal_info))

    return {
        "status": autofill_result.get("status", "unknown"),
        "application_id": app_id,
        "pdf_path": pdf_path,
        "company": context["company"],
        "job_title": context["job_title"],
        "autofill": autofill_result,
    }


@app.on_event("startup")
def startup():
    neo4j_service.ensure_schema()
    log.info("Schema de Neo4j verificado/creado.")

    queue_service.ensure_schema()
    log.info("Schema de job_queue (Postgres) verificado/creado.")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/linkedin-search")
def linkedin_search(payload: LinkedInSearchInput):
    """Una search URL por llamada: baja hasta el final y devuelve las nuevas.

    n8n ya rota las 9 URLs una por una, así que el backend no pagina el
    backlog entre ejecuciones. Cada URL se procesa completa antes de pasar
    a la siguiente.
    """
    try:
        found_urls = scraper.scrape_linkedin_search_results(
            payload.search_url,
            max_jobs=max(1, int(payload.max_jobs or 10000)),
            start=0,
        )
    except Exception as e:
        raise HTTPException(422, f"Fallo scrapeando LinkedIn: {e}")

    found_urls = list(dict.fromkeys(found_urls))
    nuevas = [u for u in found_urls if not queue_service.get_by_url(u)]

    log.info(
        "LinkedIn infinite-scroll '%s': encontradas=%d, nuevas=%d",
        payload.search_url, len(found_urls), len(nuevas),
    )

    return {
        "job_urls": found_urls,
        "new_job_urls": nuevas,
        "count": len(found_urls),
        "new_count": len(nuevas),
    }


def _triage_job_url(job_url: str) -> dict:
    """Triage a new/explicitly retried job without touching processed rows.

    Order is intentional:
      1) DB status guard/dedup
      2) scrape / closed-job check
      3) optional user-configured title/work-format/contract filters
      4) no country or language exclusions
      5) ready_for_review (NO salary estimates, scope AI or CV fit)
    """
    existing = queue_service.get_by_url(job_url)
    if existing and existing["status"] not in ("triage_pending", "failed"):
        return {
            "status": "duplicate",
            "job_url": job_url,
            "previous_status": existing["status"],
        }

    try:
        scraped = scraper.scrape_job_posting(job_url)
        job_text = scraped["text"] if isinstance(scraped, dict) else scraped
        work_format = (
            scraped.get("work_format", "Unknown")
            if isinstance(scraped, dict)
            else scraper.extract_work_format(job_text)
        )
        metrics.record("job_scraped", url=job_url, source="triage")
    except Exception as e:
        queue_service.upsert_triage_result(
            job_url,
            status="failed",
            last_error=str(e)[:2000],
        )
        return {"status": "failed", "job_url": job_url, "error": str(e)}

    if _is_linkedin_url(job_url):
        closed_marker = scraper.find_closed_application_marker(job_text)
        if closed_marker:
            reason = f"linkedin_closed:{closed_marker}"
            queue_service.upsert_triage_result(
                job_url,
                status="discarded",
                job_text=job_text,
                work_format=work_format,
                rejected_reason="closed_job",
                last_error=reason,
            )
            return {
                "status": "discarded",
                "job_url": job_url,
                "reason": "closed",
                "rejected_reason": "closed_job",
                "closed_marker": closed_marker,
            }

    try:
        job_info = gemini_service.extract_job_info(job_text, include_questions=False)
    except Exception as e:
        queue_service.upsert_triage_result(
            job_url,
            status="failed",
            job_text=job_text,
            work_format=work_format,
            last_error=str(e)[:2000],
        )
        return {"status": "failed", "job_url": job_url, "error": str(e)}

    # Language metadata is retained for API compatibility, without detection
    # or eligibility checks. Filters below are optional and disabled by default.
    language = ""
    upgrade = job_quality.evaluate_upgrade(
        company=job_info.company,
        job_title=job_info.job_title,
        job_text=job_text,
        work_format=work_format,
    )

    if not upgrade.keep:
        rejected_reason = upgrade.rejected_reason or "title_work_format_excluded"
        queue_service.upsert_triage_result(
            job_url,
            status="discarded",
            language=language,
            company=job_info.company,
            job_title=job_info.job_title,
            german_required=False,
            job_text=job_text,
            work_format=work_format,
            baseline_level=upgrade.comparison.get("level"),
            upgrade_reason=upgrade.comparison.get("reason"),
            rejected_reason=rejected_reason,
            baseline_comparison=upgrade.comparison,
            candidate_fit_decision=None,
            candidate_fit_level=None,
            candidate_fit_reason=None,
            last_error=rejected_reason,
        )
        return {
            "status": "discarded",
            "job_url": job_url,
            "reason": rejected_reason,
            "rejected_reason": rejected_reason,
            "company": job_info.company,
            "job_title": job_info.job_title,
            "work_format": work_format,
            "baseline_level": upgrade.comparison.get("level"),
            "upgrade_reason": upgrade.comparison.get("reason"),
            "baseline_comparison": upgrade.comparison,
        }

    queue_service.upsert_triage_result(
        job_url,
        status="ready_for_review",
        language=language,
        company=job_info.company,
        job_title=job_info.job_title,
        german_required=False,
        job_text=job_text,
        work_format=work_format,
        baseline_level=upgrade.comparison.get("level"),
        upgrade_reason=upgrade.comparison.get("reason"),
        rejected_reason=None,
        baseline_comparison=upgrade.comparison,
        candidate_fit_decision=None,
        candidate_fit_level=None,
        candidate_fit_reason=None,
        last_error=None,
    )
    return {
        "status": "ready",
        "job_url": job_url,
        "company": job_info.company,
        "job_title": job_info.job_title,
        "language": language,
        "description": job_text,
        "work_format": work_format,
        "baseline_level": upgrade.comparison.get("level"),
        "upgrade_reason": upgrade.comparison.get("reason"),
        "baseline_comparison": upgrade.comparison,
        "candidate_fit_level": None,
        "candidate_fit_reason": None,
    }

@app.post("/triage-job")
def triage_job(payload: TriageInput):
    return _triage_job_url(payload.url)


@app.post("/linkedin-search-and-triage")
def linkedin_search_and_triage(payload: LinkedInSearchInput):
    try:
        found_urls = scraper.scrape_linkedin_search_results(
            payload.search_url,
            max_jobs=max(1, int(payload.max_jobs or 10000)),
            start=0,
        )
    except Exception as e:
        raise HTTPException(422, f"Fallo scrapeando LinkedIn: {e}")

    found_urls = list(dict.fromkeys(found_urls))

    # Respect the user's search URL; accept LinkedIn results from any country.
    # Persist discovery BEFORE triage so a dropped n8n/HTTP connection cannot
    # make newly discovered LinkedIn URLs disappear. Existing rows are left
    # untouched by ON CONFLICT, including ready/discarded/failed records.
    enqueue_result = queue_service.enqueue_triage_urls(found_urls)

    # Automatic search processes only genuinely new triage_pending rows.
    # Previous failed rows are retried only through /retry-failed-triage, which
    # is an explicit re-analysis action. Definitive statuses are never touched.
    triage_urls = []
    for url in found_urls:
        existing = queue_service.get_by_url(url)
        if existing and existing["status"] == "triage_pending":
            triage_urls.append(url)

    ready_jobs = []
    discarded_count = 0
    failed_count = 0
    duplicate_count = 0

    for job_url in triage_urls:
        result = _triage_job_url(job_url)
        status = result.get("status")
        if status == "ready":
            ready_jobs.append(result)
        elif status == "discarded":
            discarded_count += 1
        elif status == "failed":
            failed_count += 1
        elif status == "duplicate":
            duplicate_count += 1

    log.info(
        "LinkedIn search+triage '%s': encontradas=%d, nuevas=%d, ready=%d, discarded=%d, failed=%d, duplicates=%d",
        payload.search_url, len(found_urls), len(triage_urls), len(ready_jobs),
        discarded_count, failed_count, duplicate_count,
    )

    return {
        "search_url": payload.search_url,
        "count": len(found_urls),
        "country_discarded_count": 0,
        "new_count": enqueue_result["inserted"],
        "triage_count": len(triage_urls),
        "persisted_pending_count": enqueue_result["inserted"],
        "ready_count": len(ready_jobs),
        "discarded_count": discarded_count,
        "failed_count": failed_count,
        "duplicate_count": duplicate_count,
        "ready_jobs": ready_jobs,
    }


@app.post("/check-linkedin-job-status")
def check_linkedin_job_status(payload: TriageInput):
    """Read-only check used by the manual Sheet maintenance workflow."""
    return scraper.check_linkedin_application_status(payload.url)


@app.post("/retry-failed-triage")
def retry_failed_triage(limit: int = 200):
    """Explicitly re-run failed DB rows through scraping + triage.

    This is intentionally separate from the scheduled search flow so failed
    rows can be retried immediately on demand, even if LinkedIn does not
    rediscover the same URL in a later search.
    """
    failed_jobs = queue_service.list_failed(limit=limit)
    results = []
    counts = {"ready": 0, "discarded": 0, "failed": 0, "duplicate": 0}

    for job in failed_jobs:
        result = _triage_job_url(job["job_url"])
        status = result.get("status", "failed")
        if status in counts:
            counts[status] += 1
        results.append(result)

    return {
        "requested": len(failed_jobs),
        "ready_count": counts["ready"],
        "discarded_count": counts["discarded"],
        "failed_count": counts["failed"],
        "duplicate_count": counts["duplicate"],
        "results": results,
    }


@app.post("/enqueue-jobs")
def enqueue_jobs(payload: EnqueueJobsInput):
    """
    Mete una lista de URLs (ej. la salida de /linkedin-search) a la cola
    de Postgres como 'pending'. Las URLs repetidas se ignoran solas
    (UNIQUE en job_url) -- podés correr esto todos los días sin duplicar.
    """
    result = queue_service.enqueue_urls(payload.job_urls)
    log.info(
        "Encoladas %d URLs nuevas (%d ya existían).",
        result["inserted"], result["skipped_duplicates"]
    )
    return result


@app.get("/queue-stats")
def queue_stats():
    return queue_service.queue_stats()


@app.get("/ready-for-review")
def ready_for_review():
    """Read-only DB -> Sheet reconciliation feed.

    Returns jobs already accepted by triage. This endpoint never scrapes,
    calls Gemini, or changes queue state.
    """
    jobs = queue_service.list_ready_for_review()
    return {
        "count": len(jobs),
        "jobs": [
            {
                "job_url": job["job_url"],
                "company": job.get("company") or "",
                "job_title": job.get("job_title") or "",
                "language": job.get("language") or "",
                "description": job.get("job_text") or "",
                "work_format": job.get("work_format") or "Unknown",
                "baseline_level": job.get("baseline_level") or "",
                "upgrade_reason": job.get("upgrade_reason") or "",
                "rejected_reason": job.get("rejected_reason") or "",
                "baseline_comparison": job.get("baseline_comparison") or {},
                "candidate_fit_decision": job.get("candidate_fit_decision") or "",
                "candidate_fit_level": job.get("candidate_fit_level") or "",
                "candidate_fit_reason": job.get("candidate_fit_reason") or "",
                "last_error": job.get("last_error"),
                "updated_at": job["updated_at"].isoformat() if job.get("updated_at") else None,
            }
            for job in jobs
        ],
    }


@app.post("/process-next")
def process_next():
    """
    Saca el próximo job 'pending' de la cola y corre exactamente el mismo
    pipeline que /process-application. n8n llama a este endpoint en loop
    (con un nodo de tipo 'Loop'/'SplitInBatches' + un 'If') hasta que la
    respuesta sea status='empty', momento en el que ya no queda nada por
    procesar y el flujo puede terminar.
    """
    job = queue_service.claim_next_pending()
    if job is None:
        return {"status": "empty", "detail": "No hay jobs pendientes en la cola"}

    job_id = job["id"]
    job_url = job["job_url"]
    log.info("Procesando desde la cola: %s (%s)", job_url, job_id)

    try:
        payload = ScrapeInput(job_text="", url=job_url, from_sheet=True)
        result = process_application(payload)
    except HTTPException as e:
        queue_service.mark_failed(job_id, str(e.detail))
        return {"status": "failed", "job_url": job_url, "error": str(e.detail)}
    except Exception as e:
        queue_service.mark_failed(job_id, str(e))
        return {"status": "failed", "job_url": job_url, "error": str(e)}

    if isinstance(result, dict) and result.get("status") == "discarded":
        queue_service.mark_discarded(
            job_id,
            reason=result.get("reason", "discarded"),
            language=result.get("language"),
            company=result.get("company"),
            job_title=result.get("job_title"),
        )
        return {"status": "discarded", "job_url": job_url, **result}

    queue_service.mark_done(
        job_id,
        company=result.get("company"),
        job_title=result.get("job_title"),
        language="EN",
        german_required=(result.get("german_required") == "YES"),
    )
    return {"status": "done", "job_url": job_url, "result": result}


@app.post("/create-application-pdf")
def create_application_pdf(payload: CreatePdfInput):
    app_id = str(uuid.uuid4())
    log.info("Creando PDF de aplicación %s url=%s", app_id, payload.url)
    return _build_application_pdf_artifacts(payload, app_id)


@app.post("/autofill-application")
def autofill_application(payload: AutofillInput):
    log.info("Ejecutando solo autofill application_id=%s url=%s", payload.application_id, payload.url)
    try:
        return _run_autofill_only(payload)
    except HTTPException:
        raise
    except Exception as e:
        return {
            "status": "error",
            "application_id": payload.application_id,
            "detail": str(e),
        }


@app.post("/process-application")
def process_application(payload: ScrapeInput):
    app_id = str(uuid.uuid4())
    log.info("Procesando aplicación %s url=%s", app_id, payload.url)

    result_dict = _build_application_pdf_artifacts(payload, app_id)
    if result_dict.get("status") == "discarded":
        return result_dict

    autofill_payload = AutofillInput(
        url=payload.url,
        pdf_path=result_dict["pdf_path"],
        job_text=result_dict.get("job_text", ""),
        application_id=app_id,
        company=result_dict.get("company", ""),
        job_title=result_dict.get("job_title", ""),
        expected_salary=result_dict.get("expected_salary"),
        responses=[QuestionAnswer(**item) for item in result_dict.get("responses", [])],
        motivation_answer=result_dict.get("motivation_answer", ""),
        experience_answer=result_dict.get("experience_answer", ""),
        job_context=result_dict.get("job_context", ""),
    )

    try:
        autofill_result = _run_autofill_only(autofill_payload)["autofill"]
    except Exception as e:
        autofill_result = {"status": "error", "detail": str(e)}

    result_dict["autofill"] = autofill_result
    return result_dict


@app.get("/download-pdf/{pdf_name}")
def download_pdf(pdf_name: str):
    path = pdf_generator.OUTPUT_DIR / pdf_name
    if not path.exists():
        raise HTTPException(404, "PDF no encontrado")

    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=pdf_name
    )
