# tasks.py — final with retry logic

import os
import time
import json
import re
import io
from urllib.parse import urljoin

import httpx
import pandas as pd
from playwright.sync_api import sync_playwright

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")
WORK_DIR = "/tmp/llm_quiz"
os.makedirs(WORK_DIR, exist_ok=True)


# ----------------------------------------------------------
# Helper: decode atob("BASE64") snippets in HTML
# ----------------------------------------------------------
def _decode_atob_candidates(html: str):
    out = []
    for m in re.findall(r'atob\("([^"]+)"\)', html):
        try:
            import base64
            out.append(base64.b64decode(m).decode("utf-8", errors="ignore"))
        except Exception:
            pass
    return out


# ----------------------------------------------------------
# Helper: download CSV / XLSX and try to sum a numeric column
# ----------------------------------------------------------
def _download_and_try_sum(url: str):
    try:
        with httpx.Client(timeout=30) as c:
            resp = c.get(url)
            resp.raise_for_status()
            content = resp.content

        if url.lower().endswith(".csv"):
            text = content.decode("utf-8", errors="ignore")
            df = pd.read_csv(io.StringIO(text))
        else:
            df = pd.read_excel(io.BytesIO(content))

        for col in df.columns:
            nums = pd.to_numeric(df[col], errors="coerce")
            if nums.notna().any():
                return float(nums.sum())
    except Exception:
        return None


# ----------------------------------------------------------
# Helper: find a submit URL in HTML
# ----------------------------------------------------------
def _find_submit_url(content: str):
    # Direct absolute submit URL in quotes
    m = re.search(r'"(https?://[^"]*submit[^"]*)"', content, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


# ----------------------------------------------------------
# Main solver
# ----------------------------------------------------------
def process_quiz_job(email: str, secret: str, start_url: str):
    """
    Solve a chain of quiz tasks starting from start_url.

    For each question URL:
      - Conceptually has a 3-minute window (we enforce a ~165s safety margin).
      - We allow up to MAX_ATTEMPTS submissions.
      - Only the LAST submission's response is used to decide the next URL.
    """

    history = []
    current_url = start_url
    MAX_ATTEMPTS = 2  # retry up to 2 times per question if time allows
    TIME_LIMIT = 165  # seconds, safety below 180

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()

        while current_url:
            question_start = time.time()

            def time_left() -> float:
                return TIME_LIMIT - (time.time() - question_start)

            attempts = []
            last_response = None

            # 🔁 retry loop for this question
            attempt_index = 0
            while attempt_index < MAX_ATTEMPTS and time_left() > 15:
                attempt_index += 1

                attempt_record = {
                    "attempt_index": attempt_index,
                    "question_url": current_url,
                    "started_at": time.time(),
                    "payload": None,
                    "submit_url": None,
                    "response": None,
                    "error": None,
                }

                try:
                    page = context.new_page()
                    page.goto(current_url, wait_until="networkidle")
                    time.sleep(0.4)
                    content = page.content()

                    # ======================================================
                    # Build answer_payload (same logic as before)
                    # ======================================================
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
                                    cells = [
                                        td.inner_text().strip()
                                        for td in tr.query_selector_all("td,th")
                                    ]
                                    if cells:
                                        rows.append(cells)
                                if rows and len(rows) > 1:
                                    df = pd.DataFrame(rows[1:], columns=rows[0])
                                    cand = [
                                        c for c in df.columns
                                        if "value" in str(c).lower()
                                    ]
                                    if not cand:
                                        for c in df.columns:
                                            df[c] = df[c].astype(str).str.replace(
                                                r"[^\d\.\-]", "", regex=True
                                            )
                                        numeric_cols = [
                                            c
                                            for c in df.columns
                                            if pd.to_numeric(df[c], errors="coerce")
                                            .notna()
                                            .any()
                                        ]
                                        cand = numeric_cols
                                    if cand:
                                        col = cand[0]
                                        df[col] = pd.to_numeric(df[col], errors="coerce")
                                        total = df[col].sum()
                                        if float(total).is_integer():
                                            total = int(total)
                                        else:
                                            total = float(total)
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

                    # ======================================================
                    # Find submit URL
                    # ======================================================
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

                    attempt_record["payload"] = answer_payload
                    attempt_record["submit_url"] = submit_url

                    if not submit_url:
                        attempt_record["error"] = "no_submit_found"
                        attempts.append(attempt_record)
                        # cannot continue this question
                        break

                    # Make sure we have a bit of time to submit
                    if time_left() < 5:
                        attempt_record["error"] = "time_almost_up_before_submit"
                        attempts.append(attempt_record)
                        break

                    # ======================================================
                    # Submit the answer
                    # ======================================================
                    with httpx.Client(timeout=50) as client:
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

                    attempt_record["response"] = resp_json
                    attempts.append(attempt_record)
                    last_response = resp_json

                    # ======================================================
                    # Decide whether to retry or move on
                    # ======================================================
                    correct_flag = None
                    if isinstance(resp_json, dict):
                        correct_flag = resp_json.get("correct")

                    # If marked correct → stop retrying for this question
                    if correct_flag is True:
                        break

                    # If last attempt or low time left → stop retrying
                    if attempt_index >= MAX_ATTEMPTS or time_left() < 20:
                        break

                    # Otherwise: we *could* try again (e.g., with better heuristics),
                    # but in this simple version we'll recompute the same way
                    # on the next loop iteration.

                except Exception as e:
                    attempt_record["error"] = str(e)
                    attempts.append(attempt_record)
                    break  # something exploded, stop retrying

            # ===== end of attempts for this question =====
            history.append(
                {
                    "question_url": current_url,
                    "attempts": attempts,
                }
            )

            # Decide next URL based on the LAST submission only
            if not last_response or not isinstance(last_response, dict):
                break

            next_url = (
                last_response.get("url")
                or last_response.get("next_url")
                or last_response.get("nextTaskUrl")
            )

            if not next_url:
                break  # quiz ended

            current_url = next_url  # move to next question

        browser.close()

    return {
        "status": "chain_complete",
        "history": history,
    }
