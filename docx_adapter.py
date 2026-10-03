"""
docx_adapter.py
Inyección QUIRÚRGICA de la adaptación de Gemini sobre TU CV real
(Christian_CV.docx), sin tocar nada más.

Por qué "anclas" y no {{PLACEHOLDERS}}:
  Insertar placeholders a mano en tu CV real es trabajo manual y frágil
  (si algún día editas el CV en Word, se pueden borrar por error).
  En cambio, este módulo detecta el contenido real como ancla:
    - "Christian | <TÍTULO>"           -> reemplaza <TÍTULO>
    - Heading "Personal Profile" -> siguiente párrafo -> reemplaza TODO
    - Primer heading que contiene "la empresa del CV maestro" después de
      "Experience" -> es el ROL ACTUAL -> reemplaza solo el cargo (antes de la coma)
    - Los bullets inmediatamente debajo de ese heading (hasta el próximo
      heading) -> reemplaza por las nuevas tareas de Gemini

Esto es exactamente lo que python-docx puede hacer de forma confiable.
Nota técnica importante (ver skill de docx): Word fragmenta el texto en
múltiples <w:r> runs (por corrector ortográfico, revisiones, etc.), así que
NO se puede confiar en que python-docx te de un string contiguo por run.
Por eso cada función de reemplazo:
  1. Lee el texto COMPLETO del párrafo (paragraph.text, que sí concatena runs).
  2. Decide el nuevo texto completo.
  3. Vacía todos los runs del párrafo y escribe el texto nuevo en el primer
     run, preservando su formato (bold/italic/font/size del primer run).
"""

import re
from docx import Document
from docx.text.paragraph import Paragraph
from docx.enum.section import WD_SECTION_START
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt
from reportlab.pdfbase.pdfmetrics import stringWidth
from typing import List

DEFAULT_FONT = "Arial"
EXPERIENCE_TASK_FONT_SIZE_PT = 10.0

# Medido a partir del layout real del CV: cada línea completa del párrafo de
# "Personal Profile" mide ~105 caracteres con la fuente/columna actuales.
# 6 líneas completas -> ~630 caracteres. Dejamos margen de seguridad porque
# el punto exacto de salto depende de dónde caigan los espacios entre
# palabras, no solo del conteo de caracteres.
MAX_PROFILE_CHARS = 635  # CALIBRADO con LibreOffice real sobre este CV:
                          # 643 caracteres todavía da 6 líneas, pero 650 ya
                          # se pasa a 7. Se deja margen de seguridad en 635.
MIN_PROFILE_CHARS = 555  # CALIBRADO igual: 554 da 6 líneas, 537 ya cae a 5.
                          # Por debajo de esto, docx_adapter NO puede
                          # inventar texto para alargarlo sin violar el
                          # guardarraíl anti-alucinación, así que en vez de
                          # aceptarlo en silencio, se aborta con un error
                          # claro (mejor que entregar un CV roto).

# Estimado para los bullets de "Experience" (mismo tamaño de fuente que el
# perfil pero con menos ancho disponible por el sangrado de la viñeta). Si
# al ver el resultado real te queda corto o largo, ajusta este número: es
# el máximo de caracteres que entran en UNA línea de bullet en tu CV.
BULLET_CHARS_PER_LINE = 100
MAX_BULLET_TOTAL_LINES = 8
MIN_BULLET_TOTAL_CHARS = 700  # ~7 líneas completas -> por debajo de esto,
                               # probablemente no llena las 8 líneas exigidas.

COMPANY_ANCHOR = ""
PROFILE_HEADING = "Sobre mí"
EXPERIENCE_HEADING = "Experiencia"
NAME_ANCHOR = "Christian"


def _has_drawing(run) -> bool:
    """True si este run contiene una imagen/drawing (ej. la foto del CV).
    Estos runs NUNCA deben tocarse al reemplazar texto."""
    return "w:drawing" in run._r.xml or "w:pict" in run._r.xml


