import os
import traceback
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from tasks import process_quiz_job

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")

app = FastAPI(title="LLM Analysis Quiz - HF Space")


class Payload(BaseModel):
    email: str
    secret: str
    url: str


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Spec: invalid JSON / payload -> 400
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid JSON or payload", "errors": exc.errors()},
    )


@app.post("/")
async def receive(payload: Payload):
    # Validate secret
    if payload.secret != QUIZ_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        # Run quiz logic in a background thread so Playwright doesn't block event loop
        result = await run_in_threadpool(
            process_quiz_job,
            payload.email,
            payload.secret,
            payload.url,
        )
        return {"status": "processed", "result": result}

    except Exception as e:
        tb = traceback.format_exc()
        # Return a 200 with error info so you can see it in /docs / HF logs
        return JSONResponse(
            status_code=200,
            content={
                "status": "solver_error",
                "error": str(e),
                "traceback": tb,
            },
        )
