"""
application_agent/form_engine.py
Motor deterministico y modular para formularios ATS modernos.

Se integra como helper interno de form_filler.py: no abre navegador, no cambia
endpoints y no reemplaza la arquitectura existente. Opera sobre el Page que ya
creo form_filler y ejecuta el ciclo:
inspeccionar -> clasificar -> filtrar -> mapear -> completar -> validar ->
buscar nuevos campos -> next/submit.
"""

from __future__ import annotations

import datetime
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeoutError

from models import QuestionAnswer
import gemini_service
from . import ats_detector, confidence_engine, recovery_engine, account_access, deterministic_answers
from .ats_memory import ATSMemory
from .ats_plugins import get_handler
from .config import CONFIG
from .logger import ApplicationLogger
from .metrics import metrics
from candidate_bible import load_candidate_bible

log = logging.getLogger("ats_form_engine")

# GUARDARRAÍL DE DIAGNÓSTICO: hasta ahora este módulo llamaba a log.info(...)
# / log.warning(...) en decenas de puntos clave (incluida TODA la lógica de
# "Location (City)"), pero nada en el proyecto llamaba nunca a
# logging.basicConfig(). Sin un handler configurado, el logger raíz de
# Python descarta en silencio cualquier mensaje por debajo de WARNING --
# por eso en la terminal de uvicorn nunca se veía nada, aunque el código
# ya estuviera diagnosticando el problema paso a paso.
#
# Esto configura un handler propio SOLO para este logger (no toca el
# logging de uvicorn/otros módulos) y lo deja en INFO por defecto, para
# que salga directo en la terminal donde corre `uvicorn main:app`.
# Se puede subir el detalle con: export ATS_LOG_LEVEL=DEBUG
if not log.handlers:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s", "%H:%M:%S")
    )
    log.addHandler(_console_handler)
    log.setLevel(os.environ.get("ATS_LOG_LEVEL", "INFO").upper())
    log.propagate = False  # no lo dupliques también en el logger raíz

MAX_RETRIES = int(CONFIG["retries"]["max_retries"])
MAX_DYNAMIC_FIELD_PASSES = int(os.environ.get("ATS_MAX_DYNAMIC_FIELD_PASSES", "4"))
FUZZY_MATCH_THRESHOLD = float(os.environ.get("ATS_FUZZY_MATCH_THRESHOLD", "0.45"))
ALLOW_GEMINI_FORM_OPEN_TEXT_FALLBACK = os.environ.get("ALLOW_GEMINI_FORM_OPEN_TEXT_FALLBACK", "true").lower() == "true"

DEFAULT_IGNORE_KEYWORDS = [
    "diversity", "ethnicity", "race", "gender", "pronouns", "veteran",
    "disability", "referral", "referred by",
    "marketing communications", "marketing emails", "newsletter",
    "talent community", "sms alerts", "job alerts",
    "privacy policy", "terms", "consent to receive",
]

IGNORE_KEYWORDS = [
    item.strip().lower()
    for item in os.environ.get("ATS_IGNORE_KEYWORDS", ",".join(DEFAULT_IGNORE_KEYWORDS)).split(",")
    if item.strip()
]

CAPTCHA_MARKERS = ("recaptcha", "g-recaptcha", "hcaptcha", "turnstile", "arkose")
LOGIN_MARKERS = (
    "sign in", "log in", "login", "create account", "create an account",
    "password", "two-factor", "verification code", "iniciar sesion",
)
SUCCESS_MARKERS = (
    "thank you", "application submitted", "submitted", "we received your application",
    "thanks for applying", "application complete", "successfully submitted",
)
ERROR_SELECTORS = "[aria-invalid='true'], [role='alert'], .invalid, .error, .field-error"
FIELD_SELECTOR = (
    "input, textarea, select, [role='combobox'], [contenteditable='true'], "
    "[role='checkbox'], [role='radio']"
)
NEXT_BUTTON_TEXTS = (
    "Next", "Continue", "Save & Continue", "Save and Continue",
    "Continue application", "Continue to application", "Review",
    "Review application", "Proceed", "Next step",
    "Weiter", "Weiter zur Bewerbung", "Siguiente", "Continuar",
)
SUBMIT_BUTTON_TEXTS = (
    "Submit",
    "Submit application",
    "Submit Application",
    "Submit your application",
    "Send application",
    "Send my application",
    "Complete application",
    "Finish application",
    "Apply",
    "Apply now",
    "Apply for this job",
    "Apply for this position",
    "Enviar",
    "Postular",
    "Bewerbung absenden",
)
POPUP_TEXTS = (
    "Accept", "Accept all", "I agree", "Agree", "Got it", "OK", "Close",
    "No thanks", "Reject all", "Aceptar", "Acepto", "Schliessen", "Alle akzeptieren",
)


@dataclass
class FieldCandidate:
    frame: Frame
    element: Any
    selector: str
    field_type: str
    label: str = ""
    aria_label: str = ""
    placeholder: str = ""
    name: str = ""
    element_id: str = ""
    nearby_text: str = ""
    required: bool = False
    maxlength: Optional[int] = None
    filled: bool = False
    ignored_reason: str = ""

    @property
    def match_text(self) -> str:
        parts = [
            self.aria_label, self.label, self.placeholder, self.name,
            self.element_id, self.nearby_text,
        ]
        return " ".join(p for p in parts if p).strip()


@dataclass
class EngineStats:
    ats: str = "unknown"
    ats_confidence: float = 0.0
    detected: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    buttons_clicked: list[str] = field(default_factory=list)
    pages: int = 0
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ATS": self.ats,
            "ats_confidence": self.ats_confidence,
            "detected_fields": self.detected,
            "ignored_fields": self.ignored,
            "filled_fields": self.filled,
            "skipped_fields": self.skipped,
            "confidence_scores": self.decisions,
            "errors": self.errors,
            "buttons_clicked": self.buttons_clicked,
            "pages": self.pages,
            "elapsed_seconds": round(time.time() - self.started_at, 2),
        }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _safe_attr(el: Any, attr: str) -> str:
    try:
        return (el.get_attribute(attr) or "").strip()
    except Exception:
        return ""


def _safe_inner_text(el: Any, limit: int = 160) -> str:
    try:
        return re.sub(r"\s+", " ", (el.inner_text() or "").strip())[:limit]
    except Exception:
        return ""


def _build_selector(el: Any, tag: str) -> str:
    el_id = _safe_attr(el, "id")
    if el_id:
        return f"#{el_id}"
    name = _safe_attr(el, "name")
    if name:
        return f"{tag}[name='{name}']"
    aria = _safe_attr(el, "aria-label")
    if aria:
        return f"{tag}[aria-label='{aria}']"
    placeholder = _safe_attr(el, "placeholder")
    if placeholder:
        return f"{tag}[placeholder='{placeholder}']"
    return ""


def _label_for(frame: Frame, el: Any) -> str:
    el_id = _safe_attr(el, "id")
    if el_id:
        try:
            label = frame.query_selector(f"label[for='{el_id}']")
            if label:
                return _safe_inner_text(label)
        except Exception:
            pass
    try:
        label = el.query_selector("xpath=ancestor::label[1]")
        if label:
            return _safe_inner_text(label)
    except Exception:
        pass
    try:
        labelled_by = _safe_attr(el, "aria-labelledby")
        if labelled_by:
            texts = []
            for token in labelled_by.split():
                ref = frame.query_selector(f"#{token}")
                if ref:
                    texts.append(_safe_inner_text(ref))
            if texts:
                return " ".join(texts).strip()
    except Exception:
        pass
    return ""


def _nearby_text(el: Any) -> str:
    try:
        return el.evaluate(
            """e => {
                const parts = [];
                let n = e.parentElement;
                for (let i = 0; n && i < 2; i++, n = n.parentElement) {
                    const text = (n.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (text) parts.push(text.slice(0, 160));
                }
                return parts.join(' ');
            }"""
        ) or ""
    except Exception:
        return ""


def _is_hidden_or_unusable(el: Any, include_hidden_file: bool = False) -> bool:
    tag = ""
    field_type = _safe_attr(el, "type").lower()
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()")
    except Exception:
        pass
    if tag == "input" and field_type == "file" and include_hidden_file:
        return False
    if field_type in ("hidden", "submit", "button", "reset"):
        return True
    if _safe_attr(el, "aria-hidden").lower() == "true":
        return True
    if _safe_attr(el, "disabled") or _safe_attr(el, "readonly"):
        return True
    try:
        return not el.is_visible()
    except Exception:
        return True


def _classify_field(el: Any) -> str:
    tag = ""
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()")
    except Exception:
        pass
    role = _safe_attr(el, "role").lower()
    input_type = (_safe_attr(el, "type") or "text").lower()
    autocomplete = _safe_attr(el, "autocomplete").lower()
    aria_autocomplete = _safe_attr(el, "aria-autocomplete").lower()

    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"
    if input_type == "file":
        return "file"
    if input_type in ("checkbox",) or role == "checkbox":
        return "checkbox"
    if input_type in ("radio",) or role == "radio":
        return "radio"
    if role == "combobox":
        return "combobox"
    if input_type == "date":
        return "date"
    if input_type == "number":
        return "number"
    if input_type == "email" or "email" in autocomplete:
        return "email"
    if input_type == "tel" or "tel" in autocomplete:
        return "phone"
    # ARIA es la señal REAL de "esto es un buscador con sugerencias", y no
    # depende de que el ATS haya puesto autocomplete="off" en el <input>
    # (Greenhouse lo hace justamente para bloquear el autofill nativo del
    # navegador en su propio widget de ciudad/lugar).
    if aria_autocomplete in ("list", "both"):
        return "autocomplete"
    # BUG REAL corregido acá (visto en logs de producción): había un
    # fallback `if autocomplete and autocomplete not in ("off", "on"):
    # return "autocomplete"`. El atributo HTML `autocomplete` estándar del
    # navegador usa valores como "given-name", "family-name", "tel",
    # "street-address", etc. -- NINGUNO de esos es "off" ni "on", así que
    # ese fallback clasificaba CUALQUIER campo de texto normal (First
    # Name, Last Name, dirección...) como si fuera un buscador con
    # sugerencias. Y al revés: el propio widget de ciudad de Greenhouse
    # pone autocomplete="off" a propósito (para bloquear el autofill
    # nativo), así que ese fallback lo EXCLUÍA justo a él. La lógica
    # estaba invertida. Se saca por completo -- la detección real de
    # "esto es un buscador con sugerencias" ya la cubren aria_autocomplete
    # arriba y _looks_like_location_field (llamado aparte en
    # _fill_text_like como red de seguridad por label/nombre/id).
    return "text"


_LOCATION_FIELD_KEYWORDS = (
    "location", "city", "ubicaci", "wohnort", "standort", "ciudad",
)


