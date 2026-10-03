import unittest

import gemini_service
from models import CVAdaptation


class CVLayoutFitTests(unittest.TestCase):
    def test_oversized_profile_and_tasks_are_fit_to_layout(self):
        long_sentence = (
            "Growth focused creative marketing leader with experience aligning regional campaigns, "
            "brand systems, field execution, stakeholder management, analytics, workflow optimization "
            "and high volume go to market delivery for commercial teams. "
        )
        profile = (long_sentence * 4).strip()
        tasks = [
            (
                "Managed high impact regional creative campaigns across digital ads, email, events, "
                "sales collateral, localization workflows, brand governance, stakeholder reviews and "
                "performance feedback loops for commercial marketing teams."
            ),
            (
                "Led cross functional execution with marketing, product, sales and external partners, "
                "turning technical requirements into clear creative assets, localized narratives and "
                "pipeline oriented campaign materials."
            ),
            (
                "Optimized creative operations by improving briefs, review cycles, production templates, "
                "quality assurance steps and delivery rhythms for fast moving regional requests without "
                "sacrificing brand consistency."
            ),
            (
                "Presented creative solutions to senior stakeholders, connecting visual direction, "
                "customer needs, regional market nuance, campaign goals and measurable business impact "
                "across multiple channels."
            ),
        ]
        adaptation = CVAdaptation(
            nuevo_titulo="Growth Marketing Creative Lead",
            nuevo_perfil=profile,
            nuevo_cargo_actual="Growth Marketing Creative Lead",
            nuevas_tareas=tasks,
            empresa_actual_sin_cambios="Stanley Black & Decker",
            fechas_actual_sin_cambios="(Aug 2024 – Jun 2026)",
        )

        fitted = gemini_service._fit_adaptation_to_layout(adaptation)

        # Layout helpers must leave complete prose intact so the targeted
        # Gemini repair can rewrite it, rather than silently cutting sentences.
        self.assertEqual(fitted.nuevo_perfil, profile)
        self.assertEqual(fitted.nuevas_tareas, tasks)
        problems = gemini_service._validate_adaptation(fitted)
        self.assertTrue(any("nuevo_perfil" in p for p in problems))
        self.assertTrue(any("bullet" in p for p in problems))



if __name__ == "__main__":
    unittest.main()
