"""
form_filler.py
Autofill + click REAL sobre la página de la oferta, usando Playwright
(gratis, local, mismo motor que scraper.py).

Qué hace:
  1. Abre la URL de la oferta en un navegador headless (o visible si
     HEADLESS=false, útil para ver qué está pasando la primera vez).
  2. Detecta el tipo de formulario:
       - Greenhouse (boards.greenhouse.io)
       - Lever (jobs.lever.co)
       - Personio (*.jobs.personio.com)
       - LinkedIn Easy Apply
       - Genérico (cualquier otro sitio con <form>, <input>, <textarea>)
  3. Rellena datos personales (nombre, email, teléfono, LinkedIn).
  4. Sube el CV adaptado (el PDF que ya generó pdf_generator.py).
  5. Para cada pregunta detectada por Gemini, busca el campo del
     formulario cuyo <label> más se parece a esa pregunta (fuzzy match)
     y escribe la respuesta generada.
  6. Si SUBMIT_APPLICATIONS=true, hace click en el botón de enviar.
     Por defecto es "false" (dry-run): deja todo listo y toma un
     screenshot final para que revises antes de enviar manualmente.

IMPORTANTE — por qué el dry-run es el default:
  Cada ATS tiene su propio formulario, y un selector mal detectado puede
  mandar una postulación con datos a medio llenar. Recomiendo correr unas
  10-15 veces en dry-run, revisar los screenshots en `work/screenshots/`,
  y solo después de confiar en el matching activar el auto-submit.
"""

import json
import os
import re
import threading
import queue
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

from models import QuestionAnswer
import gemini_service
import application_agent
from application_agent import form_engine, account_access, deterministic_answers
from candidate_bible import load_candidate_bible

AI_AGENT_FALLBACK = os.environ.get("AI_AGENT_FALLBACK", "true").lower() == "true"
MAX_AGENT_STEPS = 4  # tope de pasos que el agente IA puede intentar por postulación
AGENT_MIN_CONFIDENCE_TO_SUBMIT = float(os.environ.get("AGENT_MIN_CONFIDENCE_TO_SUBMIT", "0.5"))
USE_ROBUST_ATS_ENGINE = os.environ.get("USE_ROBUST_ATS_ENGINE", "true").lower() == "true"
PLAYWRIGHT_USER_DATA_DIR = Path(os.environ.get("PLAYWRIGHT_USER_DATA_DIR", "work/browser_profile"))

HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"
SUBMIT_APPLICATIONS = os.environ.get("SUBMIT_APPLICATIONS", "false").lower() == "true"
# Human-in-the-loop: pausa dura justo antes del click final de "submit"
# para que puedas revisar/corregir el formulario a mano. Activado por
# defecto -- una vez que confíes en el matching para un ATS puntual,
# desactivalo con HUMAN_REVIEW_BEFORE_SUBMIT=false para envío 100% automático.
HUMAN_REVIEW_BEFORE_SUBMIT = os.environ.get("HUMAN_REVIEW_BEFORE_SUBMIT", "true").lower() == "true"
# Cuánto esperar como MÁXIMO por un ENTER manual antes de seguir solo.
# Nunca debe ser infinito -- si esto corre disparado por n8n (sin nadie
# mirando la terminal de uvicorn), un input() sin timeout se cuelga para
# siempre: el navegador se ve "listo" pero el request nunca vuelve.
HUMAN_REVIEW_TIMEOUT_SECONDS = int(os.environ.get("HUMAN_REVIEW_TIMEOUT_SECONDS", "20"))
FUZZY_MATCH_THRESHOLD = 0.45
MAX_APPLY_HOPS = 6  # portal search -> job detail -> Apply -> account/login -> real form

# CANDADO DE CONCURRENCIA -- fix del bug de "solo procesó un link de los 2
# del Sheet". main.py define process_application con def normal (no async
# def), asi que FastAPI corre cada request en un thread del threadpool
# interno. Si n8n dispara 2 requests casi al mismo tiempo, 2 threads
# DISTINTOS del mismo proceso llaman sync_playwright() en simultaneo. La
# Sync API de Playwright NO es thread-safe (documentado por Playwright):
# usarla desde 2 threads del mismo proceso a la vez puede hacer que una de
# las 2 llamadas falle silenciosamente -- eso explica por que un link salio
# perfecto y el otro no. Este Lock serializa TODO uso de Playwright: si
# llegan 2 solicitudes a la vez, la segunda espera a que la primera termine.
_PLAYWRIGHT_LOCK = threading.Lock()

