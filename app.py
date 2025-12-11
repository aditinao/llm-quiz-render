import json
from fastapi import FastAPI, Request
import uvicorn
from solver.fetcher import fetch_quiz
from solver.solve import solve_question
from solver.submit import submit_answer

app = FastAPI()

@app.post("/")
async def start_eval(request: Request):
    """
    This endpoint is what you ping using:

    curl -X POST YOUR_HF_URL \
        -H "Content-Type: application/json" \
        -d '{"email": "...", "secret": "...", "url": "QUIZ_URL"}'
    """

    data = await request.json()
    quiz_url = data["url"]

    while quiz_url:
        print(f"\n🔵 Fetching quiz: {quiz_url}")
        question, submit_url = fetch_quiz(quiz_url)

        print(f"Question extracted: {question}")
        answer = solve_question(question)

        print(f"Submitting answer to {submit_url}")
        resp = submit_answer(submit_url, answer)

        print("Response:", resp)

        if resp.get("url"):
            quiz_url = resp["url"]
        else:
            print("🎉 Quiz completed")
            break

    return {"status": "completed"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
