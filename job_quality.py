"""Optional search filters for Christian's bot.

No eligibility filters are active unless explicitly configured. Legacy
classification helpers remain available for later customization.
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass
from typing import Any, Optional


DATA_ANALYST_BASELINE = {
    "company": "Zalando",
    "title": "Data Analyst E-commerce",
    "salary_eur": 56_000,
    "contract": "PERMANENT",
    "location": "Berlin",
    "remote_status": "HYBRID",
}

BELOW_BASELINE = "BELOW_BASELINE"
BASELINE_EQUIVALENT = "BASELINE_EQUIVALENT"
ABOVE_BASELINE = "ABOVE_BASELINE"
AMBIGUOUS_LEVEL = "AMBIGUOUS"

FULLY_REMOTE = "FULLY_REMOTE"
HYBRID = "HYBRID"
ONSITE = "ONSITE"
UNKNOWN_REMOTE = "UNKNOWN"

PERMANENT = "PERMANENT"
TEMPORARY = "TEMPORARY"
UNKNOWN_CONTRACT = "UNKNOWN"

KEEP = "KEEP"
REJECT = "REJECT"
NEEDS_SEMANTIC = "NEEDS_SEMANTIC"

_STRONG_REMOTE_PATTERNS = (
    r"\b(?:trabajo|modalidad|puesto)\s+(?:en\s+)?remot[oa]\b",
    r"\b100\s*%\s*remot[oa]\b",
    r"\bteletrabajo\b",
    r"\bfully remote\b",
    r"\b100\s*%\s*remote\b",
    r"\bremote[- ]first\b",
    r"\bhome[- ]based\b",
    r"\bwork from anywhere\b",
    r"\bremote within (?:germany|the eu|eu|europe)\b",
    r"\bremote (?:in )?(?:germany|eu|europe)\b",
    r"\b(?:germany|eu|europe)\s*\(remote\)",
    r"\blocation\s*:\s*remote\b",
)

_HYBRID_OR_OFFICE_ATTENDANCE_PATTERNS = (
    r"\bh[ií]brid[oa]\b",
    r"\bhybrid\b",
    r"\boffice[- ]first\b",
    r"\bflexible hybrid\b",
    r"\bhome office available\b",
    r"\boccasional remote\b",
    r"\bremote depending on (?:the )?team\b",
    r"\b\d+\s*(?:remote|home office) days?\b",
    r"\b\d+\s*days? (?:per week )?(?:in|at) (?:the )?office\b",
    r"\b\d+\s*days? (?:per|a) month (?:in|at) (?:the )?office\b",
    r"\bat least \d+\s*days? (?:per|a) month (?:in|at) (?:the )?office\b",
    r"\b(?:weekly|monthly|regular) office attendance\b",
    r"\boffice attendance (?:is )?(?:required|expected|mandatory)\b",
    r"\b(?:required|expected) to (?:work|be|come) (?:in|at) (?:the )?office\b",
    r"\bmust (?:work|be|come) (?:in|at) (?:the )?office\b",
    r"\b(?:attend|visit) (?:the )?office (?:weekly|monthly|regularly)\b",
    r"\bregular office attendance\b",
    r"\bcommuting distance\b.*\boffice\b",
)

_ONSITE_PATTERNS = (
    r"\bpresencial\b",
    r"\bon[- ]site\b",
    r"\bonsite\b",
    r"\bon site\b",
    r"\bwork from (?:the )?office\b",
)

_TEMPORARY_PATTERNS = (
    r"\bmaternity cover\b",
    r"\bparental leave cover\b",
    r"\bparental cover\b",
    r"\bfixed[- ]term\b",
    r"\btemporary\b",
    r"\binterim\b",
    r"\bbefristet\b",
    r"\blimited[- ]term\b",
    r"\b(?:3|6|9|12|18|24)[- ]month contract\b",
    r"\b(?:one|1)[- ]year contract\b",
    r"\b(?:one|1)[- ]year fixed[- ]term\b",
    r"\bcontract until\b",
)

_PERMANENT_PATTERNS = (
    r"\bpermanent\b",
    r"\bunlimited contract\b",
    r"\bindefinite contract\b",
    r"\bunbefristet\b",
    r"\bpermanent employment\b",
)

_BELOW_TITLE_PATTERNS = (
    r"\bjunior\b",
    r"\bentry[- ]level\b",
    r"\bentry level\b",
    r"\bintern(?:ship)?\b",
    r"\bworking[ -]?student\b",
    r"\bwerkstudent(?:in)?\b",
    r"\bstudent\b",
    r"\btrainee\b",
)

_STRONG_ABOVE_TITLE_PATTERNS = (
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\bprincipal\b",
    r"\bstaff\b",
    r"\blead\b",
)

# These role families are explicit examples from the user's new benchmark.
# They are more specific than a generic "Manager" check.
_ABOVE_SCOPE_TITLE_PATTERNS = (
    r"\banalytics manager\b",
    r"\bdata analytics manager\b",
    r"\bproduct analytics manager\b",
    r"\bstrategy(?:\s*&\s*|\s+and\s+)operations manager\b",
    r"\bstrategy manager\b",
    r"\bproduct operations manager\b",
    r"\bgo[- ]to[- ]market product operations manager\b",
    r"\bproduct manager\b",
    r"\bproduct owner\b",
    r"\bgrowth manager\b",
    r"\bperformance marketing manager\b",
    r"\be[- ]?commerce manager\b",
    r"\bcommercial manager\b",
    r"\bbusiness operations manager\b",
    r"\brevenue operations manager\b",
    r"\bcrm manager\b",
    r"\bexperimentation manager\b",
    r"\bcro manager\b",
    r"\bgo[- ]to[- ]market manager\b",
)

_BASELINE_TITLE_PATTERNS = (
    r"\bdata analyst\b",
    r"\bproduct analyst\b",
    r"\bmarketing data analyst\b",
    r"\bmarketing analyst\b",
    r"\bbusiness analyst\b",
    r"\bsales analyst\b",
    r"\bcommercial analyst\b",
    r"\bbi analyst\b",
    r"\bbusiness intelligence analyst\b",
    r"\bdata operations analyst\b",
    r"\breporting analyst\b",
    r"\bgrowth analyst\b",
)

_HIGHER_SCOPE_RESPONSIBILITY_PATTERNS = (
    r"\bmanage(?:s|d|ment)? (?:a |the )?(?:team|analysts|function)\b",
    r"\blead(?:ing|s)? (?:the )?(?:analytics|data|strategy|team|function)\b",
    r"\bown(?:s|ed|ership)? (?:the )?(?:analytics|data|strategy|roadmap|function)\b",
    r"\bbuild(?:ing)? (?:the )?(?:analytics|data) function\b",
    r"\bcompany[- ]wide\b",
    r"\bexecutive stakeholders\b",
    r"\bdefine(?:s|d|ing)? (?:the )?(?:strategy|roadmap)\b",
    r"\bmentor(?:s|ed|ing)? analysts\b",
)


@dataclass(frozen=True)
class UpgradeDecision:
    decision: str
    rejected_reason: Optional[str]
    comparison: dict[str, Any]

    @property
    def keep(self) -> bool:
        return self.decision == KEEP

    @property
    def needs_semantic(self) -> bool:
        return self.decision == NEEDS_SEMANTIC


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def classify_contract(job_title: str, job_text: str) -> str:
    haystack = _norm(f"{job_title}\n{job_text}")
    if _matches_any(haystack, _TEMPORARY_PATTERNS):
        return TEMPORARY
    if _matches_any(haystack, _PERMANENT_PATTERNS):
        return PERMANENT
    return UNKNOWN_CONTRACT


def classify_remote(job_text: str, work_format: str = "Unknown") -> str:
    """Prefer the workplace label extracted from the job page.

    Explicit mandatory office attendance overrides a Remote badge; vague
    benefits or casual mentions of remote work do not.
    """
    text = _norm(job_text)
    normalized = (work_format or "").strip().lower()
    if normalized in ("hybrid", "híbrido", "hibrido", "híbrida", "hibrida"):
        return HYBRID
    if normalized in ("on-site", "onsite", "on site", "presencial"):
        return ONSITE
    if normalized in ("remote", "fully remote", "remoto", "remota"):
        mandatory_office = (
            r"\bmodalidad\s+(?:100\s*%\s*)?presencial\b",
            r"\bpresencial de lunes a viernes\b",
            r"\b\d+\s*days? (?:per|a) week (?:in|at) (?:the )?office\b",
            r"\b\d+\s*days? (?:per|a) month (?:in|at) (?:the )?office\b",
            r"\b(?:weekly|monthly|regular) office attendance\b",
            r"\boffice attendance (?:is )?(?:required|expected|mandatory)\b",
            r"\b(?:required|expected) to (?:work|be|come) (?:in|at) (?:the )?office\b",
            r"\bmust (?:work|be|come) (?:in|at) (?:the )?office\b",
        )
        if re.search(r"\bmodalidad\s+(?:100\s*%\s*)?presencial\b|\bpresencial de lunes a viernes\b", text):
            return ONSITE
        return HYBRID if _matches_any(text, mandatory_office) else FULLY_REMOTE

    # Description-based fallback if LinkedIn did not expose the badge.
    if _matches_any(text, _HYBRID_OR_OFFICE_ATTENDANCE_PATTERNS):
        return HYBRID
    if _matches_any(text, _ONSITE_PATTERNS):
        return ONSITE
    if _matches_any(text, _STRONG_REMOTE_PATTERNS):
        return FULLY_REMOTE
    return UNKNOWN_REMOTE


def classify_title_level(job_title: str, job_text: str = "") -> tuple[str, bool]:
    """Classify *only by title*. No LLM or responsibilities/salary analysis.

    Per the user's search policy, every Manager title is treated as an
    advanced career opportunity; Senior/Principal/Staff Analyst is advanced.
    Ordinary Analyst is equivalent. Unusual titles aren't guessed/rejected.
    """
    title = _norm(job_title)
    if _matches_any(title, _BELOW_TITLE_PATTERNS):
        return BELOW_BASELINE, False
    if re.search(r"\bmanager\b", title):
        return ABOVE_BASELINE, False
    if re.search(r"\banalyst\b", title):
        if re.search(r"\b(?:senior|sr\.?|principal|staff)\b", title):
            return ABOVE_BASELINE, False
        return BASELINE_EQUIVALENT, False
    return AMBIGUOUS_LEVEL, False


def _salary_number(raw: str, suffix_k: bool = False) -> Optional[int]:
    value = raw.replace(".", "").replace(",", "").replace(" ", "")
    if not value.isdigit():
        return None
    number = int(value)
    if suffix_k or number < 1000:
        number *= 1000
    if 40_000 <= number <= 200_000:
        return number
    return None


def extract_explicit_salary_eur(job_text: str) -> Optional[int]:
    """Conservatively extract annual EUR base salary evidence.

    Only salary/compensation-looking lines are considered. For an explicit
    range we use its midpoint as the expected base salary evidence.
    """
    candidates: list[int] = []
    for raw_line in (job_text or "").splitlines():
        line = _norm(raw_line)
        if not line:
            continue
        if not (
            any(k in line for k in ("salary", "base pay", "base salary", "compensation", "gehalt", "annual", "per year", "p.a."))
            and ("€" in raw_line or "eur" in line)
        ):
            continue

        values: list[int] = []
        # €65k / EUR 65,000 / €65.000
        for match in re.finditer(
            r"(?:€|eur)\s*([0-9]{2,3}(?:[.,][0-9]{3})?|[0-9]{5,6})\s*([kK])?",
            raw_line,
            re.IGNORECASE,
        ):
            number = _salary_number(match.group(1), bool(match.group(2)))
            if number is not None:
                values.append(number)

        # 65k EUR / 65,000 EUR
        for match in re.finditer(
            r"([0-9]{2,3}(?:[.,][0-9]{3})?|[0-9]{5,6})\s*([kK])?\s*(?:eur|€)",
            raw_line,
            re.IGNORECASE,
        ):
            number = _salary_number(match.group(1), bool(match.group(2)))
            if number is not None:
                values.append(number)

        values = sorted(set(values))
        if len(values) >= 2:
            candidates.append(round((values[0] + values[-1]) / 2))
        elif len(values) == 1:
            candidates.append(values[0])

    return max(candidates) if candidates else None


def _semantic_value(semantic: Any, name: str, default: Any = None) -> Any:
    if semantic is None:
        return default
    if isinstance(semantic, dict):
        return semantic.get(name, default)
    return getattr(semantic, name, default)


def _comparison(
    *,
    level: str,
    remote_status: str,
    salary_eur: Optional[int],
    salary_source: str,
    contract_type: str,
    overall_upgrade: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "level": level,
        "remote_upgrade": remote_status == FULLY_REMOTE,
        "salary_upgrade": None if salary_eur is None else salary_eur >= 60_000,
        # The accepted baseline is already permanent/unlimited, so another
        # permanent contract meets the baseline but is not itself an upgrade.
        "contract_upgrade": False,
        "contract_meets_baseline": contract_type == PERMANENT,
        "overall_upgrade": overall_upgrade,
        "reason": reason,
        "remote_status": remote_status,
        "contract_type": contract_type,
        "expected_base_salary_eur": salary_eur,
        "salary_source": salary_source,
    }


def evaluate_upgrade(
    *,
    company: str,
    job_title: str,
    job_text: str,
    work_format: str = "Unknown",
    salary_eur: Optional[int] = None,
    semantic: Any = None,
) -> UpgradeDecision:
    """Apply only explicit optional filters, with no personal career baseline.

    Empty environment variables mean unrestricted searches. Salary and language
    are not evaluated. The signature remains compatible with the pipeline.
    """
    title_pattern = os.environ.get("SEARCH_EXCLUDED_TITLE_REGEX", "").strip()
    allowed_formats = {
        value.strip().upper()
        for value in os.environ.get("SEARCH_ALLOWED_WORK_FORMATS", "").split(",")
        if value.strip()
    }
    excluded_contracts = {
        value.strip().upper()
        for value in os.environ.get("SEARCH_EXCLUDED_CONTRACT_TYPES", "").split(",")
        if value.strip()
    }
    remote_status = classify_remote(job_text, work_format)
    contract_type = classify_contract(job_title, job_text)
    rejected_reason = None
    if title_pattern and re.search(title_pattern, job_title or "", re.IGNORECASE):
        rejected_reason = "configured_title_exclusion"
    elif allowed_formats and remote_status not in allowed_formats:
        rejected_reason = "configured_work_format_exclusion"
    elif excluded_contracts and contract_type in excluded_contracts:
        rejected_reason = "configured_contract_exclusion"

    configured = bool(title_pattern or allowed_formats or excluded_contracts)
    reason = (
        rejected_reason or
        ("Passed configured search filters." if configured else
         "No search restrictions configured.")
    )
    return UpgradeDecision(
        decision=REJECT if rejected_reason else KEEP,
        rejected_reason=rejected_reason,
        comparison={
            "level": "NOT_EVALUATED",
            "filter_policy": "configured_filters" if configured else "unrestricted",
            "reason": reason,
            "remote_status": remote_status,
            "contract_type": contract_type,
            "expected_base_salary_eur": None,
            "salary_source": "not_evaluated",
        },
    )


def evaluate_search_filters(**kwargs):
    """Apply the current policy; unrestricted mode is an explicit legacy opt-out."""
    evidence = kwargs.pop("evidence", None)
    if os.environ.get("SEARCH_POLICY", "christian").strip().lower() == "unrestricted":
        return evaluate_upgrade(**kwargs)
    from search_filters import evaluate
    decision = evaluate(**kwargs, evidence=evidence)
    # Preserve additional exclusions only when explicitly configured.
    optional = evaluate_upgrade(**kwargs)
    if not optional.keep:
        from search_filters import SearchDecision, REJECT
        return SearchDecision(REJECT, [optional.rejected_reason], decision.facts)
    return decision