SCREENSHOT_DIR = Path("work/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Muchas ofertas (Personio, Greenhouse, sitios propios como el de Navera en
# el ejemplo) muestran primero una LANDING PAGE de la oferta con un botón
# "Apply for this job" / "Bewerben" / "Postular" — el formulario real (el
# que tiene los campos a rellenar) solo existe DESPUÉS de ese click. Antes,
# el bot solo miraba la landing page y tomaba screenshot de esa, nunca del
# formulario. Estos son los textos de botón que buscamos para cruzar esa
# landing page.
APPLY_BUTTON_SELECTORS = [
    "button:has-text('Apply for this job')",
    "a:has-text('Apply for this job')",
    "button:has-text('Apply now')",
    "a:has-text('Apply now')",
    "button:has-text('Start application')",
    "a:has-text('Start application')",
    "button:has-text('Start your application')",
    "a:has-text('Start your application')",
    "button:has-text('Continue application')",
    "a:has-text('Continue application')",
    "button:has-text('Continue to application')",
    "a:has-text('Continue to application')",
    "button:has-text('Apply for this position')",
    "a:has-text('Apply for this position')",
    "button:has-text('Apply to this job')",
    "a:has-text('Apply to this job')",
    "button:has-text('Easy Apply')",
    "[role='button']:has-text('Easy Apply')",
    "button:has-text('Apply with resume')",
    "button:has-text('Apply with CV')",
    "[data-automation-id*='apply' i]",
    "a[href*='apply' i]",
    "a:has-text('Bewerben')",
    "button:has-text('Bewerben')",
    "a:has-text('Postular')",
    "button:has-text('Postular')",
    "button:has-text('Apply')",
    "a:has-text('Apply')",
    "[role='button']:has-text('Apply')",
]

# Señales de texto de que el sitio exige crear cuenta / iniciar sesión
# antes de poder postular. Esto el bot NO lo automatiza (requiere manejo
# de contraseñas y a veces verificación de email/2FA) — lo detecta y avisa
# en vez de quedarse "atascado" rellenando campos que no existen.
ACCOUNT_WALL_MARKERS = [
    "create an account", "create your account", "sign up to apply",
    "log in to apply", "please log in", "create your profile",
    "crear una cuenta", "crea tu cuenta", "inicia sesión para postular",
    "registrieren", "konto erstellen",
]

# Datos personales fijos (edítalos o muévelos a variables de entorno).
# NOTA: "expected_salary" ya NO es fijo — main.py lo calcula por oferta con
# gemini_service.estimate_expected_salary() y lo inyecta en fill_and_submit_application().
PERSONAL_INFO = {
    "first_name": os.environ.get("APPLICANT_FIRST_NAME", ""),
    "last_name": os.environ.get("APPLICANT_LAST_NAME", ""),
    "email": os.environ.get("APPLICANT_EMAIL", ""),
    "phone": os.environ.get("APPLICANT_PHONE", ""),
    "linkedin": os.environ.get("APPLICANT_LINKEDIN", ""),
    "location": os.environ.get("APPLICANT_LOCATION", ""),
    "available_from": os.environ.get("APPLICANT_AVAILABLE_FROM", ""),
}

# Nombres/ids reales más comunes que usan Greenhouse/Lever/Ashby/Personio/
# formularios propios para cada campo -- el bookmarklet prueba estos tokens
# contra name/id/aria-label hasta encontrar el primer input que matchee.
_BOOKMARKLET_FIELD_TOKENS: dict[str, tuple[str, ...]] = {
    "first_name": ("first_name", "firstName", "first-name", "fname"),
    "last_name": ("last_name", "lastName", "last-name", "lname"),
    "email": ("email",),
    "phone": ("phone", "mobile", "telephone"),
    "linkedin": ("linkedin",),
    "location": ("location", "city"),
    "expected_salary": ("salary", "compensation", "gehalt"),
}


def build_bookmarklet(personal_info: dict) -> str:
    """Genera un `javascript:(function(){...})();` que llena los campos de
    texto más comunes de un formulario de postulación (nombre, email,
    teléfono, LinkedIn, ubicación, sueldo esperado) -- para usar en el
    celular/tablet donde no existe el perfil persistente de Chrome del PC.

    Cómo usarlo: en el navegador del celular, crea un marcador nuevo,
    pegá esta URL completa (empieza con "javascript:") como dirección del
    marcador, y tocalo estando parado en la página real de la
    postulación.

    LIMITACIÓN IMPORTANTE (no es un bug, es una restricción de seguridad
    de TODOS los navegadores): ningún bookmarklet puede setear el valor
    de un <input type="file">, así que el CV/carta de presentación NUNCA
    se puede autocompletar así -- siempre hay que subirlo a mano. Este
    bookmarklet solo cubre campos de texto simples.
    """
    data = {key: str(personal_info.get(key, "") or "") for key in _BOOKMARKLET_FIELD_TOKENS}
    data_json = json.dumps(data, ensure_ascii=False)
    tokens_json = json.dumps(_BOOKMARKLET_FIELD_TOKENS, ensure_ascii=False)

    script = (
        "(function(){"
        f"var DATA={data_json};var TOKENS={tokens_json};"
        "function setVal(el,val){"
        "var proto=Object.getPrototypeOf(el);"
        "var d=Object.getOwnPropertyDescriptor(proto,'value');"
        "var setter=d&&d.set;"
        "if(setter){setter.call(el,val);}else{el.value=val;}"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));"
        "}"
        "var filled=[],missed=[];"
        "Object.keys(TOKENS).forEach(function(key){"
        "var val=DATA[key];if(!val)return;"
        "var found=null;"
        "TOKENS[key].forEach(function(tok){"
        "if(found)return;"
        "var q=\"input[name*='\"+tok+\"' i],input[id*='\"+tok+\"' i],\"+"
        "\"textarea[name*='\"+tok+\"' i],input[aria-label*='\"+tok+\"' i]\";"
        "var els=document.querySelectorAll(q);"
        "if(els.length)found=els[0];"
        "});"
        "if(found){setVal(found,val);filled.push(key);}else{missed.push(key);}"
        "});"
        "var msg='Bookmarklet: '+filled.length+' campos llenados ('+filled.join(', ')+').';"
        "if(missed.length){msg+=' Sin encontrar: '+missed.join(', ')+'.';}"
        "msg+=' Sube el CV/carta a mano, eso el navegador no lo permite por seguridad.';"
        "alert(msg);"
        "})();"
    )
    return "javascript:" + script


# Botones para avanzar entre PASOS de un formulario multi-página (distinto
# de APPLY_BUTTON_SELECTORS, que cruza la landing page hacia el formulario).
# Muchos ATS (Personio, Greenhouse Job Boards nuevos, formularios propios)
# piden Nombre/Email en el paso 1, CV en el paso 2, preguntas en el paso 3,
# etc. Antes el bot solo llenaba el primer paso que encontraba y nunca
# avanzaba -> se perdían todos los campos de los pasos siguientes.
NEXT_STEP_SELECTORS = [
    "button:has-text('Next')",
    "button:has-text('Continue')",
    "button:has-text('Weiter')",
    "button:has-text('Siguiente')",
    "button:has-text('Continuar')",
    "a:has-text('Next')",
    "a:has-text('Continue')",
    "button[type='button']:has-text('Next step')",
]
MAX_FORM_STEPS = 6  # cuántos pasos/páginas del formulario recorremos como máximo


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _label_text_for_field(page: Page, field) -> str:
    """Intenta obtener el texto del <label> asociado a un input/textarea."""
    try:
        field_id = field.get_attribute("id")
        if field_id:
            label = page.query_selector(f"label[for='{field_id}']")
            if label:
                return label.inner_text().strip()
        # Fallback: label envolvente
        label = field.query_selector("xpath=ancestor::label[1]")
        if label:
            return label.inner_text().strip()
        # Fallback: placeholder o aria-label
        return field.get_attribute("placeholder") or field.get_attribute("aria-label") or ""
    except Exception:
        return ""


