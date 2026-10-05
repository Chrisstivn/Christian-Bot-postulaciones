"""Explicit recheck of unprocessed Excel rows and their matching queue records.

Pause n8n before running. Dry run by default; --apply creates a workbook backup.
No PDF generation or applications. Previously processed rows are never touched.
"""
import argparse
from collections import Counter
from datetime import datetime
import shutil

import excel_output
import gemini_service
import queue_service
import scraper
import search_filters


def recheck_row(row, existing):
    if row.get('Status') not in ('NEW', 'FILTER_REVIEW'):
        return None
    if not existing or existing.get('status') not in ('ready_for_review', 'filter_review'):
        return None
    text = existing.get('job_text') or row.get('description') or ''
    if not text:
        raise ValueError('No hay descripción guardada; se conserva la fila.')
    comparison = existing.get('baseline_comparison') or {}
    facts = comparison.get('filter_facts', {}).get('evidence', {})
    company_text = ''
    # Reject mandatory experience first, without another network/Gemini request.
    years = search_filters.description_required_years(text)
    over_limit = years and (years[0] > 4 or (years[0] == 4 and years[1]))
    if not over_limit:
        try:
            html = scraper._fetch_linkedin_guest_job_html(row['Link'])
            company_text = scraper.linkedin_company_text(html)
        except Exception as exc:
            print(f"Perfil no disponible: {row['Link']}: {exc}", flush=True)
        if company_text or not facts:
            source = text + ('\n\nPERFIL PÚBLICO DEL EMPLEADOR (no es la descripción del cargo):\n' + company_text if company_text else '')
            info = gemini_service.extract_job_info(source, include_questions=False, include_search_filters=True)
            facts = info.search_filter_evidence
    return search_filters.evaluate(company=existing.get('company') or row.get('company', ''),
        job_title=existing.get('job_title') or row.get('job_title', ''), job_text=text,
        work_format=existing.get('work_format') or row.get('Format') or 'Unknown',
        evidence=facts, company_text=company_text)


def run(apply=False):
    rows = excel_output.read_rows()
    if apply:
        with excel_output._locked_book() as (_, __, path):
            backup = path.with_name(path.stem + '.backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + path.suffix)
            shutil.copy2(path, backup)
        print(f'Respaldo: {backup}', flush=True)
    counts = Counter()
    for row in rows:
        if row.get('Status') not in ('NEW', 'FILTER_REVIEW'):
            continue
        try:
            existing = queue_service.get_by_url(row['Link'])
            decision = recheck_row(row, existing)
            if decision is None:
                counts['skipped'] += 1
                continue
            counts[decision.decision] += 1
            reason = decision.comparison['reason']
            print(f"{decision.decision}: {row.get('company')} / {row.get('job_title')}: {reason}", flush=True)
            if not apply:
                continue
            # Re-read before writing to avoid overwriting a job processed meanwhile.
            current = queue_service.get_by_url(row['Link'])
            if not current or current['status'] not in ('ready_for_review', 'filter_review'):
                counts['skipped'] += 1
                continue
            db_status, excel_status = {
                'KEEP': ('ready_for_review', 'NEW'),
                'REVIEW': ('filter_review', 'FILTER_REVIEW'),
                'REJECT': ('discarded', 'DISCARDED'),
            }[decision.decision]
            queue_service.upsert_triage_result(row['Link'], status=db_status,
                baseline_comparison=decision.comparison, upgrade_reason=reason,
                rejected_reason=decision.rejected_reason,
                last_error=decision.rejected_reason)
            excel_output.write_row(excel_output.RowInput(row={
                'Link': row['Link'], 'Status': excel_status, 'reason': reason,
                'Revisar': 'Verificar filtros: ' + reason if decision.decision == 'REVIEW' else '',
            }))
        except Exception as exc:
            counts['failed'] += 1
            print(f"ERROR; fila conservada: {row.get('Link')}: {exc}", flush=True)
    print(dict(counts), flush=True)
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Guardar cambios en DB y Excel con respaldo previo.')
    args = parser.parse_args()
    run(apply=args.apply)
