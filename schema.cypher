// ============================================================
// schema.cypher — Job Application Automation
// Ejecutar una vez en Neo4j Browser / cypher-shell (o dejar que
// neo4j_service.ensure_schema() lo haga automáticamente al levantar
// el backend).
// ============================================================

// --- Constraints (garantizan unicidad = previene duplicados por
//     reintentos de n8n; también actúan como índice automático) ---

CREATE CONSTRAINT company_name IF NOT EXISTS
FOR (c:Company) REQUIRE c.name IS UNIQUE;

CREATE CONSTRAINT job_id IF NOT EXISTS
FOR (j:Job) REQUIRE j.id IS UNIQUE;

CREATE CONSTRAINT application_id IF NOT EXISTS
FOR (a:Application) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT question_key IF NOT EXISTS
FOR (q:Question) REQUIRE q.key IS UNIQUE;

// --- Modelo ---
// (:Company {name})
// (:Job {id, title, created_at})
// (:Application {id, german_required, cv_profile, last_position,
//                 motivation_answer, experience_answer, pdf_name, created_at})
// (:Question {key, text})
//
// (:Company)-[:HAS_JOB]->(:Job)
// (:Job)-[:HAS_APPLICATION]->(:Application)
// (:Application)-[:ANSWERED {answer}]->(:Question)


// ============================================================
// QUERIES DE REFERENCIA (para explorar el grafo desde Neo4j Browser)
// ============================================================

// 1. Ver todas las postulaciones a una empresa
// MATCH (c:Company {name: "Stanley Black & Decker"})-[:HAS_JOB]->(j:Job)-[:HAS_APPLICATION]->(a:Application)
// RETURN c, j, a;

// 2. Ver todas las respuestas dadas a una pregunta específica (útil para
//    reusar respuestas ya redactadas cuando se repite la misma pregunta
//    en distintas ofertas)
// MATCH (q:Question {text: "Experience with GA4"})<-[r:ANSWERED]-(a:Application)
// RETURN a.id, r.answer;

// 3. Grafo completo de una aplicación puntual (por ID)
// MATCH (a:Application {id: $application_id})<-[:HAS_APPLICATION]-(j:Job)<-[:HAS_JOB]-(c:Company)
// OPTIONAL MATCH (a)-[r:ANSWERED]->(q:Question)
// RETURN c, j, a, collect({question: q.text, answer: r.answer}) AS respuestas;

// 4. Cuántas postulaciones se han hecho por semana (analítica simple)
// MATCH (a:Application)
// RETURN date.truncate('week', a.created_at) AS semana, count(*) AS total
// ORDER BY semana;

// 5. Detectar preguntas que se repiten mucho entre ofertas (para armar un
//    banco de respuestas reusables)
// MATCH (q:Question)<-[:ANSWERED]-(a:Application)
// RETURN q.text, count(a) AS veces_preguntada
// ORDER BY veces_preguntada DESC
// LIMIT 20;