def _fill_personal_fields(page: Page, personal_info: dict) -> None:
    """Rellena TODOS los campos de datos personales que reconozcamos por su
    <label>, incluyendo los que antes se quedaban vacíos: sueldo esperado,
    ubicación y fecha de disponibilidad (además de nombre/email/teléfono/
    LinkedIn). Cubre <input> de texto, <input type=date>, y <select>."""
    mapping = {
        "first_name": ["first name", "nombre", "given name", "vorname"],
        "last_name": ["last name", "apellido", "family name", "surname", "nachname"],
        "email": ["email", "correo", "e-mail"],
        "phone": ["phone", "telefono", "teléfono", "mobile", "telefon"],
        "linkedin": ["linkedin"],
        "expected_salary": [
            "expected salary", "salary expectation", "salary", "sueldo",
            "pretensión salarial", "pretension salarial", "gehaltsvorstellung",
        ],
        "location": ["location", "ubicación", "ubicacion", "city", "wohnort", "standort"],
        "available_from": [
            "available from", "availability", "start date", "earliest start",
            "disponible desde", "eintrittsdatum", "verfügbar ab",
        ],
    }

    def _best_match(label: str) -> Optional[str]:
        for key, keywords in mapping.items():
            if any(kw in label for kw in keywords):
                return key
        return None

    # <input> de texto / número / fecha
    for field in page.query_selector_all("input"):
        try:
            if not field.is_visible():
                continue
            field_type = (field.get_attribute("type") or "text").lower()
            if field_type in ("file", "checkbox", "radio", "hidden", "submit", "button"):
                continue
        except Exception:
            continue
        label = _label_text_for_field(page, field).lower()
        if not label:
            continue
        key = _best_match(label)
        if not key:
            continue
        value = personal_info.get(key, "")
        if not value:
            continue
        try:
            if field_type == "date":
                field.fill(_to_iso_date(value))
            else:
                field.fill(str(value))
        except Exception:
            pass

    # <select> (algunos ATS piden rango de sueldo o ciudad como dropdown)
    for select in page.query_selector_all("select"):
        try:
            if not select.is_visible():
                continue
        except Exception:
            continue
        label = _label_text_for_field(page, select).lower()
        if not label:
            continue
        key = _best_match(label)
        if not key:
            continue
        value = str(personal_info.get(key, ""))
        if not value:
            continue
        try:
            select.select_option(label=value)
        except Exception:
            try:
                select.select_option(value=value)
            except Exception:
                pass


def _to_iso_date(value: str) -> str:
    """Convierte 'Immediately' u otro texto libre a una fecha real (hoy) en
    formato YYYY-MM-DD, porque un <input type=date> rechaza texto libre."""
    import datetime
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    return datetime.date.today().isoformat()


def _upload_cv(page: Page, pdf_path: str) -> None:
    file_inputs = page.query_selector_all("input[type='file']")
    for f in file_inputs:
        try:
            f.set_input_files(pdf_path)
        except Exception:
            continue


# Labels que YA maneja _fill_personal_fields con datos reales (no deben
# ser sobreescritos con una respuesta inventada por IA).
PERSONAL_FIELD_KEYWORDS = [
    "first name", "nombre", "given name", "vorname",
    "last name", "apellido", "family name", "surname", "nachname",
    "email", "correo", "e-mail",
    "phone", "telefono", "teléfono", "mobile", "telefon",
    "linkedin",
    "expected salary", "salary expectation", "salary", "sueldo",
    "pretensión salarial", "pretension salarial", "gehaltsvorstellung",
    "location", "ubicación", "ubicacion", "city", "wohnort", "standort",
    "available from", "availability", "start date", "earliest start",
    "disponible desde", "eintrittsdatum", "verfügbar ab",
]


def _is_personal_field(label: str) -> bool:
    return any(kw in label for kw in PERSONAL_FIELD_KEYWORDS)


_COMBOBOX_PLACEHOLDER_TEXTS = {
    "", "select", "select...", "please select", "select an option",
    "-- select --", "choose", "choose an option", "bitte wählen",
    "bitte auswählen", "seleccione", "seleccionar",
}


def _combobox_is_unanswered(trigger) -> bool:
    """True si el trigger del dropdown custom sigue sin una respuesta real
    -- nunca lo tocamos, para no pisar un valor que el formulario ya
    trajera preseleccionado (o que el motor principal ya haya llenado
    bien en la pasada anterior).

    BUG REAL corregido acá (causa de "rellena el dropdown 2-3 veces"):
    cuando role='combobox' vive en el propio <input> (patrón muy común --
    el mismo que ya vimos en el campo de ciudad), `trigger.inner_text()`
    SIEMPRE es "" -- un <input> no tiene texto interno, su valor vive en
    `.value`. Eso hacía que este chequeo tratara como "sin responder" a
    CUALQUIER combobox tipo <input> que ya tenía una respuesta correcta
    (ej. "No" en la pregunta de países sancionados, o "Berlin, Germany" en
    ubicación), así que esta "red de seguridad" lo reabría y volvía a
    elegir una opción -- a veces distinta -- encima de lo que el motor
    principal ya había llenado bien. Ahora, si el trigger es un
    <input>/<textarea>, se revisa `.value` real primero."""
    try:
        tag = trigger.evaluate("e => e.tagName.toLowerCase()")
    except Exception:
        tag = ""
    if tag in ("input", "textarea"):
        try:
            value = (trigger.input_value() or "").strip()
            if value and value.lower() not in _COMBOBOX_PLACEHOLDER_TEXTS:
                return False
        except Exception:
            pass
    try:
        text = (trigger.inner_text() or "").strip().lower()
    except Exception:
        return True
    return text in _COMBOBOX_PLACEHOLDER_TEXTS


