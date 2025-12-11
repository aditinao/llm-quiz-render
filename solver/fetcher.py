import requests
from bs4 import BeautifulSoup

def fetch_quiz(url):
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    # Extract question text
    question = soup.find(id="question").text.strip()

    # Extract submit URL
    submit_url = soup.find(id="submit-url").text.strip()

    return question, submit_url
