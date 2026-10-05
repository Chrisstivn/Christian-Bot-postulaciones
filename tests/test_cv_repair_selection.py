import unittest
from unittest.mock import patch
import gemini_service as service
from models import CVAdaptation


def draft(profile='P' * 600):
    return CVAdaptation(nuevo_perfil=profile,
        nuevo_titulo='Ingeniero Proyectos y Mejora de Procesos',
        nuevo_cargo_actual='Ingeniero de Proyectos',
        nuevas_tareas=['Gestiono ' + 'a' * 165 + '.' for _ in range(4)],
        empresa_actual_sin_cambios='Empresa', fechas_actual_sin_cambios='2024 – 2026')


class RepairSelectionTests(unittest.TestCase):
    def test_only_failed_bullet_retries_and_valid_bullets_are_frozen(self):
        original = draft()
        original.nuevas_tareas[0] = 'Gestiono los plazos o.'
        frozen = original.nuevas_tareas[1:].copy()
        bad = draft().nuevas_tareas.copy(); bad[0] = 'Gestiono los plazos o.'
        good = draft().nuevas_tareas.copy()
        good[1:] = ['Coordino ' + 'b' * 165 + '.' for _ in range(3)]
        with patch.object(service, '_estimated_title_lines', return_value=2), \
             patch.object(service, '_estimated_bullet_lines', return_value=2), \
             patch.object(service, '_call_gemini_json', side_effect=[
                 {'nuevas_tareas': bad}, {'nuevas_tareas': good}]) as llm:
            result = service._stable_cv_repair_pass(original,'Job','Source')
        self.assertEqual(llm.call_count,2)
        self.assertEqual(result.nuevas_tareas[1:],frozen)
        self.assertEqual(result.nuevas_tareas[0],good[0])

    def test_people_management_is_rejected_but_coordination_is_allowed(self):
        for text in ['Lidero equipos multidisciplinarios.', 'Lideré la coordinación de equipos.',
                     'Tengo personal a cargo.', 'Superviso personas del área.']:
            self.assertTrue(service._people_management_claim(text),text)
        self.assertFalse(service._people_management_claim('Coordino actividades con clientes, proveedores y áreas internas.'))
        a=draft('Ingeniero con equipos a cargo. ' + 'P'*560)
        self.assertTrue(any('liderazgo' in p for p in service._validate_adaptation(a)))

    def test_failed_targeted_repair_has_bounded_retry_budget(self):
        a=draft('P'*486)
        with patch.object(service,'_call_gemini_json',return_value={'nuevo_perfil':'P'*486}) as llm:
            result=service._stable_cv_repair_pass(a,'Job','Source')
        self.assertEqual(llm.call_count,4)
        self.assertEqual(len(result.nuevo_perfil),486)

    def test_incomplete_spanish_title_is_rejected(self):
        self.assertTrue(service._title_style_problems('Ingeniero de Planificación y Control de'))
        self.assertFalse(service._title_style_problems('Ingeniero de Planificación y Control'))

    def test_long_title_is_preserved_for_rewrite_instead_of_cutting(self):
        original = draft()
        original.nuevo_titulo = 'Ingeniero de Planificación y Control de Proyectos'
        title = original.nuevo_titulo
        self.assertEqual(service._force_fix_adaptation(original).nuevo_titulo, title)

    def test_word_adapter_never_cuts_title_words_or_comma_clauses(self):
        import docx_adapter
        titles = [
            'Ingeniero de Planificación y Control de Proyectos',
            'Ingeniero de Proyectos, Planificación y Control',
            'Ingeniero de Proyectos - Planificación y Control',
            'Ingeniero de Proyectos (Planificación y Control)',
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(docx_adapter._shorten_title_to_two_lines(title), title)

    def test_word_adapter_rejects_overlong_title_without_modifying_document(self):
        from docx import Document
        import docx_adapter
        doc = Document()
        heading = doc.add_paragraph('Christian Molina | Gestión de proyectos')
        before = heading.text
        with self.assertRaises(ValueError):
            docx_adapter.update_main_title(doc, 'Ingeniero de Planificación y Control de Proyectos')
        self.assertEqual(heading.text, before)

    def test_current_tasks_reject_past_and_allow_present(self):
        self.assertTrue(any('presente' in p for p in service._bullet_style_problems('Desarrollé los cronogramas del proyecto.')))
        self.assertEqual(service._bullet_style_problems('Desarrollo los cronogramas del proyecto.'), [])

    def test_task_repair_receives_job_specific_original_present_rules(self):
        original = draft()
        original.nuevas_tareas[0] = 'Gestioné ' + 'a' * 165 + '.'
        replacement = draft().nuevas_tareas
        with patch.object(service, '_estimated_bullet_lines', return_value=2), \
             patch.object(service, '_call_gemini_json', return_value={'nuevas_tareas': replacement}) as llm:
            result = service._repair_invalid_cv_fields_with_gemini(original,
                'Planificación de propuestas y seguimiento de cronogramas', 'Source current and historical roles')
        prompt, content = llm.call_args.args
        self.assertIn('PRESENT tense', prompt)
        self.assertIn('Do not copy vacancy phrases', prompt)
        self.assertIn('Never borrow a historical', prompt)
        self.assertIn('Planificación de propuestas', content)
        self.assertEqual(result.nuevas_tareas, replacement)

    def test_rendered_layout_repair_corrects_short_profile_without_overwriting_valid_title(self):
        original = draft()
        title = original.nuevo_titulo
        with patch.object(service, '_estimated_title_lines', return_value=2), \
             patch.object(service, '_estimated_bullet_lines', return_value=2), \
             patch.object(service, '_call_gemini_json', side_effect=[
                 {'nuevo_perfil': 'P' * 512, 'nuevo_titulo': title},
                 {'nuevo_perfil': 'P' * 570},
             ]) as llm:
            result = service.repair_rendered_cv_layout(original, 'Source facts', 'Job', '',
                [{'field': 'nuevo_perfil'}, {'field': 'nuevo_titulo'}])
        self.assertEqual(len(result.nuevo_perfil), 570)
        self.assertEqual(result.nuevo_titulo, title)
        self.assertEqual(result.empresa_actual_sin_cambios, original.empresa_actual_sin_cambios)
        self.assertEqual(llm.call_count, 2)

    def test_rendered_layout_repair_still_rejects_invalid_final_response(self):
        with patch.object(service, '_call_gemini_json', return_value={'nuevo_perfil': 'P' * 512}) as llm:
            with self.assertRaisesRegex(ValueError, '512 caracteres'):
                service.repair_rendered_cv_layout(draft(), 'Source facts', 'Job', '', [{'field': 'nuevo_perfil'}])
        self.assertEqual(llm.call_count, 2)

    def test_valid_repair_is_accepted_without_truncation_and_cannot_change_company(self):
        original = draft('P' * 767)
        replacement = 'Texto completo ' + 'b' * 583 + '.'
        result = service._accept_valid_cv_replacements(original, {
            'nuevo_perfil': replacement,
            'empresa_actual_sin_cambios': 'Invented company',
        }, ['nuevo_perfil'])
        self.assertEqual(result.nuevo_perfil, replacement)
        self.assertEqual(result.empresa_actual_sin_cambios, 'Empresa')
        self.assertEqual(original.nuevo_perfil, 'P' * 767)

    def test_invalid_repair_does_not_replace_original_with_worse_text(self):
        original = draft('P' * 640)
        result = service._accept_valid_cv_replacements(original, {'nuevo_perfil': 'X' * 767}, ['nuevo_perfil'])
        self.assertEqual(result.nuevo_perfil, 'P' * 640)

    def test_valid_profile_is_preserved_when_bullet_fallback_regresses_it(self):
        initial = draft()
        initial.nuevas_tareas[0] = 'Gestiono ' + 'a' * 195 + '.'
        fallback = draft('X' * 767)
        # Isolate title width; the regression concerns complete profile and bullet groups.
        with patch.object(service, '_estimated_title_lines', return_value=2), \
             patch.object(service, '_estimated_bullet_lines', return_value=2), \
             patch.object(service, '_force_fix_adaptation', side_effect=lambda value: value), \
             patch.object(service, '_call_gemini_json', side_effect=[initial.model_dump(), {}, fallback.model_dump()]) as llm:
            result = service.adapt_cv('Source facts', 'Job description')
        self.assertEqual(result.nuevo_perfil, initial.nuevo_perfil)
        self.assertEqual(result.nuevas_tareas, fallback.nuevas_tareas)
        self.assertEqual(llm.call_count, 3)
