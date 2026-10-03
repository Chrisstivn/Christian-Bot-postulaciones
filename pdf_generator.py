r"""
pdf_generator.py
Conversión docx -> pdf usando MICROSOFT WORD REAL vía PowerShell desde WSL.

Por qué se cambió de LibreOffice a Word:
  LibreOffice re-renderiza el documento con su propio motor de layout
  (fuentes, kerning, saltos de página, márgenes) y eso hacía que el PDF
  final se viera distinto al .docx original, sobre todo en el encabezado.
  Word real produce un PDF idéntico al docx porque usa el mismo motor que
  lo escribió.

Cómo funciona (WSL -> Windows -> WSL):
  1. Python (corriendo en WSL/Ubuntu) recibe la ruta del .docx, que vive en
     algo como /home/candidate/mi_proyecto_gemini/work/CV_xxx.docx
  2. Convertimos esa ruta a su equivalente UNC de Windows con `wslpath -w`,
     que da algo como:
     \\wsl.localhost\Ubuntu\home\candidate\mi_proyecto_gemini\work\CV_xxx.docx
     (esto es automático, no importa tu usuario ni el nombre de la distro).
  3. Ejecutamos `powershell.exe` (accesible desde WSL por la interop nativa)
     apuntando a un script .ps1 que vive en Windows (C:\Scripts\word_to_pdf.ps1
     por defecto, configurable con la env var WORD_TO_PDF_SCRIPT).
  4. Ese script abre Word en segundo plano, hace SaveAs a PDF, cierra Word.
  5. Como la ruta de salida también es la UNC de la MISMA carpeta de WSL,
     el PDF queda escrito directamente donde Python lo espera, sin copiar
     nada a mano.

Requisitos en Windows (una sola vez):
  1. Crear la carpeta C:\Scripts
  2. Guardar ahí word_to_pdf.ps1 con este contenido:

     param(
         [string]$DocxPath,
         [string]$PdfPath
     )
     $word = New-Object -ComObject Word.Application
     $word.Visible = $false
     $doc = $word.Documents.Open($DocxPath)
     $wdFormatPDF = 17
     $doc.SaveAs([ref]$PdfPath, [ref]$wdFormatPDF)
     $doc.Close()
     $word.Quit()
     [System.Runtime.Interopservices.Marshal]::ReleaseComObject($doc) | Out-Null
     [System.Runtime.Interopservices.Marshal]::ReleaseComObject($word) | Out-Null

  3. Tener Microsoft Word instalado en Windows (con licencia activa).
  4. Interop de WSL habilitado (viene activado por defecto; permite llamar
     powershell.exe desde Ubuntu).

Variables de entorno opcionales:
  WORD_TO_PDF_SCRIPT   Ruta Windows del .ps1 (default: C:\\Scripts\\word_to_pdf.ps1)
  PDF_ENGINE            "word" (default) o "libreoffice" como fallback si
                        corres esto en un Ubuntu sin acceso a Windows/Word
                        (ej. un servidor Linux puro, sin WSL).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

OUTPUT_DIR = Path("output_pdfs")
OUTPUT_DIR.mkdir(exist_ok=True)

PDF_ENGINE = os.environ.get("PDF_ENGINE", "word").strip().lower()
WORD_TO_PDF_SCRIPT = os.environ.get("WORD_TO_PDF_SCRIPT", r"C:\Scripts\word_to_pdf.ps1")

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

    subprocess.run(
        [
            "powershell.exe",
            "-ExecutionPolicy", "Bypass",
            "-File", WORD_TO_PDF_SCRIPT,
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
            f"Revisa que Word esté instalado y que {WORD_TO_PDF_SCRIPT} "
            f"exista en Windows."
        )


def docx_to_pdf_via_libreoffice(docx_path: str, out_dir: Path) -> Path:
    """Fallback opcional (PDF_ENGINE=libreoffice) para cuando esto corre en
    un Linux sin Windows/Word disponible (ej. servidor de producción)."""
    subprocess.run(
        [
            "soffice", "--headless", "--convert-to", "pdf",
            "--outdir", str(out_dir), docx_path,
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
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cv_pdf = docx_to_pdf_via_libreoffice(adapted_docx_path, Path(tmp))
            shutil.copyfile(cv_pdf, final_path)
    else:
        # Word escribe DIRECTO en la ruta final (misma carpeta que ve WSL),
        # no hace falta copiar nada después.
        docx_to_pdf_via_word(adapted_docx_path, str(final_path.resolve()))

    return str(final_path)
