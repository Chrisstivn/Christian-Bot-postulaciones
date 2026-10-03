"""
Deterministic answer resolution for job application forms.

This module intentionally does NOT call Gemini. It resolves common application
questions from:
- personal_info supplied by the autofill pipeline
- candidate_bible.yaml source-of-truth facts
- fixed safe form conventions (e.g. required demographic field -> decline)

Unknown questions return None so the caller can decide whether an open-text
Gemini fallback is appropriate. Closed-choice fields should never be guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from candidate_bible import CandidateBible, load_candidate_bible


@dataclass
class AnswerResolution:
    value: str
    source: str
    confidence: float = 1.0
    key: str = ""


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def _bible_value(bible: CandidateBible, path: str, default: str = "") -> str:
    value = bible.get_path(path, default)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _personal(personal_info: dict[str, str], key: str, default: str = "") -> str:
    value = personal_info.get(key, default)
    return str(value or default).strip()


def resolve_known_answer(
    question: str,
    personal_info: dict[str, str],
    bible: CandidateBible | None = None,
    cv_text: str = "",
) -> Optional[AnswerResolution]:
    """Resolve a known application answer without AI."""
    bible = bible or load_candidate_bible()
    text = _norm(question)
    if not text:
        return None

    # ------------------------------------------------------------------
    # Personal identity/contact fields
    # ------------------------------------------------------------------
    if _has(text, "first name", "given name", "forename", "vorname"):
        value = _personal(personal_info, "first_name") or _bible_value(bible, "personal.first_name")
        return AnswerResolution(value, "personal_info", 1.0, "first_name") if value else None

    if _has(text, "last name", "family name", "surname", "nachname"):
        value = _personal(personal_info, "last_name") or _bible_value(bible, "personal.last_name")
        return AnswerResolution(value, "personal_info", 1.0, "last_name") if value else None

    if _has(text, "full name", "legal name", "your name") and not _has(text, "company name", "employer name"):
        value = _bible_value(bible, "personal.full_name")
        if not value:
            value = " ".join(
                p for p in (
                    _personal(personal_info, "first_name"),
                    _personal(personal_info, "last_name"),
                )
                if p
            )
        return AnswerResolution(value, "candidate_bible", 0.98, "full_name") if value else None

    if _has(text, "email", "e-mail", "mail address"):
        value = _personal(personal_info, "email") or _bible_value(bible, "personal.email")
        return AnswerResolution(value, "personal_info", 1.0, "email") if value else None

    if _has(text, "phone", "mobile", "telephone", "telefon"):
        value = _personal(personal_info, "phone") or _bible_value(bible, "personal.phone")
        return AnswerResolution(value, "personal_info", 1.0, "phone") if value else None

    if "linkedin" in text:
        value = _personal(personal_info, "linkedin") or _bible_value(bible, "personal.linkedin")
        return AnswerResolution(value, "personal_info", 1.0, "linkedin") if value else None

    # ------------------------------------------------------------------
    # Work authorization / sponsorship / relocation
    # Order matters: "require sponsorship" must NEVER inherit work-auth Yes.
    # ------------------------------------------------------------------
    if _has(
        text,
        "require sponsorship",
        "need sponsorship",
        "visa sponsorship",
        "sponsorship now or in the future",
        "sponsorship in the future",
        "require visa",
        "need a visa",
    ):
        value = _bible_value(
            bible,
            "autofill.default_answers.sponsorship_required",
            "No",
        )
        return AnswerResolution(value, "candidate_bible", 1.0, "sponsorship_required")

    if _has(
        text,
        "authorized to work",
        "authorised to work",
        "eligible to work",
        "legally allowed to work",
        "legally eligible to work",
        "right to work",
        "work authorization",
        "work authorisation",
        "work permit",
        "arbeitserlaubnis",
        "berechtigt zu arbeiten",
    ):
        # Boolean questions should receive Yes. Status-oriented fields can use
        # the richer "Permanent resident" fact.
        if _has(text, "status", "type of", "which authorization", "which authorisation"):
            value = _bible_value(bible, "autofill.default_answers.work_authorization", "Permanent resident")
        else:
            value = "Yes"
        return AnswerResolution(value, "candidate_bible", 1.0, "work_authorization")

    if _has(text, "willing to relocate", "open to relocation", "relocate for", "relocation"):
        value = _bible_value(
            bible,
            "autofill.default_answers.willing_to_relocate",
            "Yes",
        )
        return AnswerResolution(value, "candidate_bible", 0.98, "willing_to_relocate")

    # ------------------------------------------------------------------
    # Location / citizenship / country
    # ------------------------------------------------------------------
    known_location = _norm(
        _personal(personal_info, "location")
        or _bible_value(bible, "personal.location")
    )
    if _has(text, "do you live in", "are you based in", "are you located in", "do you reside in", "currently reside in"):
        places = ("berlin", "germany")
        mentioned = [place for place in places if place in text]
        if mentioned:
            answer = "Yes" if all(place in known_location for place in mentioned) else "No"
            return AnswerResolution(answer, "candidate_bible", 1.0, "location_boolean")

    if _has(text, "citizenship", "nationality", "citizen of"):
        value = _bible_value(bible, "personal.citizenship")
        return AnswerResolution(value, "candidate_bible", 1.0, "citizenship") if value else None

    if _has(text, "country of residence", "country do you live", "country where you live", "residence country"):
        location = _bible_value(bible, "personal.location")
        if "germany" in _norm(location):
            return AnswerResolution("Germany", "candidate_bible", 1.0, "country_of_residence")

    if _has(text, "current location", "where are you based", "city", "location", "wohnort", "standort"):
        value = _personal(personal_info, "location") or _bible_value(bible, "personal.location")
        return AnswerResolution(value, "personal_info", 0.98, "location") if value else None

    if _has(text, "country", "land") and not _has(text, "country code", "phone country"):
        location = _bible_value(bible, "personal.location")
        if "germany" in _norm(location):
            return AnswerResolution("Germany", "candidate_bible", 0.97, "country")

    # ------------------------------------------------------------------
    # Availability / salary / experience
    # ------------------------------------------------------------------
    if _has(text, "notice period", "how soon can you start", "when can you start"):
        value = _personal(personal_info, "available_from") or _bible_value(bible, "personal.availability", "Immediately")
        return AnswerResolution(value, "candidate_bible", 1.0, "availability")

    if _has(text, "available from", "availability", "earliest start", "start date"):
        value = _personal(personal_info, "available_from") or _bible_value(bible, "personal.availability", "Immediately")
        return AnswerResolution(value, "candidate_bible", 0.98, "availability")

    if _has(text, "salary", "compensation", "salary expectation", "expected compensation", "gehalt", "sueldo"):
        value = _personal(personal_info, "expected_salary")
        if not value:
            value = _bible_value(bible, "preferences.minimum_salary_eur")
        return AnswerResolution(value, "personal_info", 0.98, "expected_salary") if value else None

    if _has(text, "years of experience", "years experience", "how many years", "professional experience"):
        # Total career years must not be reused as "years with SQL/CRM/etc.".
        # Specific-tool/domain tenure needs an explicit source; otherwise leave
        # it unresolved instead of overstating experience.
        specific_tenure = bool(
            re.search(r"years\s+(?:of\s+)?experience\s+(?:with|in|using)\s+", text)
            or re.search(r"years\s+of\s+(?!professional\b|work\b|relevant\b|total\b)[a-z0-9+#.& -]{2,35}\s+experience", text)
        )
        if specific_tenure:
            return None

        value = _bible_value(bible, "career.years_experience")
        if value:
            try:
                years = float(re.findall(r"\d+(?:\.\d+)?", value)[0])
            except Exception:
                years = None

            threshold_match = re.search(
                r"(?:at least|minimum of|minimum|over|more than|\b)(\d+(?:\.\d+)?)\s*\+?\s*years",
                text,
            )
            boolean_wording = _has(
                text,
                "do you have",
                "have you got",
                "at least",
                "minimum",
                "more than",
                "or more",
                "+ years",
            )
            if years is not None and threshold_match and boolean_wording:
                threshold = float(threshold_match.group(1))
                return AnswerResolution(
                    "Yes" if years >= threshold else "No",
                    "candidate_bible",
                    1.0,
                    "years_experience_threshold",
                )
            return AnswerResolution(value, "candidate_bible", 0.98, "years_experience")

    if _has(text, "current company", "current employer", "present employer"):
        value = _bible_value(bible, "career.current_company")
        return AnswerResolution(value, "candidate_bible", 0.95, "current_company") if value else None

    if _has(text, "current position", "current job title", "current role", "present title"):
        value = _bible_value(bible, "career.current_position")
        return AnswerResolution(value, "candidate_bible", 0.95, "current_position") if value else None

    # ------------------------------------------------------------------
    # Languages
    # ------------------------------------------------------------------
    if _has(text, "english", "englisch"):
        value = _bible_value(bible, "languages.english.level")
        if value and _has(text, "fluent", "proficient", "professional working proficiency", "business fluent"):
            yes = _norm(value) in ("c1", "c2", "native")
            return AnswerResolution("Yes" if yes else "No", "candidate_bible", 1.0, "english_fluency")
        return AnswerResolution(value, "candidate_bible", 0.99, "english_level") if value else None

    if _has(text, "german", "deutsch"):
        value = _bible_value(bible, "languages.german.level")
        if value and _has(text, "fluent", "proficient", "business fluent"):
            yes = _norm(value) in ("c1", "c2", "native")
            return AnswerResolution("Yes" if yes else "No", "candidate_bible", 1.0, "german_fluency")
        return AnswerResolution(value, "candidate_bible", 0.99, "german_level") if value else None

    if _has(text, "spanish", "espanol", "español"):
        value = _bible_value(bible, "languages.spanish.level")
        if value and _has(text, "fluent", "proficient", "native"):
            yes = _norm(value) in ("c1", "c2", "native")
            return AnswerResolution("Yes" if yes else "No", "candidate_bible", 1.0, "spanish_fluency")
        return AnswerResolution(value, "candidate_bible", 0.99, "spanish_level") if value else None

    # ------------------------------------------------------------------
    # CV fact-presence checks for Boolean capability questions.
    # We only answer Yes when the named fact is explicitly present in the
    # real CV/Bible. Missing text is NOT interpreted as No.
    # ------------------------------------------------------------------
    cv_norm = _norm(cv_text)
    if cv_norm and _has(
        text,
        "experience with",
        "worked with",
        "familiar with",
        "knowledge of",
        "proficiency in",
        "skilled in",
    ):
        patterns = (
            r"(?:experience with|worked with|familiar with|knowledge of|proficiency in|skilled in)\s+([a-z0-9+#. /&-]{2,60})",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            candidate_fact = match.group(1)
            candidate_fact = re.split(
                r"[?.,;]|\b(?:for|and do you|in this role|professionally)\b",
                candidate_fact,
            )[0].strip()
            if len(candidate_fact) >= 2 and candidate_fact in cv_norm:
                return AnswerResolution(
                    "Yes",
                    "cv_fact_presence",
                    0.98,
                    "capability_present",
                )

    if cv_norm and _has(text, "bachelor", "master", "university degree", "college degree", "degree"):
        degree_terms = {
            "bachelor": ("bachelor", "business administration"),
            "master": ("master",),
        }
        for key, terms in degree_terms.items():
            if key in text and any(term in cv_norm for term in terms):
                return AnswerResolution("Yes", "cv_fact_presence", 0.98, f"{key}_degree")

    # ------------------------------------------------------------------
    # Stable application/compliance conventions
    # ------------------------------------------------------------------
    if _has(text, "previously worked", "former employee", "worked for us before", "already worked for"):
        return AnswerResolution("No", "fixed_application_fact", 1.0, "previous_employee")

    if _has(text, "privacy policy", "terms and conditions", "terms of use", "data privacy", "gdpr"):
        return AnswerResolution("Yes", "required_consent", 1.0, "required_consent")

    # Demographic/EEO questions are never inferred.
    if _has(
        text,
        "gender",
        "race",
        "ethnicity",
        "ethnic",
        "disability",
        "veteran",
        "sexual orientation",
        "religion",
        "pronouns",
        "marital status",
    ):
        return AnswerResolution("prefer not to say", "privacy_default", 1.0, "demographic_decline")

    return None


YES_WORDS = {
    "yes", "y", "true", "authorized", "authorised", "eligible", "i agree",
    "agree", "accept", "accepted",
}
NO_WORDS = {"no", "n", "false", "not required", "do not", "don't"}
DECLINE_WORDS = (
    "prefer not to", "decline to", "rather not say", "do not wish",
    "don't wish", "not disclosed", "not specified", "prefer not",
)


def _canonical_bool(value: str) -> str:
    text = _norm(value)
    if text in YES_WORDS or text.startswith("yes "):
        return "yes"
    if text in NO_WORDS or text.startswith("no "):
        return "no"
    return ""


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for raw, suffix in re.findall(r"(\d+(?:[.,]\d+)?)\s*([kK]?)", text):
        normalized = raw
        # 50,000 and 50.000 are common thousands formats in salary options;
        # 6.5 / 6,5 should remain decimal values.
        if re.match(r"^\d{1,3}[.,]\d{3}$", raw):
            normalized = raw.replace(",", "").replace(".", "")
        else:
            normalized = raw.replace(",", ".")
        try:
            number = float(normalized)
        except ValueError:
            continue
        if suffix.lower() == "k":
            number *= 1000
        values.append(number)
    return values


def _numeric_option_score(desired: str, option: str) -> float:
    desired_nums = _numbers(desired)
    option_nums = _numbers(option)
    if not desired_nums or not option_nums:
        return 0.0
    target = desired_nums[0]

    low_text = _norm(option)
    if len(option_nums) >= 2:
        lo, hi = min(option_nums[0], option_nums[1]), max(option_nums[0], option_nums[1])
        if lo <= target <= hi:
            return 0.97
    if "+" in low_text or "or more" in low_text or "and above" in low_text:
        if target >= option_nums[0]:
            return 0.95
    if _has(low_text, "less than", "under", "up to", "below") and target <= option_nums[0]:
        return 0.93
    if abs(target - option_nums[0]) < 0.001:
        return 0.96
    return 0.0


_LANGUAGE_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "c1": ("c1", "advanced", "fluent", "professional working", "full professional"),
    "c2": ("c2", "proficient", "native or bilingual", "full professional"),
    "a2": ("a2", "elementary", "basic", "beginner"),
    "a1": ("a1", "beginner", "basic"),
    "b1": ("b1", "intermediate"),
    "b2": ("b2", "upper intermediate", "professional working"),
    "native": ("native", "native or bilingual", "mother tongue"),
}


def option_match_score(question: str, desired: str, option: str) -> float:
    """Semantic score for a REAL option. No AI and no arbitrary first-option fallback."""
    q = _norm(question)
    d = _norm(desired)
    o = _norm(option)
    if not d or not o:
        return 0.0

    if o == d:
        return 1.0

    if any(word in d for word in DECLINE_WORDS):
        if any(word in o for word in DECLINE_WORDS):
            return 1.0

    d_bool = _canonical_bool(d)
    o_bool = _canonical_bool(o)
    if d_bool and o_bool:
        return 1.0 if d_bool == o_bool else 0.0

    # Rich work-authorization answer can map to either a matching legal status
    # or Yes when the question itself is Boolean.
    if "permanent resident" in d:
        if "permanent resident" in o or "permanent residence" in o:
            return 1.0
        if _has(q, "authorized to work", "eligible to work", "right to work") and o_bool == "yes":
            return 0.97

    if d in _LANGUAGE_EQUIVALENTS:
        for token in _LANGUAGE_EQUIVALENTS[d]:
            if token in o:
                return 0.96

    if d == "immediately":
        if _has(o, "immediately", "as soon as possible", "available now", "no notice", "0 week", "0 month"):
            return 0.98

    numeric_score = _numeric_option_score(d, o)
    if numeric_score:
        return numeric_score

    # Location/country and other concise factual values.
    if d in o or o in d:
        return 0.92

    # Conservative token overlap. Closed-choice selection should require
    # substantial evidence rather than fuzzy character similarity.
    d_tokens = {t for t in re.findall(r"[a-z0-9]+", d) if len(t) > 1}
    o_tokens = {t for t in re.findall(r"[a-z0-9]+", o) if len(t) > 1}
    if d_tokens and o_tokens:
        overlap = len(d_tokens & o_tokens) / max(1, len(d_tokens))
        if overlap >= 0.75:
            return 0.85
        if overlap >= 0.5 and len(d_tokens) >= 2:
            return 0.72

    return 0.0


def best_option_index(
    question: str,
    desired: str,
    option_texts: list[str],
    *,
    sensitive: bool = False,
) -> tuple[Optional[int], float]:
    if sensitive:
        for idx, option in enumerate(option_texts):
            if any(word in _norm(option) for word in DECLINE_WORDS):
                return idx, 1.0

    best_idx: Optional[int] = None
    best_score = 0.0
    for idx, option in enumerate(option_texts):
        score = option_match_score(question, desired, option)
        if score > best_score:
            best_idx = idx
            best_score = score

    # Closed-choice answers need a high threshold. Never choose "first option"
    # merely because the field is required.
    if best_idx is not None and best_score >= 0.62:
        return best_idx, best_score
    return None, best_score