def _fill_custom_comboboxes(
    page: Page,
    personal_info: dict,
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    cv_maestro_text: str = "",
    job_context: str = "",
) -> None:
    """Deterministic safety pass for custom dropdowns left unanswered.

    This path never asks Gemini which option to choose. It uses Candidate
    Bible/personal facts or an answer already attached to this application,
    then selects only a semantically supported REAL option.
    """
    bible = load_candidate_bible()
    triggers = page.query_selector_all(
        "[role='combobox'], button[aria-haspopup='listbox'], div[aria-haspopup='listbox']"
    )

    all_qa = [qa for qa in responses if qa.answer]
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

    for trigger in triggers:
        try:
            if not trigger.is_visible() or not _combobox_is_unanswered(trigger):
                continue
        except Exception:
            continue

        label = _label_text_for_field(page, trigger)
        if not label:
            continue

        known = deterministic_answers.resolve_known_answer(
            label,
            personal_info,
            bible,
            cv_maestro_text,
        )
        answer = known.value if known and known.value else ""

        if not answer:
            best_match = None
            best_score = 0.0
            for qa in all_qa:
                score = _similarity(label, qa.question)
                if score > best_score:
                    best_score = score
                    best_match = qa
            if best_match and best_score >= FUZZY_MATCH_THRESHOLD:
                answer = best_match.answer

        # Closed-choice questions are never generated by Gemini.
        if not answer:
            continue

        try:
            trigger.click()
            page.wait_for_timeout(300)
        except Exception:
            continue

        visible_options = []
        option_texts = []
        for opt in page.query_selector_all("[role='option'], li[role='option']"):
            try:
                if opt.is_visible():
                    text_value = (opt.inner_text() or "").strip()
                    if text_value:
                        visible_options.append(opt)
                        option_texts.append(text_value)
            except Exception:
                continue

        sensitive = any(
            token in label.lower()
            for token in (
                "gender", "race", "ethnicity", "disability", "veteran",
                "pronouns", "sexual orientation", "religion", "marital status",
            )
        )
        idx, _score = deterministic_answers.best_option_index(
            label,
            answer,
            option_texts,
            sensitive=sensitive,
        )
        if idx is None:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            continue

        try:
            visible_options[idx].click()
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass


def _fill_dynamic_answers(
    page: Page,
    personal_info: dict,
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    cv_maestro_text: str = "",
    job_context: str = "",
) -> None:
    """Legacy text-field path aligned with the deterministic robust engine."""
    bible = load_candidate_bible()
    all_qa = [qa for qa in responses if qa.answer]
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

    fields = page.query_selector_all("textarea, input[type='text']")
    for field in fields:
        try:
            if not field.is_visible():
                continue
            existing_value = (field.input_value() or "").strip()
        except Exception:
            existing_value = ""
        if existing_value:
            continue

        label = _label_text_for_field(page, field)
        if not label or _is_personal_field(label.lower()):
            continue

        known = deterministic_answers.resolve_known_answer(
            label,
            personal_info,
            bible,
            cv_maestro_text,
        )
        answer = known.value if known and known.value else ""

        if not answer:
            best_match = None
            best_score = 0.0
            for qa in all_qa:
                score = _similarity(label, qa.question)
                if score > best_score:
                    best_score = score
                    best_match = qa
            if best_match and best_score >= FUZZY_MATCH_THRESHOLD:
                answer = best_match.answer

        # Only a genuinely required OPEN text field may trigger Gemini, and
        # only after deterministic sources and existing answers failed.
        if not answer:
            try:
                required = bool(
                    field.get_attribute("required")
                    or (field.get_attribute("aria-required") or "").lower() == "true"
                )
            except Exception:
                required = False

            if (
                required
                and form_engine.ALLOW_GEMINI_FORM_OPEN_TEXT_FALLBACK
                and cv_maestro_text
            ):
                answer = gemini_service.answer_open_field(
                    cv_maestro_text,
                    job_context,
                    label,
                )
                if answer:
                    responses.append(QuestionAnswer(question=label, answer=answer))

        if answer:
            try:
                field.fill(answer)
            except Exception:
                pass


def _page_has_application_form(page: Page) -> bool:
    """Detect the REAL application form, not search/filter inputs on a landing.

    Strong signals (resume upload / textarea) win immediately. Otherwise we
    require several visible fields inside a form/dialog, or several global
    fields plus application-specific copy such as First Name + Last Name + CV.
    This prevents LinkedIn/corporate landing pages from being mistaken for the
    application before Apply/Easy Apply is clicked.
    """
    try:
        file_inputs = [
            el for el in page.query_selector_all("input[type='file']")
            if el.is_visible()
        ]
        if file_inputs:
            return True

        textareas = [
            el for el in page.query_selector_all("textarea")
            if el.is_visible()
        ]
        if textareas:
            return True

        scoped = [
            el for el in page.query_selector_all(
                "form input, form select, form [role='combobox'], "
                "[role='dialog'] input, [role='dialog'] select, "
                "[role='dialog'] [role='combobox']"
            )
            if el.is_visible()
        ]
        if len(scoped) >= 3:
            return True

        visible_inputs = [
            el for el in page.query_selector_all(
                "input[type='text'], input[type='email'], input[type='tel'], "
                "input:not([type]), select, [role='combobox']"
            )
            if el.is_visible()
        ]
        if len(visible_inputs) < 3:
            return False

        try:
            body = (page.inner_text("body", timeout=1200) or "").lower()
        except Exception:
            body = ""
        application_markers = (
            "first name", "last name", "resume", "curriculum", "cover letter",
            "phone", "linkedin", "application questions", "personal information",
            "vorname", "nachname", "lebenslauf",
        )
        return sum(1 for marker in application_markers if marker in body) >= 2
    except Exception:
        return False


def _detect_account_wall(page: Page) -> bool:
    try:
        body_text = page.inner_text("body").lower()
    except Exception:
        return False
    return any(marker in body_text for marker in ACCOUNT_WALL_MARKERS)


