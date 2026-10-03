"""Candidate Bible loader and semantic lookup helpers.

The Bible is a versionable YAML file that acts as a source of truth for
Gemini, CV generation and autofill. The parser intentionally supports the
simple YAML shape used by candidate_bible.yaml without requiring PyYAML.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

DEFAULT_BIBLE_PATH = Path("candidate_bible.yaml")


def _coerce(value: str) -> Any:
    value = value.strip().strip('"').strip("'")
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value == "":
        return ""
    try:
        return int(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    last_key_at_indent: dict[int, str] = {}

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if stripped.startswith("- "):
            value = _coerce(stripped[2:])
            if isinstance(parent, list):
                parent.append(value)
            continue

        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            parent[key] = _coerce(value)
            last_key_at_indent[indent] = key
            continue

        next_container: Any = {}
        parent[key] = next_container
        last_key_at_indent[indent] = key
        stack.append((indent, next_container))

        # If following lines are list items, convert lazily when encountered.
        # The bundled Bible uses explicit keys for lists, handled by post pass.
    return _convert_empty_dict_lists(root, text)


def _convert_empty_dict_lists(data: Any, text: str) -> Any:
    if isinstance(data, dict):
        for key, value in list(data.items()):
            if value == {} and re.search(rf"^\s*{re.escape(key)}:\s*\n\s+- ", text, re.M):
                items = []
                capture = False
                base_indent = None
                for raw in text.splitlines():
                    if re.match(rf"^(\s*){re.escape(key)}:\s*$", raw):
                        capture = True
                        base_indent = len(raw) - len(raw.lstrip(" "))
                        continue
                    if capture:
                        indent = len(raw) - len(raw.lstrip(" "))
                        stripped = raw.strip()
                        if indent <= (base_indent or 0) and stripped:
                            break
                        if stripped.startswith("- "):
                            items.append(_coerce(stripped[2:]))
                data[key] = items
            else:
                data[key] = _convert_empty_dict_lists(value, text)
    return data


def _load_yaml_or_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except Exception:
        return _parse_simple_yaml(text)


def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    rows: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            next_prefix = f"{prefix}.{key}" if prefix else key
            rows.update(_flatten(value, next_prefix))
    elif isinstance(data, list):
        rows[prefix] = ", ".join(str(v) for v in data if v)
    else:
        rows[prefix] = data
    return rows


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


SEMANTIC_ALIASES = {
    "languages.english.level": ("english", "english proficiency", "fluent in english", "english skills"),
    "languages.german.level": ("german", "german proficiency", "deutsch", "german skills"),
    "languages.spanish.level": ("spanish", "spanish proficiency", "espanol", "español"),
    "personal.work_authorization": ("work authorization", "authorized to work", "work permit", "visa", "eligible to work"),
    "personal.availability": ("availability", "available from", "start date", "notice period"),
    "personal.location": ("location", "current location", "city", "where are you based", "address"),
    "preferences.minimum_salary_eur": ("salary", "expected salary", "compensation", "salary expectation"),
    "restrictions.no_relocation_required": ("relocation", "willing to relocate", "relocate"),
    "autofill.default_answers.sponsorship_required": ("sponsorship", "visa sponsorship", "require sponsorship"),
    "autofill.default_answers.willing_to_relocate": ("willing to relocate", "relocation required", "relocate for this role"),
    "autofill.default_answers.english_proficiency": ("english proficiency", "rate your english", "english level"),
    "career.years_experience": ("years of experience", "how many years", "professional experience"),
    "career.target_roles": ("target role", "desired role", "role interest", "position you are applying for"),
    "skills.core": ("skills", "core skills", "areas of expertise", "expertise"),
    "skills.tools": ("tools", "software", "marketing tools", "platform tools"),
}


@dataclass
class CandidateBible:
    data: dict[str, Any]
    path: Path = DEFAULT_BIBLE_PATH

    @classmethod
    def load(cls, path: str | Path = DEFAULT_BIBLE_PATH) -> "CandidateBible":
        path = Path(path)
        return cls(_load_yaml_or_json(path), path)

    def get_path(self, dotted: str, default: Any = "") -> Any:
        current: Any = self.data
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def to_gemini_context(self) -> str:
        flat = _flatten(self.data)
        lines = ["CANDIDATE_BIBLE_SOURCE_OF_TRUTH:"]
        for key, value in sorted(flat.items()):
            if value not in ("", None, []):
                lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def semantic_lookup(self, question_or_label: str, min_score: float = 0.58) -> tuple[str, Any, float]:
        flat = _flatten(self.data)
        best_key = ""
        best_value: Any = ""
        best_score = 0.0
        for key, value in flat.items():
            if value in ("", None, []):
                continue
            terms = (key.replace(".", " "),) + SEMANTIC_ALIASES.get(key, ())
            score = max(_sim(question_or_label, term) for term in terms)
            if score > best_score:
                best_key = key
                best_value = value
                best_score = score
        if best_score < min_score:
            return "", "", best_score
        return best_key, best_value, round(best_score, 3)

    def autofill_lookup(self, question_or_label: str) -> tuple[str, str, float]:
        key, value, score = self.semantic_lookup(question_or_label, min_score=0.56)
        if value in (True, False):
            value = "Yes" if value else "No"
        return key, str(value) if value not in ("", None) else "", score

    def get_learned(self, key: str) -> Any:
        """Lee una respuesta previamente aprendida (ver `learn()`).
        Devuelve "" si nunca se aprendió esta key."""
        return self.data.get("learned_answers", {}).get(key, "")

    def learn(self, key: str, value: Any) -> None:
        """
        Guarda una respuesta (típicamente inferida por IA una sola vez)
        para no volver a preguntarle a un modelo con temperature>0 cada
        vez -- la primera vez la infiere la IA mirando el CV real, desde
        la segunda vez en adelante es instantánea y siempre la misma.

        Estrategia ADITIVA a propósito: nunca reescribe ni reparsea el
        resto del YAML (evita corromper listas anidadas como
        personal.location). Si ya existe un bloque "learned_answers:" al
        final del archivo, actualiza/agrega la línea de esa key ahí. Si
        no existe, lo crea al final. El resto del archivo queda
        byte-por-byte intacto salvo por ese bloque.
        """
        self.data.setdefault("learned_answers", {})[key] = value

        if not self.path.exists():
            return
        text = self.path.read_text(encoding="utf-8")
        rendered_value = str(value)
        new_line = f"  {key}: \"{rendered_value}\""

        section_match = re.search(r"(?m)^learned_answers:\s*$", text)
        if not section_match:
            # No existe el bloque todavia -> lo agregamos al final.
            sep = "" if text.endswith("\n") else "\n"
            text = f"{text}{sep}learned_answers:\n{new_line}\n"
            self.path.write_text(text, encoding="utf-8")
            return

        # Ya existe el bloque -> ver si esta key en particular ya estaba.
        section_start = section_match.end()
        key_pattern = re.compile(rf"(?m)^  {re.escape(key)}:.*$")
        # Buscamos la key SOLO dentro del bloque learned_answers (desde
        # section_start hasta la siguiente línea que no esté indentada,
        # o el final del archivo).
        rest = text[section_start:]
        end_match = re.search(r"(?m)^(?!  |\s*$)", rest)
        block_end = section_start + (end_match.start() if end_match else len(rest))
        block = text[section_start:block_end]

        existing_key_match = key_pattern.search(block)
        if existing_key_match:
            new_block = block[: existing_key_match.start()] + new_line + block[existing_key_match.end():]
        else:
            new_block = block.rstrip("\n") + "\n" + new_line + "\n"

        text = text[:section_start] + new_block + text[block_end:]
        self.path.write_text(text, encoding="utf-8")


def _add_local_cv_facts(bible: CandidateBible, cv_path: Path) -> CandidateBible:
    """Read candidate facts from the local Word; never write private data to YAML.

    The authoritative CV overrides inherited identity, tools and career facts.
    Undocumented legal status, availability and preferences remain user-managed.
    """
    from docx import Document
    from cv_date_guard import read_current_role
    doc = Document(cv_path)
    paragraphs = doc.paragraphs
    lines = [p.text.strip() for p in paragraphs if p.text.strip()]
    text = "\n".join(lines)
    personal = bible.data.setdefault("personal", {})
    header = next((line for line in lines if "|" in line), "")
    if header:
        name = header.partition("|")[0].strip()
        personal["full_name"] = name
        personal["first_name"], _, personal["last_name"] = name.partition(" ")
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    phone = re.search(r"\+\d[\d ()-]{6,}\d", text)
    linkedin = re.search(r"https?://(?:www\.)?linkedin\.com/[^\s]+", text)
    if email:
        personal["email"] = email.group()
    if phone:
        personal["phone"] = phone.group().strip()
        location_line = next((line for line in lines if phone.group() in line), "")
        location = location_line.partition(phone.group())[0].strip(" |")
        if location:
            personal["location"] = location
    if linkedin:
        personal["linkedin"] = linkedin.group()

    sections = {}
    active = ""
    for paragraph in paragraphs:
        value = paragraph.text.strip()
        if not value or set(value) <= {"_"}:
            continue
        if paragraph.style.name == "Heading 1":
            active = value.casefold()
            sections.setdefault(active, [])
        elif active:
            sections[active].append(value)

    role = read_current_role(str(cv_path))
    professional = bible.data.setdefault("professional", {})
    professional["current_company"] = role.company
    professional["current_role_dates"] = role.dates
    first_role = next((p.text for p in paragraphs if p.style.name == "Heading 2" and "," in p.text), "")
    professional["current_position"] = first_role.partition(",")[0].strip()
    professional["experience"] = sections.get("experiencia", [])
    # Do not feed the template's placeholder zero as a factual career total.
    professional.pop("years_experience", None)
    bible.data["education"] = sections.get("estudios", [])
    bible.data["certifications"] = sections.get("certificados", [])
    bible.data.setdefault("skills", {})["tools"] = [
        item.strip() for line in sections.get("habilidades", [])
        for item in line.split(",") if item.strip()
    ]
    languages = {}
    aliases = {"español": "spanish", "inglés": "english", "portugués": "portuguese"}
    for line in sections.get("idiomas", []):
        parts = re.split(r"\s+[–—-]\s+", line, maxsplit=1)
        if len(parts) == 2:
            language, level = parts
            notes = ""
            match = re.fullmatch(r"\s*(.*?)\s*\(([^()]*)\)\s*", level)
            if match:
                level, notes = match.groups()
            languages[aliases.get(language.casefold(), language.casefold())] = {
                "level": level.strip(), "notes": notes.strip()}

    if languages:
        bible.data["languages"] = languages
    return bible


def load_candidate_bible(path: str | Path = DEFAULT_BIBLE_PATH,
                         cv_path: str | Path | None = None) -> CandidateBible:
    import os
    bible = CandidateBible.load(path)
    source = Path(cv_path or os.environ.get("CV_MAESTRO_DOCX", "Christian_CV.docx"))
    if source.exists():
        bible = _add_local_cv_facts(bible, source)
    return bible
