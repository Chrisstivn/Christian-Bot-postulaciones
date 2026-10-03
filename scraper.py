"""
scraper.py
Scraping DINÁMICO de ofertas laborales.
Versión optimizada con Stealth para evitar detecciones anti-bot.
"""

import html as html_lib
import re
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

MIN_CHARS = 400
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Serializa CUALQUIER uso de Playwright en todo el proceso. n8n dispara
# /triage-job en paralelo para 25-49 URLs de una vez, y varios hilos
# intentando lanzar un navegador (subprocess) al mismo tiempo contra el
# mismo event loop de uvicorn (uvloop) revientan con
# "RuntimeError: Racing with another loop to spawn a process." -- un bug
# conocido de Playwright sync API + uvloop bajo concurrencia. El costo es
# que los scrapes con Playwright ahora corren de a uno (más lento), pero
# nunca más se pierde una oferta por un crash de concurrencia que nada
# tiene que ver con el contenido real de la página.
_PLAYWRIGHT_LOCK = threading.Lock()

# Tags que casi nunca contienen contenido útil de la oferta
NOISE_TAGS = ["script", "style", "nav", "footer", "header", "svg", "noscript", "form"]


def _clean_html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_TAGS):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _fetch_raw_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def scrape_static(url: str) -> str:
    return _clean_html_to_text(_fetch_raw_html(url))