def _find_apply_button(page: Page):
    for selector in APPLY_BUTTON_SELECTORS:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible() and (not hasattr(btn, "is_enabled") or btn.is_enabled()):
                return btn
        except Exception:
            continue

    # Semantic fallback for custom buttons/links where the ATS changes the
    # DOM but keeps an accessible Apply label.
    for text in (
        "Apply for this job",
        "Apply now",
        "Start application",
        "Start your application",
        "Continue application",
        "Continue to application",
        "Apply for this position",
        "Apply to this job",
        "Easy Apply",
        "Apply",
        "Bewerben",
        "Postular",
    ):
        for role in ("button", "link"):
            try:
                locator = page.get_by_role(role, name=re.compile(text, re.I)).first
                if locator.is_visible(timeout=350):
                    return locator
            except Exception:
                continue

    # Last deterministic fallback: score every visible interactive element by
    # its text/aria/title/href instead of relying on one ATS DOM structure.
    # This is intentionally limited to application-entry semantics, never final
    # submission semantics.
    positive = (
        "apply", "easy apply", "start application", "start your application",
        "apply for this job", "apply for this position", "bewerben", "postular",
        "candidatar", "candidatura",
    )
    negative = (
        "application status", "my applications", "already applied", "saved jobs",
        "save job", "withdraw", "view application",
    )
    best = None
    best_score = 0
    try:
        candidates = page.query_selector_all("button, a, [role='button']")
    except Exception:
        candidates = []

    for element in candidates:
        try:
            if not element.is_visible():
                continue
            text_value = (element.inner_text() or "").strip()
            attrs = " ".join(
                str(element.get_attribute(attr) or "")
                for attr in ("aria-label", "title", "href", "data-automation-id", "data-testid")
            )
            haystack = re.sub(r"\s+", " ", f"{text_value} {attrs}").strip().lower()
            if not haystack or any(token in haystack for token in negative):
                continue

            score = 0
            for token in positive:
                if token in haystack:
                    score += 3 if token in text_value.lower() else 2
            href = (element.get_attribute("href") or "").lower()
            if re.search(r"(?:/|=)(apply|application)(?:/|\?|&|$)", href):
                score += 2
            if score > best_score:
                best = element
                best_score = score
        except Exception:
            continue

    return best if best_score >= 3 else None


def _normalize_job_title_for_navigation(value: str) -> str:
    """Normalize cosmetic title variants without changing job meaning."""
    value = (value or "").lower()
    value = re.sub(
        r"\b(?:all genders|m/f/d|f/m/d|m/w/d|w/m/d|d/m/w|gn)\b",
        " ",
        value,
    )
    # Keep Unicode letters (ä/ö/ü/é/etc.) so German and international
    # titles compare faithfully on SuccessFactors and other portals.
    value = re.sub(r"[^\w+#]+", " ", value, flags=re.UNICODE).replace("_", " ")
    return re.sub(r"\s+", " ", value).strip()


def _find_matching_job_link(page: Page, job_title: str):
    """Find the requested job on a generic career portal with a strict match.

    Some SuccessFactors URLs only identify the company career portal and do
    not contain a requisition id. We already know the target job_title from
    triage, so use it to locate the correct listing. The threshold is
    intentionally strict: a near-miss stays manual rather than opening a
    different role.
    """
    target = _normalize_job_title_for_navigation(job_title)
    if len(target) < 5:
        return None

    target_tokens = {t for t in target.split() if len(t) >= 3}
    best = None
    best_score = 0.0

    try:
        links = page.query_selector_all("a[href]")
    except Exception:
        return None

    for link in links:
        try:
            if not link.is_visible():
                continue
            label = re.sub(r"\s+", " ", (link.inner_text() or "").strip())
            if not label or len(label) > 220:
                continue
            candidate = _normalize_job_title_for_navigation(label)
            if len(candidate) < 5:
                continue

            ratio = SequenceMatcher(None, target, candidate).ratio()
            candidate_tokens = {t for t in candidate.split() if len(t) >= 3}
            overlap = (
                len(target_tokens & candidate_tokens) / max(1, len(target_tokens))
            )

            if target == candidate:
                score = 1.0
            elif target in candidate or candidate in target:
                score = max(ratio, 0.90)
            else:
                score = (ratio * 0.70) + (overlap * 0.30)

            if score > best_score:
                best = link
                best_score = score
        except Exception:
            continue

    return best if best_score >= 0.82 else None


def _find_job_via_portal_search(page: Page, job_title: str):
    """Resolve company-level career portals to the exact requested job."""
    direct = _find_matching_job_link(page, job_title)
    if direct:
        return direct

    search_selectors = (
        "input[placeholder*='Search jobs' i]",
        "input[placeholder*='Search by Keyword' i]",
        "input[placeholder*='Search' i]",
        "input[aria-label*='Search jobs' i]",
        "input[aria-label*='Search' i]",
        "input[name*='keyword' i]",
        "input[id*='keyword' i]",
        "input[name*='search' i]",
    )
    search = None
    for selector in search_selectors:
        try:
            candidate = page.query_selector(selector)
            if candidate and candidate.is_visible():
                search = candidate
                break
        except Exception:
            continue

    if search is None:
        return None

    try:
        search.fill(job_title)
        search.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
    except Exception:
        return None

    found = _find_matching_job_link(page, job_title)
    if found:
        return found

    # Some SuccessFactors templates do not submit the keyword form on Enter.
    # Click an explicit Search button as a second deterministic attempt.
    for selector in (
        "button:has-text('Search')",
        "button:has-text('Search jobs')",
        "button:has-text('Jobs suchen')",
        "input[type='submit'][value*='Search' i]",
    ):
        try:
            button = page.query_selector(selector)
            if button and button.is_visible() and button.is_enabled():
                button.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)
                break
        except Exception:
            continue

    return _find_matching_job_link(page, job_title)


