$Root = "\\wsl.localhost\Ubuntu\home\candidate\mi_proyecto_gemini"
$Src = (Get-ChildItem -LiteralPath $Root -Filter 'N8N Job Application Automation - PRO PIPELINE V2*.json' | Where-Object { $Root = "\\wsl.localhost\Ubuntu\home\candidate\mi_proyecto_gemini"
$Src = Join-Path $Root "N8N Job Application Automation - PRO PIPELINE V2 (con revisiÃ³n manual) (4).json"
$Out = Join-Path $Root "N8N Job Application Automation - PRO PIPELINE V2 - PDF AUTOFILL SEPARADO.json"

$Data = Get-Content -Raw -LiteralPath $Src | ConvertFrom-Json
$Data.name = "Job Application Automation - PRO PIPELINE V2 - PDF + Autofill Separados"

function Get-NodeByName {
    param([string]$Name)
    return @($Data.nodes | Where-Object { $_.name -eq $Name })[0]
}

$Schedule = Get-NodeByName "Schedule (Buscar en LinkedIn)"
$Schedule.parameters = [pscustomobject]@{
    rule = [pscustomobject]@{
        interval = @(
            [pscustomobject]@{
                field = "minutes"
                minutesInterval = 30
            }
        )
    }
}
$Schedule.notes = "Corre automatico cada 30 minutos. Mantiene batch de 25 y el backend combina front + backlog con dedup."

$SearchParams = Get-NodeByName "Search Params1"
$Assignments = @($SearchParams.parameters.assignments.assignments)
if (-not @($Assignments | Where-Object { $_.name -eq "backlog_pages" })) {
    $Assignments += [pscustomobject]@{
        id = "backlog_pages_field"
        name = "backlog_pages"
        value = 2
        type = "number"
    }
    $SearchParams.parameters.assignments.assignments = $Assignments
}

$CallSearch = Get-NodeByName "Call LinkedIn Search1"
$CallSearch.parameters.jsonBody = '={{ { search_url: $json.search_url, max_jobs: $json.max_jobs, backlog_pages: $json.backlog_pages || 2 } }}'
$CallSearch.notes = "El backend revisa start=0 para nuevas + backlog paginado. Usa new_job_urls para no mandar duplicados al triage."

$SplitUrls = Get-NodeByName "Split URLs1"
$SplitUrls.parameters.jsCode = "const urls = `$input.first().json.new_job_urls || [];`nreturn urls.map(u => ({ json: { url: u } }));"
$SplitUrls.notes = "Procesa solo URLs nuevas segun dedup del backend. No usa job_urls porque incluye URLs ya vistas."

$CreatePdf = Get-NodeByName "Call Backend (PRO)1"
$CreatePdf.name = "1 - Create PDF (PRO)1"
$CreatePdf.parameters.url = "http://host.docker.internal:8000/create-application-pdf"
$CreatePdf.parameters.jsonBody = '={{ { url: $json.real_apply_url, job_text: $json.description || $json.job_text || "", from_sheet: true } }}'
$CreatePdf.position = @(-752, -1728)
$CreatePdf.retryOnFail = $true
$CreatePdf.notes = "Solo crea CV/DOCX/PDF y respuestas. Si falla autofill despues, este nodo NO se reejecuta al reintentar solo autofill."

$ExistingPdfReady = @($Data.nodes | Where-Object { $_.name -eq "PDF Ready? (PRO)1" })
if (-not $ExistingPdfReady) {
    $Data.nodes += [pscustomobject]@{
        parameters = [pscustomobject]@{
            conditions = [pscustomobject]@{
                options = [pscustomobject]@{
                    caseSensitive = $true
                    leftValue = ""
                    typeValidation = "strict"
                    version = 1
                }
                conditions = @(
                    [pscustomobject]@{
                        id = "cond_pdf_ready"
                        leftValue = '={{ $json.status }}'
                        rightValue = "pdf_ready"
                        operator = [pscustomobject]@{
                            type = "string"
                            operation = "equals"
                        }
                    }
                )
                combinator = "and"
            }
            options = [pscustomobject]@{}
        }
        id = "a7b2d55c-75df-42be-971a-6ecdbd819d88"
        name = "PDF Ready? (PRO)1"
        type = "n8n-nodes-base.if"
        typeVersion = 2.2
        position = @(-560, -1728)
        notes = "Solo ejecuta autofill cuando el PDF ya existe. Si el backend descarta la oferta, pasa directo al Switch existente."
    }
}

$ExistingAutofill = @($Data.nodes | Where-Object { $_.name -eq "2 - Autofill Existing PDF (PRO)1" })
if (-not $ExistingAutofill) {
    $Data.nodes += [pscustomobject]@{
        parameters = [pscustomobject]@{
            method = "POST"
            url = "http://host.docker.internal:8000/autofill-application"
            sendBody = $true
            specifyBody = "json"
            jsonBody = '={{ { url: $("Filter Ready1").item.json.real_apply_url, pdf_path: $json.pdf_path, application_id: $json.application_id || $json.ID, company: $json.company || "", job_title: $json.job_title || "", expected_salary: $json.expected_salary || null, responses: $json.responses || [], motivation_answer: $json.motivation_answer || "", experience_answer: $json.experience_answer || "", job_context: $json.job_context || "" } }}'
            options = [pscustomobject]@{}
        }
        id = "f933bff9-f5ea-4d2f-9063-efcf45bd1ad0"
        name = "2 - Autofill Existing PDF (PRO)1"
        type = "n8n-nodes-base.httpRequest"
        typeVersion = 4
        position = @(-352, -1696)
        retryOnFail = $true
        notes = "Reintenta solo este nodo si falla Playwright/CAPTCHA/ATS. Reutiliza pdf_path creado por el nodo anterior."
    }
}

(Get-NodeByName "Switch Status1").position = @(-144, -1696)
(Get-NodeByName "Save Discarded1").position = @(80, -1888)
(Get-NodeByName "Save Success1").position = @(80, -1488)

$Data.connections.'Filter Ready1' = [pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "1 - Create PDF (PRO)1"
                type = "main"
                index = 0
            }
        )
    )
}
$Data.connections.PSObject.Properties.Remove("Call Backend (PRO)1")
$Data.connections | Add-Member -Force -NotePropertyName "1 - Create PDF (PRO)1" -NotePropertyValue ([pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "PDF Ready? (PRO)1"
                type = "main"
                index = 0
            }
        )
    )
})
$Data.connections | Add-Member -Force -NotePropertyName "PDF Ready? (PRO)1" -NotePropertyValue ([pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "2 - Autofill Existing PDF (PRO)1"
                type = "main"
                index = 0
            }
        ),
        @(
            [pscustomobject]@{
                node = "Switch Status1"
                type = "main"
                index = 0
            }
        )
    )
})
$Data.connections | Add-Member -Force -NotePropertyName "2 - Autofill Existing PDF (PRO)1" -NotePropertyValue ([pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "Switch Status1"
                type = "main"
                index = 0
            }
        )
    )
})

