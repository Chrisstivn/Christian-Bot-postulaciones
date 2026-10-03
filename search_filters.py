"""Christian's LinkedIn policy. Unknown facts require review, never invented facts."""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

KEEP = 'KEEP'
REJECT = 'REJECT'
REVIEW = 'REVIEW'


def norm(text: str) -> str:
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', text).strip().lower()


@dataclass(frozen=True)
class SearchDecision:
    decision: str
    reasons: list[str]
    facts: dict[str, Any]

    @property
    def keep(self):
        return self.decision == KEEP

    @property
    def rejected_reason(self):
        return self.reasons[0] if self.decision == REJECT else None

    @property
    def comparison(self):
        return {'filter_policy': 'christian_linkedin', 'decision': self.decision,
                'reason': ', '.join(self.reasons) or 'passed_search_filters',
                'reasons': self.reasons, 'filter_facts': self.facts,
                'remote_status': self.facts.get('remote_status', 'UNKNOWN')}


def _evidence_value(evidence, field, source, unknown='UNKNOWN'):
    item = evidence.get(field, {})
    quote = item.get('evidence', '')
    if not quote or norm(quote) not in norm(source):
        return unknown
    return item.get('value', unknown)


def required_years(quote: str):
    """Recount cited numeric requirements instead of trusting an LLM's number."""
    clauses = re.split(r";|\n|(?<=[.!?])\s+", quote.strip())
    if len(clauses) > 1:
        return max((value for clause in clauses if (value := required_years(clause)) is not None), default=None)
    text = norm(quote)
    if re.search(r"\b(?:deseable|ideal|preferible|preferred|preferably|nice to have|valorara)\b", text):
        return None
    words = {"uno": "1", "un": "1", "dos": "2", "tres": "3", "cuatro": "4",
             "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
             "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
             "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
    for word, value in words.items():
        text = re.sub(r"\b" + word + r"\b", value, text)
    matches = re.finditer(
        r"(?:(mas de|more than|over)\s+)?(\d+(?:[.,]\d+)?)\s*"
        r"(?:(?:[-–—]|a|to)\s*\d+(?:[.,]\d+)?\s*)?\+?\s*(?:anos?|years?)\b", text)
    values = [(float(m.group(2).replace(',', '.')), bool(m.group(1))) for m in matches]
    return max(values, default=None)


def evaluate(*, company: str, job_title: str, job_text: str,
             work_format: str = 'Unknown', evidence=None) -> SearchDecision:
    from job_quality import classify_remote
    raw = evidence.model_dump() if hasattr(evidence, 'model_dump') else evidence
    raw = raw if isinstance(raw, dict) else {}
    title = norm(job_title)
    rejected, unknown = [], []
    if re.search(r'\b(?:practica(?:s|ntes?)?|pasantia|pasante|intern(?:ship)?|becari[oa])\b', title):
        rejected.append('internship')
    if re.search(r'\b(?:director(?:a)?|gerente(?:s)?|subgerente|gerencia|vice[ -]?president[ea]?|vp|svp|evp|general manager|managing director)\b|\bv\.?\s*p\.(?:\s|$)', title):
        rejected.append('excluded_executive_title')

    mining = _evidence_value(raw, 'mining', job_text)
    size = _evidence_value(raw, 'company_size', job_text)
    multinational = _evidence_value(raw, 'multinational', job_text)
    internship = _evidence_value(raw, 'internship', job_text)
    remote = classify_remote(job_text, work_format)
    if internship == 'YES' and 'internship' not in rejected:
        rejected.append('internship')
    if size == 'SMALL':
        rejected.append('small_company')
    elif size != 'MEDIUM_OR_LARGE':
        unknown.append('company_size_unknown')

    # Mining exceptions affect only workplace and international operations.
    # A small mining employer or a mining internship is still rejected.
    if mining != 'YES':
        if multinational == 'NO':
            if mining == 'NO':
                rejected.append('not_multinational')
            else:
                unknown.append('mining_exception_unconfirmed')
        elif multinational != 'YES':
            unknown.append('multinational_unknown')
        if remote == 'ONSITE':
            if mining == 'NO':
                rejected.append('onsite_non_mining')
            else:
                unknown.append('mining_exception_unconfirmed')
        elif remote not in ('HYBRID', 'FULLY_REMOTE'):
            unknown.append('work_format_unknown')

    exp = raw.get('experience', {})
    quote = exp.get('evidence', '')
    years = None
    if quote and norm(quote) in norm(job_text):
        parsed = required_years(quote)
        if parsed:
            years, strict = parsed
            if years > 4 or (years == 4 and strict):
                rejected.append('requires_more_than_four_years')
        elif exp.get('minimum_years') is not None and not re.search(
                r'\b(?:deseable|ideal|preferible|preferred|preferably|nice to have)\b', norm(quote)):
            unknown.append('experience_evidence_unverified')
    elif exp.get('minimum_years') is not None:
        unknown.append('experience_evidence_unverified')

    reasons = list(dict.fromkeys(rejected if rejected else unknown))
    return SearchDecision(REJECT if rejected else REVIEW if unknown else KEEP, reasons,
        {'company': company, 'company_size': size, 'multinational': multinational,
         'mining': mining, 'remote_status': remote,
         'minimum_required_years': years if quote and norm(quote) in norm(job_text) else None,
         'evidence': raw})