def _has_manual_kerning(run) -> bool:
    """True si el run trae un <w:spacing> EN w:rPr (character kerning /
    tracking manual), distinto del <w:spacing> de w:pPr (espaciado entre
    líneas). Tu CV maestro usa esto en el nombre|título y en la línea de
    teléfono | email | LinkedIn: cada run (incluidos los espacios sueltos
    entre "+49", "157", "582"...) trae un valor negativo distinto
    (-23, -21, -13...) calculado a mano para que todo el bloque quepa en
    una sola línea dentro del ancho de columna reservado para la foto.
    Ese kerning se calculó para la fuente ORIGINAL heredada del theme
    (minorHAnsi), no para Arial. Si forzamos Arial en estos runs, el ancho
    de cada glyph cambia pero el kerning no se recalcula -> el texto deja
    de caber en una línea y se reparte en 2-3 líneas (el bug de
    "espaciados" que rompe teléfono/email/LinkedIn). Por eso estos runs
    deben quedar completamente afuera de _normalize_fonts."""
    rPr = run._r.find(qn("w:rPr"))
    if rPr is None:
        return False
    return rPr.find(qn("w:spacing")) is not None


def _clear_paragraph_mark_highlight(paragraph: Paragraph) -> None:
    """El símbolo de la viñeta (bullet) se pinta con el formato de la
    'marca de párrafo' (w:pPr/w:rPr), NO con el de los runs de texto. Si tu
    CV maestro tiene esa viñeta resaltada en amarillo, limpiar solo los
    runs (como hace _set_paragraph_text) no alcanza — hay que quitar el
    <w:highlight> de ahí también, o el punto de la viñeta queda amarillo
    aunque el texto ya esté limpio."""
    pPr = paragraph._p.find(qn("w:pPr"))
    if pPr is None:
        return
    rPr = pPr.find(qn("w:rPr"))
    if rPr is None:
        return
    highlight = rPr.find(qn("w:highlight"))
    if highlight is not None:
        rPr.remove(highlight)


def _set_paragraph_text(paragraph: Paragraph, new_text: str) -> None:
    """Reemplaza el texto de un párrafo preservando el formato del primer
    run de TEXTO (bold/italic/font/size), sin tocar jamás runs que
    contengan imágenes/drawings (ej. la foto embebida en el mismo párrafo
    que el título "Christian | <título>"). También quita el resaltado
    amarillo que tu CV maestro trae de fábrica para marcar "esto se
    cambia" — esa nota es para humanos, no debe llegar al PDF final."""
    text_runs = [r for r in paragraph.runs if not _has_drawing(r)]
    _clear_paragraph_mark_highlight(paragraph)

    if not text_runs:
        run = paragraph.add_run(new_text)
        run.font.highlight_color = None
        return

    first_run = text_runs[0]
    first_run.text = new_text
    first_run.font.highlight_color = None
    # Vaciar el resto de los runs DE TEXTO (los runs con drawing quedan
    # completamente intactos, nunca se listan aquí).
    for run in text_runs[1:]:
        run.text = ""
        run.font.highlight_color = None


def _delete_paragraph(paragraph: Paragraph) -> None:
    """Elimina el párrafo completo del XML (no solo su texto), para no dejar
    viñetas vacías sueltas cuando Gemini devuelve menos tareas que bullets
    existentes en la plantilla."""
    element = paragraph._element
    element.getparent().remove(element)


def _is_separator(paragraph: Paragraph) -> bool:
    return bool(paragraph.text.strip()) and set(paragraph.text.strip()) <= {"_"}


def _is_bullet(paragraph: Paragraph) -> bool:
    return paragraph.style.name.lower().startswith("list") or bool(
        paragraph._p.xpath("./w:pPr/w:numPr")
    )


def _is_heading(paragraph: Paragraph) -> bool:
    return paragraph.style.name.lower().startswith("heading")


