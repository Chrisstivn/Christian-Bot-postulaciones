"""Interactive CLI to create or update candidate_bible.yaml."""

from __future__ import annotations

from pathlib import Path

from candidate_bible import DEFAULT_BIBLE_PATH, CandidateBible


SECTIONS = [
    (
        "Personal",
        [
            ("personal.full_name", "Full name"),
            ("personal.first_name", "First name"),
            ("personal.last_name", "Last name"),
            ("personal.location", "Current city/country"),
            ("personal.citizenship", "Citizenship(s)"),
            ("personal.work_authorization", "Work authorization / permit"),
            ("personal.availability", "Availability or notice period"),
            ("personal.email", "Email"),
            ("personal.phone", "Phone"),
            ("personal.linkedin", "LinkedIn URL"),
        ],
    ),
    (
        "Languages",
        [
            ("languages.spanish.level", "Spanish level"),
            ("languages.english.level", "English level"),
            ("languages.english.notes", "English evidence/notes"),
            ("languages.german.level", "German level"),
            ("languages.german.notes", "German evidence/notes"),
        ],
    ),
    (
        "Career Positioning",
        [
            ("career.years_experience", "Years of experience"),
            ("career.current_position", "Current/latest position"),
            ("career.current_company", "Current/latest company"),
            ("career.target_roles", "Target roles, comma separated"),
            ("career.target_seniority", "Target seniority levels, comma separated"),
            ("career.target_industries", "Target industries, comma separated"),
            ("career.positioning_statement", "One sentence positioning statement"),
            ("career.key_achievements", "Key achievements, comma separated"),
            ("career.signature_projects", "Signature projects, comma separated"),
        ],
    ),
    (
        "Skills",
        [
            ("skills.core", "Core skills, comma separated"),
            ("skills.tools", "Tools/software, comma separated"),
            ("skills.platforms", "Platforms, comma separated"),
            ("skills.methodologies", "Methodologies, comma separated"),
            ("skills.keywords_to_emphasize", "Keywords to emphasize, comma separated"),
            ("skills.keywords_to_avoid", "Keywords to avoid, comma separated"),
        ],
    ),
    (
        "Preferences",
        [
            ("preferences.minimum_salary_eur", "Minimum salary EUR"),
            ("preferences.locations", "Preferred locations/countries, comma separated"),
            ("preferences.remote_preference", "Remote/hybrid/on-site preference"),
            ("preferences.company_types", "Preferred company types, comma separated"),
            ("preferences.dealbreakers", "Dealbreakers, comma separated"),
        ],
    ),
    (
        "Application Rules",
        [
            ("restrictions.no_german_required", "Do not apply if German is mandatory? true/false"),
            ("restrictions.no_relocation_required", "Do not apply if relocation is mandatory? true/false"),
            ("restrictions.no_unpaid_roles", "Do not apply to unpaid roles? true/false"),
            ("autofill.default_answers.work_authorization", "Default answer: work authorization"),
            ("autofill.default_answers.sponsorship_required", "Default answer: sponsorship required"),
            ("autofill.default_answers.willing_to_relocate", "Default answer: willing to relocate"),
            ("autofill.default_answers.english_proficiency", "Default answer: English proficiency"),
        ],
    ),
]


def _set_path(data: dict, dotted: str, value):
    current = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _coerce_answer(answer: str):
    answer = answer.strip()
    if "," in answer:
        return [item.strip() for item in answer.split(",") if item.strip()]
    if answer.lower() == "true":
        return True
    if answer.lower() == "false":
        return False
    try:
        return int(answer)
    except ValueError:
        return answer


def _dump_simple_yaml(data: dict, indent: int = 0) -> str:
    lines = []
    pad = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(_dump_simple_yaml(value, indent + 2))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                lines.append(f"{pad}  - {item}")
        else:
            rendered = "true" if value is True else "false" if value is False else value
            lines.append(f"{pad}{key}: \"{rendered}\"" if isinstance(rendered, str) else f"{pad}{key}: {rendered}")
    return "\n".join(lines)


def run_onboarding(path: Path = DEFAULT_BIBLE_PATH) -> None:
    bible = CandidateBible.load(path)
    data = bible.data or {"version": 1}
    print(f"Updating {path}. Press Enter to keep an existing value.\n")
    for section, questions in SECTIONS:
        print(f"\n=== {section} ===")
        for dotted, prompt in questions:
            current = bible.get_path(dotted, "")
            answer = input(f"{prompt} [{current}]: ").strip()
            if answer:
                _set_path(data, dotted, _coerce_answer(answer))
    path.write_text(_dump_simple_yaml(data) + "\n", encoding="utf-8")
    print(f"\nCandidate Bible saved to {path}")


if __name__ == "__main__":
    run_onboarding()
