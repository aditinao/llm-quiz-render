import httpx
import asyncio

async def solve_question(url):
    # fetch question
    async with httpx.AsyncClient() as client:
        q = await client.get(url)
        data = q.json()

    # ---- your logic to compute answer here ----
    answer = compute_answer(data)

    # submit answer
    submit_url = data["submit"]
    payload = {"answer": answer}

    async with httpx.AsyncClient() as client:
        resp = await client.post(submit_url, json=payload)
        result = resp.json()

    return result


async def solve_quiz(email, secret, start_url):

    current_url = start_url

    while True:
        result = await solve_question(current_url)
        print("Solved:", result)

        if "url" not in result or not result["url"]:
            break  # finished quiz

        current_url = result["url"]

    return "Quiz Completed"