def _find_paragraph_index(doc: Document, contains: str, start: int = 0) -> int:
    for i in range(start, len(doc.paragraphs)):
        if contains in doc.paragraphs[i].text:
            return i
    return -1


# ---------------------------------------------------------------------------
# 1. Título principal: "Christian | <TÍTULO>"
# ---------------------------------------------------------------------------

MAX_TITLE_CHARS = 45  # ver _shorten_title_to_two_lines: calibrado para que
                       # "Christian | <título>" quepa en 2 líneas dentro
                       # de la columna angosta que deja libre la foto
                       # (Ttulo style: ind left=753 right=3617, Arial 16pt
                       # bold). Con nombre+"|" ya ocupando la primera línea,
                       # esto deja ~45 caracteres de presupuesto real para
                       # el título antes de necesitar una 3ª línea.
MIN_TITLE_CHARS = 39   # por debajo de esto, "Christian | <título>" cabe
                       # entero en 1 línea y no ocupa las 2 líneas exigidas.

# Same calibrated visual guard used by gemini_service.py. ReportLab's
# Helvetica metrics are close enough to Arial to reproduce the actual Word
# wrap seen in the generated PDFs, unlike raw character counts.
_TITLE_LAYOUT_PREFIX = "Christian Molina | "
_TITLE_FONT_NAME = "Helvetica-Bold"
_TITLE_FONT_SIZE_PT = 15.96
_TITLE_LINE_WIDTH_PT = 278.0
_BULLET_FONT_NAME = "Helvetica"
_BULLET_FONT_SIZE_PT = 9.96
_BULLET_LINE_WIDTH_PT = 442.0


def _estimated_visual_lines(
    text: str,
    *,
    font_name: str,
    font_size_pt: float,
    line_width_pt: float,
) -> int:
    words = re.sub(r"\s+", " ", (text or "").strip()).split(" ")
    words = [word for word in words if word]
    if not words:
        return 0

    lines = 1
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if current and stringWidth(candidate, font_name, font_size_pt) > line_width_pt:
            lines += 1
            current = word
        else:
            current = candidate
    return lines


def _estimated_title_lines(title: str) -> int:
    return _estimated_visual_lines(
        f"{_TITLE_LAYOUT_PREFIX}{(title or '').strip()}",
        font_name=_TITLE_FONT_NAME,
        font_size_pt=_TITLE_FONT_SIZE_PT,
        line_width_pt=_TITLE_LINE_WIDTH_PT,
    )


def _estimated_bullet_lines(task: str) -> int:
    return _estimated_visual_lines(
        task,
        font_name=_BULLET_FONT_NAME,
        font_size_pt=_BULLET_FONT_SIZE_PT,
        line_width_pt=_BULLET_LINE_WIDTH_PT,
    )