def _navigate_to_application_form(
    page: Page,
    context: BrowserContext,
    personal_info: dict,
    company: str = "",
    job_title: str = "",
    application_id: str = "",
) -> dict:
    """
    Cross landing pages, account/login gates and intermediate navigation until
    the real application form is reachable.

    Account creation/login is delegated to application_agent.account_access so
    the existing application form engine remains unchanged.
    """
    for _ in range(MAX_APPLY_HOPS):
        # Account/login gates must be checked BEFORE the generic "has form"
        # heuristic: signup pages often have 3+ inputs and otherwise look like
        # an application form.
        account_state = account_access.detect_account_state(page)
        if account_state is not None:
            account_result = account_access.ensure_account_access(
                page=page,
                personal_info=personal_info,
                company=company,
                job_title=job_title,
                application_id=application_id,
            )
            page = account_result.page
            if not account_result.ok:
                manual_status = (
                    "manual_required_captcha"
                    if account_result.status == "captcha"
                    else "manual_required_login"
                )
                return {
                    "ok": False,
                    "status": manual_status,
                    "reason": account_result.detail,
                    "page": page,
                    "account_status": account_result.status,
                }

            # Successful login/signup/email verification may reveal the
            # application form immediately or redirect to another landing step.
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            if _page_has_application_form(page):
                return {
                    "ok": True,
                    "status": "form_found",
                    "reason": "form_found_after_account_access",
                    "page": page,
                }
            continue

        if _page_has_application_form(page):
            return {"ok": True, "status": "form_found", "reason": "form_found", "page": page}

        apply_btn = _find_apply_button(page)
        if not apply_btn and job_title:
            # Company-level career portals (notably SAP SuccessFactors) can
            # arrive without a requisition id. Resolve the exact role by its
            # already-known job title before giving up.
            apply_btn = _find_job_via_portal_search(page, job_title)

        if not apply_btn:
            return {
                "ok": False,
                "status": "manual_required",
                "reason": (
                    "No encontré un formulario, acceso de cuenta, botón Apply "
                    "ni un enlace de puesto que coincida con suficiente "
                    "confianza con el job_title. No haré click en otro puesto."
                ),
                "page": page,
            }

        try:
            with context.expect_page(timeout=4000) as new_page_info:
                apply_btn.click()
            new_page = new_page_info.value
            new_page.wait_for_load_state("networkidle", timeout=15000)
            page = new_page
        except PlaywrightTimeoutError:
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1200)

    # One final account/form check after the last navigation hop.
    account_state = account_access.detect_account_state(page)
    if account_state is not None:
        account_result = account_access.ensure_account_access(
            page=page,
            personal_info=personal_info,
            company=company,
            job_title=job_title,
            application_id=application_id,
        )
        page = account_result.page
        if not account_result.ok:
            return {
                "ok": False,
                "status": (
                    "manual_required_captcha"
                    if account_result.status == "captcha"
                    else "manual_required_login"
                ),
                "reason": account_result.detail,
                "page": page,
                "account_status": account_result.status,
            }

    found = _page_has_application_form(page)
    return {
        "ok": found,
        "status": "form_found" if found else "manual_required",
        "reason": "form_found" if found else "max_hops_reached",
        "page": page,
    }


def _find_next_step_button(page: Page):
    """Busca un botón de 'siguiente paso' DENTRO del formulario (distinto de
    un botón final de submit). Se ignoran botones que también calcen con
    _find_submit_button para no hacer doble-click confuso."""
    for selector in NEXT_STEP_SELECTORS:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible() and btn.is_enabled():
                return btn
        except Exception:
            continue
    return None


def _fill_current_step(
    page: Page,
    personal_info: dict,
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    adapted_pdf_path: str,
    cv_maestro_text: str = "",
    job_context: str = "",
) -> None:
    _fill_personal_fields(page, personal_info)
    _upload_cv(page, adapted_pdf_path)
    _fill_dynamic_answers(
        page, personal_info, responses, motivation_answer, experience_answer,
        cv_maestro_text, job_context
    )
    _fill_custom_comboboxes(
        page, personal_info, responses, motivation_answer, experience_answer,
        cv_maestro_text, job_context
    )


def _advance_through_form_steps(
    page: Page,
    personal_info: dict,
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    adapted_pdf_path: str,
    cv_maestro_text: str = "",
    job_context: str = "",
    application_id: str = "",
    company: str = "",
    job_title: str = "",
) -> None:
    """Muchos formularios reales (Personio, Greenhouse, propios) tienen
    VARIOS pasos: paso 1 datos personales, paso 2 CV, paso 3 preguntas, etc.,
    con un botón 'Next'/'Continue'/'Weiter' entre cada uno. Antes el bot
    solo llenaba el primer paso que encontraba y nunca avanzaba, dejando
    todos los campos de los pasos siguientes vacíos. Esto rellena CADA paso
    y avanza hasta llegar al último (donde ya no hay botón de 'next', solo
    el de submit) o hasta MAX_FORM_STEPS."""
    if USE_ROBUST_ATS_ENGINE:
        result = form_engine.run_form_state_machine(
            page=page,
            personal_info=personal_info,
            responses=responses,
            motivation_answer=motivation_answer,
            experience_answer=experience_answer,
            adapted_pdf_path=adapted_pdf_path,
            cv_maestro_text=cv_maestro_text,
            job_context=job_context,
            application_id=application_id,
            company=company,
            job_title=job_title,
        )
        # Red de seguridad FINAL, sin importar qué haya hecho el motor por
        # dentro: dropdowns custom (role='combobox', ej. Greenhouse) que
        # hayan quedado en "Select..." y bloquearían el envío por
        # validación. No pisa nada que el motor ya haya llenado bien.
        _fill_custom_comboboxes(
            page, personal_info, responses, motivation_answer, experience_answer,
            cv_maestro_text, job_context
        )
        return result

    for step_number in range(MAX_FORM_STEPS):
        _fill_current_step(
            page, personal_info, responses, motivation_answer, experience_answer,
            adapted_pdf_path, cv_maestro_text, job_context,
        )

        next_btn = _find_next_step_button(page)
        if not next_btn:
            # No hay botón 'Next' -> presumiblemente es el último paso.
            # Fallback AI: acá es donde antes se quedaba atascado (ej. un
            # campo requerido tipo "Location (City)" en estado de error
            # porque .fill() plano no dispara la validación de un
            # autocomplete). El agente observa el paso y reintenta esos
            # campos puntuales, SIN tocar submit (eso lo sigue controlando
            # SUBMIT_APPLICATIONS más arriba).
            agent_min_confidence = None
            if AI_AGENT_FALLBACK:
                for agent_step in range(MAX_AGENT_STEPS):
                    try:
                        agent_result = application_agent.run_step(
                            page, application_id or "no-id", agent_step,
                            job_context, cv_maestro_text,
                        )
                    except Exception:
                        break  # el agente nunca debe tumbar el flujo determinístico
                    conf = agent_result.get("confidence")
                    if conf is not None:
                        agent_min_confidence = conf if agent_min_confidence is None else min(agent_min_confidence, conf)
                    if agent_result.get("page_state") in ("login_required", "captcha", "confirmation"):
                        break
                    if agent_result.get("action") == "none" and not agent_result.get("filled"):
                        break  # el agente no encontró nada más que hacer
            return {"agent_intervened": agent_min_confidence is not None, "agent_min_confidence": agent_min_confidence}

        try:
            next_btn.click()
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(800)

    return {"agent_intervened": False, "agent_min_confidence": None}


