# tasks.py  (only showing the updated process_quiz_job)
import os
import time
import json
import re
from urllib.parse import urljoin, urlparse
import httpx
import pandas as pd
from playwright.sync_api import sync_playwright

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")
WORK_DIR = "/tmp/llm_quiz"
os.makedirs(WORK_DIR, exist_ok=True)

# ... keep your helper functions here: _decode_atob_candidates, _find_submit_url, _download_and_try_sum ...


def process_quiz_job(email: str, secret: str, start_url: str):
    """
    Solve a *chain* of quiz tasks starting from start_url, within a single
    ~3 minute window.
    Behaviour:
      - Start a global timer when called.
      - While we still have time and a current_url:
          - Visit current_url with Playwright
          - Extract/compute an answer
          - POST to that question's submit_url
          - Read the server response
          - If response has a `url`, set current_url to that
            (this is the "next task URL")
          - Otherwise stop
      - We always follow the URL from the *last* submission we made
        for that question.
    """
    start_ts = time.time()
    time_limit_seconds = 170  # little less than 3 min to be safe

    def time_remaining() -> float:
        return time_limit_seconds - (time.time() - start_ts)

    history = []  # keep info for each question attempt
    current_url = start_url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()

        while current_url and time_remaining() > 15:  # keep some buffer
            question_start = time.time()
            this_attempt = {
                "question_url": current_url,
                "answer_payload": None,
                "submit_url": None,
                "response": None,
                "error": None,
            }

            try:
                page = context.new_page()
                page.goto(current_url, wait_until="networkidle")
                time.sleep(0.4)
                content = page.content()

                # -------- build answer_payload as before --------
                answer_payload = None

                # 1) Try JSON in <pre>
                try:
                    pre_el = page.query_selector("pre")
                    if pre_el:
                        txt = pre_el.inner_text().strip()
                        try:
                            obj = json.loads(txt)
                            obj.setdefault("email", email)
                            obj.setdefault("secret", QUIZ_SECRET)
                            obj.setdefault("url", current_url)
                            answer_payload = obj
                        except Exception:
                            pass
                except Exception:
                    pass

                # 2) Try atob base64 JSON
                if not answer_payload:
                    decs = _decode_atob_candidates(content)
                    for d in decs:
                        try:
                            m = re.search(r"\{[\s\S]*\}", d)
                            if m:
                                candidate = m.group(0)
                                obj = json.loads(candidate)
                                obj.setdefault("email", email)
                                obj.setdefault("secret", QUIZ_SECRET)
                                obj.setdefault("url", current_url)
                                answer_payload = obj
                                break
                        except Exception:
                            continue

                # 3) Try HTML tables
                if not answer_payload:
                    tables = page.query_selector_all("table")
                    if tables:
                        try:
                            rows = []
                            for tr in tables[0].query_selector_all("tr"):
                                cells = [td.inner_text().strip()
                                         for td in tr.query_selector_all("td,th")]
                                if cells:
                                    rows.append(cells)
                            if rows and len(rows) > 1:
                                df = pd.DataFrame(rows[1:], columns=rows[0])
                                cand = [c for c in df.columns
                                        if "value" in str(c).lower()]
                                if not cand:
                                    for c in df.columns:
                                        df[c] = df[c].astype(str).str.replace(
                                            r"[^\d\.\-]", "", regex=True)
                                    numeric_cols = [
                                        c for c in df.columns
                                        if pd.to_numeric(df[c], errors="coerce").notna().any()
                                    ]
                                    cand = numeric_cols
                                if cand:
                                    col = cand[0]
                                    df[col] = pd.to_numeric(df[col], errors="coerce")
                                    total = df[col].sum()
                                    total = int(total) if float(total).is_integer() else float(total)
                                    answer_payload = {
                                        "email": email,
                                        "secret": QUIZ_SECRET,
                                        "url": current_url,
                                        "answer": total,
                                    }
                        except Exception:
                            pass

                # 4) Try downloadable CSV/XLSX
                if not answer_payload:
                    anchors = page.query_selector_all("a")
                    for a in anchors:
                        try:
                            href = a.get_attribute("href") or ""
                            if not href:
                                continue
                            full = urljoin(current_url, href)
                            if full.lower().endswith((".csv", ".xls", ".xlsx")):
                                total = _download_and_try_sum(full)
                                if total is not None:
                                    answer_payload = {
                                        "email": email,
                                        "secret": QUIZ_SECRET,
                                        "url": current_url,
                                        "answer": total,
                                    }
                                    break
                        except Exception:
                            continue

                # fallback payload if we couldn't compute anything
                if not answer_payload:
                    answer_payload = {
                        "email": email,
                        "secret": QUIZ_SECRET,
                        "url": current_url,
                        "answer": None,
                        "note": "could not auto-solve",
                    }

                # -------- find submit URL --------
                submit_url = _find_submit_url(content)
                if not submit_url:
                    for a in page.query_selector_all("a"):
                        try:
                            txt = (a.inner_text() or "").lower()
                            href = a.get_attribute("href") or ""
                            if "submit" in txt or "submit" in href:
                                submit_url = urljoin(current_url, href)
                                break
                        except Exception:
                            continue

                this_attempt["answer_payload"] = answer_payload
                this_attempt["submit_url"] = submit_url

                if not submit_url:
                    this_attempt["error"] = "no_submit_found"
                    history.append(this_attempt)
                    break  # can't continue the chain

                # Make sure we still have enough time to POST
                if time_remaining() < 5:
                    this_attempt["error"] = "time_almost_up_before_submit"
                    history.append(this_attempt)
                    break

                # -------- submit the answer --------
                with httpx.Client(timeout=60) as client:
                    resp = client.post(
                        submit_url,
                        json=answer_payload,
                        headers={"Content-Type": "application/json"},
                    )
                    try:
                        resp_json = resp.json()
                    except Exception:
                        resp_json = {
                            "status_code": resp.status_code,
                            "text": resp.text[:1000],
                        }

                this_attempt["response"] = resp_json
                history.append(this_attempt)

                # -------- decide the next URL --------
                # We always follow the URL from this *latest* response
                next_url = None
                if isinstance(resp_json, dict):
                    # They might use `url`, `next_url`, `nextTaskUrl`, etc.
                    next_url = (
                        resp_json.get("url")
                        or resp_json.get("next_url")
                        or resp_json.get("nextTaskUrl")
                    )

                # No new URL or time nearly gone => stop the outer loop
                if not next_url or time_remaining() < 15:
                    current_url = None
                else:
                    current_url = next_url

            except Exception as e:
                this_attempt["error"] = str(e)
                history.append(this_attempt)
                # If something explodes badly, break out
                break

        browser.close()

    # We return the whole chain; judge only cares about what we POSTed upstream
    return {
        "status": "chain_complete" if time_remaining() > 0 else "time_up",
        "time_remaining": time_remaining(),
        "history": history,
    }

