import os
import traceback
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool  # 👈 REQUIRED FIX
from tasks import process_quiz_job

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")

app = FastAPI(title="LLM Analysis Quiz - HF Space")

class Payload(BaseModel):
    email: str
    secret: str
    url: str

@app.post("/")
async def receive(payload: Payload):
    # Validate secret
    if payload.secret != QUIZ_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        # 🟢 Run quiz logic in background thread so Playwright doesn’t crash
        result = await run_in_threadpool(
            process_quiz_job,
            payload.email,
            payload.secret,
            payload.url
        )
        return {"status": "processed", "result": result}

    except Exception as e:
        tb = traceback.format_exc()
        # Instead of 500, return traceback to debug inside /docs
        return JSONResponse(
            status_code=200,
            content={
                "status": "solver_error",
                "error": str(e),
                "traceback": tb
            }
        )

