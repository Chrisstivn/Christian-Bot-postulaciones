import os
import time
import requests
import psycopg2

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "user": "jobbot",
    "password": os.environ.get("POSTGRES_PASSWORD", ""),
    "dbname": "jobqueue"
}

BACKEND_URL = "http://localhost:8000/process-application"


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def fetch_jobs():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, job_url
        FROM job_queue
        WHERE status = 'pending'
        AND retry_count < 3
        ORDER BY created_at ASC
        LIMIT 5;
    """)

    jobs = cur.fetchall()
    cur.close()
    conn.close()
    return jobs


def mark_processing(job_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE job_queue
        SET status = 'processing', updated_at = NOW()
        WHERE id = %s;
    """, (job_id,))

    conn.commit()
    cur.close()
    conn.close()


def mark_done(job_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE job_queue
        SET status = 'done', updated_at = NOW()
        WHERE id = %s;
    """, (job_id,))

    conn.commit()
    cur.close()
    conn.close()


def mark_failed(job_id, error):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE job_queue
        SET status = 'failed',
            retry_count = retry_count + 1,
            last_error = %s,
            updated_at = NOW()
        WHERE id = %s;
    """, (str(error), job_id))

    conn.commit()
    cur.close()
    conn.close()


def process_job(job_id, job_url):
    try:
        mark_processing(job_id)

        r = requests.post(BACKEND_URL, json={
            "url": job_url,
            "job_text": ""
        }, timeout=300)

        if r.status_code != 200:
            raise Exception(r.text)

        mark_done(job_id)

    except Exception as e:
        mark_failed(job_id, e)


def main():
    while True:
        jobs = fetch_jobs()

        if not jobs:
            print("No pending jobs...")
            time.sleep(10)
            continue

        for job_id, job_url in jobs:
            print(f"Processing {job_url}")
            process_job(job_id, job_url)

        time.sleep(3)


if __name__ == "__main__":
    main()
