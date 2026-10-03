"""
Generic account/login/email-verification layer for job application sites.

Purpose:
- Reuse an authenticated browser session when one already exists.
- Create/login to candidate accounts when the ATS requires it.
- Resolve ordinary email verification codes / magic links via IMAP.
- Hand control back to the existing form engine once access is granted.

Explicit non-goals:
- Do not bypass CAPTCHA challenges.
- Do not bypass SMS/authenticator/security-key MFA.
- Do not click a final job-application Submit button.
"""

from __future__ import annotations

import base64
import email
import hashlib
import hmac
import imaplib
import json
import logging
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

log = logging.getLogger("application_account_access")

_SECRET_PATH = Path(os.environ.get("APPLICATION_ACCOUNT_SECRET_PATH", "work/account_master_secret"))
_MAX_ACCOUNT_STEPS = int(os.environ.get("APPLICATION_ACCOUNT_MAX_STEPS", "8"))
_EMAIL_WAIT_SECONDS = int(os.environ.get("APPLICATION_EMAIL_WAIT_SECONDS", "90"))
_EMAIL_POLL_SECONDS = max(2, int(os.environ.get("APPLICATION_EMAIL_POLL_SECONDS", "5")))

_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='recaptcha/enterprise/bframe']",
    "iframe[src*='arkoselabs']",
    "[data-testid*='captcha-challenge']",
    "[data-testid*='turnstile-challenge']",
)
_CAPTCHA_TEXT = (
    "verify you are human",
    "complete the captcha",
    "solve the captcha",
    "security check",
    "checking if the site connection is secure",
)

_EMAIL_VERIFY_TEXT = (
    "verify your email",
    "verify email",
    "verification code",
    "enter the code",
    "enter code",
    "we sent a code",
    "check your inbox",
    "check your email",
    "one time code",
    "one-time code",
    "email confirmation",
    "confirm your email",
)

_MFA_TEXT = (
    "authenticator app",
    "security key",
    "text message code",
    "sms code",
    "two factor authentication",
    "two-factor authentication",
    "2fa",
    "verification code sent to your phone",
)

_SIGNUP_TEXT = (
    "create account",
    "create an account",
    "create your account",
    "sign up",
    "register",
    "create profile",
    "create your profile",
    "konto erstellen",
    "registrieren",
)

_LOGIN_TEXT = (
    "sign in",
    "log in",
    "login",
    "already have an account",
    "anmelden",
)

_ACCOUNT_BUTTON_TEXT = (
    "Create account",
    "Create an account",
    "Sign up",
    "Register",
    "Create profile",
    "Continue",
    "Next",
    "Sign in",
    "Log in",
    "Login",
    "Verify",
    "Confirm",
)

_CREATE_BUTTON_TEXT = (
    "Create account",
    "Create an account",
    "Sign up",
    "Register",
    "Create profile",
    "Create your profile",
)

_LOGIN_BUTTON_TEXT = ("Sign in", "Log in", "Login")

_VERIFY_BUTTON_TEXT = (
    "Verify",
    "Confirm",
    "Continue",
    "Next",
    "Verify email",
    "Confirm email",
)

_ACCOUNT_FAILURE_MARKERS = (
    "invalid password",
    "incorrect password",
    "wrong password",
    "account not found",
    "no account",
    "does not exist",
    "email or password is incorrect",
)

_ACCOUNT_EXISTS_MARKERS = (
    "already exists",
    "already registered",
    "email is already in use",
    "account with this email",
)


@dataclass
class AccountAccessResult:
    ok: bool
    status: str
    detail: str
    page: Page

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "page": self.page,
        }


def _body_text(page: Page) -> str:
    try:
        return re.sub(r"\s+", " ", page.inner_text("body", timeout=2000) or "").strip().lower()
    except Exception:
        return ""


def _visible(page: Page, selector: str, timeout: int = 300) -> bool:
    try:
        return page.locator(selector).first.is_visible(timeout=timeout)
    except Exception:
        return False


def _has_visible_password(page: Page) -> bool:
    return _visible(page, "input[type='password']", timeout=500)


def _visible_password_count(page: Page) -> int:
    try:
        return sum(
            1
            for el in page.query_selector_all("input[type='password']")
            if el.is_visible()
        )
    except Exception:
        return 0


