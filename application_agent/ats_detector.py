"""ATS detection from URL, HTML, scripts and meta tags."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Any


@dataclass(frozen=True)
class ATSDetection:
    ats: str
    confidence: float
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"ats": self.ats, "confidence": self.confidence, "reasons": self.reasons}


ATS_SIGNATURES: dict[str, tuple[str, ...]] = {
    # gh_jid / gh_src cover company-owned career pages that embed Greenhouse
    # without using a greenhouse.io hostname (e.g. lucanet.com careers).
    "greenhouse": (
        "greenhouse.io", "boards.greenhouse", "grnh.se",
        "greenhouse-job-board", "gh_jid=", "gh_src=",
    ),
    "lever": ("jobs.lever.co", "lever.co", "leverTRM", "lever-job"),
    "workday": ("myworkdayjobs.com", "workday", "wd-careers", "workdayjobs"),
    "ashby": ("ashbyhq.com", "jobs.ashbyhq", "ashby_embed", "ashby-job-posting"),
    "smartrecruiters": ("smartrecruiters.com", "smartrecruiters", "sr-careers"),
    "icims": ("icims.com", "iCIMS", "icims2", "platform.icims"),
    "personio": ("jobs.personio.de", "jobs.personio.com", "personio.de", "personio.com"),
    "successfactors": (
        "successfactors.com", "sapsf.eu", "sapsf.com",
        "sfcareer", "career-site-builder",
    ),
    "teamtailor": (
        "teamtailor.com", "teamtailor-cdn", "teamtailor",
        "tt-job-application", "data-teamtailor",
    ),
    "join": ("join.com", "join-com", "join application"),
    "hibob": ("careers.hibob.com", "hibob.com", "bob-careers", "hibob"),
    "amazonjobs": ("amazon.jobs", "account.amazon.jobs", "jobs.amazon"),
    "linkedin": (
        "linkedin.com/jobs", "linkedin-easy-apply",
        "jobs-apply-button",
    ),
}


def detect_from_text(url: str = "", html: str = "") -> ATSDetection:
    haystack = f"{url}\n{html}".lower()
    parsed = urlparse(url or "")
    domain = parsed.netloc.lower()
    best_ats = "unknown"
    best_score = 0.0
    best_reasons: list[str] = []

    for ats, signatures in ATS_SIGNATURES.items():
        score = 0.0
        reasons: list[str] = []
        for signature in signatures:
            sig = signature.lower()
            if sig in domain:
                score += 0.55
                reasons.append(f"domain:{signature}")
            elif sig in haystack:
                score += 0.25
                reasons.append(f"html:{signature}")
        if re.search(rf"data-[\w-]*{ats}|class=['\"][^'\"]*{ats}", haystack):
            score += 0.15
            reasons.append(f"dom-marker:{ats}")
        score = min(score, 0.99)
        if score > best_score:
            best_ats = ats
            best_score = score
            best_reasons = reasons

    if best_score < 0.25:
        return ATSDetection("unknown", 0.0, [])
    return ATSDetection(best_ats, round(best_score, 2), best_reasons)


def detect_page(page) -> ATSDetection:
    try:
        html = page.content()
    except Exception:
        html = ""
    try:
        url = page.url
    except Exception:
        url = ""
    return detect_from_text(url=url, html=html)
