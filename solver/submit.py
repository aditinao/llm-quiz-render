import requests

def submit_answer(url, answer):
    payload = {"answer": answer}
    r = requests.post(url, json=payload)
    return r.json()
