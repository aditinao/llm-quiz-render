# tasks.py
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

def _decode_atob_candidates(page_content: str):
    """Find atob(`...`) base64 chunks and decode them (return list of decoded strings)."""
    decs = []
    matches = re.findall(r"atob\(\s*[`'\"]([A-Za-z0-9+/=\s\n\r]+)[`'\"]\s*\)", page_content)
    for b64 in matches:
        try:
            import base64
            decoded = base64.b64decode(b64.encode()).decode(errors="ignore")
            decs.append(decoded)
        except Exception:
            continue
    return decs

def _find_submit_url(page_content: str):
    m = re.search(r"https?://[^\s'\"<>]+/submit[^\s'\"<>]*", page_content)
    if m:
        return m.group(0)
    return None

def _download_and_try_sum(file_url: str):
    """Download CSV/XLSX and try to find numeric column named 'value' or numeric column and return sum."""
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(file_url)
            if r.status_code != 200:
                return None
            fname = os.path.join(WORK_DIR, os.path.basename(urlparse(file_url).path) or "datafile")
            with open(fname, "wb") as fh:
                fh.write(r.content)
        # parse CSV/XLSX
        if fname.lower().endswith(".csv"):
            df = pd.read_csv(fname)
        elif fname.lower().endswith((".xls", ".xlsx")):
            df = pd.read_excel(fname)
        else:
            return None
        # prefer column named value
        candidates = [c for c in df.columns if "value" in str(c).lower()]
        if not candidates:
            candidates = list(df.select_dtypes(include="number").columns)
        if not candidates:
            return None
        col = candidates[0]
        total = pd.to_numeric(df[col], errors="coerce").sum()
        # convert to int if integral
        total = int(total) if float(total).is_integer() else float(total)
        return total
    except Exception:
        return None

def process_quiz_job(email: str, secret: str, url: str):
    """
    Main worker function. Visit the quiz page, try multiple extraction strategies,
    compute answer, and post to the submit endpoint found on page.
    """
    start_ts = time.time()
    time_limit_seconds = 170

    def time_remaining():
        return time_limit_seconds - (time.time() - start_ts)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="networkidle")
        time.sleep(0.4)
        content = page.content()

        # 1) Try to find JSON inside <pre>
        answer_payload = None
        try:
            pre_el = page.query_selector("pre")
            if pre_el:
                txt = pre_el.inner_text().strip()
                try:
                    obj = json.loads(txt)
                    # Ensure we set email/secret/url in payload to required values
                    obj.setdefault("email", email)
                    obj.setdefault("secret", QUIZ_SECRET)
                    obj.setdefault("url", url)
                    answer_payload = obj
                except Exception:
                    pass
        except Exception:
            pass

        # 2) Try atob base64 decode for hidden JSON or instructions
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
                        obj.setdefault("url", url)
                        answer_payload = obj
                        break
                except Exception:
                    continue

        # 3) Try tables on page (extract first table, look for 'value' column or numeric column)
        if not answer_payload:
            tables = page.query_selector_all("table")
            if tables:
                try:
                    rows = []
                    for tr in tables[0].query_selector_all("tr"):
                        cells = [td.inner_text().strip() for td in tr.query_selector_all("td,th")]
                        if cells:
                            rows.append(cells)
                    if rows and len(rows) > 1:
                        df = pd.DataFrame(rows[1:], columns=rows[0])
                        # find 'value' col or numeric col
                        cand = [c for c in df.columns if "value" in str(c).lower()]
                        if not cand:
                            # coerce numeric then pick numeric columns
                            for c in df.columns:
                                df[c] = df[c].astype(str).str.replace(r"[^\d\.\-]", "", regex=True)
                            numeric_cols = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().any()]
                            cand = numeric_cols
                        if cand:
                            col = cand[0]
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                            total = df[col].sum()
                            total = int(total) if float(total).is_integer() else float(total)
                            answer_payload = {"email": email, "secret": QUIZ_SECRET, "url": url, "answer": total}
                except Exception:
                    pass

        # 4) Try to find downloadable CSV/XLSX links and parse them
        if not answer_payload:
            anchors = page.query_selector_all("a")
            for a in anchors:
                try:
                    href = a.get_attribute("href") or ""
                    if not href:
                        continue
                    full = urljoin(url, href)
                    if full.lower().endswith((".csv", ".xls", ".xlsx")):
                        total = _download_and_try_sum(full)
                        if total is not None:
                            answer_payload = {"email": email, "secret": QUIZ_SECRET, "url": url, "answer": total}
                            break
                except Exception:
                    continue

        # If still nothing, build a safe fallback payload (acknowledgement)
        if not answer_payload:
            answer_payload = {"email": email, "secret": QUIZ_SECRET, "url": url, "answer": None, "note": "could not auto-solve"}

        # Find submit URL (prefer explicit /submit links, then anchors with text 'submit')
        submit_url = _find_submit_url(content)
        if not submit_url:
            for a in page.query_selector_all("a"):
                try:
                    txt = (a.inner_text() or "").lower()
                    href = a.get_attribute("href") or ""
                    if "submit" in txt or "submit" in href:
                        submit_url = urljoin(url, href)
                        break
                except Exception:
                    continue

        if not submit_url:
            browser.close()
            return {"status": "no_submit_found", "payload": answer_payload}

        # Ensure we have time to post
        if time_remaining() < 5:
            browser.close()
            return {"status": "timeout_before_submit", "payload": answer_payload}

        # POST the answer
        with httpx.Client(timeout=60) as client:
            try:
                resp = client.post(submit_url, json=answer_payload, headers={"Content-Type": "application/json"})
                try:
                    resp_json = resp.json()
                except Exception:
                    resp_json = {"status_code": resp.status_code, "text": resp.text[:1000]}
            except Exception as e:
                resp_json = {"error": str(e)}

        browser.close()
        return {"submitted_to": submit_url, "payload_sent": answer_payload, "submit_response": resp_json}