def _own_field_container_text(el: Any) -> str:
    """Sube por los contenedores padre hasta encontrar el que envuelve
    EXACTAMENTE este campo (y ningún otro input/select/textarea) -- el
    wrapper típico de un componente de formulario: <div><label>Location
    (City)</label><input/><span class="error"/></div>. Ese contenedor es
    seguro: su texto es la etiqueta/error DE ESTE campo, no de otros.

    Por qué no usar simplemente "2 niveles de innerText" (lo que hacía la
    versión vieja): en un formulario con los campos apilados uno tras
    otro, subir 2 niveles desde CUALQUIER campo puede llegar al
    contenedor que envuelve TODO el formulario -- ahí el innerText mezcla
    las etiquetas de TODOS los campos hermanos. Así "First Name" terminaba
    "viendo" la palabra "Location" de otro campo completamente distinto.
    Frenar la subida apenas hay más de un input evita esa mezcla."""
    try:
        return el.evaluate(
            """e => {
                let node = e.parentElement;
                for (let i = 0; node && i < 5; i++, node = node.parentElement) {
                    const inputs = node.querySelectorAll('input, select, textarea');
                    if (inputs.length > 1) break;  // ya se mezclarian varios campos
                    if (inputs.length === 1) {
                        return (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
                    }
                }
                return '';
            }"""
        ) or ""
    except Exception:
        return ""


def _looks_like_location_field(field: FieldCandidate) -> bool:
    """Red de seguridad adicional para cuando NI el atributo `autocomplete`
    NI `aria-autocomplete` delatan el widget (cada ATS/template arma esto
    distinto -- Greenhouse, Lever, Personio, formularios propios). Un
    campo de ciudad/ubicación CASI SIEMPRE es, en la práctica, un buscador
    con sugerencias que hay que confirmar haciendo click en una opción
    real -- basta con mirar el label para saber que hay que intentar
    seleccionar una sugerencia después de tipear, sin importar cómo lo
    haya marcado el HTML.

    Historial de bugs en esta función:
      1. Antes miraba `field.match_text` (incluye nearby_text = innerText
         de los 2 contenedores padre completos) -> "First Name"/"Last
         Name" matcheaban por accidente con copy circundante.
      2. Se sacó nearby_text del todo -> el campo "Location (City)" de
         Greenhouse (que no tiene label vinculado por for=/aria-labelledby,
         solo un <div> visual suelto) dejó de detectarse por completo.
      3. (fix actual) Se agrega _own_field_container_text: sube por los
         contenedores hasta el que envuelve EXACTAMENTE este campo (y
         ningún otro input), evitando tanto el ruido del bug 1 (mezclar
         texto de otros campos/copy) como el hueco del bug 2 (no
         encontrar el label visual sin ARIA)."""
    text = _norm(
        " ".join(
            p for p in (
                field.aria_label, field.label, field.placeholder,
                field.name, field.element_id, _own_field_container_text(field.element),
            )
            if p
        )
    )
    return any(k in text for k in _LOCATION_FIELD_KEYWORDS)


def _is_field_to_ignore(candidate: FieldCandidate) -> bool:
    haystack = _norm(
        " ".join(
            [
                candidate.label,
                candidate.placeholder,
                candidate.aria_label,
                candidate.name,
                candidate.element_id,
                candidate.nearby_text,
            ]
        )
    )
    matched = any(keyword in haystack for keyword in IGNORE_KEYWORDS)
    if not matched:
        return False

    # Terms/privacy checkboxes are functional application controls, not
    # optional profile questions. Process them even when the ATS forgot to
    # expose required=true so the deterministic "Yes" consent can be applied.
    if (
        candidate.field_type == "checkbox"
        and any(k in haystack for k in ("privacy policy", "terms", "gdpr", "data privacy"))
        and not any(k in haystack for k in ("newsletter", "marketing communications", "marketing emails"))
    ):
        return False

    # Si es EEO/demografico pero OBLIGATORIO y de tipo cerrado
    # (select/combobox/checkbox/radio), lo dejamos pasar -- se va a
    # responder con una opcion segura de "prefer not to say" (ver
    # DECLINE_OPTION_KEYWORDS mas abajo), NUNCA con un dato inventado en
    # texto libre. Si es opcional, se sigue ignorando como antes.
    if candidate.required and candidate.field_type in ("select", "combobox", "checkbox", "radio"):
        return False
    return True


def _candidate_from_element(frame: Frame, el: Any, include_hidden_file: bool = False) -> Optional[FieldCandidate]:
    if _is_hidden_or_unusable(el, include_hidden_file=include_hidden_file):
        return None
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()")
    except Exception:
        return None

    field_type = _classify_field(el)
    selector = _build_selector(el, tag)
    if not selector and field_type != "file":
        selector = FIELD_SELECTOR
    maxlength_raw = _safe_attr(el, "maxlength")
    maxlength = int(maxlength_raw) if maxlength_raw.isdigit() else None
    return FieldCandidate(
        frame=frame,
        element=el,
        selector=selector,
        field_type=field_type,
        aria_label=_safe_attr(el, "aria-label"),
        label=_label_for(frame, el),
        placeholder=_safe_attr(el, "placeholder"),
        name=_safe_attr(el, "name"),
        element_id=_safe_attr(el, "id"),
        nearby_text=_nearby_text(el),
        required=bool(_safe_attr(el, "required") or _safe_attr(el, "aria-required").lower() == "true"),
        maxlength=maxlength,
    )


def inspect_fields(page: Page, stats: EngineStats) -> list[FieldCandidate]:
    fields: list[FieldCandidate] = []
    for frame in page.frames:
        try:
            elements = frame.query_selector_all(FIELD_SELECTOR)
        except Exception:
            continue
        for el in elements:
            candidate = _candidate_from_element(frame, el, include_hidden_file=True)
            if not candidate:
                continue
            label = candidate.match_text or candidate.field_type
            stats.detected.append(label[:120])
            if _is_field_to_ignore(candidate):
                candidate.ignored_reason = "blacklist"
                stats.ignored.append(label[:120])
                continue
            fields.append(candidate)
    log.info("ATS engine detected=%d usable=%d ignored=%d", len(stats.detected), len(fields), len(stats.ignored))
    return fields


def _current_value(field: FieldCandidate) -> str:
    try:
        if field.field_type in ("checkbox", "radio"):
            return "true" if field.element.is_checked() else ""
        return (field.element.input_value() or "").strip()
    except Exception:
        return ""


def _normalize_value(value: str, field_type: str) -> str:
    value = str(value or "").strip()
    if field_type == "email":
        match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", value)
        return match.group(0) if match else value
    if field_type == "phone":
        phone = re.sub(r"[^\d+]", "", value)
        # Greenhouse normally has a separate country selector; when Germany is
        # selected, the phone input should receive the national number only.
        if phone.startswith("+49"):
            phone = phone[3:]
        elif phone.startswith("0049"):
            phone = phone[4:]
        return phone.lstrip("0").replace(" ", "")
    if field_type == "number":
        match = re.search(r"\d+(?:[.,]\d+)?", value)
        return match.group(0).replace(",", ".") if match else value
    if field_type == "date":
        if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return value
        return datetime.date.today().isoformat()
    return value


def _truncate_for_maxlength(value: str, maxlength: Optional[int]) -> str:
    if maxlength and len(value) > maxlength:
        return value[: max(0, maxlength - 1)].rstrip()
    return value


def _personal_answer(field: FieldCandidate, personal_info: dict[str, str]) -> Optional[str]:
    text = _norm(field.match_text)
    mapping = {
        "first_name": ("first name", "given name", "nombre", "vorname"),
        "last_name": ("last name", "family name", "surname", "apellido", "nachname"),
        "email": ("email", "e-mail", "correo"),
        "phone": ("phone", "mobile", "telephone", "telefono", "telefon"),
        "linkedin": ("linkedin",),
        "location": ("location", "city", "ubicacion", "standort", "wohnort"),
        "available_from": ("available from", "availability", "start date", "earliest start"),
        "expected_salary": ("salary", "compensation", "gehalt", "sueldo"),
    }
    for key, keywords in mapping.items():
        if any(keyword in text for keyword in keywords):
            return str(personal_info.get(key, "") or "")
    return None


def map_answer(
    field: FieldCandidate,
    personal_info: dict[str, str],
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    cv_maestro_text: str,
    job_context: str,
) -> Optional[str]:
    if field.field_type == "file":
        return None

    # 1) Legal/compliance facts: deterministic only.
    compliance_answer = _match_compliance_fact(field.match_text, cv_maestro_text)
    if compliance_answer is not None:
        return compliance_answer

    # 2) Candidate Bible + fixed personal facts: deterministic only.
    known = deterministic_answers.resolve_known_answer(
        field.match_text,
        personal_info,
        load_candidate_bible(),
        cv_maestro_text,
    )
    if known and known.value:
        return known.value

    personal = _personal_answer(field, personal_info)
    if personal:
        return personal

    # 3) Reuse answers already supplied/generated for THIS application.
    # This costs zero extra model calls and gives exact job-specific answers
    # precedence over a new generic generation.
    all_qa = list(responses)
    if motivation_answer:
        all_qa.append(
            QuestionAnswer(
                question="motivation cover letter why do you want this job",
                answer=motivation_answer,
            )
        )
    if experience_answer:
        all_qa.append(
            QuestionAnswer(
                question="relevant experience for this role",
                answer=experience_answer,
            )
        )

    best_match = None
    best_score = 0.0
    for qa in all_qa:
        if not qa.answer:
            continue
        score = _similarity(field.match_text, qa.question)
        if score > best_score:
            best_match = qa
            best_score = score
    if best_match and best_score >= FUZZY_MATCH_THRESHOLD:
        return best_match.answer

    # 4) Closed choices are NEVER answered by Gemini. Sensitive required
    # fields use a deterministic decline answer; every other unresolved
    # select/radio/checkbox remains unresolved rather than being guessed.
    if field.field_type in ("select", "combobox", "checkbox", "radio"):
        is_sensitive = any(k in _norm(field.match_text) for k in DEFAULT_IGNORE_KEYWORDS)
        if field.required and is_sensitive:
            return "prefer not to say"
        return None

    # 5) Gemini is a last resort ONLY for a genuinely open, required text
    # question that appeared on the real form and was not resolvable above.
    if (
        ALLOW_GEMINI_FORM_OPEN_TEXT_FALLBACK
        and field.required
        and cv_maestro_text
        and field.field_type in ("text", "textarea")
    ):
        answer = gemini_service.answer_open_field(
            cv_maestro_text,
            job_context,
            field.match_text,
        )
        if answer:
            # Cache within this application so validation/re-render retries do
            # not pay for Gemini again or produce a different answer.
            responses.append(
                QuestionAnswer(question=field.match_text, answer=answer)
            )
            return answer

    return None


def answer_for_decision(
    field: FieldCandidate,
    decision: confidence_engine.ConfidenceDecision,
    personal_info: dict[str, str],
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    cv_maestro_text: str,
    job_context: str,
) -> Optional[str]:
    """Resolve values with deterministic facts first; Gemini only for unknown open text."""
    key = decision.answer_key

    compliance_answer = _match_compliance_fact(field.match_text, cv_maestro_text)
    if compliance_answer is not None:
        decision.metadata["answer_source"] = "compliance_fact"
        return compliance_answer

    known = deterministic_answers.resolve_known_answer(
        field.match_text,
        personal_info,
        load_candidate_bible(),
        cv_maestro_text,
    )
    if known and known.value:
        decision.metadata["answer_source"] = known.source
        decision.metadata["answer_key_resolved"] = known.key
        decision.metadata["answer_confidence"] = known.confidence
        return known.value

    personal_by_key = {
        "first_name": "first_name",
        "last_name": "last_name",
        "email": "email",
        "phone": "phone",
        "linkedin": "linkedin",
        "location": "location",
        "salary": "expected_salary",
        "availability": "available_from",
    }
    if key in personal_by_key:
        value = str(personal_info.get(personal_by_key[key], "") or "")
        if value:
            decision.metadata["answer_source"] = "personal_info"
            return value

    if key == "cover_letter" and motivation_answer:
        decision.metadata["answer_source"] = "existing_application_answer"
        return motivation_answer
    if key == "experience" and experience_answer:
        decision.metadata["answer_source"] = "existing_application_answer"
        return experience_answer

    bible_key, bible_value, bible_score = load_candidate_bible().autofill_lookup(field.match_text)
    if bible_value and bible_score >= 0.56:
        decision.metadata["answer_source"] = "candidate_bible_semantic"
        decision.metadata["candidate_bible_key"] = bible_key
        decision.metadata["candidate_bible_score"] = bible_score
        return bible_value

    return map_answer(
        field,
        personal_info,
        responses,
        motivation_answer,
        experience_answer,
        cv_maestro_text,
        job_context,
    )