$Success = Get-NodeByName "Save Success1"
$SuccessValues = $Success.parameters.columns.value
$SuccessValues.Status = "DONE"
$SuccessValues.ID = '={{ $("1 - Create PDF (PRO)1").item.json.ID || $("1 - Create PDF (PRO)1").item.json.application_id }}'
$SuccessValues.company = '={{ $("1 - Create PDF (PRO)1").item.json.company || $json.company }}'
$SuccessValues.job_title = '={{ $("1 - Create PDF (PRO)1").item.json.job_title || $json.job_title }}'
$SuccessValues.pdf_name = '={{ $("1 - Create PDF (PRO)1").item.json.pdf_name || "" }}'
$SuccessValues.download_url = '={{ "http://host.docker.internal:8000/download-pdf/" + ($("1 - Create PDF (PRO)1").item.json.pdf_name || "") }}'
$SuccessValues | Add-Member -Force -NotePropertyName "autofill_status" -NotePropertyValue '={{ ($json.autofill && $json.autofill.status) || $json.status || "" }}'
$SuccessValues | Add-Member -Force -NotePropertyName "screenshot" -NotePropertyValue '={{ ($json.autofill && $json.autofill.screenshot) || "" }}'

$Discarded = Get-NodeByName "Save Discarded1"
$DiscardValues = $Discarded.parameters.columns.value
$DiscardValues.company = '={{ $json.company || $("1 - Create PDF (PRO)1").item.json.company || "" }}'
$DiscardValues.job_title = '={{ $json.job_title || $("1 - Create PDF (PRO)1").item.json.job_title || "" }}'
$DiscardValues.reason = '={{ $json.reason || $json.detail || ($json.autofill && $json.autofill.detail) || "" }}'

