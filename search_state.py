"""
search_state.py
Guarda, por cada search_url de LinkedIn, en qué offset de paginación
("backlog_start") vamos con el BACKLOG (lo antiguo) entre corridas.

Por qué no basta con un solo offset:
  Si entre una corrida y la siguiente se publican ofertas nuevas, esas se
  van al TOPE de la lista (sortBy=DD) y empujan todo lo demás una
  posición hacia abajo. Si solo avanzáramos un offset de paginación
  puro, la próxima corrida saltaría directo a "más adelante" y esas
  ofertas nuevas -- que ahora están en las primeras posiciones -- nunca
  se verían.

  Por eso /linkedin-search en main.py hace SIEMPRE 2 consultas por
  corrida:
    1. "Front" (start=0, fijo): agarra lo que sea nuevo que se haya
       colado arriba desde la corrida anterior. Se filtra contra
       Postgres, así que lo ya visto no genera trabajo extra.
    2. "Backlog" (start=backlog_start, el que persiste este módulo):
       sigue bajando en el historial para no perderse ofertas más
       antiguas que en su momento no entraron en las primeras 25.

Es un archivo JSON simple en disco, NO Postgres, a propósito: es un
estado auxiliar y descartable (si se borra, el peor caso es reempezar el
backlog desde backlog_start=max_jobs en la próxima corrida -- nunca se
pierde nada de forma permanente porque /triage-job igual deduplica
contra Postgres).

Estrategia de avance del backlog (ver main.py /linkedin-search):
  - Mientras LinkedIn devuelva AL MENOS UNA url en el offset actual
    (nueva o ya conocida, da igual) -> se avanza backlog_start (+= tamaño
    de página) para la próxima corrida. Que una página venga toda
    duplicada NO significa que haya que reiniciar: solo significa que ese
    rango ya se escaneó antes: hay que seguir explorando más profundo.
  - Solo cuando LinkedIn devuelve una página VACÍA (0 resultados en ese
    offset) se asume que se llegó al final real de los resultados de esta
    búsqueda, y ahí sí se reinicia backlog_start a su valor por defecto
    para volver a escanear desde el principio en la próxima vuelta.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

STATE_FILE = Path("linkedin_search_state.json")


def _load() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_backlog_start(search_url: str, default: int) -> int:
    state = _load()
    return int(state.get(search_url, {}).get("backlog_start", default))


def set_backlog_start(search_url: str, backlog_start: int) -> None:
    state = _load()
    state[search_url] = {
        "backlog_start": max(0, backlog_start),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(state)

