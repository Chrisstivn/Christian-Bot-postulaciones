"""
models.py
Esquemas Pydantic para validar TODO lo que entra y sale del backend.
Esto es lo que evita el bug clásico de n8n: "undefined $json" / "schema inconsistente".
Si Gemini devuelve algo que no calza con estos modelos, Pydantic lo rechaza
ANTES de que llegue a Neo4j o al generador de PDF.
"""

from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator
import re


# ---------------------------------------------------------------------------
# 1. Lo que llega desde n8n (scraping crudo)
# ---------------------------------------------------------------------------

class ScrapeInput(BaseModel):
    # min_length=0 (antes 20): el flujo de LinkedIn search manda job_text=""
    # a propósito para cada URL de la lista, y el fallback ya existente en
    # main.py (MIN_JOB_TEXT_CHARS) se encarga de scrapearla solo.
    job_text: str = Field(default="", description="Texto crudo scrapeado de la oferta (puede venir vacío)")
    url: str
    # True cuando la postulación viene del flujo automático que lee links
    # pendientes desde el Google Sheet "Postulaciones" (columna Link/Status).
    from_sheet: bool = False


class CreatePdfInput(ScrapeInput):
    """Mismo payload que /process-application, pero solo genera artefactos."""


class LinkedInSearchInput(BaseModel):
    search_url: str
    max_jobs: int = 10000
    backlog_pages: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Cuantas paginas antiguas avanzar por corrida, ademas del front start=0.",
    )


class EnqueueJobsInput(BaseModel):
    job_urls: List[str]


class TriageInput(BaseModel):
    url: str


# ---------------------------------------------------------------------------
# 1.5. AI Browser Agent (application_agent/) -- fallback SOLO cuando la
# lógica determinística de form_filler.py se queda atascada. Mismo patrón
# guardarraíl que el resto del archivo: si Gemini devuelve algo que no
# calza con esto, Pydantic lo rechaza y el agente aborta ese paso en vez
# de ejecutar una acción no validada sobre el navegador real.
# ---------------------------------------------------------------------------

AgentPageState = Literal[
    "application_form", "login_required", "captcha",
    "confirmation", "unknown",
]
AgentActionType = Literal["fill_fields", "click_next", "wait", "none"]


class AgentField(BaseModel):
    name: str
    selector: str
    value: str
    field_kind: Literal["text", "select", "checkbox", "radio", "file"] = "text"


class AgentDecision(BaseModel):
    page_state: AgentPageState
    confidence: float = Field(ge=0.0, le=1.0)
    action: AgentActionType
    fields: List[AgentField] = Field(default_factory=list)
    next_button_selector: Optional[str] = None
    reasoning: str = ""

    @field_validator("fields")
    @classmethod
    def cap_fields(cls, v):
        # Guardarraíl: nunca ejecutamos más de 25 acciones en un solo paso
        # (protección ante una respuesta rara del modelo).
        return v[:25]


# ---------------------------------------------------------------------------
# 2. Preguntas / requisitos normalizados (estructura dinámica, NO fija)
# ---------------------------------------------------------------------------

QuestionType = Literal["responsibility", "requirement", "benefit", "custom_question", "other"]


class JobQuestion(BaseModel):
    type: QuestionType
    text: str


# ---------------------------------------------------------------------------
# 3. Salida de Gemini (extracción de la oferta) — dinámica, N preguntas
# ---------------------------------------------------------------------------

class BooleanSearchEvidence(BaseModel):
    value: Literal["YES", "NO", "UNKNOWN"] = "UNKNOWN"
    evidence: str = ""


class CompanySizeEvidence(BaseModel):
    value: Literal["MEDIUM_OR_LARGE", "SMALL", "UNKNOWN"] = "UNKNOWN"
    evidence: str = ""


class ExperienceSearchEvidence(BaseModel):
    minimum_years: Optional[float] = Field(default=None, ge=0, le=80)
    strictly_more: bool = False
    evidence: str = ""


class SearchFilterEvidence(BaseModel):
    mining: BooleanSearchEvidence = Field(default_factory=BooleanSearchEvidence)
    company_size: CompanySizeEvidence = Field(default_factory=CompanySizeEvidence)
    multinational: BooleanSearchEvidence = Field(default_factory=BooleanSearchEvidence)
    internship: BooleanSearchEvidence = Field(default_factory=BooleanSearchEvidence)
    experience: ExperienceSearchEvidence = Field(default_factory=ExperienceSearchEvidence)


