"""
application_agent/vision.py
Arma el payload compacto (screenshot + snapshot DOM filtrado) y llama al
mismo cliente de Gemini que ya usa gemini_service.py (misma API key,
mismo modelo) -- no agrega una dependencia/credencial nueva.

Filosofía: esto es un FALLBACK. Si algo falla acá (parseo, timeout,
modelo devuelve basura), NUNCA debe tirar abajo form_filler.py -- siempre
devolvemos un AgentDecision seguro (action="none") y dejamos que
form_filler siga su camino normal (screenshot + manual_required).
"""

from __future__ import annotations
import json
import logging

from google.genai import types

from gemini_service import _client, MODEL_NAME  # mismo cliente/credencial ya configurados
from models import AgentDecision

log = logging.getLogger("application_agent")

AGENT_SYSTEM_PROMPT = """
Eres un agente que observa UN paso de un formulario de postulación laboral
y decide la siguiente acción. Recibes:
  1. Un screenshot del viewport actual.
  2. Una lista JSON de elementos interactivos visibles (tag, type,
     selector, label, value actual, si es requerido, si está en estado
     de error).
  3. Contexto: la oferta laboral y un resumen del candidato.

Devuelve JSON estricto, sin texto adicional ni markdown, con este esquema
EXACTO:

{
  "page_state": "application_form" | "login_required" | "captcha" | "confirmation" | "unknown",
  "confidence": float entre 0 y 1,
  "action": "fill_fields" | "click_next" | "wait" | "none",
  "fields": [
    { "name": string, "selector": string, "value": string, "field_kind": "text"|"select"|"checkbox"|"radio"|"file" }
  ],
  "next_button_selector": string o null,
  "reasoning": string breve (1 frase)
}

REGLAS:
- SOLO usa selectores que aparezcan EXACTAMENTE en la lista que te dimos.
  Nunca inventes un selector nuevo.
- ERES UN AGENTE DE INTERACCIÓN DE UI, NO DE RESPUESTAS. Está prohibido
  inferir, redactar o elegir información del candidato.
- NUNCA inventes Yes/No, salary, sponsorship, work authorization, idioma,
  experiencia, ubicación, demographic data ni ninguna respuesta.
- Solo puedes usar "fill_fields" si el DOM_SNAPSHOT YA contiene un "value"
  no vacío para ESE MISMO elemento y estás re-aplicando exactamente ese
  mismo valor para reparar una interacción/validación (por ejemplo, un
  autocomplete que tiene "Berlin, Germany" escrito pero no confirmado).
- Si un campo required está vacío, NO lo respondas. Déjalo para el motor
  determinístico o revisión manual.
- Puedes hacer "click_next" cuando el formulario ya está completo y el botón
  NO es un submit final.
- Si detectas un muro de login/crear cuenta -> "page_state":
  "login_required", "action": "none". La capa account_access lo maneja.
- Si detectas un captcha -> "page_state": "captcha", "action": "none".
- Si detectas una pantalla de confirmación/éxito -> "page_state":
  "confirmation", "action": "none".
- NUNCA devuelvas un selector que corresponda a un botón de "Submit" /
  "Enviar" / "Postular" / "Send application" final. Este agente jamás
  envía la postulación.
- Si no hay una acción de UI claramente segura, "action": "none".
"""


def _safe_fallback(reason: str) -> AgentDecision:
    return AgentDecision(
        page_state="unknown", confidence=0.0, action="none", reasoning=reason
    )


def analyze_step(
    screenshot_path: str,
    dom_snapshot: list[dict],
    job_context: str,
    cv_summary: str,
) -> AgentDecision:
    try:
        with open(screenshot_path, "rb") as f:
            image_bytes = f.read()

        user_content = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            (
                f"DOM_SNAPSHOT:\n{json.dumps(dom_snapshot, ensure_ascii=False)}\n\n"
                "Use ONLY the values already present in DOM_SNAPSHOT. "
                "Do not derive candidate answers from external context."
            ),
        ]

        response = _client.models.generate_content(
            model=MODEL_NAME,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=AGENT_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        raw = json.loads(response.text)
        return AgentDecision.model_validate(raw)
    except Exception as e:
        log.warning("application_agent.vision falló, fallback seguro: %s", e)
        return _safe_fallback(f"vision_error: {e}")