def _type_char_by_char_with_native_events(el: Any, value: str) -> None:
    """MÉTODO CONFIRMADO por prueba real contra el combobox de Location
    (City) de Planet Labs (Greenhouse Job Board 2.0, react-select +
    proxy de geocoding "pelias"): por cada letra, setea el valor con el
    setter NATIVO del prototipo (resetenado antes el `_valueTracker`
    interno de React para que SÍ note el cambio) y dispara
    keydown + InputEvent('input', {inputType:'insertText'}) + keyup a
    mano. Esto fue lo único de 4 variantes probadas que dejó
    aria-expanded="true" con el dropdown de sugerencias realmente abierto.
    """
    for ch in value:
        try:
            el.evaluate(
                """(node, ch) => {
                    const proto = Object.getPrototypeOf(node);
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    const tracker = node._valueTracker;
                    const newValue = node.value + ch;
                    if (tracker) tracker.setValue(node.value);
                    setter.call(node, newValue);
                    node.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
                    node.dispatchEvent(new InputEvent('input', { bubbles: true, data: ch, inputType: 'insertText' }));
                    node.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
                }""",
                ch,
            )
        except Exception:
            break
        _human_pause(90, 150)


def _dispatch_framework_events(el: Any) -> None:
    try:
        el.evaluate(
            """e => {
                for (const type of ['input', 'change', 'blur']) {
                    e.dispatchEvent(new Event(type, { bubbles: true }));
                }
            }"""
        )
    except Exception:
        pass


def _human_pause(min_ms: int = 80, max_ms: int = 220) -> None:
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


_LOCATE_ME_TEXTS = ("locate me", "use my location", "detect my location", "find my location")


def _field_shows_required_error(field: FieldCandidate) -> bool:
    """Chequea si TODAVÍA aparece un mensaje de error/obligatorio visible
    cerca del campo (ej. 'Please enter your location'). Esto es agnóstico
    a cómo cada ATS guarde el valor internamente (hidden inputs, estado de
    React, etc.) -- si el error visual ya no está, la selección funcionó
    de verdad, sin importar el mecanismo interno."""
    try:
        frame = field.element.owner_frame()
        container = field.element.evaluate_handle(
            "e => e.closest('div, fieldset') || e.parentElement"
        )
        text = container.as_element().inner_text().lower() if container else ""
    except Exception:
        return False
    return any(k in text for k in ("please enter", "this field is required", "required field", "campo obligatorio"))


def _try_locate_me_button(field: FieldCandidate) -> bool:
    """Greenhouse (y varios ATS mas) ofrecen un link 'Locate me' al lado
    del campo de ciudad que usa la Geolocation API real del navegador +
    su propio JS para llenar Y confirmar el campo -- evita por completo
    pelear con nuestro parseo del dropdown de sugerencias. Requiere que el
    browser context tenga permiso de geolocalizacion concedido (ver
    form_filler.py: permissions=['geolocation'] + geolocation={...}); si
    no lo tiene, el click simplemente no hace nada util y caemos al flujo
    normal de todos modos."""
    el = field.element
    try:
        frame = el.owner_frame()
    except Exception:
        return False
    if not frame:
        return False

    link = None
    for text in _LOCATE_ME_TEXTS:
        try:
            candidate = frame.get_by_text(text, exact=False).first
            if candidate and candidate.is_visible(timeout=800):
                link = candidate
                break
        except Exception:
            continue
    if link is None:
        return False

    try:
        before = (el.input_value() or "").strip()
    except Exception:
        before = ""

    try:
        link.click(timeout=3000)
    except Exception:
        return False

    # La resolucion via geolocation+reverse-geocoding es async -- se
    # espera en pasos cortos en vez de un sleep fijo largo.
    for _ in range(10):
        _human_pause(250, 400)
        try:
            after = (el.input_value() or "").strip()
        except Exception:
            after = ""
        if after and after != before:
            log.info("Location: 'Locate me' llenó el campo con '%s'.", after)
            return True
    log.info(
        "Location: 'Locate me' fue clickeado pero el campo no cambió tras %ss "
        "(probable permiso de geolocalización no concedido) -- sigo con typing manual.",
        "~3"
    )
    return False