class JobExtraction(BaseModel):
    company: str
    job_title: str
    location: Optional[str] = ""
    german_required: Literal["YES", "NO"] = "NO"
    # Idioma REAL de la descripción del puesto, decidido por Gemini en la
    # MISMA llamada que ya extrae company/job_title/german_required --
    # reemplaza a langdetect, que daba falsos positivos con texto de
    # LinkedIn mezclado (chrome de la página en alemán + oferta en inglés).
    detected_language: Literal["EN", "DE", "OTHER"] = "OTHER"
    questions: List[JobQuestion] = Field(default_factory=list)
    search_filter_evidence: SearchFilterEvidence = Field(default_factory=SearchFilterEvidence)

    @field_validator("questions")
    @classmethod
    def no_empty_questions(cls, v):
        # Mata el bug de "EMPTY ITEM" en n8n en el origen, no al final del pipeline
        return [q for q in v if q.text and q.text.strip()]


# ---------------------------------------------------------------------------
# 4. Respuesta dinámica a cada pregunta (nunca respuesta_1 / respuesta_2)
# ---------------------------------------------------------------------------

class RoleUpgradeSemanticAssessment(BaseModel):
    """Semantic evidence only; final keep/reject thresholds live in job_quality.py."""

    level: Literal["BELOW_BASELINE", "BASELINE_EQUIVALENT", "ABOVE_BASELINE"]
    remote_status: Literal["FULLY_REMOTE", "HYBRID", "ONSITE", "UNKNOWN"] = "UNKNOWN"
    contract_type: Literal["PERMANENT", "TEMPORARY", "UNKNOWN"] = "UNKNOWN"
    estimated_base_salary_eur: Optional[int] = None
    salary_confidence: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    company_quality: Literal[
        "BELOW_ZALANDO",
        "COMPARABLE_TO_ZALANDO",
        "STRONG_TECH_OR_GLOBAL",
        "UNKNOWN",
    ] = "UNKNOWN"
    strong_bonus_equity: bool = False
    overall_role_superior: bool = False
    exceptional_temporary_upgrade: bool = False
    reason: str = ""


class CandidateFitAssessment(BaseModel):
    """Separate CV/profile-fit decision, run only after the upgrade filter passes."""

    decision: Literal["KEEP", "REJECT"]
    fit_level: Literal["STRONG", "PLAUSIBLE", "WEAK"]
    reason: str


class QuestionAnswer(BaseModel):
    question: str
    answer: str


class AutofillInput(BaseModel):
    url: str
    pdf_path: Optional[str] = None
    pdf_name: Optional[str] = None
    job_text: str = Field(default="", description="Texto de la oferta, si n8n ya lo tiene")
    application_id: Optional[str] = None
    company: str = ""
    job_title: str = ""
    expected_salary: Optional[int] = None
    responses: List[QuestionAnswer] = Field(default_factory=list)
    motivation_answer: str = ""
    experience_answer: str = ""
    job_context: str = ""


# ---------------------------------------------------------------------------
# 5. Bloque de adaptación del CV (lo que Gemini debe devolver para el docx)
# ---------------------------------------------------------------------------

class CVAdaptation(BaseModel):
    nuevo_titulo: str
    nuevo_perfil: str
    nuevo_cargo_actual: str
    nuevas_tareas: List[str] = Field(..., min_length=3, max_length=6)

    # Guardarraíles de seguridad (ver gemini_service.py para el prompt completo)
    empresa_actual_sin_cambios: str  # eco de control, debe matchear el CV maestro
    fechas_actual_sin_cambios: str   # eco de control, debe matchear el CV maestro


# ---------------------------------------------------------------------------
# 6. Objeto final que se guarda en Neo4j / se manda a n8n / arma el PDF
# ---------------------------------------------------------------------------

class ApplicationResult(BaseModel):
    ID: str
    company: str
    job_title: str
    german_required: Literal["YES", "NO"]
    cv_profile: str
    last_position: str
    responses: List[QuestionAnswer]
    motivation_answer: str
    experience_answer: str
    pdf_name: str
    source_url: str = ""
    from_sheet: bool = False

    @field_validator("pdf_name")
    @classmethod
    def safe_filename(cls, v):
        # CV_{Company}_{JobTitle}.pdf, sin espacios ni caracteres raros
        return re.sub(r"[^A-Za-z0-9_.-]", "_", v)
