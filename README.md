# Christian Bot postulaciones

Búsquedas para Chile con filtros de empresa, modalidad, cargo y experiencia.
La URL de búsqueda y sus filtros siguen siendo editables; no se descarta por
idioma ni nacionalidad del empleador si cumple estas reglas:

| Regla | Sectores generales | Minería, faenas y servicios mineros dedicados |
| --- | --- | --- |
| Tamaño | Mediana o grande (50+ empleados por defecto) | Mismo requisito |
| Operaciones | Multinacional | Chilena o extranjera; no necesita ser multinacional |
| Modalidad | Híbrida o remota | También presencial o en faena |
| Prácticas | Excluidas | Excluidas |
| Cargo | Sin director, gerente, gerencia ni vicepresidente/VP | Mismas exclusiones |
| Experiencia obligatoria | Máximo 4 años | Mismo límite |

Project Manager, junior y trainee no se excluyen por esas palabras solas.
Un rango de 3–5 años admite candidatos con 3: pasa este filtro. Una exigencia
mínima de 5+ o más de 4 años se descarta; experiencia solo deseable no se usa
como mínimo. La excepción minera necesita evidencia del sector o del trabajo
en faena, no una mención casual de clientes o experiencia minera deseable.

La misma llamada de Gemini que extrae la oferta recoge citas para los filtros.
No adivina tamaño o multinacionalidad por reputación, nombre o país de origen.
Si faltan datos para resolver una regla, la oferta queda en `FILTER_REVIEW`
en el Excel, con el motivo en `reason` y `Revisar`. No se trata como aceptada
ni se prepara un PDF automáticamente. Los rechazos guardan motivos específicos.
La reconciliación de la cola conserva también las ofertas de revisión.

`SEARCH_POLICY=christian` es el valor activo por defecto.
`SEARCH_MIN_COMPANY_EMPLOYEES=50` permite ajustar el umbral del tamaño.
`SEARCH_POLICY=unrestricted` desactiva explícitamente esta política si alguna
vez se necesita. Las exclusiones opcionales adicionales siguen disponibles.

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

Además de los límites de caracteres, el PDF convertido se comprueba con
sus líneas reales: encabezado de dos líneas, perfil de seis y máximo dos
por tarea. Si falla, Gemini recibe una única reparación dirigida con el CV
maestro; se convierte de nuevo y se comprueba. Si persiste, responde 422 y
no entrega el PDF como listo. La plantilla debe ocupar dos páginas y la segunda
debe empezar con Habilidades; se bloquea cualquier desbordamiento de Experiencia.
Esto no sustituye la revisión visual final. En LibreOffice se explicita el alto
de línea en una copia temporal para evitar que sus métricas automáticas creen
una tercera página. El Word maestro, sus fuentes y sus saltos de sección se conservan.

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

### QA de conversión y scraping

El flujo inicia LinkedIn mediante `POST /linkedin-search-tasks` (202 con
`task_id`) y consulta `GET /linkedin-search-tasks/{task_id}` cada diez segundos.
Así no mantiene una petición abierta durante todo el scraping y triage de
Gemini. Los estados son queued, running, completed y failed. Solo completed
entrega ofertas a Excel y avanza a la siguiente búsqueda; failed muestra el
error del backend. Las peticiones usan timeout de 30 segundos y tres intentos.
Un reintento del inicio reutiliza la tarea activa o su resultado reciente.

Ejecuta un único proceso de uvicorn, sin `--workers` ni `--reload`: el trabajador
serializa las búsquedas y guarda sus estados/resultados en
`work/linkedin_search_runs.sqlite3`. Si reinicias durante una búsqueda, se
marca interrumpida; las ofertas descubiertas siguen en la cola PostgreSQL y
puedes iniciar de nuevo para procesar las pendientes. Actualiza backend y
JSON juntos. n8n Cloud necesita la URL pública del túnel; 127.0.0.1 solo sirve
cuando n8n y el backend corren en el mismo equipo.

La revisión real encontró y corrigió una diferencia entre el nombre del PDF
creado y el enviado a n8n: los paréntesis de las iniciales se conservan.
La carga de idiomas separa C1 de sus notas para que el autofill reconozca
la fluidez, y los cargos actuales se consultan en los datos del Word.
La normalización de modalidad reconoce también etiquetas españolas.
`requirements.txt` incluye el adaptador PostgreSQL, el módulo de stealth
usado por los fallbacks y el lector de PDF usado por el guardarraíl visual.

La revisión completa incluye pruebas antiguas del proyecto anterior que
esperan filtros, idiomas y nodos de Google Sheets ya eliminados. Las pruebas
actuales del flujo de Christian son las de `test_christian*`,
`test_search_filters`, `test_final_qa_regressions`, `test_cv*`,
`test_linkedin_job_scrape_guard`, `test_job_extraction_shape` y
`test_excel_output`. Validar Gemini real exige ADC en la laptop.

### Revisión de filtros y filas existentes

Los años obligatorios se comprueban también en la descripción completa,
independientemente de la cita seleccionada por Gemini. Un requisito de más de
tres años se descarta incluso en minería. La preferencia por un sector
(por ejemplo, «15 años de experiencia, preferentemente minería») no convierte
los años en deseables. Se mantienen los mínimos de rangos como 3–5 años.

Cuando la oferta contiene el enlace del empleador, se consulta su sección
pública About de LinkedIn para aportar evidencia de tamaño y operaciones.
Cada perfil se consulta una vez por proceso, con timeout de diez segundos y
sin seguir redirecciones de login. Si LinkedIn bloquea el perfil o la sección
no contiene el dato, sigue siendo FILTER_REVIEW; no se inventan hechos.
El perfil no se utiliza para experiencia, cargo ni modalidad del puesto.

Actualizar el código no cambia automáticamente filas ya guardadas. Una vez
terminada la búsqueda activa, pausa n8n y ejecuta desde el repositorio, con
el entorno virtual y las mismas variables de entorno del backend:

```bash
python recheck_search_filters.py --apply
```

El comando guarda un respaldo junto al Excel antes de actualizar. Revisa solo
NEW/FILTER_REVIEW cuyo registro de cola sigue en ready_for_review/filter_review.
Actualiza PostgreSQL y Excel: KEEP → NEW, REVIEW → FILTER_REVIEW y REJECT →
DISCARDED. Conserva enlaces, otras columnas y filas ya procesadas. No genera
PDFs ni envía postulaciones. Sin `--apply` muestra resultados sin guardar,
aunque puede hacer consultas públicas y llamadas a Gemini. Reinicia el backend
con el nuevo código antes de reanudar n8n. El JSON del workflow no cambia.

La política actual exige un mínimo obligatorio de experiencia de 3 años o menos
y estudios que acepten Ingeniería Civil Industrial o Ingeniería Industrial.
Se reconocen tildes y abreviaturas (Ing., Ing Civil Industrial, Civil Industrial).
Una carrera distinta con «afín/similar» queda pendiente si no se confirma
compatibilidad. Negratín se excluye explícitamente por preferencia del candidato.
