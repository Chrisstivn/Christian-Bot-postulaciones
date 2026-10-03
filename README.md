# Christian Bot postulaciones

Búsquedas para Chile sin restricciones de idioma, país, cargo, experiencia,
modalidad ni contrato. Los flujos n8n empiezan con `location=Chile`, sin
keywords, filtros de experiencia ni ventanas de fecha predefinidas.
Reemplaza esa URL por la búsqueda que quieras: sus filtros se respetan.

Gemini ya no verifica el idioma ni si alemán es requisito. Se mantienen la
deduplicación, las ofertas cerradas y los fallos de scraping/extracción.

## Credenciales locales

Copia `.env.example` a `.env` y completa tu propia clave
`VERTEX_EXPRESS_API_KEY` y las credenciales de tus bases de datos.
No subas `.env` ni sus copias de respaldo. El ejemplo no contiene claves
ni contraseñas y usa `SUBMIT_APPLICATIONS=false` mientras se adapta el CV.

## Filtros opcionales

`job_quality.py` admite estas variables de entorno, todas vacías por defecto:

- `SEARCH_EXCLUDED_TITLE_REGEX`: expresión regular de cargos a excluir.
- `SEARCH_ALLOWED_WORK_FORMATS`: valores separados por coma (`FULLY_REMOTE`,
  `HYBRID`, `ONSITE`, `UNKNOWN`).
- `SEARCH_EXCLUDED_CONTRACT_TYPES`: valores separados por coma (`TEMPORARY`,
  `PERMANENT`, `UNKNOWN`).

No hay filtro salarial activo. Para añadirlo en Chile hay que definir CLP,
período mensual/anual y qué hacer si no se publica sueldo. Los helpers en EUR
heredados no intervienen en la búsqueda.

## Archivos para adaptar el CV y candidato

La copia todavía contiene el CV y datos de Christian. Antes de postular como
Christian hay que revisar:

| Archivo | Cambio necesario |
| --- | --- |
| `Christian_CV.docx` | Nuevo Word maestro con secciones de Christian. |
| `main.py` | Ruta del Word, empresa y fechas reales del rol. |
| `cv_maestro_cache.txt` | Se creará localmente desde el Word nuevo. |
| `docx_adapter.py` | Nombre, encabezados, empresa/ancla del rol y límites de diseño. |
| `gemini_service.py` | Prompts y validaciones: ya genera en español, pero conserva los límites del diseño anterior. |
| `cv_date_guard.py` | Sustituir las fechas heredadas por las reales de Christian. |
| `pdf_generator.py` | Revisar el nombre del archivo PDF. |
| `candidate_bible.yaml` | Datos, experiencia, idiomas y preferencias reales. |
| `form_filler.py` | Datos personales, ubicación, renta y contexto de Chile. |
| `models.py` | Cambiar `CVAdaptation` si se reescriben secciones distintas. |

El CV generado y las respuestas de postulación se redactan en español.
El template está pendiente y los datos del candidato están vacíos.


## Estado de esta versión pública

Esta versión comienza con un historial nuevo y sin CV, PDFs generados, datos
personales, cookies, archivos de trabajo ni claves del candidato anterior.
Antes de generar/postular, añade tu Word maestro `Christian_CV.docx`, completa
`candidate_bible.yaml` y las variables `APPLICANT_*`, y adapta las secciones,
empresa y fechas reales. Configura `CV_CURRENT_COMPANY` y `CV_CURRENT_DATES`
con los valores exactos del nuevo Word.

El archivo Word no se incluye para mantener privados los datos personales.
El adaptador y guardarraíl de fechas todavía deben ajustarse al template
nuevo. La búsqueda puede configurarse mientras se prepara ese CV.

Instala Python y LibreOffice; crea un entorno virtual e instala
`pip install -r requirements.txt` y `playwright install chromium`.
Si necesitas las dependencias JavaScript, ejecuta `npm install`.
`node_modules` se regenera localmente y no se publica.

No subas CV, documentos personales, capturas ni `.env` al repositorio público.