$Data | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $Out -Encoding UTF8
Write-Output $Out

.Name -like '*con revisi*n manual*' } | Select-Object -First 1).FullName
$Out = Join-Path $Root "N8N Job Application Automation - PRO PIPELINE V2 - PDF AUTOFILL SEPARADO.json"

$Data = Get-Content -Raw -LiteralPath $Src | ConvertFrom-Json
$Data.name = "Job Application Automation - PRO PIPELINE V2 - PDF + Autofill Separados"

function Get-NodeByName {
    param([string]$Name)
    return @($Data.nodes | Where-Object { $_.name -eq $Name })[0]
}

$Schedule = Get-NodeByName "Schedule (Buscar en LinkedIn)"
$Schedule.parameters = [pscustomobject]@{
    rule = [pscustomobject]@{
        interval = @(
            [pscustomobject]@{
                field = "minutes"
                minutesInterval = 30
            }
        )
    }
}
$Schedule.notes = "Corre automatico cada 30 minutos. Mantiene batch de 25 y el backend combina front + backlog con dedup."

$SearchParams = Get-NodeByName "Search Params1"
$Assignments = @($SearchParams.parameters.assignments.assignments)
if (-not @($Assignments | Where-Object { $_.name -eq "backlog_pages" })) {
    $Assignments += [pscustomobject]@{
        id = "backlog_pages_field"
        name = "backlog_pages"
        value = 2
        type = "number"
    }
    $SearchParams.parameters.assignments.assignments = $Assignments
}

$CallSearch = Get-NodeByName "Call LinkedIn Search1"
$CallSearch.parameters.jsonBody = '={{ { search_url: $json.search_url, max_jobs: $json.max_jobs, backlog_pages: $json.backlog_pages || 2 } }}'
$CallSearch.notes = "El backend revisa start=0 para nuevas + backlog paginado. Usa new_job_urls para no mandar duplicados al triage."

$SplitUrls = Get-NodeByName "Split URLs1"
$SplitUrls.parameters.jsCode = "const urls = `$input.first().json.new_job_urls || [];`nreturn urls.map(u => ({ json: { url: u } }));"
$SplitUrls.notes = "Procesa solo URLs nuevas segun dedup del backend. No usa job_urls porque incluye URLs ya vistas."

$CreatePdf = Get-NodeByName "Call Backend (PRO)1"
$CreatePdf.name = "1 - Create PDF (PRO)1"
$CreatePdf.parameters.url = "http://host.docker.internal:8000/create-application-pdf"
$CreatePdf.parameters.jsonBody = '={{ { url: $json.real_apply_url, job_text: $json.description || $json.job_text || "", from_sheet: true } }}'
$CreatePdf.position = @(-752, -1728)
$CreatePdf.retryOnFail = $true
$CreatePdf.notes = "Solo crea CV/DOCX/PDF y respuestas. Si falla autofill despues, este nodo NO se reejecuta al reintentar solo autofill."

$ExistingPdfReady = @($Data.nodes | Where-Object { $_.name -eq "PDF Ready? (PRO)1" })
if (-not $ExistingPdfReady) {
    $Data.nodes += [pscustomobject]@{
        parameters = [pscustomobject]@{
            conditions = [pscustomobject]@{
                options = [pscustomobject]@{
                    caseSensitive = $true
                    leftValue = ""
                    typeValidation = "strict"
                    version = 1
                }
                conditions = @(
                    [pscustomobject]@{
                        id = "cond_pdf_ready"
                        leftValue = '={{ $json.status }}'
                        rightValue = "pdf_ready"
                        operator = [pscustomobject]@{
                            type = "string"
                            operation = "equals"
                        }
                    }
                )
                combinator = "and"
            }
            options = [pscustomobject]@{}
        }
        id = "a7b2d55c-75df-42be-971a-6ecdbd819d88"
        name = "PDF Ready? (PRO)1"
        type = "n8n-nodes-base.if"
        typeVersion = 2.2
        position = @(-560, -1728)
        notes = "Solo ejecuta autofill cuando el PDF ya existe. Si el backend descarta la oferta, pasa directo al Switch existente."
    }
}

