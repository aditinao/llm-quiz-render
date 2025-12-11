import google.generativeai as genai
import os
import requests

GEMINI_KEY = os.getenv("GEMINI_KEY")
PIPE_KEY = os.getenv("AIPIPE_KEY")

def solve_question(question):
    # First try Gemini
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(question)
        return response.text.strip()

    except Exception:
        pass  # fallback

    # Fallback to aipipe
    payload = {"input": question}
    r = requests.post(
        "https://api.aipipe.ai/generate",
        json=payload,
        headers={"Authorization": f"Bearer {PIPE_KEY}"}
    )
    return r.json()["output"]