def _shorten_title_to_two_lines(nuevo_titulo: str, max_chars: int = MAX_TITLE_CHARS) -> str:
    """Los job titles reales vienen con sufijos que Gemini copia tal cual
    de la oferta (ej. 'Performance Marketing Manager - Lead Generation &
    Growth (f/m/d)'), pero el estilo 'Ttulo' del CV solo tiene espacio
    para 2 líneas antes de romper el layout del encabezado (empuja la
    foto/dirección hacia abajo). En vez de dejar que Word reparta esto en
    3 líneas, recortamos lo no esencial en este orden:
      1. Anotaciones de género entre paréntesis al final, ej. '(f/m/d)',
         '(m/w/d)', '(w/m/d)' -> no aportan nada al CV.
      2. Todo lo que va después de un guion largo o un ' - ' con espacios
         (normalmente el subtítulo de la oferta, no el cargo en sí) -> se
         corta ahí.
      3. Si aún así no cabe, se recorta al último espacio dentro del
         presupuesto de caracteres (nunca se corta una palabra a la
         mitad).
    """
    titulo = nuevo_titulo.strip()

    # 1. Quitar anotaciones de género tipo "(f/m/d)" al final (puede haber
    #    más de una, ej. "... (f/m/d) (all genders)")
    while True:
        sin_parentesis = re.sub(r"\s*\([^)]*\)\s*$", "", titulo).strip()
        if sin_parentesis == titulo:
            break
        titulo = sin_parentesis

    if len(titulo) <= max_chars:
        return titulo

    # 2. Cortar en el primer separador de "subtítulo": guion largo/medio
    #    o COMA (ej. "Manager, Lead Generation & Growth" -> "Manager").
    #    Sin la coma acá, el recorte duro del paso 3 puede caer a mitad de
    #    la segunda mitad del título y dejar una palabra suelta colgando
    #    (ej. el bug "Performance Marketing Manager, Lead").
    match = re.search(r"\s[\u2013\u2014]\s|\s-\s|,\s*", titulo)
    if match:
        titulo_corto = titulo[: match.start()].strip()
        if titulo_corto:
            titulo = titulo_corto

    if len(titulo) <= max_chars:
        return titulo

    # 3. Último recurso: recorte duro por palabra completa.
    recortado = titulo[:max_chars].rsplit(" ", 1)[0].strip()
    return recortado or titulo[:max_chars].strip()


def update_main_title(doc: Document, nuevo_titulo: str) -> None:
    idx = _find_paragraph_index(doc, NAME_ANCHOR)
    if idx == -1:
        raise ValueError(f"No encontré el ancla '{NAME_ANCHOR}' en el CV.")
    paragraph = doc.paragraphs[idx]
    full_text = paragraph.text
    if "|" not in full_text:
        raise ValueError("El formato 'Nombre | Título' cambió en el CV; revisa el ancla.")
    name_part = full_text.split("|")[0].strip()
    titulo_final = _shorten_title_to_two_lines(nuevo_titulo)
    if len(titulo_final) < MIN_TITLE_CHARS:
        raise ValueError(
            f"nuevo_titulo quedó en {len(titulo_final)} caracteres tras el "
            f"recorte (mínimo {MIN_TITLE_CHARS}) -> probablemente no llena "
            f"las 2 líneas. Se aborta antes de generar un CV con el título "
            f"corto; revisa el largo que devuelve Gemini."
        )
    estimated_lines = _estimated_title_lines(titulo_final)
    if estimated_lines != 2:
        raise ValueError(
            f"nuevo_titulo se estima en {estimated_lines} líneas visuales "
            "en el encabezado real; deben ser exactamente 2. Se aborta antes "
            "de generar un PDF con el header roto."
        )
    new_text = f"{name_part} | {titulo_final}"
    _set_paragraph_text(paragraph, new_text)


# ---------------------------------------------------------------------------
# 2. Personal Profile
# ---------------------------------------------------------------------------

def _fit_profile_to_max_chars(nuevo_perfil: str, max_chars: int = MAX_PROFILE_CHARS) -> str:
    """Gemini a veces devuelve un perfil de largo variable; si se pasa del
    presupuesto calibrado para 6 líneas, Word lo reparte en una 7ª línea y
    descuadra el layout del header. Recortamos en este orden, igual que
    _shorten_title_to_two_lines:
      1. Si entra completo, no se toca.
      2. Si no entra, se corta en el último punto de oración (". ") dentro
         del presupuesto, para no dejar una frase a medias.
      3. Si no hay un punto razonablemente cerca del límite, se recorta en
         la última palabra completa dentro del presupuesto.
    """
    perfil = nuevo_perfil.strip()
    if len(perfil) <= max_chars:
        return perfil

    corte_oracion = perfil.rfind(". ", 0, max_chars)
    if corte_oracion != -1 and corte_oracion >= max_chars * 0.6:
        return perfil[: corte_oracion + 1].strip()

    recortado = perfil[:max_chars].rsplit(" ", 1)[0].strip()
    return recortado or perfil[:max_chars].strip()


