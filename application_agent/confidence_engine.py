"""Weighted field mapping confidence engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
import re

from .config import CONFIG


@dataclass
class ConfidenceDecision:
    field_type: str
    confidence: float
    action: str
    reason: str
    original_field: str
    answer_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_type": self.field_type,
            "confidence": self.confidence,
            "action": self.action,
            "reason": self.reason,
            "original_field": self.original_field,
            "answer_key": self.answer_key,
            "metadata": self.metadata,
        }


FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "first_name": ("first name", "given name", "forename", "nombre", "vorname"),
    "last_name": ("last name", "surname", "family name", "apellido", "nachname"),
    "email": ("email", "e-mail", "mail address", "corporate email", "correo"),
    "phone": ("phone", "mobile", "telephone", "tel", "telefon"),
    "linkedin": ("linkedin", "linked in"),
    "resume": ("resume", "cv", "curriculum", "upload", "attach file"),
    "location": ("location", "city", "address", "standort", "wohnort"),
    "salary": ("salary", "compensation", "expected salary", "gehalt", "sueldo"),
    "availability": ("available", "availability", "start date", "earliest start"),
    "cover_letter": ("cover letter", "motivation", "why do you want", "communication", "message"),
    "experience": ("experience", "relevant experience", "background"),
    "achievement": ("achievement", "biggest achievement", "accomplishment"),
    "behavioral": ("challenge", "conflict", "failure", "tell us about a time", "star"),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _keyword_score(text: str, field_type: str) -> tuple[float, str]:
    normalized = _norm(text)
    best = 0.0
    best_keyword = ""
    for keyword in FIELD_KEYWORDS.get(field_type, ()):
        if keyword in normalized:
            score = 1.0 if normalized == keyword else 0.88
        else:
            score = _ratio(normalized, keyword)
        if score > best:
            best = score
            best_keyword = keyword
    return best, best_keyword


def _action_for(confidence: float) -> str:
    thresholds = CONFIG["confidence"]
    if confidence >= float(thresholds["auto_fill_threshold"]):
        return "auto_fill"
    if confidence >= float(thresholds["fill_log_threshold"]):
        return "fill_log"
    return "skip_review"


def score_field(candidate: Any, ats: str = "unknown", memory_hit: bool = False, plugin_hint: str = "") -> ConfidenceDecision:
    original = getattr(candidate, "match_text", "") or getattr(candidate, "label", "") or getattr(candidate, "name", "")
    detected_type = getattr(candidate, "field_type", "text")
    candidates = set(FIELD_KEYWORDS) | {detected_type}
    best_type = detected_type
    best_score = 0.35 if detected_type in ("text", "textarea") else 0.55
    best_reason = f"native_type:{detected_type}"

    for field_type in candidates:
        score, keyword = _keyword_score(original, field_type)
        if score > best_score:
            best_type = field_type
            best_score = score
            best_reason = f"keyword:{keyword}"

    source_bonus = 0.0
    if getattr(candidate, "aria_label", ""):
        source_bonus += 0.05
    if getattr(candidate, "label", ""):
        source_bonus += 0.04
    if getattr(candidate, "name", ""):
        source_bonus += 0.03
    if memory_hit:
        source_bonus += 0.10
        best_reason += "|memory"
    if plugin_hint:
        source_bonus += 0.08
        best_reason += f"|plugin:{plugin_hint}"
    if ats != "unknown":
        source_bonus += 0.02

    confidence = min(0.99, best_score + source_bonus)
    return ConfidenceDecision(
        field_type=best_type,
        confidence=round(confidence, 2),
        action=_action_for(confidence),
        reason=best_reason,
        original_field=original,
        answer_key=best_type,
        metadata={"ats": ats, "native_type": detected_type, "memory_hit": memory_hit},
    )


def classify_open_question(question: str) -> str:
    text = _norm(question)
    if any(k in text for k in ("why do you want", "motivation", "why this role", "why join")):
        return "motivation"
    if any(k in text for k in ("achievement", "accomplishment", "proud")):
        return "achievement"
    if any(k in text for k in ("challenge", "conflict", "failure", "difficult", "tell us about a time")):
        return "star_behavioral"
    if any(k in text for k in ("experience", "background", "worked with")):
        return "experience"
    return "general"
