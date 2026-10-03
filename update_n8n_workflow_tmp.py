import json
from pathlib import Path

src = Path("N8N Job Application Automation - PRO PIPELINE V2 (con revisión manual) (4).json")
out = Path("N8N Job Application Automation - PRO PIPELINE V2 - PDF AUTOFILL SEPARADO.json")
data = json.loads(src.read_text(encoding="utf-8"))
data["name"] = "Job Application Automation - PRO PIPELINE V2 - PDF + Autofill Separados"


def node(name: str) -> dict:
    return next(n for n in data["nodes"] if n.get("name") == name)


schedule = node("Schedule (Buscar en LinkedIn)")
schedule["parameters"] = {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}}
schedule["notes"] = "Corre automatico cada 30 minutos. Mantiene batch de 25 y el backend combina front + backlog con dedup."

search_params = node("Search Params1")
assignments = search_params["parameters"]["assignments"]["assignments"]
if not any(a.get("name") == "backlog_pages" for a in assignments):
    assignments.append({
        "id": "backlog_pages_field",
        "name": "backlog_pages",
        "value": 2,
        "type": "number",
    })

call_search = node("Call LinkedIn Search1")
call_search["parameters"]["jsonBody"] = "={{ { search_url: $json.search_url, max_jobs: $json.max_jobs, backlog_pages: $json.backlog_pages || 2 } }}"
call_search["notes"] = "El backend revisa start=0 para nuevas + backlog paginado. Usa new_job_urls para no mandar duplicados al triage."

split_urls = node("Split URLs1")
split_urls["parameters"]["jsCode"] = """const urls = $input.first().json.new_job_urls || [];
return urls.map(u => ({ json: { url: u } }));"""
split_urls["notes"] = "Procesa solo URLs nuevas segun dedup del backend. No usa job_urls porque incluye URLs ya vistas."

create_pdf = node("Call Backend (PRO)1")
create_pdf["name"] = "1 - Create PDF (PRO)1"
create_pdf["parameters"]["url"] = "http://host.docker.internal:8000/create-application-pdf"
create_pdf["parameters"]["jsonBody"] = '={{ { url: $json.real_apply_url, job_text: $json.description || $json.job_text || "", from_sheet: true } }}'
create_pdf["position"] = [-752, -1728]
create_pdf["retryOnFail"] = True
create_pdf["notes"] = "Solo crea CV/DOCX/PDF y respuestas. Si falla autofill despues, este nodo NO se reejecuta al reintentar solo autofill."

pdf_ready_if = {
    "parameters": {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 1},
            "conditions": [{
                "id": "cond_pdf_ready",
                "leftValue": "={{ $json.status }}",
                "rightValue": "pdf_ready",
                "operator": {"type": "string", "operation": "equals"},
            }],
            "combinator": "and",
        },
        "options": {},
    },
    "id": "a7b2d55c-75df-42be-971a-6ecdbd819d88",
    "name": "PDF Ready? (PRO)1",
    "type": "n8n-nodes-base.if",
    "typeVersion": 2.2,
    "position": [-560, -1728],
    "notes": "Solo ejecuta autofill cuando el PDF ya existe. Si el backend descarta la oferta, pasa directo al Switch existente.",
}
if not any(n.get("name") == pdf_ready_if["name"] for n in data["nodes"]):
    data["nodes"].append(pdf_ready_if)

autofill = {
    "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:8000/autofill-application",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": '={{ { url: $("Filter Ready1").item.json.real_apply_url, pdf_path: $json.pdf_path, application_id: $json.application_id || $json.ID, company: $json.company || "", job_title: $json.job_title || "", expected_salary: $json.expected_salary || null, responses: $json.responses || [], motivation_answer: $json.motivation_answer || "", experience_answer: $json.experience_answer || "", job_context: $json.job_context || "" } }}',
        "options": {},
    },
    "id": "f933bff9-f5ea-4d2f-9063-efcf45bd1ad0",
    "name": "2 - Autofill Existing PDF (PRO)1",
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4,
    "position": [-352, -1696],
    "retryOnFail": True,
    "notes": "Reintenta solo este nodo si falla Playwright/CAPTCHA/ATS. Reutiliza pdf_path creado por el nodo anterior.",
}
if not any(n.get("name") == autofill["name"] for n in data["nodes"]):
    data["nodes"].append(autofill)

node("Switch Status1")["position"] = [-144, -1696]
node("Save Discarded1")["position"] = [80, -1888]
node("Save Success1")["position"] = [80, -1488]

connections = data["connections"]
connections["Filter Ready1"] = {"main": [[{"node": "1 - Create PDF (PRO)1", "type": "main", "index": 0}]]}
connections.pop("Call Backend (PRO)1", None)
connections["1 - Create PDF (PRO)1"] = {"main": [[{"node": "PDF Ready? (PRO)1", "type": "main", "index": 0}]]}
connections["PDF Ready? (PRO)1"] = {
    "main": [
        [{"node": "2 - Autofill Existing PDF (PRO)1", "type": "main", "index": 0}],
        [{"node": "Switch Status1", "type": "main", "index": 0}],
    ]
}
connections["2 - Autofill Existing PDF (PRO)1"] = {"main": [[{"node": "Switch Status1", "type": "main", "index": 0}]]}

success = node("Save Success1")
success_values = success["parameters"]["columns"]["value"]
success_values["Status"] = "DONE"
success_values["ID"] = '={{ $("1 - Create PDF (PRO)1").item.json.ID || $("1 - Create PDF (PRO)1").item.json.application_id }}'
success_values["company"] = '={{ $("1 - Create PDF (PRO)1").item.json.company || $json.company }}'
success_values["job_title"] = '={{ $("1 - Create PDF (PRO)1").item.json.job_title || $json.job_title }}'
success_values["pdf_name"] = '={{ (($("1 - Create PDF (PRO)1").item.json.pdf_path || "").split("/").pop()) || $("1 - Create PDF (PRO)1").item.json.pdf_name || "" }}'
success_values["download_url"] = '={{ "http://host.docker.internal:8000/download-pdf/" + ((($("1 - Create PDF (PRO)1").item.json.pdf_path || "").split("/").pop()) || $("1 - Create PDF (PRO)1").item.json.pdf_name || "") }}'
success_values["autofill_status"] = '={{ ($json.autofill && $json.autofill.status) || $json.status || "" }}'
success_values["screenshot"] = '={{ ($json.autofill && $json.autofill.screenshot) || "" }}'

schema = success["parameters"]["columns"].setdefault("schema", [])
existing_schema_ids = {c.get("id") for c in schema}
for col in ["autofill_status", "screenshot"]:
    if col not in existing_schema_ids:
        schema.append({
            "id": col,
            "displayName": col,
            "required": False,
            "defaultMatch": False,
            "display": True,
            "type": "string",
            "canBeUsedToMatch": True,
            "removed": False,
        })

discarded = node("Save Discarded1")
discard_values = discarded["parameters"]["columns"]["value"]
discard_values["company"] = '={{ $json.company || $("1 - Create PDF (PRO)1").item.json.company || "" }}'
discard_values["job_title"] = '={{ $json.job_title || $("1 - Create PDF (PRO)1").item.json.job_title || "" }}'
discard_values["reason"] = '={{ $json.reason || $json.detail || ($json.autofill && $json.autofill.detail) || "" }}'

out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(out)
print(len(data["nodes"]), "nodes")