def update_profile(doc: Document, nuevo_perfil: str) -> None:
    heading_idx = _find_paragraph_index(doc, PROFILE_HEADING)
    if heading_idx == -1:
        raise ValueError(f"No encontré el heading '{PROFILE_HEADING}'.")
    perfil_final = _fit_profile_to_max_chars(nuevo_perfil)
    if len(perfil_final) < MIN_PROFILE_CHARS:
        raise ValueError(
            f"nuevo_perfil quedó en {len(perfil_final)} caracteres tras el "
            f"recorte (mínimo {MIN_PROFILE_CHARS}) -> probablemente no llena "
            f"las 6 líneas. Se aborta antes de generar un CV con el perfil "
            f"corto; revisa el largo que devuelve Gemini."
        )
    # El párrafo del perfil es el primer párrafo NO VACÍO después del heading
    for i in range(heading_idx + 1, len(doc.paragraphs)):
        if doc.paragraphs[i].text.strip() and not _is_separator(doc.paragraphs[i]):
            _set_paragraph_text(doc.paragraphs[i], perfil_final)
            return
    raise ValueError("No encontré el párrafo del perfil debajo del heading.")


# ---------------------------------------------------------------------------
# 3. Cargo actual (solo el título del rol, la empresa/fecha quedan intactas)
# ---------------------------------------------------------------------------

def _find_exact_heading_index(doc: Document, heading_text: str) -> int:
    """Find a heading by exact visible text, never by substring.

    This is critical for "Experience": a profile beginning with "Experienced
    Account Manager..." must NOT be mistaken for the Experience section.
    """
    target = re.sub(r"\s+", " ", heading_text or "").strip().casefold()
    for i, paragraph in enumerate(doc.paragraphs):
        visible = re.sub(r"\s+", " ", paragraph.text or "").strip().casefold()
        if _is_heading(paragraph) and visible == target:
            return i
    return -1


def _find_current_role_heading_index(doc: Document) -> int:
    exp_idx = _find_exact_heading_index(doc, EXPERIENCE_HEADING)
    if exp_idx == -1:
        raise ValueError(
            f"No encontré el heading exacto '{EXPERIENCE_HEADING}'. "
            "Se aborta para no modificar otra sección del CV."
        )

    for i in range(exp_idx + 1, len(doc.paragraphs)):
        p = doc.paragraphs[i]
        # The current role itself must be a heading. Never accept a Training
        # bullet or any other paragraph that merely mentions the company.
        if _is_heading(p) and "," in p.text and (not COMPANY_ANCHOR or COMPANY_ANCHOR in p.text):
            return i

    raise ValueError(
        f"No encontré el heading del rol actual después de '{EXPERIENCE_HEADING}' "
        f"(ancla '{COMPANY_ANCHOR}')."
    )


def update_current_role_title(doc: Document, nuevo_cargo_actual: str) -> int:
    """Devuelve el índice del heading para que el caller sepa dónde
    empiezan los bullets de ese rol."""
    idx = _find_current_role_heading_index(doc)
    paragraph = doc.paragraphs[idx]
    full_text = paragraph.text
    if "," not in full_text:
        raise ValueError("El formato 'Cargo, Empresa' cambió; revisa el ancla.")
    # Todo lo que va DESPUÉS de la primera coma (empresa) queda 100% intacto
    _, _, resto = full_text.partition(",")
    # Defensa: si Gemini metió la empresa dentro de nuevo_cargo_actual
    # (ej. "Senior Manager, la empresa del CV maestro"), usar solo lo que va
    # ANTES de su propia primera coma -> evita duplicar la empresa al
    # pegarle "resto" (que ya trae la empresa real después).
    cargo_limpio = nuevo_cargo_actual.split(",")[0].strip()
    new_text = f"{cargo_limpio},{resto}"
    _set_paragraph_text(paragraph, new_text)
    return idx


