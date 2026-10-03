# Christian Bot postulaciones

Búsquedas para Chile sin restricciones de idioma, país, cargo, experiencia,
modalidad ni contrato. Los flujos n8n empiezan con `location=Chile`, sin
keywords, filtros de experiencia ni ventanas de fecha predefinidas.
Reemplaza esa URL por la búsqueda que quieras: sus filtros se respetan.

Gemini ya no verifica el idioma ni si alemán es requisito. Se mantienen la
deduplicación, las ofertas cerradas y los fallos de scraping/extracción.

## Excel de resultados local

Importa **`n8n_christian_postulaciones.json`** en n8n: es la adaptación del flujo
probado `Job Application Automation (7)`, con sus bucles y conexiones conservados,
búsqueda para Chile y salida al Excel local. Se quitó la recuperación puntual
`EXACT80` y las referencias a credenciales y planillas anteriores.
Se importa desactivado; configúralo y adapta el CV antes de activarlo.
El horario del flujo usa `America/Santiago` y conserva el intervalo de tres horas.
En `Search Params`, cambia `search_urls_raw` para añadir las búsquedas que prefieras,
una URL por línea.

Los flujos n8n de este repositorio leen y escriben `data/postulaciones.xlsx`,
en la pestaña `Postulaciones`, mediante el backend. Ya no usan el Google Sheets
del proyecto anterior ni sus credenciales. El archivo se crea automáticamente
al iniciar el backend. También puedes crearlo antes con `python excel_output.py`,
después de instalar `pip install -r requirements.txt`.

Importa de nuevo el JSON del flujo que uses en n8n y reinicia el backend para
activar esta salida. Las URLs usan `http://host.docker.internal:8000` como los
demás nodos: si n8n corre directamente en la laptop, usa `http://localhost:8000`.

Abre el archivo en Excel o LibreOffice para revisar ofertas y cambiar `Status`
de `NEW` a `READY` cuando quieras procesarlas. Guarda y cierra el archivo antes
de ejecutar el bot. Las columnas `Link`, `real_apply_url` y `Status` conservan sus
nombres para que los filtros del flujo sigan funcionando. Los resultados nuevos
se agregan sin resetear el estado de ofertas existentes; los resultados finales
actualizan la fila correspondiente.

Puedes descargarlo en `http://localhost:8000/excel-output/download` o cambiar
su ubicación con `OUTPUT_EXCEL_PATH`. El Excel y su archivo de bloqueo están
excluidos de Git; cada clon crea su propio archivo vacío sin publicar resultados.

### Configuración de credenciales

Copia `.env.example` a `.env`, configura `GOOGLE_CLOUD_PROJECT` y
`GOOGLE_CLOUD_LOCATION`, autentica Vertex AI con Application Default Credentials
y completa las credenciales de tus bases de datos.
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

## CV de Christian y adaptación con Gemini

Guarda localmente el Word actualizado como `Christian_CV.docx` en la raíz del
proyecto, o configura su ruta con `CV_MAESTRO_DOCX`. El Word y los PDFs siguen
excluidos de Git. Completa los datos de contacto en tus variables `APPLICANT_*`
o en tu copia local de `candidate_bible.yaml`.

La fuente de hechos del candidato se completa en memoria desde el Word local:
identidad, contacto, experiencia, fechas, estudios, certificados, herramientas
e idiomas. No escribe esos datos en el YAML versionado ni publica el Word.
El CV maestro prevalece ante contradicciones con datos anteriores. Autorización
laboral, disponibilidad, años totales y preferencias no documentadas quedan
sin completar y requieren información proporcionada por el candidato.

Gemini genera en español y conserva los límites: título de 39–45 caracteres,
perfil de 555–635, cuatro tareas de 140–200 cada una y 700–800 en total. La
reparación dirigida recibe el CV maestro y la fuente de hechos del candidato,
además de la oferta. No debe añadir habilidades que solo aparezcan en la oferta.
La estrategia conserva una generación, reparación dirigida y un único intento
completo adicional cuando sea necesario.

El adaptador reconoce `Sobre mí`, `Experiencia`, la línea decorativa y las
viñetas con numeración de Word aunque tengan estilo Normal. Solo cambia título,
perfil y el primer rol de la sección Experiencia; preserva foto, fuentes, secciones y cargos
anteriores. El backend lee el Word actual en cada generación, sin reutilizar
un caché antiguo. Empresa y fechas se obtienen del Word; las variables
`CV_CURRENT_COMPANY` y `CV_CURRENT_DATES`, si están definidas, deben coincidir.
Todos los rangos de fechas laborales se comprueban después de adaptar.

La estimación de líneas conserva el control previo, pero no sustituye la
revisión visual del PDF con las fuentes instaladas en la laptop. El documento
maestro actual ocupa tres páginas; esta actualización no rediseña su paginación.

### Verificación local

```bash
python -m unittest discover -s tests -p 'test_cv*.py' -v
python -m unittest discover -s tests -p 'test_christian*.py' -v
```

Las pruebas del Word real necesitan `Christian_CV.docx`; se omiten cuando no
está disponible. Las pruebas de prompts usan respuestas simuladas de Gemini,
sin credenciales ni llamadas pagadas. Otras pruebas heredadas de filtros,
Google Sheets y preferencias del candidato anterior siguen pendientes de
migración al flujo de Christian.

Instala las dependencias con `pip install -r requirements.txt` y
`playwright install chromium`. Para PDF usa Word en Windows/WSL con
`WORD_TO_PDF_SCRIPT`, o `PDF_ENGINE=libreoffice` con LibreOffice y las fuentes
del documento instaladas. Reinicia el backend después de actualizar el código.