def _extract_meta_description(html: str) -> str:
    """Muchos ATS modernos (Ashby, y varios embeds de Greenhouse/Lever/
    Workable) son SPAs 100% client-side -- el body estático solo trae un
    "You need to enable JavaScript to run this app.", así que scrape_static
    (y a veces hasta Playwright, si la app tarda en hidratar o detecta el
    navegador headless) devuelven muy poco o nada de texto.

    Pero para que el link se vea bien al compartirlo (WhatsApp, LinkedIn,
    Slack, etc.) estos mismos sitios casi siempre ponen la descripción
    COMPLETA de la oferta en <meta name="description"> y/o <meta
    property="og:description">. Es HTML estático plano -- no necesita
    browser -- así que es un fallback gratis y mucho más confiable que
    pelear con una SPA vía Playwright."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        tag = soup.find("meta", attrs=attrs)
        content = tag.get("content") if tag else None
        if content and content.strip():
            candidates.append(content.strip())
    if not candidates:
        return ""
    # el más largo suele ser el más completo (a veces uno de los dos tags
    # viene truncado o es un resumen más corto)
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def _normalize_text_lines(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def scrape_dynamic_playwright(url: str) -> str:
    """
    Fallback para SPAs con técnicas de camuflaje (stealth).
    Requiere: pip install playwright playwright-stealth
    """
    from playwright.sync_api import sync_playwright
    from playwright_stealth import stealth_sync

    with _PLAYWRIGHT_LOCK:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1920, "height": 1080},
                locale="de-DE",
                timezone_id="Europe/Berlin"
            )

            page = context.new_page()
            stealth_sync(page)

            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            # OJO: NO usar page.content() (outerHTML) aquí. Varios ATS
            # modernos (Ashby, algunos embeds de Greenhouse/Lever)
            # renderizan la descripción del puesto DENTRO de un Shadow
            # DOM (Web Components), y el shadow root no se serializa en
            # el outerHTML del documento -- BeautifulSoup solo ve el
            # <div> contenedor vacío del SPA (por eso salían 30-40
            # caracteres, sin importar cuántos reintentos). innerText SÍ
            # atraviesa el Shadow DOM porque refleja lo que el usuario ve
            # renderizado en pantalla (incluye contenido proyectado vía
            # <slot>), así que es más confiable que parsear HTML crudo.
            try:
                text = page.locator("body").inner_text(timeout=10000)
            except Exception:
                # Fallback final por si alguna página no soporta innerText
                # por el motivo que sea (ej. body vacío/oculto raro).
                text = _clean_html_to_text(page.content())
                browser.close()
                return text

            browser.close()
    return _normalize_text_lines(text)


# ---------------------------------------------------------------------------
# Búsqueda de LinkedIn Jobs -- lista de URLs, no el contenido de cada oferta
# ---------------------------------------------------------------------------
#
# Por qué se agrega un endpoint "guest" en vez de solo usar la página normal:
#   La página completa (linkedin.com/jobs/search/...) renderiza el orden
#   "Most recent" vs "Most relevant" con JavaScript ligado a TU SESIÓN
#   logueada. Sin login -- que es exactamente lo que hace este scraper --
#   LinkedIn a veces ignora el &sortBy=DD de la URL y cae de vuelta a su
#   orden "relevante" por defecto, aunque el link se vea idéntico al que
#   copiaste logueada.
#
#   LinkedIn tiene un endpoint público más liviano, pensado para el
#   "cargar más resultados" del propio buscador sin sesión, que SÍ honra
#   sortBy/start de forma confiable (es lo que usan casi todos los
#   scrapers públicos de LinkedIn en la práctica):
#
#     https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
#
#   Reusa los mismos parámetros de tu URL de búsqueda (keywords, geoId,
#   f_E, etc.) y le agrega/fuerza sortBy=DD + start=N. Como es un
#   endpoint HTML server-side (no una SPA), no necesita Playwright para
#   esto -- un simple requests.get() basta, es más rápido y más difícil
#   de detectar que abrir un navegador completo.
#
#   Si por lo que sea ese endpoint no devuelve nada (LinkedIn cambia
#   cosas sin avisar), se cae de vuelta al método viejo con Playwright +
#   stealth sobre la página completa, para no dejar la búsqueda sin
#   resultados de un día para otro.

GUEST_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
GUEST_JOB_URL_TEMPLATE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


def _linkedin_job_id(url: str) -> str:
    """Extract the final numeric LinkedIn job id from a /jobs/view/... URL."""
    path = urlsplit(url or "").path.rstrip("/")
    match = re.search(r"/jobs/view/(?:.*-)?(\d+)$", path)
    return match.group(1) if match else ""


def _is_linkedin_job_url(url: str) -> bool:
    parsed = urlsplit(url or "")
    return "linkedin.com" in (parsed.hostname or "").lower() and bool(_linkedin_job_id(url))


def _fetch_linkedin_guest_job_html(url: str) -> str:
    """Fetch exactly one LinkedIn job via the public guest job endpoint.

    Crucially, redirects are NOT followed. If LinkedIn considers the job
    unavailable, we fail instead of accepting a redirect to a generic search
    page containing dozens of unrelated jobs.
    """
    job_id = _linkedin_job_id(url)
    if not job_id:
        raise ValueError(f"No pude extraer el job id de LinkedIn: {url}")

    guest_url = GUEST_JOB_URL_TEMPLATE.format(job_id=job_id)
    resp = requests.get(
        guest_url,
        headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"},
        timeout=15,
        allow_redirects=False,
    )

    if 300 <= resp.status_code < 400:
        raise ValueError(
            f"LinkedIn redirigió el job individual {job_id}; "
            "no se usará una página de búsqueda como si fuera la oferta."
        )

    resp.raise_for_status()
    return resp.text


def _fetch_linkedin_direct_job_html(url: str) -> str:
    """Fallback to the public job URL only if it stays on the same job id."""
    requested_id = _linkedin_job_id(url)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    final_id = _linkedin_job_id(resp.url)
    if not final_id or final_id != requested_id:
        raise ValueError(
            f"LinkedIn redirigió el job {requested_id} a otra página "
            f"({resp.url}); contenido rechazado."
        )

    return resp.text


def _extract_linkedin_rendered_work_format(url: str) -> str:
    """Read LinkedIn's rendered top-card workplace badge safely.

    The guest/static HTML often omits the visible On-site / Hybrid / Remote
    badge even though a browser shows it. Use Playwright only as metadata
    enrichment, and reject the result unless the rendered page still points
    to the exact same numeric LinkedIn job id.
    """
    from playwright.sync_api import sync_playwright
    from playwright_stealth import stealth_sync

    requested_id = _linkedin_job_id(url)
    if not requested_id:
        return "Unknown"

    with _PLAYWRIGHT_LOCK:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    viewport={"width": 1920, "height": 1080},
                    locale="de-DE",
                    timezone_id="Europe/Berlin",
                )
                page = context.new_page()
                stealth_sync(page)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)

                final_id = _linkedin_job_id(page.url)
                if final_id != requested_id:
                    return "Unknown"

                # Prefer exact rendered badges near the top card. Bounding-box
                # filtering avoids mistaking a description sentence for the
                # workplace badge.
                for label, normalized in (
                    ("On-site", "On-site"),
                    ("On site", "On-site"),
                    ("Hybrid", "Hybrid"),
                    ("Remote", "Remote"),
                ):
                    try:
                        matches = page.get_by_text(label, exact=True)
                        count = min(matches.count(), 8)
                        for idx in range(count):
                            el = matches.nth(idx)
                            if not el.is_visible(timeout=300):
                                continue
                            box = el.bounding_box()
                            if box and box.get("y", 99999) <= 1400:
                                return normalized
                    except Exception:
                        continue

                # Conservative text fallback: only inspect the first rendered
                # lines where LinkedIn's top card lives, and only accept an
                # exact standalone canonical label.
                try:
                    body = page.locator("body").inner_text(timeout=5000)
                except Exception:
                    body = ""
                for line in _normalize_text_lines(body).splitlines()[:80]:
                    normalized = _normalize_work_format(line.strip())
                    if normalized != "Unknown":
                        return normalized
                return "Unknown"
            finally:
                browser.close()


def _build_paginated_linkedin_url(search_url: str, start: int) -> str:
    """Inyecta &start=N (paginación real de LinkedIn Jobs) y fuerza
    &sortBy=DD (Date posted descending -- más reciente primero) en la URL
    de búsqueda, sin importar qué parámetros traiga ya. Esto es lo que
    permite "continuar donde lo dejamos": start=0 son los 25 más
    recientes, start=25 los siguientes 25 más antiguos, etc."""
    parsed = urlsplit(search_url)
    query = dict(parse_qsl(parsed.query))
    query["sortBy"] = "DD"
    query["start"] = str(start)
    new_query = urlencode(query)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def _extract_job_urls_from_html(html: str, max_jobs: int) -> list:
    soup = BeautifulSoup(html, "html.parser")
    job_urls = []
    for a in soup.select("a[href*='/jobs/view/']"):
        href = a.get("href", "")
        if not href:
            continue
        clean_url = href.split("?")[0]
        if clean_url not in job_urls:
            job_urls.append(clean_url)
        if len(job_urls) >= max_jobs:
            break
    return job_urls


def _scrape_linkedin_guest_api(search_url: str, max_jobs: int, start: int) -> list:
    """Método PRINCIPAL: endpoint guest server-side, sin login, sin
    Playwright. Reintenta el MISMO offset varias veces porque LinkedIn puede
    devolver respuestas vacías/intermitentes aunque todavía existan jobs."""
    parsed = urlsplit(search_url)
    query = dict(parse_qsl(parsed.query))
    query["sortBy"] = "DD"
    query["start"] = str(start)

    best_urls = []
    last_error = None

    # Keep each request bounded so /linkedin-search stays comfortably below
    # n8n's 300s HTTP timeout. One quick retry is enough for transient guest
    # endpoint hiccups; deeper coverage comes from advancing offsets, not from
    # sleeping repeatedly on the same page.
    for attempt in range(2):
        try:
            resp = requests.get(
                GUEST_SEARCH_URL,
                params=query,
                headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"},
                timeout=8,
            )
            resp.raise_for_status()
            urls = _extract_job_urls_from_html(resp.text, max_jobs)
            if len(urls) > len(best_urls):
                best_urls = urls
            if urls:
                return urls
        except Exception as exc:
            last_error = exc

        if attempt == 0:
            import time
            time.sleep(1)

    if best_urls:
        return best_urls
    if last_error:
        raise last_error
    return []


def _scrape_linkedin_playwright(search_url: str, max_jobs: int, start: int = 0) -> list:
    """Infinite scroll real sobre el panel de resultados de LinkedIn.

    LinkedIn Jobs suele tener su propio contenedor scrollable; mover solo
    window/document puede quedarse clavado alrededor de ~60 resultados.
    Aquí se desplaza el panel y la ventana, intenta activar explícitamente
    cualquier botón de "ver más", y solo termina tras muchos intentos sin
    crecimiento.
    """
    from playwright.sync_api import sync_playwright
    from playwright_stealth import stealth_sync

    job_urls = []
    with _PLAYWRIGHT_LOCK:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1920, "height": 1080},
                locale="de-DE",
                timezone_id="Europe/Berlin",
            )
            page = context.new_page()
            stealth_sync(page)
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            no_growth_rounds = 0
            previous_count = -1

            while len(job_urls) < max_jobs and no_growth_rounds < 12:
                links = page.query_selector_all("a[href*='/jobs/view/']")
                for link in links:
                    href = link.get_attribute("href")
                    if not href:
                        continue
                    clean_url = href.split("?")[0]
                    if clean_url not in job_urls:
                        job_urls.append(clean_url)
                        if len(job_urls) >= max_jobs:
                            break

                if len(job_urls) > previous_count:
                    previous_count = len(job_urls)
                    no_growth_rounds = 0
                else:
                    no_growth_rounds += 1

                # Tres intentos por ronda. Lo importante es desplazar el PANEL
                # de resultados, no solamente window: LinkedIn virtualiza/lazy-
                # loadea esa lista de forma independiente.
                for _ in range(3):
                    page.evaluate("""
                        () => {
                          const selectors = [
                            '.jobs-search-results-list',
                            '.scaffold-layout__list',
                            '.jobs-search-results-list__list',
                            'main'
                          ];
                          for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) {
                              el.scrollTop = el.scrollHeight;
                              el.dispatchEvent(new Event('scroll', {bubbles:true}));
                            }
                          }
                          window.scrollTo(0, document.body.scrollHeight);
                        }
                    """)
                    page.mouse.wheel(0, 6000)
                    page.wait_for_timeout(4000)

                    # Algunas variantes de LinkedIn paran el lazy-load y muestran
                    # un botón para continuar. Si aparece, púlsalo y sigue.
                    for label in ["See more jobs", "Show more", "Mehr Jobs anzeigen", "Weitere Jobs anzeigen"]:
                        try:
                            btn = page.get_by_role("button", name=label)
                            if btn.count() and btn.first.is_visible():
                                btn.first.click(timeout=2000)
                                page.wait_for_timeout(4000)
                                break
                        except Exception:
                            pass

            browser.close()

    return job_urls


def scrape_linkedin_search_results(search_url: str, max_jobs: int = 10000, start: int = 0) -> list:
    """Recorre COMPLETA una búsqueda de LinkedIn antes de responder a n8n.

    El endpoint guest entrega bloques de hasta 10 ofertas. Avanzar de 25 en
    25 saltaba resultados y podía hacer que una búsqueda pareciera terminar
    en 10/20/60 aunque hubiera muchas más. Recorremos offsets de 10 en 10,
    reintentamos respuestas vacías/transitorias y solo terminamos después
    de varios bloques vacíos consecutivos.
    """
    import time

    max_jobs = max(1, int(max_jobs or 10000))
    offset_step = 10
    current_start = max(0, int(start or 0))
    all_urls = []
    seen = set()
    consecutive_empty_offsets = 0
    # LinkedIn puede devolver huecos transitorios bastante profundos.
    # No cortar una búsqueda grande por 5 bloques vacíos aislados.
    max_empty_offsets = 12
    max_offset = 50000

    while (
        len(all_urls) < max_jobs
        and consecutive_empty_offsets < max_empty_offsets
        and current_start < max_offset
    ):
        page_urls = []

        # LinkedIn a veces devuelve un bloque vacío aunque todavía queden
        # resultados. Reintentamos el MISMO offset antes de avanzar.
        for attempt in range(4):
            try:
                page_urls = _scrape_linkedin_guest_api(
                    search_url,
                    min(offset_step, max_jobs - len(all_urls)),
                    current_start,
                )
            except Exception:
                page_urls = []

            if page_urls:
                break

            if attempt < 3:
                time.sleep(2 + attempt)

        if not page_urls:
            consecutive_empty_offsets += 1
            current_start += offset_step
            continue

        consecutive_empty_offsets = 0
        for url in page_urls:
            if url not in seen:
                seen.add(url)
                all_urls.append(url)
                if len(all_urls) >= max_jobs:
                    break

        # El seeMoreJobPostings guest endpoint pagina en bloques de 10.
        # Es importante NO saltar 25: eso omitía ofertas entre páginas.
        current_start += offset_step

    # Fallback solo si el endpoint guest no consiguió absolutamente nada.
    if not all_urls:
        paginated_url = _build_paginated_linkedin_url(search_url, start)
        return _scrape_linkedin_playwright(
            paginated_url,
            max_jobs=max_jobs,
            start=start,
        )

    return all_urls


def _normalize_work_format(value: str) -> str:
    normalized = re.sub(r"[_\s]+", " ", value or "").strip().lower()
    if normalized in ("hybrid", "hybrid work", "hybrid working"):
        return "Hybrid"
    if normalized in ("remote", "fully remote", "telecommute", "telecommuting"):
        return "Remote"
    if normalized in ("on-site", "onsite", "on site", "on-site work", "onsite work"):
        return "On-site"
    return "Unknown"


def extract_linkedin_work_format_from_html(raw_html: str) -> str:
    """Extract LinkedIn's own workplace-type metadata when it is exposed.

    The public guest description often omits the badge that LinkedIn renders
    in the normal top card. Before inferring anything from prose, inspect raw
    HTML/scripts/attributes for LinkedIn's canonical workplace values.

    This function is deliberately conservative: it returns Unknown rather
    than guessing from location or employment type.
    """
    if not raw_html:
        return "Unknown"

    decoded = html_lib.unescape(raw_html)

    # LinkedIn/ATS structured payloads use several casing variants depending
    # on the surface. The official value set is On-Site / Hybrid / Remote.
    structured_patterns = (
        r'["\']workPlaceTypes["\']\s*:\s*\[\s*["\']([^"\']+)["\']',
        r'["\']workplaceTypes["\']\s*:\s*\[\s*["\']([^"\']+)["\']',
        r'["\']workplaceType["\']\s*:\s*["\']([^"\']+)["\']',
        r'["\']workPlaceType["\']\s*:\s*["\']([^"\']+)["\']',
    )
    for pattern in structured_patterns:
        match = re.search(pattern, decoded, re.IGNORECASE)
        if match:
            normalized = _normalize_work_format(match.group(1))
            if normalized != "Unknown":
                return normalized

    # Schema.org-style remote metadata is unambiguous for Remote.
    if re.search(
        r'["\']jobLocationType["\']\s*:\s*["\']TELECOMMUTE["\']',
        decoded,
        re.IGNORECASE,
    ):
        return "Remote"

    # Common LinkedIn job-distribution override tags.
    hashtag_map = (
        ("#li-hybrid", "Hybrid"),
        ("#li-remote", "Remote"),
        ("#li-onsite", "On-site"),
        ("#li-on-site", "On-site"),
    )
    lowered = decoded.lower()
    for marker, workplace in hashtag_map:
        if marker in lowered:
            return workplace

    # Some LinkedIn layouts expose the value in an element whose class/id or
    # data attribute contains "workplace". Restrict the exact text to the
    # canonical values so ordinary description prose cannot create a false
    # metadata hit here.
    soup = BeautifulSoup(decoded, "html.parser")
    for element in soup.find_all(True):
        attrs = " ".join(
            str(value)
            for key, value in element.attrs.items()
            if "workplace" in str(key).lower()
            or "workplace" in str(value).lower()
        )
        if not attrs:
            continue
        normalized = _normalize_work_format(element.get_text(" ", strip=True))
        if normalized != "Unknown":
            return normalized

    return "Unknown"


def extract_work_format(text: str) -> str:
    """Infer workplace type from explicit description text as a fallback."""
    value = re.sub(r"\s+", " ", text or "").strip().lower()
    # Prefer Hybrid before Remote: hybrid descriptions often mention
    # remote/home-office days elsewhere in the page text.
    if re.search(r"\b(hybrid|hybrid work|hybrid working|hybrid workplace)\b", value):
        return "Hybrid"
    if re.search(r"\b(remote|work from home|fully remote|100% remote|remote work)\b", value):
        return "Remote"
    if re.search(r"\b(on[- ]site|onsite|on site|workplace type: on-site)\b", value):
        return "On-site"
    return "Unknown"


_CLOSED_APPLICATION_MARKERS = (
    "no longer accepting applications",
    "no longer accepting application",
    "applications are closed",
    "application period has ended",
    "this job is no longer available",
    "this job is no longer accepting applications",
    "job is no longer available",
    "position is no longer available",
    "nimmt keine bewerbungen mehr an",
    "bewerbungen werden nicht mehr angenommen",
    "stelle ist nicht mehr verfügbar",
    "stellenangebot ist nicht mehr verfügbar",
)


def find_closed_application_marker(text: str) -> str:
    """Return an explicit closed-applications marker found in scraped text."""
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()
    for closed_marker in _CLOSED_APPLICATION_MARKERS:
        if closed_marker in normalized:
            return closed_marker
    return ""


def check_linkedin_application_status(url: str) -> dict:
    """Check whether a LinkedIn job explicitly says applications are closed.

    This is intentionally read-only and uses no Gemini call. If LinkedIn does
    not expose a clear closed marker, the result stays unknown so we never
    discard a potentially live job by guessing.
    """
    if "linkedin.com/jobs/" not in (url or "").lower():
        return {
            "url": url,
            "status": "unknown",
            "application_open": None,
            "reason": "not_linkedin_job_url",
        }

    texts = []
    errors = []

    try:
        raw_html = _fetch_raw_html(url)
        texts.append(("static_html", _clean_html_to_text(raw_html)))
    except Exception as exc:
        errors.append(f"static: {exc}")

    # Closed/expired pages can contain only a short notice, so do not apply
    # MIN_CHARS here. Rendered text is a fallback only when static HTML did
    # not already expose the explicit closure notice.
    for source, page_text in texts:
        closed_marker = find_closed_application_marker(page_text)
        if closed_marker:
            return {
                "url": url,
                "status": "closed",
                "application_open": False,
                "reason": closed_marker,
                "source": source,
            }

    try:
        rendered_text = scrape_dynamic_playwright(url)
        closed_marker = find_closed_application_marker(rendered_text)
        if closed_marker:
            return {
                "url": url,
                "status": "closed",
                "application_open": False,
                "reason": closed_marker,
                "source": "playwright",
            }
    except Exception as exc:
        errors.append(f"playwright: {exc}")

    return {
        "url": url,
        "status": "unknown",
        "application_open": None,
        "reason": "no_explicit_closed_marker",
        "errors": errors[:2],
    }

def scrape_job_posting(url: str) -> dict:
    """
    Punto de entrada único. Devuelve el texto y metadata determinística.

    LinkedIn requiere una ruta especial: una URL /jobs/view/... vieja puede
    redirigir silenciosamente a /jobs/search/... y esa página contiene muchas
    ofertas. Antes se aceptaba porque superaba MIN_CHARS, lo que podía hacer
    que Gemini extrajera otra empresa/puesto distinto al URL solicitado.
    """
    raw_html = ""
    text = ""
    work_format = "Unknown"
    linkedin_errors = []

    if _is_linkedin_job_url(url):
        # 1) Endpoint guest de UN solo job. No sigue redirects.
        try:
            raw_html = _fetch_linkedin_guest_job_html(url)
            text = _clean_html_to_text(raw_html)
            work_format = extract_linkedin_work_format_from_html(raw_html)
        except Exception as exc:
            linkedin_errors.append(f"guest_job: {exc}")
            raw_html = ""
            text = ""

        # 2) The guest detail frequently omits LinkedIn's top-card workplace
        # badge even when the normal job page displays it. If metadata is still
        # unknown, inspect the verified public URL as an enrichment request.
        # _fetch_linkedin_direct_job_html rejects redirects unless the final
        # URL retains the exact same numeric LinkedIn job id.
        if work_format == "Unknown":
            try:
                direct_html = _fetch_linkedin_direct_job_html(url)
                direct_format = extract_linkedin_work_format_from_html(direct_html)
                if direct_format != "Unknown":
                    work_format = direct_format
                if len(text) < MIN_CHARS:
                    raw_html = direct_html
                    text = _clean_html_to_text(direct_html)
            except Exception as exc:
                linkedin_errors.append(f"direct_job: {exc}")

        # 3) LinkedIn frequently renders the workplace badge only in the
        # browser UI. If both static sources omitted it, enrich from the
        # rendered top card while verifying the exact same numeric job id.
        if work_format == "Unknown":
            try:
                rendered_format = _extract_linkedin_rendered_work_format(url)
                if rendered_format != "Unknown":
                    work_format = rendered_format
            except Exception as exc:
                linkedin_errors.append(f"rendered_work_format: {exc}")

        # 4) If guest content itself was too short and the enrichment request
        # above did not already replace it, retry the verified direct page for
        # content as before.
        if len(text) < MIN_CHARS:
            try:
                raw_html = _fetch_linkedin_direct_job_html(url)
                text = _clean_html_to_text(raw_html)
                if work_format == "Unknown":
                    work_format = extract_linkedin_work_format_from_html(raw_html)
            except Exception as exc:
                linkedin_errors.append(f"direct_job: {exc}")
                raw_html = ""
                text = ""

        # Para LinkedIn NO usamos el Playwright genérico como fallback:
        # también podría seguir una redirección a /jobs/search/ y volver a
        # introducir el mismo bug. Si ambas rutas verificadas fallan, dejamos
        # el job como failed con un error explícito.
        if len(text) < MIN_CHARS:
            detail = " | ".join(linkedin_errors[-2:]) or "sin detalle"
            raise ValueError(
                f"No se pudo extraer de forma segura el job individual de LinkedIn "
                f"{url}. {detail}"
            )
    else:
        try:
            raw_html = _fetch_raw_html(url)
            text = _clean_html_to_text(raw_html)
        except Exception:
            text = ""

        # Fallback barato ANTES de prender un navegador: si el body vino corto
        # (típico de SPAs full client-side como Ashby), probamos con el meta
        # description -- sin browser, sin riesgo de detección anti-bot.
        if len(text) < MIN_CHARS and raw_html:
            meta_text = _extract_meta_description(raw_html)
            if len(meta_text) > len(text):
                text = meta_text

        if len(text) < MIN_CHARS:
            text = scrape_dynamic_playwright(url)

        if len(text) < MIN_CHARS:
            raise ValueError(
                f"No se pudo extraer contenido suficiente de {url} "
                f"(solo {len(text)} caracteres). Puede requerir login o "
                f"tener anti-bot."
            )

    text = re.sub(r"[ \t]{2,}", " ", text)
    clean_text = text.strip()
    if work_format == "Unknown":
        work_format = extract_work_format(clean_text)
    return {"text": clean_text, "work_format": work_format}