def _form_has_unmet_required_fields(page: Page) -> list[str]:
    """Escanea TODOS los frames buscando campos requeridos que sigan
    vacíos o en estado de error visible. Devuelve una lista de labels
    cortos para loguear -- vacía si el formulario ya está listo para
    enviarse (que es exactamente lo que se ve en el screenshot: todo en
    negro, sin bordes rojos, sin 'This field is required')."""
    unmet = []
    for frame in page.frames:
        try:
            elements = frame.query_selector_all(
                "[required], [aria-required='true'], [aria-invalid='true'], "
                "[role='alert'], .invalid, .error, .field-error"
            )
        except Exception:
            continue
        for el in elements:
            try:
                if not el.is_visible():
                    continue
                is_invalid = (el.get_attribute("aria-invalid") or "").lower() == "true"
                role = (el.get_attribute("role") or "").lower()
                cls = (el.get_attribute("class") or "").lower()
                looks_like_error_marker = role == "alert" or "invalid" in cls or "error" in cls
                if looks_like_error_marker:
                    text = (el.inner_text() or "").strip()
                    if text:
                        unmet.append(text[:80])
                    continue
                if is_invalid:
                    unmet.append((el.get_attribute("name") or el.get_attribute("id") or "campo sin nombre")[:80])
                    continue
                # required/aria-required sin marca de error explícita: solo
                # cuenta como "sin llenar" si de verdad está vacío.
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag in ("input", "textarea", "select"):
                    value = (el.input_value() or "").strip()
                    if not value:
                        label = (
                            el.get_attribute("aria-label")
                            or el.get_attribute("placeholder")
                            or el.get_attribute("name")
                            or "campo requerido"
                        )
                        unmet.append(label[:80])
            except Exception:
                continue
    return unmet


def _human_review_pause(page: Page, application_id: str) -> None:
    """Pausa ANTES del click final de submit -- pero NUNCA se cuelga para
    siempre. Primero revisa si ya no queda ningún campo requerido sin
    llenar/en error; si el formulario ya está listo (como en el
    screenshot: todo completo, sin bordes rojos), sigue directo, sin
    esperar ningún ENTER. Solo si de verdad falta algo espera un ENTER
    manual, y aun así con un tope de tiempo (HUMAN_REVIEW_TIMEOUT_SECONDS)
    para no dejar el proceso congelado cuando esto corre disparado por n8n
    sin nadie mirando la terminal."""
    if not HUMAN_REVIEW_BEFORE_SUBMIT:
        return

    unmet = _form_has_unmet_required_fields(page)
    if not unmet:
        print(f"[{application_id}] Formulario ya completo (sin campos requeridos pendientes) -- sigo sin pausar.")
        return

    print("\n" + "=" * 70)
    print(f"BOT PAUSADO [{application_id}] -- quedan campos requeridos sin llenar:")
    for u in unmet[:10]:
        print(f"  - {u}")
    print(f"Esperando ENTER hasta {HUMAN_REVIEW_TIMEOUT_SECONDS}s (Ctrl+C para abortar sin enviar).")
    print("Si nadie responde, sigo solo para no colgar el pipeline de n8n.")
    print("=" * 70)

    result_q: "queue.Queue[str]" = queue.Queue()

    def _read_input():
        try:
            result_q.put(input(">>> ENTER para continuar: "))
        except EOFError:
            result_q.put("")  # sin terminal interactiva -- no hay nada que leer

    reader = threading.Thread(target=_read_input, daemon=True)
    reader.start()
    try:
        result_q.get(timeout=HUMAN_REVIEW_TIMEOUT_SECONDS)
        print(f"[{application_id}] Confirmación recibida, continuando.")
    except queue.Empty:
        print(
            f"[{application_id}] Sin respuesta en {HUMAN_REVIEW_TIMEOUT_SECONDS}s -- "
            f"sigo automáticamente (quedaban {len(unmet)} campos sin confirmar, "
            f"revisa el screenshot de before_submit)."
        )


def _find_submit_button(page: Page):
    candidates = [
        "button:has-text('Submit')",
        "button:has-text('Apply')",
        "button:has-text('Enviar')",
        "button:has-text('Postular')",
        "input[type='submit']",
    ]
    for selector in candidates:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible() and btn.is_enabled():
                return btn
        except Exception:
            continue
    return None


