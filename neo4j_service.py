"""
neo4j_service.py
Ingesta del grafo en Neo4j (local Docker o AuraDB free tier).

Modelo:
  (:Company)-[:HAS_JOB]->(:Job)
  (:Job)-[:HAS_APPLICATION]->(:Application)
  (:Application)-[:ANSWERED]->(:Question)

Todo con MERGE (no CREATE) para que reintentos de n8n no dupliquen nodos
-> esto elimina la race condition típica cuando n8n reintenta un webhook.
"""

import os
from neo4j import GraphDatabase
from models import ApplicationResult

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


SCHEMA_CONSTRAINTS = [
    "CREATE CONSTRAINT company_name IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT job_id IF NOT EXISTS FOR (j:Job) REQUIRE j.id IS UNIQUE",
    "CREATE CONSTRAINT application_id IF NOT EXISTS FOR (a:Application) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT question_key IF NOT EXISTS FOR (q:Question) REQUIRE q.key IS UNIQUE",
]

INGEST_QUERY = """
MERGE (c:Company {name: $company})

MERGE (j:Job {id: $job_id})
  ON CREATE SET j.title = $job_title, j.created_at = datetime()
MERGE (c)-[:HAS_JOB]->(j)

MERGE (a:Application {id: $application_id})
  ON CREATE SET
    a.german_required = $german_required,
    a.cv_profile = $cv_profile,
    a.last_position = $last_position,
    a.motivation_answer = $motivation_answer,
    a.experience_answer = $experience_answer,
    a.pdf_name = $pdf_name,
    a.created_at = datetime()
MERGE (j)-[:HAS_APPLICATION]->(a)

WITH a
UNWIND $responses AS resp
MERGE (q:Question {key: resp.question})
  ON CREATE SET q.text = resp.question
MERGE (a)-[r:ANSWERED]->(q)
  ON CREATE SET r.answer = resp.answer
  ON MATCH SET r.answer = resp.answer
"""


def ensure_schema() -> None:
    with _driver.session() as session:
        for stmt in SCHEMA_CONSTRAINTS:
            session.run(stmt)


def ingest_application(result: ApplicationResult) -> None:
    with _driver.session() as session:
        session.run(
            INGEST_QUERY,
            company=result.company,
            job_id=f"{result.company}::{result.job_title}",
            job_title=result.job_title,
            application_id=result.ID,
            german_required=result.german_required,
            cv_profile=result.cv_profile,
            last_position=result.last_position,
            motivation_answer=result.motivation_answer,
            experience_answer=result.experience_answer,
            pdf_name=result.pdf_name,
            responses=[r.model_dump() for r in result.responses],
        )


def close() -> None:
    _driver.close()