def _has_name_fields(page: Page) -> bool:
    selectors = (
        "input[name*='first' i]",
        "input[id*='first' i]",
        "input[autocomplete='given-name']",
        "input[name*='last' i]",
        "input[id*='last' i]",
        "input[autocomplete='family-name']",
    )
    return any(_visible(page, selector) for selector in selectors)


def _has_application_signals(page: Page) -> bool:
    """Avoid classifying ordinary application pages as account/captcha walls."""
    try:
        if any(
            el.is_visible()
            for el in page.query_selector_all("input[type='file'], textarea")
        ):
            return True
        controls = [
            el for el in page.query_selector_all(
                "form input, form select, form [role='combobox'], "
                "[role='dialog'] input, [role='dialog'] select, "
                "[role='dialog'] [role='combobox']"
            )
            if el.is_visible()
        ]
        return len(controls) >= 3
    except Exception:
        return False


def _active_captcha(page: Page) -> bool:
    if any(_visible(page, selector, timeout=350) for selector in _CAPTCHA_SELECTORS):
        return True
    body = _body_text(page)
    return (
        any(marker in body for marker in _CAPTCHA_TEXT)
        and not _has_application_signals(page)
    )


def detect_account_state(page: Page) -> Optional[str]:
    """Return captcha/email_verification/mfa/signup/login or None."""
    if _active_captcha(page):
        return "captcha"

    body = _body_text(page)
    if any(marker in body for marker in _MFA_TEXT):
        # Email OTP is handled separately; do not misclassify it as MFA.
        if not any(marker in body for marker in _EMAIL_VERIFY_TEXT):
            return "mfa"

    if any(marker in body for marker in _EMAIL_VERIFY_TEXT):
        return "email_verification"

    password_count = _visible_password_count(page)
    if password_count >= 2 or (_has_name_fields(page) and password_count >= 1):
        return "signup"

    if _has_visible_password(page):
        return "login"

    # Copy such as "Already have an account? Sign in" often appears next
    # to a perfectly usable application form. Only treat copy-only login/signup
    # as a gate when the page does not already expose application-form signals.
    if _has_application_signals(page):
        return None

    has_signup_copy = any(marker in body for marker in _SIGNUP_TEXT)
    has_login_copy = any(marker in body for marker in _LOGIN_TEXT)
    if has_signup_copy and not has_login_copy:
        return "signup"
    if has_login_copy:
        return "login"
    return None


def _decode_header_value(value: str) -> str:
    parts = []
    for chunk, charset in decode_header(value or ""):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(charset or "utf-8", errors="replace"))
            except Exception:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


