import unittest
from unittest.mock import patch
import gemini_service as service
from models import CVAdaptation


def draft(profile='P' * 600):
    return CVAdaptation(nuevo_perfil=profile,
        nuevo_titulo='Ingeniero Proyectos y Mejora de Procesos',
        nuevo_cargo_actual='Ingeniero de Proyectos',
        nuevas_tareas=['Gestioné ' + 'a' * 165 + '.' for _ in range(4)],
        empresa_actual_sin_cambios='Empresa', fechas_actual_sin_cambios='2024 – 2026')


class RepairSelectionTests(unittest.TestCase):
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
        initial.nuevas_tareas[0] = 'Gestioné ' + 'a' * 195 + '.'
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
