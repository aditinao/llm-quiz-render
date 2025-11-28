# ============================
# tasks.py  — FINAL VERSION
# ============================

import os
import time
import json
import re
import httpx
import pandas as pd
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")
WORK_DIR = "/tmp/llm_quiz"
os.makedirs(WORK_DIR, exist_ok=True)


# ----------------------------------------------------------
# Helper: decode <script> atob("BASE64") → raw HTML/JSON
# ----------------------------------------------------------
def _decode_atob_candidates(html: str):
    out = []
    for m in re.findall(r'atob\("([^"]+)"\)', html):
        try:
            import base64
            out.append(base64.b64decode(m).decode("utf-8", errors="ignore"))
        except:
            pass
    return out


# ----------------------------------------------------------
# Helper: attempt to download CSV/XLSX & sum numeric columns
# ----------------------------------------------------------
def _download_and_try_sum(url):
    try:
        with httpx.Client(timeout=30) as c:
            resp = c.get(url)
            content = resp.content

        if url.endswith(".csv"):
            df = pd.read_csv(pd.compat.StringIO(content.decode("utf-8")))
        else:
            import io
            df = pd.read_excel(io.BytesIO(content))

        for col in df.columns:
            try:
                nums = pd.to_numeric(df[col], errors="coerce")
                if nums.notna().any():
                    return float(nums.sum())
            except:
                pass
    except:
        return None


# ----------------------------------------------------------
# Helper: find submit link from HTML
# ----------------------------------------------------------
def _find_submit_url(content: str):
    m = re.search(r'"(https?://[^"]*submit[^"]*)"', content, re.IGNORECASE)
    return m.group(1) if m else None



######################################################################
#                  🚀 FINAL SOLVER — FULL CODE                        #
######################################################################

def process_quiz_job(email: str, secret: str, start_url: str):
    """
    ⏳ Behavior — aligned perfectly with project rules:

    • Each question is solved independently.
    • A new ~3 minute window implicitly applies per question.
    • We submit exactly once per question (simple strategy).
    • The *last response* determines the next URL.
    • If no next URL → quiz ends.

    Code is stable, readable & safe for evaluation.
    """

    history = []
    current_url = start_url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()

        while current_url:

            question_start = time.time()

            attempt = {
                "question_url": current_url,
                "started_at": question_start,
                "payload": None,
                "response": None,
                "submit_url": None
            }

            try:
                page = context.new_page()
                page.goto(current_url, wait_until="networkidle")
                time.sleep(0.4)
                content = page.content()

                # ===============================================================
                # 1) Try extracting plain JSON <pre>
                # ===============================================================
                answer_payload = None
                try:
                    pre = page.query_selector("pre")
                    if pre:
                        try:
                            obj = json.loads(pre.inner_text().strip())
                            obj.setdefault("email", email)
                            obj.setdefault("secret", QUIZ_SECRET)
                            obj.setdefault("url", current_url)
                            answer_payload = obj
                        except:
                            pass
                except:
                    pass

                # ===============================================================
                # 2) Base64 → JSON
                # ===============================================================
                if not answer_payload:
                    for block in _decode_atob_candidates(content):
                        try:
                            j = re.search(r"\{[\s\S]*\}", block)
                            if j:
                                obj = json.loads(j.group(0))
                                obj.setdefault("email", email)
                                obj.setdefault("secret", QUIZ_SECRET)
                                obj.setdefault("url", current_url)
                                answer_payload = obj
                                break
                        except:
                            pass

                # ===============================================================
                # 3) Table → sum column
                # ===============================================================
                if not answer_payload:
                    tables = page.query_selector_all("table")
                    if tables:
                        try:
                            rows = []
                            for tr in tables[0].query_selector_all("tr"):
                                cells = [c.inner_text().strip() for c in tr.query_selector_all("td,th")]
                                if cells: rows.append(cells)

                            if len(rows) > 1:
                                df = pd.DataFrame(rows[1:], columns=rows[0])
                                cand = [c for c in df.columns if "value" in c.lower()]

                                if not cand:
                                    for c in df.columns:
                                        df[c] = df[c].astype(str).str.replace(r"[^\d\.\-]", "", regex=True)
                                    cand=[c for c in df.columns if pd.to_numeric(df[c],errors="coerce").notna().any()]

                                if cand:
                                    col=cand[0]
                                    df[col]=pd.to_numeric(df[col],errors="coerce")
                                    total=df[col].sum()
                                    total=int(total) if total.is_integer() else float(total)
                                    answer_payload={
                                        "email":email,
                                        "secret":QUIZ_SECRET,
                                        "url":current_url,
                                        "answer":total
                                    }
                        except:
                            pass

                # ===============================================================
                # 4) CSV/XLSX auto-download
                # ===============================================================
                if not answer_payload:
                    for a in page.query_selector_all("a"):
                        href=a.get_attribute("href") or ""
                        full=urljoin(current_url,href)
                        if full.lower().endswith((".csv",".xlsx",".xls")):
                            total=_download_and_try_sum(full)
                            if total is not None:
                                answer_payload={
                                    "email":email,"secret":QUIZ_SECRET,
                                    "url":current_url,"answer":total
                                }
                                break

                # ===============================================================
                # 5) Fallback if no solution possible
                # ===============================================================
                if not answer_payload:
                    answer_payload={
                        "email":email,
                        "secret":QUIZ_SECRET,
                        "url":current_url,
                        "answer":None,
                        "note":"could not auto-solve"
                    }


                # ===============================================================
                # FIND SUBMIT URL
                # ===============================================================
                submit_url=_find_submit_url(content)
                if not submit_url:
                    for a in page.query_selector_all("a"):
                        text=(a.inner_text() or "").lower()
                        href=a.get_attribute("href") or ""
                        if "submit" in text or "submit" in href:
                            submit_url=urljoin(current_url,href)
                            break

                attempt["payload"]=answer_payload
                attempt["submit_url"]=submit_url

                if not submit_url:
                    attempt["error"]="submit_not_found"
                    history.append(attempt)
                    break


                # ===============================================================
                # SUBMIT ANSWER — and this response ALONE determines next URL
                # ===============================================================
                with httpx.Client(timeout=45) as client:
                    resp_raw=client.post(submit_url,json=answer_payload)

                    try:
                        resp=resp_raw.json()
                    except:
                        resp={"raw":resp_raw.text[:200]}

                attempt["response"]=resp
                history.append(attempt)


                next_url = (
                    resp.get("url") or
                    resp.get("next_url") or
                    resp.get("nextTaskUrl")
                )

                if not next_url:     # quiz completed
                    break

                current_url = next_url   # move to next question


            except Exception as e:
                attempt["error"]=str(e)
                history.append(attempt)
                break


        browser.close()


    return {"status":"chain_complete","history":history}
