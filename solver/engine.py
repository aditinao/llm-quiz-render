# solver/engine.py
import os
import time
import json
import requests
from bs4 import BeautifulSoup

# Simple fetch helper using requests
def fetch_url_text(url, timeout=20):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text, r.headers

def post_json(url, payload, timeout=20):
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    # return parsed json if any, else raw text
    try:
        return r.json()
    except Exception:
        return r.text

def extract_submit_url_from_html(html, base_url=None):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form and form.get("action"):
        action = form.get("action")
        # handle relative action
        if action.startswith("http"):
            return action
        if base_url:
            return base_url.rstrip("/") + "/" + action.lstrip("/")
        return action
    # fallback: look for first https://.../submit in scripts
    for s in soup.find_all("script"):
        text = s.string or s.text or ""
        import re
        m = re.search(r"(https?://[^\s'\"<>]+/submit[^\s'\"<>]*)", text)
        if m:
            return m.group(1)
    # fallback: append /submit
    return None

def answer_from_text(text):
    # very simple heuristics; extend per test patterns
    low = text.lower()
    if "true or false" in low or "true/false" in low or "boolean" in low:
        return True
    import re
    m = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if m:
        num = m.group(1)
        return float(num) if "." in num else int(num)
    # default: short string
    return text.strip()[:500]

def run_quiz_flow(start_url, email, secret, logger=None, overall_timeout=180):
    """
    Visit start_url, extract question, produce an answer, submit to the submit URL.
    This is a simple, safe baseline for the test harness.
    """
    start_time = time.time()
    current_url = start_url
    session = requests.Session()

    while current_url and (time.time() - start_time) < overall_timeout:
        if logger:
            logger.info("Visiting: %s", current_url)
        html, headers = fetch_url_text(current_url)
        # get text to decide answer
        # prefer structured pre#quiz-data etc if present
        soup = BeautifulSoup(html, "html.parser")
        # try a <pre id="quiz-data">
        pre = soup.find("pre", id="quiz-data")
        if pre:
            quiz_text = pre.get_text()
        else:
            # otherwise full page text
            quiz_text = soup.get_text(separator=" ", strip=True)

        answer = answer_from_text(quiz_text)
        payload = {"email": email, "secret": secret, "answer": answer}

        # find submit URL
        submit_url = extract_submit_url_from_html(html, base_url=current_url)
        if not submit_url:
            # fallback assumption: same host + /submit
            from urllib.parse import urlparse, urljoin
            parsed = urlparse(current_url)
            submit_url = f"{parsed.scheme}://{parsed.netloc}/submit"

        if logger:
            logger.info("Submitting to: %s (answer type=%s)", submit_url, type(answer).__name__)

        resp = None
        try:
            resp = post_json(submit_url, payload)
        except Exception as e:
            if logger:
                logger.exception("POST JSON failed: %s", e)
            raise

        if logger:
            logger.info("Server response: %s", str(resp)[:1000])

        # parse next URL if provided
        next_url = None
        if isinstance(resp, dict):
            next_url = resp.get("url")
            # if no url and correct/incorrect included, we could continue/resubmit; for baseline just follow url
        else:
            # try to parse url from plain text
            try:
                j = json.loads(resp)
                next_url = j.get("url")
            except Exception:
                next_url = None

        if not next_url:
            return {"last_response": resp}

        current_url = next_url

    raise RuntimeError("Timeout or no next URL within allowed time")
