"""Verify actual PDF wrapping rather than assuming character counts imply layout."""
from __future__ import annotations
import re
import unicodedata
import fitz


def _norm(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).replace('\u00ad','')).strip()


def _find_lines(document, target):
    target = _norm(target)
    matches=[]
    for page_number,page in enumerate(document):
        lines=[]
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines',[]):
                text=_norm(''.join(s['text'] for s in line['spans']))
                if text:lines.append((text,line['bbox']))
        text=' '.join(line[0] for line in lines)
        if not target:continue
        start=text.find(target)
        while start >= 0:
            end=start+len(target)
            offset=0
            selected=[]
            for value,box in lines:
                if offset < end and offset+len(value)>start:selected.append(box)
                offset+=len(value)+1
            matches.append((page_number,selected))
            start=text.find(target,end)
    return matches


def validate_pdf_layout(path, adaptation, full_name, expected_pages=None):
    """Return field-specific problems. Unreadable/missing generated text fails closed."""
    problems=[]
    fields=[('nuevo_titulo',full_name+' | '+adaptation.nuevo_titulo,2,2),
            ('nuevo_perfil',adaptation.nuevo_perfil,5,6)]
    fields += [('nuevas_tareas',task,1,2) for task in adaptation.nuevas_tareas]
    with fitz.open(path) as document:
        if expected_pages is not None:
            if len(document) != expected_pages:
                problems.append({'field':'pagination','reason':'page_count',
                                 'pages':len(document),'expected':expected_pages})
            elif not document[1].get_text().lstrip().startswith('Habilidades'):
                problems.append({'field':'pagination','reason':'experience_overflows_first_page'})
        for field,target,minimum,maximum in fields:
            matches=_find_lines(document,target)
            # Historical roles can legitimately repeat a current-role bullet.
            # The adapter always places the current role before historical roles.
            # Validate its first occurrence, while profile/title stay unique.
            if field == 'nuevas_tareas' and matches:
                matches=matches[:1]
            if len(matches)!=1:
                problems.append({'field':field,'reason':'generated_text_missing_or_duplicated'})
                continue
            page_number,lines=matches[0]
            line_count=len(lines)
            if not minimum <= line_count <= maximum:
                problems.append({'field':field,'reason':'rendered_line_count',
                                 'lines':line_count,'minimum':minimum,'maximum':maximum})
            page=document[page_number]
            if any(x0<0 or y0<0 or x1>page.rect.width+1 or y1>page.rect.height+1
                   for x0,y0,x1,y1 in lines):
                problems.append({'field':field,'reason':'text_outside_page'})
    return problems
