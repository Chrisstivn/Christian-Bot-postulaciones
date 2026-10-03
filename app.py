from fastapi import FastAPI

app = FastAPI()

@app.post("/process-application")
def process():
    return {"status": "ok"}