def _message_text(msg: Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue
            if part.get_content_disposition() == "attachment":
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                chunks.append(payload.decode(charset, errors="replace"))
            except Exception:
                chunks.append(payload.decode("utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                chunks.append(payload.decode(charset, errors="replace"))
            except Exception:
                chunks.append(payload.decode("utf-8", errors="replace"))
    text = "\n".join(chunks)
    # Preserve verification URLs carried only in HTML href attributes before
    # stripping markup.
    hrefs = re.findall(
        r"""href\s*=\s*["'](https?://[^"']+)["']""",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    if hrefs:
        text += " " + " ".join(hrefs)
    return re.sub(r"\s+", " ", text).strip()


def _message_timestamp(msg: Message) -> float:
    raw = msg.get("Date", "")
    if not raw:
        return 0.0
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _host_tokens(page: Page, company: str = "") -> set[str]:
    host = (urlparse(page.url).hostname or "").lower()
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", host)
        if len(token) >= 4 and token not in {"www", "jobs", "careers", "apply", "career"}
    }
    for token in re.split(r"[^a-z0-9]+", (company or "").lower()):
        if len(token) >= 4:
            tokens.add(token)
    return tokens


def _verification_score(subject: str, sender: str, body: str, tokens: set[str]) -> int:
    haystack = f"{subject} {sender} {body[:1200]}".lower()
    score = 0
    for marker in (
        "verification",
        "verify",
        "confirm",
        "activation",
        "activate",
        "one-time",
        "one time",
        "code",
        "candidate account",
        "application account",
    ):
        if marker in haystack:
            score += 2
    score += sum(2 for token in tokens if token in haystack)
    return score


def _extract_verification_code(text: str) -> str:
    patterns = (
        r"(?:verification|verify|security|one[- ]time|otp|confirmation)\s*(?:code)?\s*[:\-]?\s*([0-9]{4,8})",
        r"\bcode\s*[:\-]?\s*([0-9]{4,8})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _extract_verification_link(text: str) -> str:
    urls = re.findall(r"https?://[^\s<>\"']+", text)
    preferred = (
        "verify",
        "verification",
        "confirm",
        "activate",
        "activation",
        "magic",
        "token",
        "email",
        "candidate",
        "account",
    )
    for url in urls:
        clean = url.rstrip(").,;")
        if any(token in clean.lower() for token in preferred):
            return clean
    return ""


def _imap_credentials(applicant_email: str) -> tuple[str, str, str]:
    host = os.environ.get("APPLICATION_EMAIL_IMAP_HOST", "imap.gmail.com").strip()
    user = os.environ.get("APPLICATION_EMAIL_IMAP_USER", applicant_email).strip()
    password = os.environ.get("APPLICATION_EMAIL_IMAP_APP_PASSWORD", "").strip()

    # Persistent local secret option. work/ is already gitignored, so the
    # mailbox credential never needs to be committed to the repository.
    if not password:
        password_file = Path(
            os.environ.get(
                "APPLICATION_EMAIL_IMAP_APP_PASSWORD_FILE",
                "work/gmail_imap_app_password",
            )
        )
        try:
            if password_file.exists():
                password = password_file.read_text(encoding="utf-8").strip()
        except Exception:
            password = ""

    return host, user, password


def _poll_verification_email(
    page: Page,
    applicant_email: str,
    company: str,
    not_before: float,
) -> tuple[str, str]:
    """Return (kind, value), where kind is code/link/empty."""
    host, user, password = _imap_credentials(applicant_email)
    if not password:
        return "", ""

    tokens = _host_tokens(page, company)
    deadline = time.time() + _EMAIL_WAIT_SECONDS

    while time.time() < deadline:
        try:
            with imaplib.IMAP4_SSL(host) as mailbox:
                mailbox.login(user, password)
                mailbox.select("INBOX", readonly=True)

                since = datetime.fromtimestamp(max(0, not_before - 3600)).strftime("%d-%b-%Y")
                status, data = mailbox.search(None, "SINCE", since)
                if status != "OK" or not data:
                    time.sleep(_EMAIL_POLL_SECONDS)
                    continue

                ids = data[0].split()[-30:]
                candidates: list[tuple[int, float, Message, str]] = []

                for msg_id in reversed(ids):
                    status, raw_parts = mailbox.fetch(msg_id, "(RFC822)")
                    if status != "OK" or not raw_parts:
                        continue
                    raw = next(
                        (part[1] for part in raw_parts if isinstance(part, tuple) and len(part) > 1),
                        None,
                    )
                    if not raw:
                        continue
                    msg = email.message_from_bytes(raw)
                    ts = _message_timestamp(msg)
                    if ts and ts < not_before - 120:
                        continue
                    subject = _decode_header_value(msg.get("Subject", ""))
                    sender = _decode_header_value(msg.get("From", ""))
                    body = _message_text(msg)
                    score = _verification_score(subject, sender, body, tokens)
                    if score >= 4:
                        candidates.append((score, ts, msg, body))

                candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                for _, _, msg, body in candidates:
                    subject = _decode_header_value(msg.get("Subject", ""))
                    sender = _decode_header_value(msg.get("From", ""))
                    combined = f"{subject}\n{sender}\n{body}"
                    code = _extract_verification_code(combined)
                    if code:
                        return "code", code
                    link = _extract_verification_link(combined)
                    if link:
                        return "link", link
        except Exception as exc:
            log.warning("No se pudo consultar el correo de verificacion: %s", exc)

        time.sleep(_EMAIL_POLL_SECONDS)

    return "", ""


def _set_input(page: Page, selectors: tuple[str, ...], value: str) -> bool:
    if not value:
        return False
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=350):
                locator.fill(value)
                return True
        except Exception:
            continue
    return False


def _fill_email(page: Page, value: str) -> bool:
    if not value:
        return False
    selector = (
        "input[type='email'], input[name*='email' i], input[id*='email' i], "
        "input[autocomplete='email'], input[name*='username' i], "
        "input[id*='username' i]"
    )
    filled = False
    try:
        elements = page.query_selector_all(selector)
    except Exception:
        elements = []

    for el in elements:
        try:
            if not el.is_visible():
                continue
            if el.get_attribute("disabled") is not None or el.get_attribute("readonly") is not None:
                continue
            current = (el.input_value() or "").strip()
            if current and current.lower() == value.lower():
                filled = True
                continue
            el.fill(value)
            filled = True
        except Exception:
            continue
    return filled


def _fill_passwords(page: Page, password: str) -> int:
    if not password:
        return 0
    filled = 0
    try:
        inputs = [el for el in page.query_selector_all("input[type='password']") if el.is_visible()]
    except Exception:
        inputs = []
    for el in inputs[:3]:
        try:
            el.fill(password)
            filled += 1
        except Exception:
            continue
    return filled


def _fill_name_fields(page: Page, first_name: str, last_name: str) -> None:
    _set_input(
        page,
        (
            "input[autocomplete='given-name']",
            "input[name*='first_name' i]",
            "input[name*='firstname' i]",
            "input[id*='first_name' i]",
            "input[id*='firstname' i]",
            "input[placeholder*='First name' i]",
        ),
        first_name,
    )
    _set_input(
        page,
        (
            "input[autocomplete='family-name']",
            "input[name*='last_name' i]",
            "input[name*='lastname' i]",
            "input[id*='last_name' i]",
            "input[id*='lastname' i]",
            "input[placeholder*='Last name' i]",
        ),
        last_name,
    )



def _fill_account_profile_fields(page: Page, personal_info: dict[str, str]) -> None:
    """Fill safe profile fields sometimes requested during account creation."""
    phone = str(personal_info.get("phone", "") or "").strip()
    location = str(personal_info.get("location", "") or "").strip()

    _set_input(
        page,
        (
            "input[type='tel']",
            "input[name*='phone' i]",
            "input[id*='phone' i]",
            "input[name*='mobile' i]",
            "input[id*='mobile' i]",
        ),
        phone,
    )

    # Only simple text city/location fields here. Application-specific
    # autocomplete widgets are handled later by the existing form engine.
    _set_input(
        page,
        (
            "input[name='city' i]",
            "input[id='city' i]",
            "input[name*='location' i]:not([role='combobox'])",
            "input[id*='location' i]:not([role='combobox'])",
        ),
        location,
    )

    # Common account-country dropdowns. Country comes from the configured
    # applicant location, not from Gemini.
    country = ""
    if "germany" in location.lower() or "berlin" in location.lower():
        country = "Germany"
    if not country:
        return

    try:
        selects = page.query_selector_all("select")
    except Exception:
        selects = []

    for select in selects:
        try:
            if not select.is_visible():
                continue
            meta = " ".join(
                str(select.get_attribute(attr) or "")
                for attr in ("name", "id", "aria-label")
            ).lower()
            if "country" not in meta and "land" not in meta:
                continue
            try:
                select.select_option(label=country)
            except Exception:
                options = select.query_selector_all("option")
                chosen = None
                for option in options:
                    text = (option.inner_text() or "").strip().lower()
                    if text in ("germany", "deutschland") or "germany" in text:
                        chosen = option.get_attribute("value")
                        break
                if chosen:
                    select.select_option(value=chosen)
        except Exception:
            continue


def _accept_required_account_terms(page: Page) -> None:
    """Check only account/terms/privacy required boxes, not marketing/EEO."""
    try:
        boxes = page.query_selector_all("input[type='checkbox'], [role='checkbox']")
    except Exception:
        return
    for box in boxes:
        try:
            if not box.is_visible():
                continue
            checked = box.is_checked() if hasattr(box, "is_checked") else False
            if checked:
                continue
            required = bool(
                box.get_attribute("required")
                or (box.get_attribute("aria-required") or "").lower() == "true"
            )
            try:
                label = box.evaluate(
                    """e => {
                        const id=e.id;
                        const explicit=id ? document.querySelector('label[for="'+CSS.escape(id)+'"]') : null;
                        const implicit=e.closest('label');
                        const node=explicit || implicit || e.parentElement;
                        return (node && node.innerText || '').replace(/\s+/g,' ').trim();
                    }"""
                ) or ""
            except Exception:
                label = ""
            low = label.lower()
            allowed = any(k in low for k in ("terms", "privacy", "agree", "consent", "bedingungen", "datenschutz"))
            disallowed = any(k in low for k in ("newsletter", "marketing", "job alert", "talent community", "sms"))
            if (required or allowed) and allowed and not disallowed:
                try:
                    box.check()
                except Exception:
                    box.click()
        except Exception:
            continue


def _click_named_button(page: Page, texts: tuple[str, ...]) -> bool:
    for text in texts:
        try:
            locator = page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)).first
            if locator.is_visible(timeout=350) and locator.is_enabled():
                locator.click()
                return True
        except Exception:
            pass
        try:
            locator = page.get_by_role("link", name=re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)).first
            if locator.is_visible(timeout=350):
                locator.click()
                return True
        except Exception:
            pass
    return False


