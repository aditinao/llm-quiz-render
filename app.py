# app.py
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from tasks import process_quiz_job  # we call your solver directly

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")

app = FastAPI(title="LLM Analysis Quiz - HF Space")

class Payload(BaseModel):
    email: str
    secret: str
    url: str

@app.post("/")
async def receive(payload: Payload):
    # 1) verify secret
    if payload.secret != QUIZ_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # 2) run the quiz solver (this will visit the URL, compute answer, submit it)
    result = process_quiz_job(payload.email, payload.secret, payload.url)

    # 3) return result (we already POST to the submit URL inside tasks.py)
    return {
        "status": "processed",
        "result": result
    }
