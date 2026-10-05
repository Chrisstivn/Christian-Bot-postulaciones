import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import search_filters
import gemini_service
import job_quality
from test_christian_search import load_pipeline_functions

TEXT = ('Somos una empresa multinacional mediana con operaciones en varios países. '
        'Sector de tecnología, trabajo híbrido. Requisito: 4 años de experiencia.')
MINING = 'Empresa minera chilena mediana, operación en faena presencial y exclusivamente en Chile.'


def evidence(text=TEXT, mining='NO', size='MEDIUM_OR_LARGE', multi='YES', years=4, quote=None):
    return {'mining': {'value': mining, 'evidence': text},
            'company_size': {'value': size, 'evidence': text},
            'multinational': {'value': multi, 'evidence': text},
            'experience': {'minimum_years': years,
                           'evidence': quote if quote is not None else text}}


class FilterPolicyTests(unittest.TestCase):
    def decide(self, title='Project Manager', text=TEXT, work='Hybrid', facts=None):
        return search_filters.evaluate(company='Example', job_title=title, job_text=text,
                    work_format=work, evidence=facts if facts is not None else evidence(text))

    def test_accepts_hybrid_and_remote_up_to_four_years(self):
        for work in ('Hybrid', 'Remote', 'Híbrido', 'Remoto'):
            self.assertEqual(self.decide(work=work).decision, 'KEEP')

    def test_project_manager_and_junior_are_not_executive_exclusions(self):
        for title in ('Project Manager', 'Junior Analyst', 'Project Manager Trainee', 'Ingeniero'):
            self.assertEqual(self.decide(title=title).decision, 'KEEP')

    def test_internship_and_executive_titles_rejected_including_mining(self):
        for title in ('Práctica profesional', 'Practicante', 'Practicantes', 'Pasantía', 'Internship',
                      'Director', 'Directora', 'Gerente', 'Gerencia', 'Subgerente',
                      'Vice Presidente', 'Vicepresidenta', 'Vice President', 'VP', 'V.P.', 'SVP'):
            with self.subTest(title=title):
                self.assertEqual(self.decide(title=title, text=MINING, work='On-site',
                    facts=evidence(MINING, mining='YES', multi='NO', years=None)).decision, 'REJECT')

    def test_mining_exception_allows_chilean_or_foreign_and_faena(self):
        for multi in ('NO', 'YES', 'UNKNOWN'):
            result=self.decide(text=MINING, work='On-site',
                facts=evidence(MINING, mining='YES', multi=multi, years=None))
            self.assertEqual(result.decision, 'KEEP')

    def test_small_mining_company_is_still_rejected(self):
        result=self.decide(text=MINING, work='On-site',
            facts=evidence(MINING, mining='YES', size='SMALL', multi='NO', years=None))
        self.assertEqual(result.rejected_reason, 'small_company')

    def test_non_mining_local_and_onsite_are_rejected(self):
        self.assertIn('not_multinational', self.decide(facts=evidence(multi='NO')).reasons)
        self.assertIn('onsite_non_mining', self.decide(work='On-site').reasons)

    def test_unknown_size_and_international_operations_require_review(self):
        result=self.decide(facts={})
        self.assertEqual(result.decision, 'REVIEW')
        self.assertIn('company_size_unknown', result.reasons)
        self.assertIn('multinational_unknown', result.reasons)

    def test_unverified_mining_exception_does_not_admit_onsite_local_job(self):
        result=self.decide(work='On-site', facts=evidence(mining='UNKNOWN',multi='NO'))
        self.assertEqual(result.decision,'REVIEW')
        self.assertIn('mining_exception_unconfirmed',result.reasons)

    def test_fabricated_quote_is_not_used_as_company_evidence(self):
        facts=evidence()
        facts['company_size']['evidence']='Not present in the source'
        self.assertEqual(self.decide(facts=facts).decision,'REVIEW')

    def test_experience_ranges_preferred_and_strict_thresholds(self):
        cases={'Mínimo 4 años de experiencia.':'KEEP', 'Requisitos: 5+ años de experiencia.':'REJECT',
               'Experiencia de 3 a 5 años.':'KEEP', 'Experiencia de 3–5 años.':'KEEP',
               'Más de 4 años de experiencia.':'REJECT', 'Al menos cinco años de experiencia.':'REJECT',
               '5 years of experience preferred.':'KEEP', 'Deseable: 6 años de experiencia.':'KEEP',
               'More than four years of experience.':'REJECT',
               'Requisito: 5 años de experiencia; deseable 6 años.':'REJECT'}
        for quote,expected in cases.items():
            with self.subTest(quote=quote):
                text=TEXT+' '+quote
                self.assertEqual(self.decide(text=text,
                    facts=evidence(text,years=99,quote=quote)).decision,expected)

    def test_mining_still_excludes_more_than_four_years(self):
        text=MINING+' Requisito: 5 años de experiencia.'
        result=self.decide(text=text,work='On-site',facts=evidence(text,mining='YES',multi='NO',years=5))
        self.assertIn('requires_more_than_four_years',result.reasons)

    def test_complete_description_catches_requirement_omitted_by_model(self):
        text = MINING + '\nLo Que Requerimos\nMás de 15 años de experiencia geotécnica y en relaves, preferentemente minería'
        result = self.decide(text=text, work='Hybrid', facts=evidence(text, mining='YES', years=None, quote=''))
        self.assertEqual(result.decision, 'REJECT')
        self.assertEqual(result.facts['minimum_required_years'], 15)

    def test_preferred_section_and_company_age_are_not_requirements(self):
        text = TEXT + '\nEmpresa con 40 años de trayectoria.\nDeseable:\n6 años de experiencia en minería\nBeneficios\nSeguro de salud'
        self.assertEqual(self.decide(text=text, facts=evidence(text, quote='Requisito: 4 años de experiencia.')).decision, 'KEEP')
        for history in ('Somos redbee, una empresa con más de 14 años de experiencia.',
                        'Con más de 40 años de experiencia, nos hemos consolidado como empresa.',
                        'Example is a global company with 30 years of experience.'):
            self.assertEqual(search_filters.description_required_years(TEXT + '\n' + history), (4, False))

    def test_requirement_section_does_not_need_word_experience_on_each_line(self):
        self.assertEqual(search_filters.description_required_years('Requisitos\n5 años en minería\nBeneficios\nEmpresa con 50 años en el mercado'), (5, False))
        self.assertEqual(search_filters.description_required_years('We are seeking an engineer with 5 years of experience.'), (5, False))

    def test_profile_evidence_resolves_size_without_using_profile_experience(self):
        profile = 'Company size: 201-500 employees. Multinacional. Nuestro equipo tiene 20 años de experiencia.'
        facts = evidence()
        facts['company_size']['evidence'] = '201-500 employees'
        facts['multinational']['evidence'] = 'Multinacional'
        result = search_filters.evaluate(company='Example', job_title='Engineer', job_text=TEXT,
                work_format='Hybrid', evidence=facts, company_text=profile)
        self.assertEqual(result.decision, 'KEEP')

    def test_default_policy_is_active_without_env_configuration(self):
        with patch.dict(os.environ, {'SEARCH_POLICY':'christian','SEARCH_EXCLUDED_TITLE_REGEX':'',
             'SEARCH_ALLOWED_WORK_FORMATS':'','SEARCH_EXCLUDED_CONTRACT_TYPES':''}):
            result=job_quality.evaluate_search_filters(company='Example',job_title='Director',job_text=TEXT,
                evidence=evidence(),work_format='Hybrid')
            self.assertEqual(result.decision,'REJECT')


class FilterPipelineTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ, {'SEARCH_POLICY':'christian','SEARCH_EXCLUDED_TITLE_REGEX':'',
                  'SEARCH_ALLOWED_WORK_FORMATS':'','SEARCH_EXCLUDED_CONTRACT_TYPES':''})
        self.env.start();self.addCleanup(self.env.stop)
        self.app=load_pipeline_functions()
        self.app['queue_service'].get_by_url.return_value=None
        self.app['scraper'].find_closed_application_marker.return_value=None
        self.app['scraper'].scrape_job_posting.return_value={'text':TEXT,'work_format':'Hybrid'}
        self.app['gemini_service'].extract_job_info.return_value=SimpleNamespace(
            company='Example',job_title='Project Manager',search_filter_evidence=evidence())

    def test_accepted_job_enters_ready_queue_using_one_extraction(self):
        result=self.app['_triage_job_url']('https://linkedin.com/jobs/view/1')
        self.assertEqual(result['status'],'ready')
        self.app['gemini_service'].extract_job_info.assert_called_once_with(
            TEXT,include_questions=False,include_search_filters=True)
        self.assertEqual(self.app['queue_service'].upsert_triage_result.call_args.kwargs['status'],'ready_for_review')

    def test_missing_facts_go_to_separate_filter_review_queue(self):
        self.app['gemini_service'].extract_job_info.return_value.search_filter_evidence={}
        result=self.app['_triage_job_url']('https://linkedin.com/jobs/view/1')
        self.assertEqual(result['status'],'review')
        self.assertEqual(self.app['queue_service'].upsert_triage_result.call_args.kwargs['status'],'filter_review')

    def test_employer_profile_is_added_to_extraction_and_checked_separately(self):
        profile = 'Company size: 201-500 employees. Multinacional. 30 years of experience.'
        facts = evidence()
        facts['company_size']['evidence'] = '201-500 employees'
        facts['multinational']['evidence'] = 'Multinacional'
        self.app['scraper'].scrape_job_posting.return_value = {'text': TEXT, 'work_format': 'Hybrid', 'company_text': profile}
        self.app['gemini_service'].extract_job_info.return_value.search_filter_evidence = facts
        result = self.app['_triage_job_url']('https://linkedin.com/jobs/view/1')
        self.assertEqual(result['status'], 'ready')
        self.assertIn(profile, self.app['gemini_service'].extract_job_info.call_args.args[0])
        saved = self.app['queue_service'].upsert_triage_result.call_args.kwargs
        self.assertEqual(saved['job_text'], TEXT)
        self.assertEqual(saved['baseline_comparison']['filter_facts']['minimum_required_years'], 4)

    def test_excluded_titles_never_enter_ready_queue(self):
        self.app['gemini_service'].extract_job_info.return_value.job_title='Gerente'
        result=self.app['_triage_job_url']('https://linkedin.com/jobs/view/1')
        self.assertEqual(result['status'],'discarded')
        self.assertEqual(result['rejected_reason'],'excluded_executive_title')

    def test_search_filter_extraction_is_validated_and_uses_one_gemini_call(self):
        payload={'company':'Example','job_title':'Project Manager','location':'',
                 'search_filter_evidence':evidence()}
        with patch.object(gemini_service,'_call_gemini_json',return_value=payload) as call:
            result=gemini_service.extract_job_info(TEXT,include_questions=False,include_search_filters=True)
        self.assertEqual(call.call_count,1)
        self.assertEqual(result.search_filter_evidence.multinational.value,'YES')
        self.assertIn('nunca uses memoria',call.call_args.args[0])
        self.assertIn('50 empleados',call.call_args.args[0])

    def test_n8n_routes_unknown_facts_to_filter_review_and_keeps_ready_gate(self):
        path=Path(__file__).resolve().parents[1]/'n8n_christian_postulaciones.json'
        workflow=json.loads(path.read_text())
        nodes={n['name']:n for n in workflow['nodes']}
        self.assertIn('review_jobs',nodes['Split ready_jobs (backend triage)']['parameters']['jsCode'])
        self.assertIn('FILTER_REVIEW',nodes['Guardar ofertas nuevas en Excel']['parameters']['jsonBody'])
        self.assertIn('READY',str(nodes['Filter Ready']['parameters']))

if __name__=='__main__':unittest.main()
