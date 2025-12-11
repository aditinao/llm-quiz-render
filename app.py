from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from solver.submit import solve_quiz

app = FastAPI()

class EvalRequest(BaseModel):
    email: str
    secret: str
    url: str


@app.post("/")
async def start_evaluation(payload: EvalRequest):
    # This is called when YOU manually POST using curl
    result = await solve_quiz(payload.email, payload.secret, payload.url)
    return {"status": "completed", "result": result}


# Run HF space
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
