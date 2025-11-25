# app.py
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from redis import Redis
from rq import Queue

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

r = Redis.from_url(REDIS_URL)
q = Queue("quiz-jobs", connection=r)

app = FastAPI(title="LLM Analysis Quiz - Render-ready")

class Payload(BaseModel):
    email: str
    secret: str
    url: str

@app.post("/")
async def receive(payload: Payload):
    if payload.secret != QUIZ_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    job = q.enqueue("tasks.process_quiz_job", payload.email, payload.secret, payload.url, job_timeout=180)
    return {"status": "accepted", "job_id": job.get_id()}