$ExistingAutofill = @($Data.nodes | Where-Object { $_.name -eq "2 - Autofill Existing PDF (PRO)1" })
if (-not $ExistingAutofill) {
    $Data.nodes += [pscustomobject]@{
        parameters = [pscustomobject]@{
            method = "POST"
            url = "http://host.docker.internal:8000/autofill-application"
            sendBody = $true
            specifyBody = "json"
            jsonBody = '={{ { url: $("Filter Ready1").item.json.real_apply_url, pdf_path: $json.pdf_path, application_id: $json.application_id || $json.ID, company: $json.company || "", job_title: $json.job_title || "", expected_salary: $json.expected_salary || null, responses: $json.responses || [], motivation_answer: $json.motivation_answer || "", experience_answer: $json.experience_answer || "", job_context: $json.job_context || "" } }}'
            options = [pscustomobject]@{}
        }
        id = "f933bff9-f5ea-4d2f-9063-efcf45bd1ad0"
        name = "2 - Autofill Existing PDF (PRO)1"
        type = "n8n-nodes-base.httpRequest"
        typeVersion = 4
        position = @(-352, -1696)
        retryOnFail = $true
        notes = "Reintenta solo este nodo si falla Playwright/CAPTCHA/ATS. Reutiliza pdf_path creado por el nodo anterior."
    }
}

(Get-NodeByName "Switch Status1").position = @(-144, -1696)
(Get-NodeByName "Save Discarded1").position = @(80, -1888)
(Get-NodeByName "Save Success1").position = @(80, -1488)

$Data.connections.'Filter Ready1' = [pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "1 - Create PDF (PRO)1"
                type = "main"
                index = 0
            }
        )
    )
}
$Data.connections.PSObject.Properties.Remove("Call Backend (PRO)1")
$Data.connections | Add-Member -Force -NotePropertyName "1 - Create PDF (PRO)1" -NotePropertyValue ([pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "PDF Ready? (PRO)1"
                type = "main"
                index = 0
            }
        )
    )
})
$Data.connections | Add-Member -Force -NotePropertyName "PDF Ready? (PRO)1" -NotePropertyValue ([pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "2 - Autofill Existing PDF (PRO)1"
                type = "main"
                index = 0
            }
        ),
        @(
            [pscustomobject]@{
                node = "Switch Status1"
                type = "main"
                index = 0
            }
        )
    )
})
$Data.connections | Add-Member -Force -NotePropertyName "2 - Autofill Existing PDF (PRO)1" -NotePropertyValue ([pscustomobject]@{
    main = @(
        @(
            [pscustomobject]@{
                node = "Switch Status1"
                type = "main"
                index = 0
            }
        )
    )
})

$Success = Get-NodeByName "Save Success1"
$SuccessValues = $Success.parameters.columns.value
$SuccessValues.Status = "DONE"
$SuccessValues.ID = '={{ $("1 - Create PDF (PRO)1").item.json.ID || $("1 - Create PDF (PRO)1").item.json.application_id }}'
$SuccessValues.company = '={{ $("1 - Create PDF (PRO)1").item.json.company || $json.company }}'
$SuccessValues.job_title = '={{ $("1 - Create PDF (PRO)1").item.json.job_title || $json.job_title }}'
$SuccessValues.pdf_name = '={{ $("1 - Create PDF (PRO)1").item.json.pdf_name || "" }}'
$SuccessValues.download_url = '={{ "http://host.docker.internal:8000/download-pdf/" + ($("1 - Create PDF (PRO)1").item.json.pdf_name || "") }}'
$SuccessValues | Add-Member -Force -NotePropertyName "autofill_status" -NotePropertyValue '={{ ($json.autofill && $json.autofill.status) || $json.status || "" }}'
$SuccessValues | Add-Member -Force -NotePropertyName "screenshot" -NotePropertyValue '={{ ($json.autofill && $json.autofill.screenshot) || "" }}'

$Discarded = Get-NodeByName "Save Discarded1"
$DiscardValues = $Discarded.parameters.columns.value
$DiscardValues.company = '={{ $json.company || $("1 - Create PDF (PRO)1").item.json.company || "" }}'
$DiscardValues.job_title = '={{ $json.job_title || $("1 - Create PDF (PRO)1").item.json.job_title || "" }}'
$DiscardValues.reason = '={{ $json.reason || $json.detail || ($json.autofill && $json.autofill.detail) || "" }}'

$Data | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $Out -Encoding UTF8
Write-Output $Out