# ---------------------------------------------------------------------------
# 4. Bullets del rol actual (tareas)
# ---------------------------------------------------------------------------

def _fit_tasks_to_max_lines(
    tareas: List[str],
    max_lines: int = MAX_BULLET_TOTAL_LINES,
    chars_per_line: int = BULLET_CHARS_PER_LINE,
) -> List[str]:
    """Recorta el conjunto de bullets para que, SUMADOS, no superen
    max_lines líneas renderizadas.

    En vez de repartir caracteres proporcionalmente (eso deja sumas de
    líneas redondeadas hacia arriba que se pasan del total), se reparte un
    PRESUPUESTO DE LÍNEAS por bullet: cada bullet recibe max_lines // n
    líneas, y las líneas sobrantes de la división se le dan a los bullets
    que originalmente eran más largos (para no dejarlos con una frase
    cortada de forma absurda). Cada bullet se recorta luego en la última
    palabra completa dentro de su propio presupuesto en caracteres."""
    n = len(tareas)
    if n == 0:
        return tareas

    def _lineas_estimadas(texto: str) -> int:
        return max(1, -(-len(texto) // chars_per_line))  # ceil sin importar math

    lineas_actuales = sum(_lineas_estimadas(t) for t in tareas)
    if lineas_actuales <= max_lines:
        return tareas

    base_lines = max(1, max_lines // n)
    extra_lines = max(0, max_lines - base_lines * n)
    orden_por_largo = sorted(range(n), key=lambda i: -len(tareas[i]))
    lineas_por_bullet = [base_lines] * n
    for i in orden_por_largo[:extra_lines]:
        lineas_por_bullet[i] += 1

    resultado = []
    for i, tarea in enumerate(tareas):
        tarea = tarea.strip()
        budget_chars = lineas_por_bullet[i] * chars_per_line
        if len(tarea) <= budget_chars:
            resultado.append(tarea)
            continue
        recorte = tarea[:budget_chars].rsplit(" ", 1)[0].strip()
        resultado.append(recorte or tarea[:budget_chars].strip())
    return resultado


def update_current_role_bullets(doc: Document, role_heading_idx: int, nuevas_tareas: List[str]) -> None:
    """Replace exactly the four current-role bullet placeholders, 1:1.

    Structural safety rule: this function never inserts or deletes paragraphs.
    If the anchor is wrong or the template no longer exposes exactly four
    bullet placeholders before the next role heading, abort before modifying
    anything. This prevents current-role content from leaking into Training or
    deleting bullets from historical roles.
    """
    if not (0 <= role_heading_idx < len(doc.paragraphs)):
        raise ValueError("Índice del rol actual inválido.")

    role_paragraph = doc.paragraphs[role_heading_idx]
    if not _is_heading(role_paragraph) or "," not in role_paragraph.text or (COMPANY_ANCHOR and COMPANY_ANCHOR not in role_paragraph.text):
        raise ValueError(
            "El ancla del rol actual no es un heading válido de la empresa del CV maestro. "
            "Se aborta antes de tocar bullets."
        )

    if len(nuevas_tareas) != 4:
        raise ValueError(
            f"Gemini devolvió {len(nuevas_tareas)} tareas; deben ser exactamente 4."
        )

    visual_line_counts = [_estimated_bullet_lines(task) for task in nuevas_tareas]
    too_long = [
        (idx + 1, lines)
        for idx, lines in enumerate(visual_line_counts)
        if lines > 2
    ]
    if too_long:
        detail = ", ".join(
            f"bullet {idx}: {lines} líneas"
            for idx, lines in too_long
        )
        raise ValueError(
            "Una o más tareas del rol actual exceden las 2 líneas visuales "
            f"permitidas ({detail}). Se aborta antes de generar un PDF roto."
        )

    nuevas_tareas = _fit_tasks_to_max_lines(nuevas_tareas)
    tareas_total_chars = sum(len(t) for t in nuevas_tareas)
    if tareas_total_chars < MIN_BULLET_TOTAL_CHARS:
        raise ValueError(
            f"nuevas_tareas suma {tareas_total_chars} caracteres combinados "
            f"(mínimo {MIN_BULLET_TOTAL_CHARS}) -> probablemente no llena "
            f"las 8 líneas. Se aborta antes de generar un CV con las tareas "
            f"cortas; revisa el largo que devuelve Gemini."
        )

    role_level = role_paragraph.style.name
    bullet_indices: List[int] = []

    for i in range(role_heading_idx + 1, len(doc.paragraphs)):
        p = doc.paragraphs[i]

        # The next role at the same heading level is the hard boundary.
        if _is_heading(p) and p.style.name == role_level:
            break

        if p.text.strip() and _is_bullet(p):
            bullet_indices.append(i)

    if len(bullet_indices) != 4:
        raise ValueError(
            "El bloque del rol actual no contiene exactamente 4 bullets "
            f"(encontrados: {len(bullet_indices)}). Se aborta sin insertar, "
            "borrar ni tocar bullets históricos."
        )

    # 1:1 only. No insertions, no deletions, no structural mutations.
    for bullet_idx, new_text in zip(bullet_indices, nuevas_tareas):
        _set_paragraph_text(doc.paragraphs[bullet_idx], new_text)


# ---------------------------------------------------------------------------
# 5. Normalización de layout para LibreOffice
# ---------------------------------------------------------------------------

def _normalize_sections_for_libreoffice(doc: Document) -> None:
    """Tu CV maestro tiene un salto de sección 'continuo' con un margen
    superior distinto al de la sección final. Word lo renderiza bien, pero
    LibreOffice (el que usamos para convertir a PDF, gratis y local) tiene
    un bug conocido: en ciertos casos convierte ese salto 'continuo' en un
    salto de página real, insertando una página casi vacía de la nada y
    empujando el resto del CV a una página extra.
    Como este es solo un ajuste de layout (no de contenido), unificamos el
    margen superior de todas las secciones y forzamos el tipo CONTINUOUS
    explícitamente -> LibreOffice deja de insertar la página fantasma."""
    final_top_margin = doc.sections[-1].top_margin
    for section in doc.sections:
        section.top_margin = final_top_margin
        section.start_type = WD_SECTION_START.CONTINUOUS


# ---------------------------------------------------------------------------
# 5.5 Normalización de fuentes (fix encabezado/fuentes inconsistentes)
# ---------------------------------------------------------------------------

def _normalize_fonts(doc: Document, font_name: str = DEFAULT_FONT) -> None:
    """Fuerza el mismo font en TODOS los runs y estilos base del documento.

    Por qué es necesario: si un run no trae el nombre de fuente explícito
    en su XML (<w:rFonts>), hereda el de su estilo. Word resuelve eso bien
    en pantalla, pero LibreOffice (usado en pdf_generator.py para convertir
    a PDF) a veces sustituye por "DejaVu Sans" en vez de "Arial" en esos
    runs "huérfanos" -> cambia el ancho del texto -> el encabezado
    (teléfono | email / LinkedIn) se reparte en más líneas que en el
    original hecho en Word. Forzando <w:rFonts> explícito en cada run (y
    en los estilos) se elimina la ambigüedad y el resultado es idéntico
    sin importar el motor de renderizado."""
    for style in doc.styles:
        try:
            if style.font is not None:
                style.font.name = font_name
        except AttributeError:
            # Estilos de tipo NUMBERING (viñetas/listas) no tienen .font
            continue

    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            if _has_manual_kerning(run):
                # No tocar: este run pertenece a un bloque con character
                # kerning calibrado a mano (nombre|título, o la línea de
                # teléfono | email | LinkedIn). Forzar la fuente aquí
                # rompe el ajuste y desalinea/reparte esa línea en varias.
                continue
            run.font.name = font_name
            rPr = run._r.get_or_add_rPr()
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is None:
                rFonts = rPr.makeelement(qn("w:rFonts"), {})
                rPr.append(rFonts)
            rFonts.set(qn("w:ascii"), font_name)
            rFonts.set(qn("w:hAnsi"), font_name)
            rFonts.set(qn("w:cs"), font_name)

    # Tablas (si el CV usa alguna para el layout del encabezado)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        if _has_manual_kerning(run):
                            continue
                        run.font.name = font_name
                        rPr = run._r.get_or_add_rPr()
                        rFonts = rPr.find(qn("w:rFonts"))
                        if rFonts is None:
                            rFonts = rPr.makeelement(qn("w:rFonts"), {})
                            rPr.append(rFonts)
                        rFonts.set(qn("w:ascii"), font_name)
                        rFonts.set(qn("w:hAnsi"), font_name)
                        rFonts.set(qn("w:cs"), font_name)


def _set_paragraph_mark_font_size(paragraph: Paragraph, size_pt: float) -> None:
    """Keep the bullet glyph/paragraph mark at the same size as its text."""
    pPr = paragraph._p.get_or_add_pPr()
    rPr = pPr.find(qn("w:rPr"))
    if rPr is None:
        rPr = OxmlElement("w:rPr")
        pPr.append(rPr)

    half_points = str(int(round(size_pt * 2)))
    for tag_name in ("w:sz", "w:szCs"):
        element = rPr.find(qn(tag_name))
        if element is None:
            element = OxmlElement(tag_name)
            rPr.append(element)
        element.set(qn("w:val"), half_points)


def _normalize_experience_task_font_sizes(
    doc: Document,
    size_pt: float = EXPERIENCE_TASK_FONT_SIZE_PT,
) -> None:
    """Force one font size for every task bullet inside Experience only.

    The master CV currently contains inconsistent direct run formatting:
    recent-role bullets can render at 9 pt while older roles render at 10 pt,
    and a single historical bullet can even contain both sizes in one sentence.

    We intentionally touch only List-style paragraphs between the exact
    "Experience" heading and the next heading at the same level (Education in
    the current template). Titles, dates, company descriptions, Training,
    Profile, Education and later sections remain untouched.
    """
    experience_idx = _find_exact_heading_index(doc, EXPERIENCE_HEADING)
    if experience_idx == -1:
        raise ValueError(
            f"No encontré el heading exacto '{EXPERIENCE_HEADING}' para "
            "normalizar el tamaño de las tareas."
        )

    experience_heading = doc.paragraphs[experience_idx]
    section_heading_style = experience_heading.style.name

    for i in range(experience_idx + 1, len(doc.paragraphs)):
        paragraph = doc.paragraphs[i]

        if _is_heading(paragraph) and paragraph.style.name == section_heading_style:
            break

        if not paragraph.text.strip():
            continue
        if not _is_bullet(paragraph):
            continue

        for run in paragraph.runs:
            if _has_drawing(run):
                continue
            run.font.size = Pt(size_pt)

        _set_paragraph_mark_font_size(paragraph, size_pt)


# ---------------------------------------------------------------------------
# 6. Orquestador
# ---------------------------------------------------------------------------

def apply_cv_adaptation(input_path: str, output_path: str, adaptation: dict) -> str:
    """
    adaptation: dict con las keys de models.CVAdaptation
      nuevo_titulo, nuevo_perfil, nuevo_cargo_actual, nuevas_tareas
    Devuelve output_path.
    """
    doc = Document(input_path)

    update_main_title(doc, adaptation["nuevo_titulo"])
    update_profile(doc, adaptation["nuevo_perfil"])
    role_idx = update_current_role_title(doc, adaptation["nuevo_cargo_actual"])
    update_current_role_bullets(doc, role_idx, adaptation["nuevas_tareas"])
    # Preserve the source CV fonts, section settings and historical formatting.

    doc.save(output_path)
    return output_path
