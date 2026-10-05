r"""
Conversión DOCX a PDF con Word real desde WSL o LibreOffice en Linux.

Word usa el script incluido scripts/word_to_pdf.ps1. wslpath convierte las
rutas del documento, PDF y script a rutas Windows; no hace falta crear
C:\Scripts. Se requiere Microsoft Word instalado en Windows e interop WSL.
WORD_TO_PDF_SCRIPT permite indicar un script Windows propio; vacío usa el
script incluido. PDF_ENGINE=libreoffice activa el motor alternativo.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

OUTPUT_DIR = Path("output_pdfs")
OUTPUT_DIR.mkdir(exist_ok=True)

PDF_ENGINE = os.environ.get("PDF_ENGINE", "word").strip().lower()
WORD_TO_PDF_SCRIPT = os.environ.get("WORD_TO_PDF_SCRIPT", "").strip()
BUNDLED_WORD_SCRIPT = Path(__file__).resolve().parent / "scripts" / "word_to_pdf.ps1"

# Words/markers that should not create meaningless filename initials.
_PDF_TITLE_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "with",
    "all", "genders", "m", "f", "d", "w",
}


def job_title_initials(job_title: str) -> str:
    """Return stable uppercase initials for the advertised job title.

    Examples:
      Data Analyst Recommerce -> DAR
      Product Data Analyst -> PDA
      Performance Marketing Manager (m/f/d) -> PMM
    """
    initials: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", job_title or ""):
        if token.lower() in _PDF_TITLE_STOPWORDS:
            continue
        # One initial per meaningful title word. This keeps filenames short
        # and deterministic even when the title contains acronyms.
        initials.append(token[0].upper())
    return "".join(initials) or "ROLE"


def build_pdf_name(company: str, job_title: str) -> str:
    safe_company = re.sub(r"[^A-Za-z0-9]+", "", company or "") or "Company"
    return f"CV_Christian_{safe_company}_({job_title_initials(job_title)}).pdf"


def _wsl_to_windows_path(wsl_path: str) -> str:
    """Convierte una ruta de WSL (/home/usuario/...) a su UNC de Windows
    (\\\\wsl.localhost\\Ubuntu\\home\\usuario\\...) usando `wslpath`, que ya
    viene instalado en cualquier WSL. Esto evita hardcodear usuario/distro."""
    result = subprocess.run(
        ["wslpath", "-w", str(Path(wsl_path).resolve())],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def docx_to_pdf_via_word(docx_path: str, pdf_path: str) -> None:
    """Convierte docx -> pdf abriendo Word REAL en Windows desde WSL."""
    windows_docx = _wsl_to_windows_path(docx_path)
    windows_pdf = _wsl_to_windows_path(pdf_path)
    script = WORD_TO_PDF_SCRIPT or _wsl_to_windows_path(str(BUNDLED_WORD_SCRIPT))

    subprocess.run(
        [
            "powershell.exe",
            "-ExecutionPolicy", "Bypass",
            "-File", script,
            "-DocxPath", windows_docx,
            "-PdfPath", windows_pdf,
        ],
        check=True,
        timeout=90,
    )

    if not Path(pdf_path).exists():
        raise RuntimeError(
            f"Word no generó el PDF esperado en {pdf_path} "
            f"(docx enviado: {windows_docx}, pdf esperado: {windows_pdf}). "
            f"Revisa que Word esté instalado y que {script} "
            f"exista en Windows."
        )


def _prepare_libreoffice_cv(docx_path: str, output_path: Path) -> None:
    """Make Word's compact Spanish CV line heights explicit for LibreOffice.

    LibreOffice expands automatic lines using fallback/empty-run metrics,
    overflowing Experience before the template's next-page section break.
    Only the conversion copy is changed; text, fonts and section breaks stay.
    """
    from docx import Document
    from docx.enum.text import WD_LINE_SPACING
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = Document(docx_path)
    headings = {p.text.strip() for p in doc.paragraphs}
    if {'Sobre mí', 'Experiencia', 'Habilidades'} <= headings:
        for paragraph in doc.paragraphs:
            if paragraph.style.name.lower().startswith('heading') or paragraph.style.name == 'Title':
                continue
            if paragraph.paragraph_format.line_spacing_rule in (WD_LINE_SPACING.EXACTLY, WD_LINE_SPACING.AT_LEAST):
                continue
            sizes = paragraph._p.xpath('./w:pPr/w:rPr/w:sz')
            if sizes:
                size = int(sizes[0].get(qn('w:val'))) / 2
            else:
                run_sizes = [r.font.size.pt for r in paragraph.runs if r.text and r.font.size]
                size = run_sizes[0] if run_sizes else 11
            paragraph.paragraph_format.line_spacing = Pt(size * 1.15)
    doc.save(output_path)


def docx_to_pdf_via_libreoffice(docx_path: str, out_dir: Path) -> Path:
    """Fallback opcional (PDF_ENGINE=libreoffice) para cuando esto corre en
    un Linux sin Windows/Word disponible (ej. servidor de producción)."""
    out_dir = out_dir.resolve()
    with tempfile.TemporaryDirectory() as temp:
        conversion_copy = Path(temp) / Path(docx_path).name
        _prepare_libreoffice_cv(docx_path, conversion_copy)
        subprocess.run(
            [
                "soffice", f"-env:UserInstallation={(Path(temp) / 'profile').as_uri()}",
                "--headless", "--convert-to", "pdf",
                "--outdir", str(out_dir), str(conversion_copy),
            ],
            check=True,
            timeout=60,
        )
    pdf_path = out_dir / (Path(docx_path).stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"LibreOffice no generó el PDF esperado en {pdf_path}")
    return pdf_path


def build_final_pdf(
    adapted_docx_path: str,
    pdf_name: str,
    **_ignored,  # acepta y descarta company/job_title/motivation_answer/etc.
) -> str:
    """Convierte el CV adaptado a PDF y lo deja en OUTPUT_DIR con el nombre
    final (CV_Christian_{Company}_{JobTitleInitials}.pdf). Sin páginas extra."""
    final_path = OUTPUT_DIR / pdf_name

    if PDF_ENGINE == "libreoffice":
        with tempfile.TemporaryDirectory() as tmp:
            cv_pdf = docx_to_pdf_via_libreoffice(adapted_docx_path, Path(tmp))
            shutil.copyfile(cv_pdf, final_path)
    else:
        # Word escribe DIRECTO en la ruta final (misma carpeta que ve WSL),
        # no hace falta copiar nada después.
        docx_to_pdf_via_word(adapted_docx_path, str(final_path.resolve()))

    return str(final_path)