def _account_secret() -> str:
    explicit = os.environ.get("APPLICATION_ACCOUNT_MASTER_SECRET", "").strip()
    if explicit:
        return explicit

    try:
        if _SECRET_PATH.exists():
            return _SECRET_PATH.read_text(encoding="utf-8").strip()

        _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_urlsafe(36)
        _SECRET_PATH.write_text(secret, encoding="utf-8")
        try:
            os.chmod(_SECRET_PATH, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        return secret
    except Exception:
        return ""


def _site_password(page: Page) -> str:
    host = (urlparse(page.url).hostname or "job-site").lower()

    site_passwords_raw = os.environ.get("APPLICATION_SITE_PASSWORDS_JSON", "").strip()
    if site_passwords_raw:
        try:
            site_passwords = json.loads(site_passwords_raw)
            for key, value in site_passwords.items():
                if key.lower() in host and str(value).strip():
                    return str(value).strip()
        except Exception:
            pass

    if "linkedin.com" in host:
        linked_in = os.environ.get("LINKEDIN_PASSWORD", "").strip()
        if linked_in:
            return linked_in

    shared = os.environ.get("APPLICATION_ACCOUNT_PASSWORD", "").strip()
    if shared:
        return shared

    # Do not invent a new password for an established LinkedIn account.
    # LinkedIn can still be fully automated when LINKEDIN_PASSWORD (or a
    # site-specific/shared password above) is explicitly configured.
    if "linkedin.com" in host:
        return ""

    secret = _account_secret()
    if not secret:
        return ""

    digest = hmac.new(secret.encode("utf-8"), host.encode("utf-8"), hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    alnum = re.sub(r"[^A-Za-z0-9]", "", token)
    return f"Aa9!{alnum[:18]}"


def _fill_verification_code(page: Page, code: str) -> bool:
    selectors = (
        "input[autocomplete='one-time-code']",
        "input[name*='verification' i]",
        "input[id*='verification' i]",
        "input[name*='otp' i]",
        "input[id*='otp' i]",
        "input[name*='code' i]",
        "input[id*='code' i]",
        "input[inputmode='numeric']",
    )
    if _set_input(page, selectors, code):
        return True

    # Some OTP widgets split each digit into one box.
    try:
        boxes = [
            el for el in page.query_selector_all("input")
            if el.is_visible()
            and (el.get_attribute("maxlength") or "") == "1"
        ]
        if len(boxes) >= len(code):
            for el, digit in zip(boxes, code):
                el.fill(digit)
            return True
    except Exception:
        pass
    return False


def _resolve_email_verification(
    page: Page,
    applicant_email: str,
    company: str,
    not_before: float,
) -> AccountAccessResult:
    kind, value = _poll_verification_email(page, applicant_email, company, not_before)
    if not kind:
        _, _, imap_password = _imap_credentials(applicant_email)
        if not imap_password:
            return AccountAccessResult(
                False,
                "email_access_required",
                "El sitio exige verificar el email, pero no hay acceso IMAP configurado. Define APPLICATION_EMAIL_IMAP_APP_PASSWORD o guarda el App Password en work/gmail_imap_app_password.",
                page,
            )
        return AccountAccessResult(
            False,
            "email_verification_timeout",
            "No encontre a tiempo un correo de verificacion suficientemente relacionado con esta postulacion.",
            page,
        )

    if kind == "code":
        if not _fill_verification_code(page, value):
            return AccountAccessResult(
                False,
                "email_verification_failed",
                "Encontre el codigo de verificacion, pero no encontre un campo seguro donde escribirlo.",
                page,
            )
        _click_named_button(page, _VERIFY_BUTTON_TEXT)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            page.wait_for_timeout(1200)
        return AccountAccessResult(True, "email_verified", "Codigo de email verificado.", page)

    try:
        # Open the magic link in another tab of the SAME browser context.
        # Verification cookies/session state are shared, while the original
        # application/account page remains available to resume immediately.
        verify_page = page.context.new_page()
        try:
            verify_page.goto(value, wait_until="networkidle", timeout=20000)
            verify_page.wait_for_timeout(600)
        finally:
            try:
                verify_page.close()
            except Exception:
                pass

        try:
            page.reload(wait_until="networkidle", timeout=20000)
        except Exception:
            page.wait_for_timeout(1000)

        return AccountAccessResult(
            True,
            "email_verified",
            "Magic link de email abierto; se recargo el portal original con la sesion verificada.",
            page,
        )
    except Exception as exc:
        return AccountAccessResult(
            False,
            "email_verification_failed",
            f"No pude abrir el link de verificacion: {exc}",
            page,
        )


def _login_failed(page: Page) -> bool:
    body = _body_text(page)
    return any(marker in body for marker in _ACCOUNT_FAILURE_MARKERS)


def _signup_says_account_exists(page: Page) -> bool:
    body = _body_text(page)
    return any(marker in body for marker in _ACCOUNT_EXISTS_MARKERS)


def _switch_to_signup(page: Page) -> bool:
    return _click_named_button(page, _CREATE_BUTTON_TEXT)


def _switch_to_login(page: Page) -> bool:
    return _click_named_button(page, _LOGIN_BUTTON_TEXT)


def ensure_account_access(
    page: Page,
    personal_info: dict[str, str],
    company: str = "",
    job_title: str = "",
    application_id: str = "",
) -> AccountAccessResult:
    """Resolve account/login gates until the application form is reachable."""
    email_value = str(personal_info.get("email", "") or "").strip()
    first_name = str(personal_info.get("first_name", "") or "").strip()
    last_name = str(personal_info.get("last_name", "") or "").strip()
    password = _site_password(page)

    if not email_value:
        return AccountAccessResult(False, "account_credentials_missing", "Falta email del candidato.", page)
    if not password:
        return AccountAccessResult(
            False,
            "account_credentials_missing",
            "No pude crear/derivar una contraseña segura para el portal.",
            page,
        )

    verification_not_before = time.time() - 5

    for _ in range(_MAX_ACCOUNT_STEPS):
        state = detect_account_state(page)
        if state is None:
            return AccountAccessResult(True, "account_ready", "Acceso al portal resuelto.", page)

        if state == "captcha":
            return AccountAccessResult(
                False,
                "captcha",
                "CAPTCHA activo detectado. Requiere resolucion humana; no se intenta eludir.",
                page,
            )

        if state == "mfa":
            return AccountAccessResult(
                False,
                "mfa_required",
                "El portal exige SMS, authenticator app o security key. Requiere intervencion humana.",
                page,
            )

        if state == "email_verification":
            verified = _resolve_email_verification(
                page,
                applicant_email=email_value,
                company=company,
                not_before=verification_not_before,
            )
            if not verified.ok:
                return verified
            continue

        _fill_email(page, email_value)
        _fill_passwords(page, password)

        if state == "signup":
            _fill_name_fields(page, first_name, last_name)
            _fill_account_profile_fields(page, personal_info)
            _accept_required_account_terms(page)
            verification_not_before = time.time()
            clicked = _click_named_button(page, _CREATE_BUTTON_TEXT)
            if not clicked:
                clicked = _click_named_button(page, ("Continue", "Next"))
            if not clicked:
                return AccountAccessResult(
                    False,
                    "account_ui_unsupported",
                    "Detecte registro, pero no encontre un boton seguro de Create account/Continue.",
                    page,
                )
        else:
            clicked = _click_named_button(page, _LOGIN_BUTTON_TEXT)
            if not clicked:
                clicked = _click_named_button(page, ("Continue", "Next"))
            if not clicked:
                return AccountAccessResult(
                    False,
                    "account_ui_unsupported",
                    "Detecte login, pero no encontre un boton seguro de Sign in/Continue.",
                    page,
                )

        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(1200)

        if _active_captcha(page):
            return AccountAccessResult(
                False,
                "captcha",
                "CAPTCHA activo detectado despues del login/registro.",
                page,
            )

        if state == "login" and _login_failed(page):
            if _switch_to_signup(page):
                try:
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                continue

        if state == "signup" and _signup_says_account_exists(page):
            if _switch_to_login(page):
                try:
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                continue

    return AccountAccessResult(
        False,
        "account_max_steps",
        f"No logre resolver el acceso al portal despues de {_MAX_ACCOUNT_STEPS} pasos.",
        page,
    )