def fill_and_submit_application(
    url: str,
    responses: list[QuestionAnswer],
    motivation_answer: str,
    experience_answer: str,
    adapted_pdf_path: str,
    application_id: str,
    expected_salary: Optional[int] = None,
    cv_maestro_text: str = "",
    job_context: str = "",
    company: str = "",
    job_title: str = "",
) -> dict:
    """
    Devuelve un dict con el resultado:
      { "status": "dry_run" | "submitted" | "error", "screenshot": path, "detail": str }
    """
    result = {"status": "error", "screenshot": None, "detail": ""}

    # Copia de PERSONAL_INFO con el sueldo ya estimado para ESTA oferta
    # (viene de gemini_service.estimate_expected_salary en main.py). Si no
    # se pasó nada, usamos 55000 como default razonable de mercado.
    personal_info = dict(PERSONAL_INFO)
    personal_info["expected_salary"] = str(expected_salary or 55000)

    if not url or not url.strip():
        result["detail"] = (
            "URL vacía o inválida recibida — no se puede hacer autofill. "
            "Revisa el nodo 'HTML to Text' en n8n: probablemente el scraping "
            "falló y no se propagó la URL original del trigger."
        )
        return result

    # Lock: serializa el uso de Playwright entre threads concurrentes
    # (ver _PLAYWRIGHT_LOCK arriba) -- evita el fallo silencioso cuando
    # n8n dispara 2 postulaciones casi al mismo tiempo.
    with _PLAYWRIGHT_LOCK:
        with sync_playwright() as p:
            PLAYWRIGHT_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PLAYWRIGHT_USER_DATA_DIR),
                headless=HEADLESS,
                viewport={"width": 1366, "height": 900},
                # SIN esto, el botón "Locate me" (Greenhouse y otros ATS) hace
                # que Chrome intente pedir permiso de geolocalización REAL --
                # como nadie contesta esa ventana, navigator.geolocation
                # nunca llama a su callback y el bot se queda ~3-4s esperando
                # sin escribir nada antes de rendirse y pasar al siguiente
                # campo. Otorgando el permiso de entrada, "Locate me" completa
                # el campo de ciudad al instante, sin pelear con el dropdown
                # de sugerencias de texto.
                permissions=["geolocation"],
                geolocation={"latitude": 52.5200, "longitude": 13.4050},  # Berlin
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                form_engine.close_popups(page)

                # Screenshot de la landing page ANTES de cruzarla, solo como
                # referencia/debug de qué había antes del click en "Apply".
                landing_screenshot = SCREENSHOT_DIR / f"{application_id}_landing_page.png"
                page.screenshot(path=str(landing_screenshot), full_page=True)

                # Cruza la landing page (botón "Apply for this job", reveal
                # in-page, navegación a otra URL, o pestaña nueva) hasta llegar
                # al formulario real. Este es el paso que antes faltaba: el
                # bot se quedaba en la landing page y le hacía screenshot a
                # esa, nunca al formulario.
                nav_result = _navigate_to_application_form(
                    page,
                    context,
                    personal_info=personal_info,
                    company=company,
                    job_title=job_title,
                    application_id=application_id,
                )
                page = nav_result["page"]  # puede ser una pestaña nueva

                if not nav_result["ok"]:
                    result["status"] = nav_result.get("status", "manual_required")
                    result["detail"] = nav_result["reason"]
                    result["screenshot"] = str(landing_screenshot)
                    return result

                agent_info = _advance_through_form_steps(
                    page, personal_info, responses, motivation_answer, experience_answer,
                    adapted_pdf_path, cv_maestro_text, job_context, application_id,
                    company, job_title,
                )

                screenshot_path = SCREENSHOT_DIR / f"{application_id}_before_submit.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                result["screenshot"] = str(screenshot_path)

                # Guardarraíl para el envío automático: si el agente IA tuvo
                # que intervenir en el último paso con confianza baja, NO
                # asumimos que el formulario quedó bien -- lo dejamos en
                # manual_required en vez de mandar una postulación a medio
                # llenar sin que nadie se entere. AGENT_MIN_CONFIDENCE_TO_SUBMIT
                # es configurable por si querés ser más/menos estricto.
                low_confidence = False
                if agent_info.get("agent_intervened") is not None:
                    low_confidence = (
                        agent_info["agent_intervened"]
                        and agent_info["agent_min_confidence"] is not None
                        and agent_info["agent_min_confidence"] < AGENT_MIN_CONFIDENCE_TO_SUBMIT
                    )

                engine_status = agent_info.get("status")

                if engine_status == "submitted":
                    result["status"] = "submitted"
                    result["detail"] = agent_info.get("detail", "Application Submitted confirmado.")
                    result["engine"] = agent_info.get("stats", {})
                elif engine_status == "captcha":
                    result["status"] = "manual_required_captcha"
                    result["detail"] = (
                        "CAPTCHA detectado. No se intenta eludirlo automaticamente; "
                        "requiere intervención humana."
                    )
                    result["engine"] = agent_info.get("stats", {})
                elif engine_status in (
                    "login_required",
                    "account_credentials_missing",
                    "account_ui_unsupported",
                    "account_max_steps",
                    "email_access_required",
                    "email_verification_timeout",
                    "email_verification_failed",
                    "mfa_required",
                ):
                    result["status"] = "manual_required_login"
                    result["detail"] = agent_info.get(
                        "detail",
                        "El portal de cuenta no pudo resolverse automáticamente.",
                    )
                    result["engine"] = agent_info.get("stats", {})
                elif USE_ROBUST_ATS_ENGINE and engine_status not in (None, "ready_for_submit"):
                    # Never click the final submit if the state machine itself
                    # says it stalled/failed. This keeps autonomous submit
                    # deterministic instead of treating "no more progress" as
                    # equivalent to "application complete".
                    result["status"] = "manual_required"
                    result["detail"] = agent_info.get(
                        "detail",
                        f"El motor terminó en estado {engine_status!r} y no confirmó que el formulario esté listo.",
                    )
                    result["engine"] = agent_info.get("stats", {})
                elif SUBMIT_APPLICATIONS and low_confidence:
                    result["status"] = "manual_required"
                    result["detail"] = (
                        "El agente IA tuvo que intervenir en el último paso con "
                        f"confianza baja ({agent_info['agent_min_confidence']:.2f}). "
                        f"Por seguridad NO se envió automáticamente -- revisa "
                        f"{screenshot_path} y postula manualmente."
                    )
                elif SUBMIT_APPLICATIONS and USE_ROBUST_ATS_ENGINE:
                    _human_review_pause(page, application_id)
                    submit_result = form_engine.submit_and_confirm(page, application_id=application_id)
                    after_path = SCREENSHOT_DIR / f"{application_id}_after_submit.png"
                    page.screenshot(path=str(after_path), full_page=True)
                    result["screenshot"] = str(after_path)
                    result["status"] = submit_result["status"]
                    result["detail"] = submit_result["detail"]
                    result["engine"] = submit_result.get("stats", {})
                elif SUBMIT_APPLICATIONS:
                    _human_review_pause(page, application_id)
                    submit_btn = _find_submit_button(page)
                    if submit_btn:
                        submit_btn.click()
                        page.wait_for_timeout(2000)
                        after_path = SCREENSHOT_DIR / f"{application_id}_after_submit.png"
                        page.screenshot(path=str(after_path), full_page=True)
                        result["status"] = "submitted"
                        result["detail"] = f"Click en submit ejecutado. Screenshot: {after_path}"
                    else:
                        result["status"] = "error"
                        result["detail"] = "No encontré botón de submit; revisa el screenshot y ajusta selectores."
                else:
                    result["status"] = "dry_run"
                    result["detail"] = (
                        "SUBMIT_APPLICATIONS=false: formulario rellenado pero NO enviado. "
                        f"Revisa {screenshot_path} y postula manualmente, o activa "
                        "SUBMIT_APPLICATIONS=true para envío automático."
                    )
                if result["status"] == "dry_run" and agent_info.get("stats"):
                    result["engine"] = agent_info["stats"]
            except Exception as e:
                result["status"] = "error"
                result["detail"] = str(e)
                error_path = SCREENSHOT_DIR / f"{application_id}_error.png"
                try:
                    page.screenshot(path=str(error_path), full_page=True)
                    result["screenshot"] = str(error_path)
                except Exception:
                    pass
            finally:
                context.close()

    return result