def _js_native_set_value(field: FieldCandidate, value: str) -> bool:
    """Último recurso para inputs controlados por React/Vue que ignoran (o
    bloquean con preventDefault) los eventos de teclado reales. En vez de
    simular teclas, usa el setter NATIVO del prototipo de HTMLInputElement
    -- el mismo truco que usa Testing Library -- para saltarse el setter
    que el framework sobreescribió, y despues dispara 'input'/'change'
    manualmente para que React SÍ note el cambio."""
    try:
        field.element.evaluate(
            """(el, value) => {
                const proto = window.HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                setter.call(el, value);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )
        return True
    except Exception as e:
        log.warning("Location: JS native setter fallback también falló: %s", e)
        return False


def _resolve_real_input_at_point(field: FieldCandidate):
    """A veces el elemento que detectamos como 'el campo' no es el que
    realmente recibe el foco/las teclas en pantalla: algunos ATS (visto en
    Greenhouse) superponen un decoy/elemento de presentación encima del
    <input> real -- típicamente para bloquear el autofill nativo del
    navegador o mostrar un valor formateado -- y nuestro selector terminó
    agarrando ESE decoy en vez del input real.

    Síntoma que delata esto: el bot "escribe" (sin tirar excepción) pero no
    aparece NINGUNA letra en pantalla, mientras que un humano tipeando a
    mano en el mismo lugar sí funciona -- porque el click real de un humano
    siempre aterriza en lo que está VISUALMENTE arriba en ese punto exacto,
    y eso es justamente lo que este chequeo verifica con coordenadas reales
    de pantalla (en vez de confiar en que el selector que armamos apunta al
    elemento correcto).

    Devuelve (element_handle, se_cambió: bool). Si todo estaba bien, se_cambió
    es False y devuelve el mismo `field.element` de siempre -- esto NO
    cambia el comportamiento para el 99% de los campos que sí funcionan."""
    el = field.element
    try:
        box = el.bounding_box()
    except Exception:
        return el, False
    if not box:
        return el, False

    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2

    try:
        frame = el.owner_frame()
        page = frame.page
    except Exception:
        return el, False

    try:
        page.mouse.click(x, y)
    except Exception:
        pass

    try:
        is_focused = el.evaluate("e => e === document.activeElement")
    except Exception:
        is_focused = False

    if is_focused:
        return el, False

    # No quedó focused el elemento esperado -- reviso TODO el stack de
    # elementos superpuestos en ese punto exacto (no solo el de más arriba,
    # que es lo único que "ve" un click normal de Playwright).
    try:
        stack_info = frame.evaluate(
            """([x, y]) => {
                const stack = document.elementsFromPoint(x, y);
                return stack.slice(0, 6).map(e => ({
                    tag: e.tagName, id: e.id || '',
                    cls: (e.className || '').toString().slice(0, 80),
                    readOnly: !!e.readOnly, disabled: !!e.disabled,
                }));
            }""",
            [x, y],
        )
    except Exception:
        stack_info = []

    log.warning(
        "Location: el elemento esperado NO quedó focused tras click real en "
        "(%.0f, %.0f). Stack de elementos superpuestos ahí (de arriba a "
        "abajo): %s", x, y, stack_info,
    )

    try:
        real_handle = frame.evaluate_handle(
            """([x, y]) => {
                const stack = document.elementsFromPoint(x, y);
                return stack.find(e =>
                    (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA')
                    && !e.readOnly && !e.disabled
                ) || null;
            }""",
            [x, y],
        )
        real_el = real_handle.as_element()
    except Exception:
        real_el = None

    if real_el is None:
        log.warning("Location: tampoco encontré ningún <input>/<textarea> real en ese punto.")
        return el, False

    try:
        real_el.evaluate("e => e.focus()")
        still_matches = real_el.evaluate("e => e === document.activeElement")
    except Exception:
        still_matches = False

    if not still_matches:
        return el, False

    log.info(
        "Location: encontré el input REAL superpuesto en ese punto (el que "
        "detectamos antes era un decoy) -- uso ese para escribir."
    )
    return real_el, True


def _fill_text_like(field: FieldCandidate, value: str) -> bool:
    el = field.element
    is_location = _looks_like_location_field(field)

    if is_location:
        # Chequeo de decoy ANTES que cualquier otra cosa: si el elemento
        # que detectamos no es el que realmente recibe el foco en pantalla,
        # todo lo que sigue (Locate me, tipeo, native setter) apunta al
        # lugar equivocado y nunca va a funcionar por más reintentos que
        # hagamos. Ver _resolve_real_input_at_point para el detalle.
        el, _swapped = _resolve_real_input_at_point(field)

    if is_location and _try_locate_me_button(field):
        if not _field_shows_required_error(field):
            return True
        log.info("Location: 'Locate me' completó el texto pero el error seguía visible -- sigo con typing manual.")

    try:
        el.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    try:
        frame = el.owner_frame()
    except Exception:
        frame = None

    def _type_and_select(delay_range: tuple[int, int]) -> bool:
        el.click(timeout=3000)
        try:
            el.fill("", timeout=3000)
        except Exception:
            pass

        # MÉTODO PRIMARIO para campos de ubicación: confirmado por prueba
        # real contra Planet Labs (Greenhouse) -- ver
        # _type_char_by_char_with_native_events. Si esto ya deja el
        # dropdown abierto y selecciona bien, no hace falta seguir
        # intentando con el flujo viejo de abajo.
        if is_location and frame is not None:
            try:
                with frame.page.expect_response(
                    lambda r: any(k in r.url.lower() for k in ("autocomplete", "places", "geocod", "pelias")),
                    timeout=4000,
                ):
                    _type_char_by_char_with_native_events(el, value)
            except Exception:
                pass  # puede que esta oferta no dispare una llamada de red que matchee -- no es bloqueante
            if _select_autocomplete_suggestion(el, desired_value=value):
                return True
            log.info("Location: el método por eventos nativos no encontró/seleccionó opción -- sigo con el flujo de respaldo.")

        # Escribe letra por letra, y si es un campo de ubicación, ESPERA
        # la respuesta real de red del autocomplete después de tipear, en
        # vez de un timeout fijo a ciegas (esto era lo que fallaba de
        # forma intermitente: a veces 4s alcanzaban, a veces no).
        if is_location and frame is not None:
            try:
                with frame.page.expect_response(
                    lambda r: any(k in r.url.lower() for k in ("autocomplete", "places", "geocod")),
                    timeout=6000,
                ):
                    el.type(value, delay=random.randint(*delay_range), timeout=15000)
            except Exception:
                el.type(value, delay=random.randint(*delay_range), timeout=15000)
        else:
            el.type(value, delay=random.randint(*delay_range), timeout=15000)

        # DIAGNÓSTICO DIRECTO: qué queda literalmente en el campo justo
        # después de que Playwright "tipeó". Si esto sale vacío sin que
        # nada haya tirado excepción, el .type() no está llegando al
        # elemento real (foco/frame equivocado) -- si sale con texto que
        # luego desaparece, es React pisando el valor.
        if is_location:
            try:
                post_value = el.input_value(timeout=1000)
            except Exception as e:
                post_value = f"<no se pudo leer: {e}>"
            log.info("Location: justo despues de type(), el campo contiene: '%s' (esperado: '%s')", post_value, value)

        _dispatch_framework_events(el)

        if field.field_type in ("autocomplete", "combobox") or is_location:
            return _select_autocomplete_suggestion(el, desired_value=value)
        return True

    try:
        ok = _type_and_select((12, 35))

        # BUG ANTERIOR: se consideraba "éxito" apenas el texto visible
        # cambiaba. Greenhouse (y varios ATS más) validan contra campos
        # ocultos/estado interno que solo se completan si la selección
        # REALMENTE disparó su handler -- el texto puede verse bien y el
        # campo seguir "vacío" para su validación. Por eso ahora el
        # criterio real de éxito es que el mensaje de error visible
        # ("Please enter your location") haya desaparecido, no que
        # `ok` haya dado True.
        if is_location and _field_shows_required_error(field):
            log.info(
                "Location: error seguía visible tras el primer intento -- "
                "reintentando con tipeo más lento."
            )
            _type_and_select((90, 150))

        # ÚLTIMO RECURSO: si tras 2 intentos de tipeo real (letra por letra,
        # con delays distintos) el campo sigue vacío, lo más probable es que
        # el input tenga un handler de teclado que bloquea la escritura
        # directa (ej. preventDefault() en keydown, para forzar a elegir
        # SOLO de una lista de sugerencias). Guardarraíl en código: leemos
        # los atributos reales del campo para dejar de adivinar, y probamos
        # setear el valor por el setter NATIVO del prototipo del input (el
        # mismo truco que usa Testing Library) en vez de simular teclas --
        # esto sí dispara el "input"/"change" que React escucha, sin pasar
        # por el keydown que puede estar bloqueado.
        if is_location and _field_shows_required_error(field):
            try:
                attrs = field.element.evaluate(
                    "e => ({readonly: e.readOnly, disabled: e.disabled, "
                    "type: e.type, maxLength: e.maxLength, value: e.value})"
                )
                log.warning(
                    "Location: tipeo normal no funcionó tras 2 intentos. "
                    "Atributos reales del campo: %s", attrs,
                )
            except Exception:
                pass
            if _js_native_set_value(field, value):
                _human_pause(300, 500)
                _select_autocomplete_suggestion(el, desired_value=value)

        return not (is_location and _field_shows_required_error(field))
    except Exception as e:
        log.warning("Location/typing: excepcion en _type_and_select para '%s': %s", field.match_text, e)
        try:
            el.fill(value, timeout=6000)
            _dispatch_framework_events(el)
            return not (is_location and _field_shows_required_error(field))
        except Exception as e2:
            log.warning("Location/typing: el.fill() de respaldo tambien fallo para '%s': %s", field.match_text, e2)
            return False


def _dropdown_is_open(el: Any) -> bool:
    """Señal de que el listbox SÍ está abierto, independiente de si nuestro
    selector de opciones logró enumerar los <li>/<div> internos. Cualquier
    combobox accesible (y Greenhouse lo es) marca esto en el input mismo
    cuando la lista de sugerencias está visible -- no depende de adivinar
    clases/atributos internos de cada rediseño de cada ATS."""
    try:
        if (el.get_attribute("aria-expanded") or "").lower() == "true":
            return True
    except Exception:
        pass
    try:
        owns = el.get_attribute("aria-owns") or el.get_attribute("aria-controls")
        if owns:
            frame = el.owner_frame()
            target = frame.query_selector(f"#{owns}")
            if target and target.is_visible():
                return True
    except Exception:
        pass
    return False


def _select_autocomplete_suggestion(el: Any, desired_value: str = "") -> bool:
    """Selecciona una sugerencia REAL de un típeahead tipo 'buscador' (ej.
    Location (City) en Greenhouse, que consulta una API tipo Google Places
    de forma asíncrona) -- no es un <select> estático, así que la lista de
    opciones puede tardar unos cientos de ms en aparecer después de tipear.

    Bugs corregidos acá:
      1. Antes se llamaba sin `desired_value`, así que `desired` quedaba
         siempre "" y ninguna opción calzaba nunca.
      2. Antes se consultaban las opciones INMEDIATAMENTE después de
         tipear, sin esperar a la llamada asíncrona.
      3. (NUEVO) El selector de opciones solo cubría ARIA estándar y
         Google Places clásico (.pac-item). Los boards nuevos de Greenhouse
         (job-boards.greenhouse.io, reescritos en React) suelen usar
         librerías de componentes headless (Radix UI / cmdk) para su
         propio combobox, que NO siempre marcan role='option' de forma
         confiable y usan atributos/clases propios en su lugar. Se agregan
         esos patrones.
      4. (NUEVO) Logging real en cada intento: antes, si no aparecía
         ninguna opción, el código simplemente seguía en silencio y
         terminábamos adivinando a ciegas qué había pasado. Ahora se loguea
         SIEMPRE cuántas opciones se encontraron (0 incluido) y, si es 0,
         un snippet del HTML alrededor del campo para poder ver en el log
         la estructura real del widget que falló.
    """
    desired = _norm(desired_value)
    frame = None
    try:
        frame = el.owner_frame()
    except Exception:
        pass

    option_selector = (
        "[role='option'], [data-testid*='option'], li[id*='option'], "
        # Google Places Autocomplete clásico (widget vanilla-JS, inyecta
        # su dropdown directo en <body>, fuera del control de React).
        ".pac-item, .pac-container li, "
        # Radix UI (Combobox/Popover) y cmdk (cmdk.paco.me) -- MUY comunes
        # en rewrites modernos de formularios como el nuevo Greenhouse.
        # Estos son atributos ESPECÍFICOS de esas librerías (no fragmentos
        # de clase genéricos), así que no matchean nada fuera de un
        # dropdown real de esas libs.
        #
        # NO agregamos acá "ul[role='listbox'] > *, div[role='listbox'] > *":
        # eso fue lo que realmente rompió la selección de Berlin -- agarra
        # CUALQUIER listbox de la página (ej. un dropdown de país ya
        # renderizado en el DOM aunque no esté abierto), inflando el conteo
        # y disparando el guardarraíl de "demasiadas opciones" incluso
        # cuando el dropdown de ciudad SÍ tenía la sugerencia correcta.
        "[data-radix-collection-item], [cmdk-item]"
    )
    options = []
    if frame:
        try:
            # Timeout ampliado de 2500 -> 4000ms: el debounce + round-trip
            # real a una API de geocoding puede tardar más que eso bajo
            # carga, y cortar muy pronto garantiza options=[] siempre.
            frame.wait_for_selector(option_selector, timeout=4000, state="visible")
        except Exception:
            pass
        try:
            options = frame.query_selector_all(option_selector)
        except Exception:
            options = []

    # NOTA: el guardarraíl de "más de 30 opciones -> ignorar" que vivía acá
    # se sacó. Era una defensa contra el bug de "First Name"/"Last Name"
    # clasificados como campo de ubicación (ver _looks_like_location_field
    # más arriba) -- pero ESE bug ya se arregló en la raíz (dejamos de
    # mirar nearby_text y sacamos el fragmento "ort"), así que el cap ya
    # no hacía falta y encima empezó a bloquear selecciones legítimas de
    # ciudad cuando el selector ancho (ahora ya angosto de nuevo) sumaba
    # de más. Menos capas de defensa, pero la causa real ya no existe.

    # Nombre de la ciudad solo (antes de la primera coma), para matchear
    # resultados tipo "Berlin Mitte, Berlin, Germany" -- ahí "Berlin,
    # Germany" completo no es prefijo de nada, pero "berlin" SÍ aparece.
    city_only = desired.split(",")[0].strip() if desired else ""

    visible_texts = []
    best, best_score = None, 0.0
    for option in options[:20]:
        try:
            if not option.is_visible():
                continue
            text = _norm(option.inner_text())
            if not text:
                continue
            visible_texts.append(text[:40])
            # Coincidencia de prefijo (lo típico en un buscador de
            # ciudades: tipeás "Berlin" y la primera sugerencia empieza
            # con "berlin") pesa más que un simple score de similitud.
            if desired and text.startswith(desired):
                best = option
                best_score = 1.0
                break
            # Resultados con barrio primero (ej. "Berlin Mitte, Berlin,
            # Germany") no son prefijo de "berlin, germany", pero SÍ
            # contienen el nombre de la ciudad -- aceptamos eso también,
            # priorizando el que además empiece con la ciudad (así no
            # confundimos con menciones sueltas en otro contexto).
            if city_only and city_only in text:
                score_city = 0.9 if text.startswith(city_only) else 0.75
                if score_city > best_score:
                    best_score, best = score_city, option
                continue
            score = _similarity(text, desired) if desired else 0.0
            if score > best_score:
                best_score, best = score, option
        except Exception:
            continue

    if best is not None and best_score >= 0.5:
        try:
            best.click(timeout=2000)
            log.info(
                "Autocomplete OK: '%s' -> click en opción visible (score=%.2f, candidatas=%s)",
                desired_value, best_score, visible_texts,
            )
            return True
        except Exception as e:
            log.warning(
                "Autocomplete: encontré opción para '%s' pero el click falló: %s",
                desired_value, e,
            )

    # DIAGNÓSTICO REAL en vez de fallar en silencio: si llegamos hasta acá,
    # o no había opciones, o ninguna calzó lo suficiente. Logueamos qué vio
    # exactamente el bot para poder arreglar el selector con datos reales
    # en la próxima corrida, en vez de seguir adivinando.
    if not options:
        snippet = ""
        try:
            snippet = el.evaluate(
                "e => (e.closest('form, div, section') || e.parentElement).outerHTML.slice(0, 800)"
            )
        except Exception:
            pass
        log.warning(
            "Autocomplete SIN opciones detectadas para '%s' tras 4s de espera. "
            "HTML alrededor del campo (recortado): %s",
            desired_value, snippet,
        )
    else:
        log.warning(
            "Autocomplete: %d opciones encontradas para '%s' pero ninguna calzó "
            "(mejor score=%.2f). Candidatas vistas: %s",
            len(options), desired_value, best_score, visible_texts,
        )

    # Si SÍ hay opciones visibles (o el input marca aria-expanded=true,
    # es decir el listbox ESTÁ abierto aunque no supimos enumerar sus
    # opciones -- exactamente el caso de Berlin en Greenhouse nuevo) pero
    # ninguna calzó bien o no pudimos leerlas, un ArrowDown+Enter a ciegas
    # es mejor que dejar el campo sin confirmar. Pero si NO hay señal
    # alguna de que se abrió un listbox, no tocamos nada -- confirmar con
    # Enter en ese caso puede disparar un submit prematuro del formulario.
    dropdown_open = bool(options) or _dropdown_is_open(el)
    if dropdown_open:
        try:
            if not options:
                log.info(
                    "Autocomplete: aria-expanded=true detectado sin opciones "
                    "enumerables para '%s' -> uso ArrowDown+Enter igual.",
                    desired_value,
                )
            el.press("ArrowDown")
            _human_pause(150, 350)
            el.press("Enter")
            log.info("Autocomplete: usé fallback ArrowDown+Enter para '%s'.", desired_value)
            return True
        except Exception:
            pass

    return False


DECLINE_OPTION_KEYWORDS = (
    "prefer not to", "decline to", "rather not say", "not specified",
    "undisclosed", "i don't wish", "do not wish", "prefiero no",
    "no deseo", "no contestar", "not to disclose",
)
_PLACEHOLDER_OPTION_TEXTS = ("select", "select...", "please select", "-- select --", "choose")

# ---------------------------------------------------------------------------
# Preguntas de compliance / elegibilidad legal: NUNCA se responden con IA.
#
# Bug real detectado: la pregunta "Do you reside in or maintain an
# established permanent residence in any of the following countries
# (Cuba, Iran, North Korea, Syria...)" no matcheaba ningún keyword de
# DEFAULT_IGNORE_KEYWORDS (esa lista es solo para EEO/demográficos), así
# que caía en el fallback que le pide a Gemini una respuesta de texto
# libre (answer_open_field, temperature>0, prompt orientado a "maximizar
# probabilidad de avanzar"). Dos llamadas separadas (fill inicial +
# reintento tras validación) dieron dos respuestas de texto distintas,
# que el fuzzy-match contra "Yes"/"No" resolvió de forma DISTINTA cada
# vez -- así es como terminó marcando "Yes" (declarando residencia en un
# país sancionado, lo cual es falso) y un minuto después "No".
#
# Este tipo de pregunta es un HECHO binario real, no una "opinión" que
# convenga maximizar -- se resuelve con datos fijos, confirmados por el
# candidato una sola vez, nunca con una llamada a un modelo con
# temperature > 0. Ajusta estos valores vía variables de entorno según
# tu situación real.
# ---------------------------------------------------------------------------

COMPLIANCE_FACTS: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "require sponsorship", "need sponsorship", "visa sponsorship",
            "sponsorship now or in the future", "sponsorship in the future",
            "require visa", "need a visa",
        ),
        os.environ.get("APPLICANT_REQUIRES_SPONSORSHIP", "No"),
    ),
    (
        (
            "right to work", "right to reside", "authorized to work",
            "authorised to work", "work permit", "legally eligible to work",
            "eligible to work", "berechtigt zu arbeiten", "arbeitserlaubnis",
        ),
        os.environ.get("APPLICANT_RIGHT_TO_WORK_EU", "Yes"),
    ),
    (
        (
            "previously worked", "former employee", "have you worked",
            "haben sie bereits", "already worked", "worked for us before",
        ),
        os.environ.get("APPLICANT_PREVIOUS_EMPLOYEE", "No"),
    ),
]

# La pregunta de país sancionado se maneja aparte de COMPLIANCE_FACTS de
# arriba porque, a diferencia de "right to work"/"previously worked" (que
# son casi siempre iguales para vos), esta SÍ conviene resolverla mirando
# tu CV real en vez de una sola variable de entorno fija -- ver
# _resolve_sanctioned_country_answer().
_SANCTIONED_COUNTRY_KEYWORDS = (
    "cuba", "iran", "north korea", "syria", "sanctioned countr",
    "sanctions list", "embargo", "crimea", "donetsk", "luhansk",
    "restricted countr", "ofac",
)
_SANCTIONED_COUNTRY_NAMES = (
    "cuba", "iran", "north korea", "syria", "crimea", "donetsk", "luhansk",
)


def _resolve_sanctioned_country_answer(cv_maestro_text: str) -> str:
    """
    Responde "¿Residís en/tenés vínculo con un país sancionado?" en este
    orden, cacheando el resultado en la Biblia para que sea SIEMPRE la
    misma respuesta de ahí en adelante (nunca "Yes" una vez y "No" al
    minuto siguiente, que fue el bug real detectado):

      1. Ya aprendida antes (candidate_bible.learned_answers) -> esa,
         instantáneo, cero llamadas.
      2. Determinístico: si la Biblia tiene citizenship/location
         declarados y NINGUNO nombra un país sancionado -> "No" directo,
         sin IA (tu caso normal: ciudadanía chilena, vivís en Berlín).
      3. Si la Biblia no tiene esos datos -> usa el override explícito
         APPLICANT_RESIDES_SANCTIONED_COUNTRY.
      4. Si tampoco existe override -> "No" como default conservador; nunca
         se declara una residencia en país sancionado sin evidencia real.
    """
    bible = load_candidate_bible()
    learned = bible.get_learned("compliance.sanctioned_country_residence")
    if learned in ("Yes", "No"):
        return learned

    citizenship = _norm(str(bible.get_path("personal.citizenship", "")))
    location = _norm(str(bible.get_path("personal.location", "")))
    if citizenship or location:
        combined = f"{citizenship} {location}"
        if not any(name in combined for name in _SANCTIONED_COUNTRY_NAMES):
            bible.learn("compliance.sanctioned_country_residence", "No")
            return "No"

    # Compliance facts are never delegated to Gemini. If the Bible does not
    # contain enough data, use the explicit environment override/default.
    default = os.environ.get("APPLICANT_RESIDES_SANCTIONED_COUNTRY", "No")
    bible.learn("compliance.sanctioned_country_residence", default)
    return default


def _match_compliance_fact(match_text: str, cv_maestro_text: str = "") -> Optional[str]:
    """Devuelve la respuesta FIJA (o inferida+cacheada) si el campo es una
    pregunta de compliance/elegibilidad legal conocida, o None si no
    aplica (en cuyo caso el flujo normal -- bible/fuzzy/Gemini -- sigue su
    curso)."""
    text = _norm(match_text)
    if any(k in text for k in _SANCTIONED_COUNTRY_KEYWORDS):
        return _resolve_sanctioned_country_answer(cv_maestro_text)
    for keywords, answer in COMPLIANCE_FACTS:
        if any(k in text for k in keywords):
            return answer
    return None


def _get_select_options(field: FieldCandidate) -> list[tuple[str, str]]:
    """[(value, texto_visible), ...] de un <select> nativo. Esto es lo que
    faltaba: antes se intentaba matchear select_option(label=value) contra
    texto libre de Gemini, que casi nunca calza exacto con una opcion real."""
    try:
        return field.element.evaluate(
            "e => Array.from(e.options).map(o => [o.value, (o.textContent || '').trim()])"
        ) or []
    except Exception:
        return []


def _get_combobox_option_elements(field: FieldCandidate) -> list[Any]:
    """Abre un combobox custom (role='combobox', div/boton clickeable) y
    devuelve los elementos de opcion visibles. Esto es el branch que antes
    NO EXISTIA: fill_field mandaba estos widgets a _fill_text_like, que
    hace .click()+.type(), inutil sobre un boton que solo abre una lista."""
    el = field.element
    try:
        el.scroll_into_view_if_needed(timeout=3000)
        el.click(timeout=3000)
    except Exception:
        return []
    try:
        frame = field.frame
        options = frame.query_selector_all(
            "[role='option'], [role='listbox'] li, ul[role='listbox'] > *"
        )
        return [o for o in options if o.is_visible()]
    except Exception:
        return []


def _pick_best_option_index(option_texts: list[str], field: FieldCandidate, desired_hint: str) -> Optional[int]:
    """Pick only a semantically supported REAL option.

    Required fields are NOT allowed to fall back to the first non-empty option.
    That old behavior could submit a false answer simply to unblock the form.
    """
    is_sensitive = any(k in _norm(field.match_text) for k in DEFAULT_IGNORE_KEYWORDS)
    idx, score = deterministic_answers.best_option_index(
        field.match_text,
        desired_hint,
        option_texts,
        sensitive=is_sensitive,
    )
    if idx is None:
        log.info(
            "Closed choice unresolved deterministically for '%s' desired='%s' best_score=%.2f",
            (field.match_text or field.field_type)[:100],
            desired_hint,
            score,
        )
    return idx


def _select_option(field: FieldCandidate, value: str) -> bool:
    el = field.element
    try:
        el.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    options = _get_select_options(field)
    if not options:
        return False

    option_texts = [label for _, label in options]
    idx = _pick_best_option_index(option_texts, field, value)
    if idx is None:
        return False

    real_value, real_label = options[idx]
    try:
        el.select_option(value=real_value, timeout=4000)
        _dispatch_framework_events(el)
        return True
    except Exception:
        pass
    try:
        el.select_option(label=real_label, timeout=4000)
        _dispatch_framework_events(el)
        return True
    except Exception:
        # Nunca caemos a texto libre en un <select> nativo: si tampoco esto
        # funciono, mejor dejarlo sin llenar (visible para revision) que
        # tipear algo que el select ni siquiera puede interpretar.
        return False


def _select_combobox_option(field: FieldCandidate, value: str) -> bool:
    def _visible_options() -> list[Any]:
        try:
            options = field.frame.query_selector_all(
                "[role='option'], [role='listbox'] li, ul[role='listbox'] > *"
            )
            return [o for o in options if o.is_visible()]
        except Exception:
            return []

    option_elements = _get_combobox_option_elements(field)

    def _texts(elements: list[Any]) -> list[str]:
        texts: list[str] = []
        for opt in elements:
            try:
                texts.append(opt.inner_text())
            except Exception:
                texts.append("")
        return texts

    idx = _pick_best_option_index(_texts(option_elements), field, value) if option_elements else None

    # Searchable custom selects often render only a subset until text is typed.
    # If the desired option is not visible yet and the combobox is input-like,
    # type the deterministic value, then inspect the REAL options again.
    if idx is None:
        try:
            tag = field.element.evaluate("e => e.tagName.toLowerCase()")
        except Exception:
            tag = ""
        if tag == "input":
            try:
                field.element.fill("")
                field.element.type(str(value), delay=35)
                _human_pause(250, 500)
                option_elements = _visible_options()
                idx = _pick_best_option_index(_texts(option_elements), field, value)
            except Exception:
                idx = None

    if idx is None or not option_elements:
        try:
            field.element.press("Escape")
        except Exception:
            pass
        return False

    try:
        option_elements[idx].click(timeout=2500)
        _dispatch_framework_events(field.element)
        return True
    except Exception:
        return False


def _choice_option_text(field: FieldCandidate) -> str:
    parts = [
        field.label,
        field.aria_label,
        _safe_attr(field.element, "value"),
        _own_field_container_text(field.element),
    ]
    return " ".join(p for p in parts if p).strip()


def _check_choice(field: FieldCandidate, value: str) -> bool:
    desired_norm = _norm(value)

    if field.field_type == "radio":
        option_text = _choice_option_text(field)
        score = deterministic_answers.option_match_score(
            field.match_text,
            desired_norm,
            option_text,
        )
        if score < 0.62:
            return False
        try:
            field.element.check(timeout=4000)
            _dispatch_framework_events(field.element)
            return True
        except Exception:
            return False

    # Checkbox groups can represent alternatives just like radio buttons
    # (e.g. "Prefer not to say"). Match the option itself when the answer
    # is not a simple Boolean.
    desired_bool = desired_norm in ("true", "1", "yes", "si", "sí", "on", "agree", "accept")
    explicit_no = desired_norm in ("false", "0", "no", "off", "do not agree", "decline")

    if not desired_bool and not explicit_no:
        option_text = _choice_option_text(field)
        score = deterministic_answers.option_match_score(
            field.match_text,
            desired_norm,
            option_text,
        )
        if score < 0.62:
            return False
        desired_bool = True

    if not desired_bool:
        return False

    try:
        field.element.check(timeout=4000)
        _dispatch_framework_events(field.element)
        return True
    except Exception:
        return False


def _is_element_stale(el: Any) -> bool:
    """True si el nodo ya no está conectado al documento (o el handle en
    sí quedó inválido). `isConnected` es la señal estándar del DOM -- no
    depende de adivinar mensajes de error específicos de Playwright."""
    try:
        return not el.evaluate("e => e.isConnected")
    except Exception:
        return True  # cualquier excepción al evaluar == el nodo ya no existe


def _ensure_fresh_element(field: FieldCandidate) -> bool:
    """Punto único de defensa contra nodos DOM obsoletos (bug real
    detectado: seleccionar una opción en un campo anterior que también es
    un componente controlado -- ej. el combobox de país del teléfono,
    también react-select -- puede hacer que React re-renderice TODO el
    formulario y reemplace los nodos DOM de los campos siguientes. El
    ElementHandle que inspect_fields() guardó en field.element queda
    apuntando a un nodo ya desconectado, y todo lo que se intente sobre
    él falla en silencio o con una excepción genérica más abajo).

    Este chequeo vive acá -- al principio de fill_field, el ÚNICO lugar
    por el que pasan TODOS los campos antes de tocarlos -- en vez de
    parchear cada _select_*/_fill_text_like por separado. Por eso
    _candidate_from_element siempre guarda un `selector` reconstruible
    además del handle crudo: es lo que permite re-consultar el nodo
    fresco cuando el viejo ya no sirve."""
    if not _is_element_stale(field.element):
        return True

    label = (field.match_text or field.field_type)[:80]
    if not field.selector or field.selector == FIELD_SELECTOR:
        log.warning(
            "Stale element para '%s' pero no hay selector especifico para "
            "re-consultar (selector generico) -- no se puede refrescar.", label,
        )
        return False
    try:
        fresh = field.frame.query_selector(field.selector)
    except Exception as exc:
        log.warning("Stale element para '%s': fallo re-consultando '%s': %s", label, field.selector, exc)
        return False
    if fresh is None:
        log.warning(
            "Stale element para '%s': selector '%s' ya no matchea nada tras "
            "el re-render (el campo pudo haber desaparecido de verdad).", label, field.selector,
        )
        return False

    log.info("Stale element detectado y refrescado para '%s' (selector '%s').", label, field.selector)
    field.element = fresh
    return True


def fill_field(field: FieldCandidate, value: str, stats: EngineStats) -> bool:
    if not _ensure_fresh_element(field):
        stats.errors.append(f"stale_element_unrecoverable:{(field.match_text or field.field_type)[:80]}")
        return False
    if _current_value(field) and field.field_type not in ("checkbox", "radio", "file"):
        return False
    value = _truncate_for_maxlength(_normalize_value(value, field.field_type), field.maxlength)
    if not value and field.field_type not in ("checkbox", "radio"):
        return False
    if _looks_like_location_field(field):
        # BUG REAL corregido acá: el <input> real de ciudad/ubicación
        # suele traer role="combobox" (confirmado en Planet Labs/
        # Greenhouse), lo que hacía que _classify_field lo etiquetara
        # "combobox" y fill_field lo mandara a _select_combobox_option --
        # un mecanismo de "click y elegí de una lista YA renderizada" que
        # NUNCA tipea nada, así que jamás dispara la búsqueda real de
        # geocoding. Toda la lógica fina para este tipo de campo (tipeo
        # caracter por caracter con eventos nativos, manejo de decoys,
        # "Locate me", fallback de setter nativo -- validada contra la
        # página real en test_4_planetlabs_location.py) vive en
        # _fill_text_like, así que un campo de ubicación SIEMPRE va por
        # ahí, sin importar qué haya dicho _classify_field.
        ok = _fill_text_like(field, value)
    elif field.field_type == "select":
        ok = _select_option(field, value)
    elif field.field_type == "combobox":
        ok = _select_combobox_option(field, value)
    elif field.field_type in ("checkbox", "radio"):
        ok = _check_choice(field, value)
    else:
        ok = _fill_text_like(field, value)
    if ok:
        field.filled = True
        stats.filled.append((field.match_text or field.field_type)[:120])
    return ok


def upload_cv(page: Page, pdf_path: str, stats: EngineStats) -> bool:
    uploaded = False
    for frame in page.frames:
        try:
            inputs = frame.query_selector_all("input[type='file']")
        except Exception:
            continue
        for file_input in inputs:
            try:
                file_input.set_input_files(pdf_path)
                uploaded = True
                stats.filled.append("cv_upload:file_input")
            except Exception as exc:
                stats.errors.append(f"cv_upload_input:{exc}")
    if uploaded:
        return True

    upload_texts = ("Upload", "Browse", "Choose file", "Attach", "Subir", "Examinar")
    for text in upload_texts:
        try:
            with page.expect_file_chooser(timeout=2500) as chooser_info:
                page.get_by_text(text, exact=False).first.click(timeout=2500)
            chooser_info.value.set_files(pdf_path)
            stats.filled.append(f"cv_upload:button:{text}")
            return True
        except Exception:
            continue
    return False


def close_popups(page: Page) -> None:
    for text in POPUP_TEXTS:
        try:
            locator = page.get_by_role("button", name=re.compile(text, re.I)).first
            if locator.is_visible(timeout=700):
                locator.click(timeout=1000)
                _human_pause(100, 250)
        except Exception:
            continue


def _visible_locator_exists(scope: Any, selector: str, timeout: int = 500) -> bool:
    try:
        return bool(scope.locator(selector).first.is_visible(timeout=timeout))
    except Exception:
        return False


def _page_has_visible_form_controls(page: Page) -> bool:
    for frame in page.frames:
        try:
            elements = frame.query_selector_all(FIELD_SELECTOR)
        except Exception:
            continue
        for element in elements[:25]:
            try:
                if element.is_visible():
                    return True
            except Exception:
                continue
    return False


def detect_blocker(page: Page) -> Optional[str]:
    try:
        body = page.inner_text("body", timeout=1500).lower()
    except Exception:
        body = ""

    has_form_controls = _page_has_visible_form_controls(page)

    active_captcha_selectors = (
        "iframe[src*='recaptcha/api2/bframe']",
        "iframe[src*='recaptcha/enterprise/bframe']",
        "iframe[src*='arkoselabs']",
        "[data-testid*='captcha-challenge']",
        "[data-testid*='turnstile-challenge']",
    )
    if any(_visible_locator_exists(page, selector) for selector in active_captcha_selectors):
        return "captcha"

    passive_captcha_selectors = (
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='challenges.cloudflare.com']",
        ".g-recaptcha",
        ".h-captcha",
        "[class*='h-captcha']",
        "[id*='captcha']",
        "[class*='captcha']",
        "[data-sitekey]",
        "[data-testid*='captcha']",
        "[data-testid*='turnstile']",
    )
    if not has_form_controls and any(_visible_locator_exists(page, selector) for selector in passive_captcha_selectors):
        return "captcha"

    captcha_text_markers = (
        "complete the captcha",
        "solve the captcha",
        "verify you are human",
        "checking if the site connection is secure",
        "security check",
    )
    if any(marker in body for marker in captcha_text_markers) and not has_form_controls:
        return "captcha"

    if _visible_locator_exists(page, "input[type='password']", timeout=800):
        return "login_required"
    if any(marker in body for marker in LOGIN_MARKERS) and not has_form_controls:
        return "login_required"
    return None


def validation_errors(page: Page) -> list[str]:
    """
    BUG REAL detectado (screenshot: campo "Location (City)" con
    "Please enter your location" en rojo aunque ya decia "Berlin,
    Germany"): muchos ATS (Greenhouse incluido) NO limpian el
    aria-invalid="true" del <input> hasta que ocurre un evento de
    revalidacion propio del sitio -- a veces eso nunca pasa despues de
    hacer click en una sugerencia del autocomplete via Playwright, aunque
    visualmente y funcionalmente el campo ya este perfecto. Sin este
    chequeo, el motor lee ese aria-invalid stale como un error real por
    siempre, reintenta llenar un campo que ya esta bien, y nunca llega a
    "ready_for_submit" -- exactamente el loop reportado.

    Fix: si el elemento marcado invalido ES el propio campo de formulario
    (input/select/textarea) y YA tiene un valor no vacio cargado, no lo
    contamos como error bloqueante. Los mensajes de error que NO son el
    campo en si (ej. un <span role="alert"> aparte) se siguen contando
    como antes -- ahi no hay forma barata de confirmar si son stale.
    """
    errors: list[str] = []
    for frame in page.frames:
        try:
            elements = frame.query_selector_all(ERROR_SELECTORS)
        except Exception:
            continue
        for el in elements[:10]:
            try:
                if not el.is_visible():
                    continue
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag in ("input", "select", "textarea"):
                    try:
                        current = (el.input_value() or "").strip()
                    except Exception:
                        current = ""
                    if current:
                        continue  # ya tiene valor -> error stale, no bloqueante
                errors.append(_safe_inner_text(el, 140) or _safe_attr(el, "aria-label") or "invalid field")
            except Exception:
                continue
    return [e for e in errors if e]


def _click_button_by_text(page: Page, texts: tuple[str, ...], stats: EngineStats) -> bool:
    for text in texts:
        try:
            locator = page.get_by_role("button", name=re.compile(text, re.I)).first
            locator.wait_for(state="visible", timeout=1200)
            locator.wait_for(state="attached", timeout=1200)
            if locator.is_enabled(timeout=1200):
                locator.scroll_into_view_if_needed(timeout=1500)
                locator.click(timeout=4000)
                stats.buttons_clicked.append(text)
                return True
        except Exception:
            pass
        for selector in (f"button:has-text('{text}')", f"a:has-text('{text}')", f"input[type='submit'][value*='{text}']"):
            try:
                el = page.query_selector(selector)
                if el and el.is_visible() and el.is_enabled():
                    el.scroll_into_view_if_needed(timeout=1500)
                    el.click(timeout=4000)
                    stats.buttons_clicked.append(text)
                    return True
            except Exception:
                continue
    return False


def click_next(page: Page, stats: EngineStats) -> bool:
    return _click_button_by_text(page, NEXT_BUTTON_TEXTS, stats)


def click_submit(page: Page, stats: EngineStats) -> bool:
    return _click_button_by_text(page, SUBMIT_BUTTON_TEXTS, stats)


def is_submitted(page: Page, previous_url: str = "") -> bool:
    try:
        body = page.inner_text("body", timeout=3000).lower()
    except Exception:
        body = ""
    if any(marker in body for marker in SUCCESS_MARKERS):
        return True
    try:
        if previous_url and page.url != previous_url and any(token in page.url.lower() for token in ("thank", "submitted", "success")):
            return True
    except Exception:
        pass
    try:
        visible_form_fields = page.locator("input, textarea, select, [role='combobox']").count()
        return visible_form_fields == 0 and bool(body)
    except Exception:
        return False


def fill_from_plugin_selectors(
    page: Page,
    personal_info: dict[str, str],
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    cv_maestro_text: str,
    job_context: str,
    stats: EngineStats,
    handler: Any,
    app_logger: Optional[ApplicationLogger] = None,
) -> int:
    """Use ATS-specific selectors before universal/fuzzy field discovery.

    The plugin registry used to provide classification hints only. That meant
    a known selector such as Workday's data-automation-id or an Ashby field
    still depended on the generic classifier finding and interpreting it.
    Here we use unambiguous plugin selectors directly, while retaining the
    universal engine as the fallback for every field the plugin does not know.

    Selectors shared by multiple semantic field types are deliberately skipped
    (e.g. old Lever templates can expose one full-name input for both first and
    last name) so specialized support never overwrites a field ambiguously.
    """
    if handler is None or getattr(handler, "ats", "unknown") == "unknown":
        return 0

    special_fields = handler.get_special_fields() or {}
    selector_owners: dict[str, set[str]] = {}
    for field_type, selectors in special_fields.items():
        for selector in selectors:
            selector_owners.setdefault(selector, set()).add(field_type)

    filled = 0
    for field_type, selectors in special_fields.items():
        # File upload is already handled by upload_cv(), including hidden
        # input[type=file] controls and file-chooser fallbacks.
        if field_type == "resume":
            continue

        field_done = False
        for selector in selectors:
            if len(selector_owners.get(selector, ())) > 1:
                continue

            for frame in page.frames:
                try:
                    el = frame.query_selector(selector)
                except Exception:
                    el = None
                if not el:
                    continue

                candidate = _candidate_from_element(
                    frame, el, include_hidden_file=True
                )
                if not candidate or candidate.field_type == "file":
                    continue
                if _current_value(candidate):
                    field_done = True
                    break

                decision = confidence_engine.ConfidenceDecision(
                    field_type=field_type,
                    confidence=0.99,
                    action="auto_fill",
                    reason=f"plugin_selector:{getattr(handler, 'ats', 'unknown')}",
                    original_field=candidate.match_text,
                    answer_key=field_type,
                    metadata={
                        "ats": getattr(handler, "ats", "unknown"),
                        "plugin_selector": selector,
                    },
                )
                answer = answer_for_decision(
                    candidate,
                    decision,
                    personal_info,
                    responses,
                    motivation_answer,
                    experience_answer,
                    cv_maestro_text,
                    job_context,
                )
                if answer and fill_field(candidate, answer, stats):
                    filled += 1
                    field_done = True
                    stats.decisions.append(decision.as_dict())
                    if app_logger:
                        app_logger.event(
                            "plugin_field_filled",
                            ATS=getattr(handler, "ats", "unknown"),
                            field_type=field_type,
                            selector=selector,
                            decision=decision.as_dict(),
                        )
                    break

            if field_done:
                break

    return filled


def fill_from_memory(
    page: Page,
    personal_info: dict[str, str],
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    cv_maestro_text: str,
    job_context: str,
    stats: EngineStats,
    ats: str,
    memory: ATSMemory,
    app_logger: Optional[ApplicationLogger] = None,
) -> int:
    """Try selectors learned for this ATS before universal discovery.

    This is intentionally best-effort. Any stale selector is recorded as a
    memory failure and the normal DOM discovery continues.
    """
    filled = 0
    for field_type in confidence_engine.FIELD_KEYWORDS:
        for selector in memory.selectors_for(ats, field_type):
            try:
                el = page.query_selector(selector)
                if not el:
                    memory.record_failure(ats, field_type, selector)
                    continue
                frame = getattr(el, "owner_frame", lambda: page.main_frame)()
                candidate = _candidate_from_element(frame, el, include_hidden_file=True)
                if not candidate or candidate.field_type == "file":
                    continue
                decision = confidence_engine.ConfidenceDecision(
                    field_type=field_type,
                    confidence=0.99,
                    action="auto_fill",
                    reason="memory_selector",
                    original_field=candidate.match_text,
                    answer_key=field_type,
                    metadata={"ats": ats, "memory_hit": True},
                )
                answer = answer_for_decision(
                    candidate, decision, personal_info, responses, motivation_answer,
                    experience_answer, cv_maestro_text, job_context,
                )
                if answer and fill_field(candidate, answer, stats):
                    filled += 1
                    stats.decisions.append(decision.as_dict())
                    memory.record_success(ats, field_type, selector)
                    if app_logger:
                        app_logger.event("memory_field_filled", selector=selector, decision=decision.as_dict())
            except Exception as exc:
                memory.record_failure(ats, field_type, selector)
                if app_logger:
                    app_logger.event("memory_selector_failed", selector=selector, field_type=field_type, error=str(exc))
    return filled


def fill_current_state(
    page: Page,
    personal_info: dict[str, str],
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    adapted_pdf_path: str,
    cv_maestro_text: str,
    job_context: str,
    stats: EngineStats,
    ats: str = "unknown",
    memory: Optional[ATSMemory] = None,
    handler: Any = None,
    app_logger: Optional[ApplicationLogger] = None,
    application_id: str = "",
) -> int:
    close_popups(page)
    upload_recovery = recovery_engine.run_with_recovery(
        page=page,
        application_id=application_id or "no-id",
        primary=lambda: upload_cv(page, adapted_pdf_path, stats),
        field_name="resume_upload",
    )
    if not upload_recovery.ok and app_logger:
        app_logger.event("upload_recovery_failed", recovery=upload_recovery.as_dict())
    filled_count = 0
    memory = memory or ATSMemory(enabled=False)

    filled_count += fill_from_plugin_selectors(
        page,
        personal_info,
        responses,
        motivation_answer,
        experience_answer,
        cv_maestro_text,
        job_context,
        stats,
        handler,
        app_logger,
    )

    filled_count += fill_from_memory(
        page, personal_info, responses, motivation_answer, experience_answer,
        cv_maestro_text, job_context, stats, ats, memory, app_logger,
    )

    for _ in range(MAX_DYNAMIC_FIELD_PASSES):
        fields = inspect_fields(page, stats)
        pass_filled = 0
        for candidate in fields:
            if candidate.field_type == "file":
                continue
            plugin_hint = handler.hint_for_candidate(candidate) if handler else ""
            memory_hit = bool(memory.selectors_for(ats, plugin_hint or candidate.field_type))
            decision = confidence_engine.score_field(
                candidate, ats=ats, memory_hit=memory_hit, plugin_hint=plugin_hint
            )
            stats.decisions.append(decision.as_dict())
            if app_logger:
                app_logger.event("field_decision", decision=decision.as_dict(), selector=candidate.selector)

            # Even a low-confidence FIELD-TYPE classification must not hide a
            # high-confidence deterministic answer. Example: "Do you require
            # sponsorship?" may look like an unknown field to the classifier
            # but the factual answer is still known with certainty.
            compliance_answer = _match_compliance_fact(
                candidate.match_text,
                cv_maestro_text,
            )
            known_resolution = deterministic_answers.resolve_known_answer(
                candidate.match_text,
                personal_info,
                load_candidate_bible(),
                cv_maestro_text,
            )

            answer = None
            if compliance_answer is not None:
                answer = compliance_answer
                decision.metadata["answer_source"] = "compliance_fact"
            elif known_resolution and known_resolution.value:
                answer = known_resolution.value
                decision.metadata["answer_source"] = known_resolution.source
                decision.metadata["answer_key_resolved"] = known_resolution.key
                decision.metadata["answer_confidence"] = known_resolution.confidence

            if decision.action == "skip_review" and not answer and not candidate.required:
                stats.skipped.append((candidate.match_text or candidate.field_type)[:120])
                if app_logger:
                    app_logger.event("field_skipped", decision=decision.as_dict(), selector=candidate.selector)
                continue

            if not answer:
                answer = answer_for_decision(
                    candidate, decision, personal_info, responses, motivation_answer,
                    experience_answer, cv_maestro_text, job_context,
                )
            if answer and fill_field(candidate, answer, stats):
                pass_filled += 1
                memory.record_success(ats, decision.field_type, candidate.selector)
                if app_logger:
                    app_logger.event("field_filled", decision=decision.as_dict(), selector=candidate.selector)
                _human_pause()
            elif answer:
                recovery = recovery_engine.run_with_recovery(
                    page=page,
                    application_id=application_id or "no-id",
                    primary=lambda c=candidate, v=answer: fill_field(c, v, stats),
                    field_name=decision.field_type,
                )
                if recovery.ok:
                    pass_filled += 1
                    memory.record_success(ats, decision.field_type, candidate.selector)
                else:
                    memory.record_failure(ats, decision.field_type, candidate.selector)
                    stats.errors.extend(recovery.errors)
                    if recovery.screenshot:
                        stats.errors.append(f"recovery_screenshot:{recovery.screenshot}")
                    if app_logger:
                        app_logger.event("field_recovery_failed", recovery=recovery.as_dict(), decision=decision.as_dict())
        filled_count += pass_filled
        if pass_filled == 0:
            break
    return filled_count


AI_AGENT_FALLBACK = os.environ.get("AI_AGENT_FALLBACK", "true").lower() == "true"
MAX_AGENT_STEPS = int(os.environ.get("ATS_MAX_AGENT_STEPS", "4"))


def _required_candidate_satisfied(candidate: FieldCandidate) -> bool:
    if candidate.field_type == "radio":
        try:
            return bool(
                candidate.element.evaluate(
                    """e => {
                        if (e.name) {
                            const root = e.form || e.ownerDocument;
                            return Array.from(root.querySelectorAll('input[type="radio"]'))
                                .some(r => r.name === e.name && r.checked);
                        }
                        const group = e.closest('[role="radiogroup"], fieldset') || e.parentElement;
                        return !!(group && group.querySelector(
                            'input[type="radio"]:checked, [role="radio"][aria-checked="true"]'
                        ));
                    }"""
                )
            )
        except Exception:
            return bool(_current_value(candidate))

    if candidate.field_type == "checkbox":
        return bool(_current_value(candidate))

    return bool(_current_value(candidate))


def _has_unfilled_required_fields(page: Page, stats: EngineStats) -> bool:
    """Check required controls by logical field/group, not each radio option."""
    for candidate in inspect_fields(page, stats):
        if (
            candidate.required
            and candidate.field_type != "file"
            and not _required_candidate_satisfied(candidate)
        ):
            return True
    return False


def run_form_state_machine(
    page: Page,
    personal_info: dict[str, str],
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    adapted_pdf_path: str,
    cv_maestro_text: str = "",
    job_context: str = "",
    application_id: str = "",
    company: str = "",
    job_title: str = "",
) -> dict[str, Any]:
    detection = ats_detector.detect_page(page)
    stats = EngineStats(ats=detection.ats, ats_confidence=detection.confidence)
    handler = get_handler(detection.ats)
    memory = ATSMemory()
    app_logger = ApplicationLogger(application_id or "no-id", company=company, role=job_title, ats=detection.ats)
    last_url = page.url
    metrics.record("application_started", application_id=application_id, ATS=detection.ats, company=company, role=job_title)
    app_logger.event("application_started", ats_detection=detection.as_dict(), url=last_url)

    # Circuit breaker anti-loop-silencioso: si un retry completo termina
    # exactamente igual que el anterior (misma URL, mismos errores de
    # validacion, sin nada nuevo llenado), reintentar mas veces no va a
    # cambiar nada -- solo quema los MAX_RETRIES en silencio hasta
    # "max_retries" sin dar pista de por que ("sigue revisando y no hace
    # click en Apply, no se por que"). Con esto, a la 2da vuelta sin
    # progreso real se corta YA y se devuelve ready_for_submit con el
    # detalle de que quedo trabado, en vez de agotar los 8 reintentos.
    previous_progress_signature: tuple | None = None
    stale_progress_count = 0

    for retry in range(MAX_RETRIES):
        stats.pages += 1
        app_logger.event("page_cycle_started", retry=retry, url=page.url)

        # Some ATS insert account creation/login/email verification BETWEEN
        # application steps. Resolve that gate and then continue with the same
        # form engine instead of dropping to manual_required immediately.
        account_state = account_access.detect_account_state(page)
        if account_state is not None:
            account_result = account_access.ensure_account_access(
                page=page,
                personal_info=personal_info,
                company=company,
                job_title=job_title,
                application_id=application_id,
            )
            if not account_result.ok:
                detail = account_result.detail
                status = (
                    "captcha"
                    if account_result.status == "captcha"
                    else account_result.status
                )
                stats.errors.append(detail)
                metrics.record(
                    "captcha" if status == "captcha" else "account_gate",
                    application_id=application_id,
                    ATS=stats.ats,
                )
                app_logger.event(
                    "manual_intervention",
                    reason=status,
                    detail=detail,
                    stats=stats.as_dict(),
                )
                return {
                    "status": status,
                    "submitted": False,
                    "stats": stats.as_dict(),
                    "detail": detail,
                }

            # Login/registration can redirect from a generic auth host to the
            # actual ATS. Refresh ATS detection/plugin after access succeeds.
            detection = ats_detector.detect_page(page)
            stats.ats = detection.ats
            stats.ats_confidence = detection.confidence
            handler = get_handler(detection.ats)
            last_url = page.url
            app_logger.event(
                "account_access_resolved",
                account_state=account_state,
                ATS=detection.ats,
                url=page.url,
            )
            continue

        blocker = detect_blocker(page)
        if blocker:
            detail = (
                "CAPTCHA detectado; se requiere intervencion manual via n8n."
                if blocker == "captcha"
                else "Login detectado, pero la capa de acceso no pudo clasificar el portal."
            )
            stats.errors.append(detail)
            metrics.record("captcha" if blocker == "captcha" else "login", application_id=application_id, ATS=detection.ats)
            app_logger.event("manual_intervention", reason=blocker, detail=detail, stats=stats.as_dict())
            return {"status": blocker, "submitted": False, "stats": stats.as_dict(), "detail": detail}

        fill_current_state(
            page, personal_info, responses, motivation_answer, experience_answer,
            adapted_pdf_path, cv_maestro_text, job_context, stats,
            ats=detection.ats, memory=memory, handler=handler,
            app_logger=app_logger, application_id=application_id,
        )

        errors = validation_errors(page)
        if errors:
            stats.errors.extend(errors)
            app_logger.event("validation_errors", errors=errors)
            fill_current_state(
                page, personal_info, responses, motivation_answer, experience_answer,
                adapted_pdf_path, cv_maestro_text, job_context, stats,
                ats=detection.ats, memory=memory, handler=handler,
                app_logger=app_logger, application_id=application_id,
            )

        if is_submitted(page, last_url):
            metrics.record("application_submitted", application_id=application_id, ATS=detection.ats, duration=stats.as_dict()["elapsed_seconds"])
            app_logger.event("application_submitted", stats=stats.as_dict())
            return {"status": "submitted", "submitted": True, "stats": stats.as_dict(), "detail": "Application Submitted confirmado."}

        try:
            plugin_navigated = handler.handle_navigation(page, stats)
        except Exception as exc:
            plugin_navigated = False
            stats.errors.append(f"plugin_navigation:{exc}")
            app_logger.event("plugin_navigation_failed", error=str(exc))
        if plugin_navigated:
            app_logger.event("plugin_navigation", ATS=detection.ats)
            last_url = page.url
            continue

        if click_next(page, stats):
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            _human_pause(350, 900)
            last_url = page.url
            continue

        # Circuit breaker anti-loop-silencioso (ver comentario donde se
        # inicializan previous_progress_signature/stale_progress_count):
        # comparamos el estado actual contra el del retry anterior. Si es
        # IDENTICO dos veces seguidas -- misma URL, mismos errores de
        # validacion visibles, mismo total de campos llenados -- ya
        # sabemos que seguir reintentando no va a cambiar nada (esto es
        # justo lo que describiste: "sigue revisando" sin nunca llegar a
        # Apply, quemando los 8 reintentos en silencio). Cortamos ahi con
        # un detail explicito en vez de seguir dando vueltas.
        current_errors = tuple(sorted(validation_errors(page)))
        progress_signature = (page.url, current_errors, len(stats.filled))
        if progress_signature == previous_progress_signature:
            stale_progress_count += 1
        else:
            stale_progress_count = 0
        previous_progress_signature = progress_signature

        if stale_progress_count >= 2:
            detail = (
                "El motor dejo de progresar durante 2 vueltas seguidas "
                f"(misma URL, mismos {len(current_errors)} error(es) de "
                "validacion visibles, sin campos nuevos llenados) -- se "
                "corta el loop en vez de agotar los reintentos en "
                "silencio. Revisa el screenshot y los errores en 'stats'."
            )
            stats.errors.append(f"stalled_no_progress: {current_errors}")
            app_logger.event("stalled_no_progress", errors=list(current_errors), stats=stats.as_dict())
            return {
                "status": "stalled_no_progress", "submitted": False,
                "stats": stats.as_dict(), "detail": detail,
            }

        # El motor deterministico no encontro next/submit ni pudo avanzar
        # mas. Antes esto significaba "ready_for_submit" directo, aunque
        # quedaran campos obligatorios vacios (ej. un widget que
        # inspect_fields/fill_field no reconocen: date picker custom,
        # multi-select con chips, slider, etc.). Si AI_AGENT_FALLBACK esta
        # activo, el agente de vision (Gemini viendo el screenshot + el DOM)
        # intenta esos campos puntuales antes de rendirse. Nunca hace
        # click en submit -- eso lo sigue decidiendo SUBMIT_APPLICATIONS
        # en form_filler.py, como siempre.
        if AI_AGENT_FALLBACK and _has_unfilled_required_fields(page, stats):
            from . import run_step  # import local: evita ciclo al cargar el paquete

            agent_progress = False
            for agent_step in range(MAX_AGENT_STEPS):
                try:
                    agent_result = run_step(
                        page, application_id or "no-id", agent_step, job_context, cv_maestro_text,
                    )
                except Exception as exc:
                    stats.errors.append(f"vision_agent:{exc}")
                    break
                app_logger.event("vision_agent_step", result=agent_result)
                if agent_result.get("page_state") in ("login_required", "captcha", "confirmation"):
                    stats.errors.append(f"vision_agent_page_state:{agent_result.get('page_state')}")
                    break
                if agent_result.get("filled") or agent_result.get("clicked_next"):
                    agent_progress = True
                if agent_result.get("action") == "none" and not agent_result.get("filled"):
                    break  # el agente tampoco encontro que hacer -- no seguir insistiendo
            if agent_progress:
                last_url = page.url
                continue  # dale otra vuelta al loop: puede haber nuevos campos/next

        if _has_unfilled_required_fields(page, stats):
            detail = (
                "El formulario todavía tiene campos obligatorios sin resolver "
                "después del motor determinístico y del fallback de UI. "
                "No se marcará como listo ni se intentará Submit."
            )
            stats.errors.append("incomplete_required_fields")
            app_logger.event(
                "incomplete_required_fields",
                stats=stats.as_dict(),
            )
            return {
                "status": "incomplete_required_fields",
                "submitted": False,
                "stats": stats.as_dict(),
                "detail": detail,
            }

        app_logger.event("ready_for_submit", stats=stats.as_dict())
        return {"status": "ready_for_submit", "submitted": False, "stats": stats.as_dict(), "detail": "Formulario completado hasta el paso final."}

    metrics.record("application_failed", application_id=application_id, ATS=detection.ats, reason="max_retries")
    app_logger.event("application_failed", reason="max_retries", stats=stats.as_dict())
    return {
        "status": "max_retries",
        "submitted": False,
        "stats": stats.as_dict(),
        "detail": f"Se alcanzo MAX_RETRIES={MAX_RETRIES}; evitando loop infinito.",
    }


def submit_and_confirm(
    page: Page,
    stats: Optional[EngineStats] = None,
    application_id: str = "",
    app_logger: Optional[ApplicationLogger] = None,
) -> dict[str, Any]:
    stats = stats or EngineStats()

    # GUARDARRAÍL DURO: nunca clickear Submit si todavía queda un campo
    # OBLIGATORIO vacío -- sin importar que run_form_state_machine haya
    # devuelto "ready_for_submit". Ese status significa "el motor
    # determinístico y el agente de visión ya no encuentran más que
    # hacer", NO "el formulario está completo" -- si un widget raro
    # (ej. el autocomplete de ciudad) quedó sin confirmar, antes esto
    # igual clickeaba Submit, el sitio bloqueaba el envío por su propia
    # validación, y esa validación fallida pintaba de rojo TODO el
    # formulario (incluidos campos que sí estaban bien) -- exactamente lo
    # que se vio en el screenshot real.
    unfilled = [
        c.match_text or c.label or c.selector
        for c in inspect_fields(page, stats)
        if (
            c.required
            and c.field_type != "file"
            and not _required_candidate_satisfied(c)
        )
    ]
    if unfilled:
        detail = (
            f"Bloqueado ANTES de clickear Submit: {len(unfilled)} campo(s) "
            f"obligatorio(s) siguen vacíos ({', '.join(unfilled[:5])}). "
            "Nunca se envía un formulario incompleto."
        )
        stats.errors.append(f"submit_blocked_missing_required: {unfilled}")
        if app_logger:
            app_logger.event("submit_blocked_missing_required", missing_fields=unfilled)
        return {
            "status": "incomplete_required_fields", "submitted": False,
            "detail": detail, "stats": stats.as_dict(),
        }

    before_url = page.url
    submit_recovery = recovery_engine.run_with_recovery(
        page=page,
        application_id=application_id or "no-id",
        primary=lambda: click_submit(page, stats),
        field_name="submit_button",
    )
    if not submit_recovery.ok:
        if app_logger:
            app_logger.event("submit_failed", recovery=submit_recovery.as_dict())
        return {"status": "error", "submitted": False, "detail": "No encontre boton de submit habilitado.", "stats": stats.as_dict()}
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass
    _human_pause(800, 1600)
    confirmed = is_submitted(page, before_url)
    if confirmed:
        metrics.record("application_submitted", application_id=application_id, ATS=stats.ats, duration=stats.as_dict()["elapsed_seconds"])
    elif app_logger:
        app_logger.event("submit_unconfirmed", stats=stats.as_dict())
    return {
        "status": "submitted" if confirmed else "unconfirmed_submit",
        "submitted": confirmed,
        "detail": "Submit ejecutado y confirmado." if confirmed else "Submit ejecutado, pero no pude confirmar exito.",
        "stats": stats.as_dict(),
    }