import os
import time
import json
import re
import io
import base64
import urllib.request
from typing import Optional, Any, Dict

import httpx
import pandas as pd
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", None)  # Optional
WORK_DIR = "/tmp/llm_agent"
os.makedirs(WORK_DIR, exist_ok=True)

# ======================= PDF SUPPORT =======================


def extract_pdf(url: str):
    import pdfplumber

    file_path = os.path.join(WORK_DIR, f"{time.time()}.pdf")
    urllib.request.urlretrieve(url, file_path)
    text_chunks = []
    tables = []

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)
            tbl = page.extract_table()
            if tbl:
                df = pd.DataFrame(tbl[1:], columns=tbl[0])
                tables.append(df)

    full_text = "\n".join(text_chunks)
    return full_text, tables


# ======================= IMAGE OCR =======================


def extract_image(url: str) -> str:
    import pytesseract
    from PIL import Image

    data = httpx.get(url).content
    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img)


# ======================= PLOT (available if a quiz needs charts) =======================


def make_plot(df: pd.DataFrame) -> str:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    df.plot(ax=ax)
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode()


# ======================= ATOB JSON =======================


def decode_atob(html: str):
    out = []
    for b in re.findall(r'atob\("([^"]+)"\)', html):
        try:
            out.append(base64.b64decode(b).decode())
        except Exception:
            pass
    return out


# ======================= HELPER: NORMALIZE ANSWER =======================


def normalize_answer(value: Any) -> Any:
    """Convert numpy / pandas scalars to plain Python types for JSON."""
    try:
        if hasattr(value, "item"):  # numpy / pandas scalar
            return value.item()
    except Exception:
        pass

    if isinstance(value, pd.Series):
        return normalize_answer(value.to_dict())

    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="list")

    return value


# ======================= LLM REASONING FOR DATAFRAME =======================


def llm_reason_for_df(question_text: str, df: pd.DataFrame) -> Any:
    """
    Use an LLM to generate Python code that analyzes `df` to answer the question.
    The code must assign the final result to a variable named `answer`.
    """
    if not OPENAI_KEY:
        return {"note": "LLM disabled; no OPENAI_API_KEY set"}

    try:
        import openai
    except Exception:
        return {"note": "openai library not installed"}

    openai.api_key = OPENAI_KEY

    # Build a compact summary of the DataFrame
    try:
        dtypes = df.dtypes.astype(str).to_dict()
    except Exception:
        dtypes = {}

    try:
        head_str = df.head(10).to_string()
    except Exception:
        head_str = ""

    prompt = f"""
You are a careful data analysis assistant.

You are given:
- A pandas DataFrame named `df`.
- A natural language question about this data.

Write Python code that:
- Uses the existing variables `df` and `pd` (pandas as pd is already imported).
- DOES NOT import any additional libraries.
- Does not read or write files.
- Does not make any network calls.
- At the end, stores the final result in a variable named `answer`.
- Does not print anything.

Return ONLY valid Python code. No explanation, no markdown, no backticks.

QUESTION:
{question_text}

DATAFRAME COLUMNS:
{list(df.columns)}

DATAFRAME DTYPES:
{dtypes}

DATAFRAME HEAD (first rows):
{head_str}
"""

    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        code = resp["choices"][0]["message"]["content"]
    except Exception as e:
        return {"note": f"LLM call failed: {e}"}

    # Execute code in a restricted sandbox
    allowed_builtins = {
        "len": len,
        "range": range,
        "min": min,
        "max": max,
        "sum": sum,
        "float": float,
        "int": int,
        "abs": abs,
        "round": round,
    }

    safe_globals = {
        "__builtins__": allowed_builtins,
        "pd": pd,
    }
    safe_locals: Dict[str, Any] = {"df": df.copy()}

    try:
        exec(code, safe_globals, safe_locals)  # sandboxed on purpose
        if "answer" in safe_locals:
            return normalize_answer(safe_locals["answer"])
        else:
            return {"note": "LLM code executed but no `answer` variable was set"}
    except Exception as e:
        return {"note": f"Error executing LLM code: {e}"}


# ======================= RULE-BASED ANALYZE =======================


def analyze(df: pd.DataFrame, question_text: Optional[str] = None) -> Any:
    """
    Try to interpret common DS-style questions (sum/mean/min/max/count of a column).
    If that fails, fall back to generic stats. If LLM is available, delegate to it.
    """
    df_numeric = df.copy()

    # --- If we have a question, try to interpret it ---
    if question_text:
        q = question_text.lower()

        # Simple op detection
        wants_sum = any(k in q for k in ["sum", "total"])
        wants_mean = any(k in q for k in ["mean", "average", "avg"])
        wants_min = "minimum" in q or "smallest" in q or "lowest" in q
        wants_max = "maximum" in q or "largest" in q or "highest" in q
        wants_count = "count" in q or "how many" in q

        # Try to find which column they are talking about
        target_col = None
        for col in df_numeric.columns:
            col_l = str(col).lower()
            if col_l in q:
                target_col = col
                break

        if target_col is not None:
            s = pd.to_numeric(df_numeric[target_col], errors="coerce").dropna()
            if not s.empty:
                # Return a single number if the question clearly asks for one op
                if wants_sum and not (wants_mean or wants_min or wants_max or wants_count):
                    return float(s.sum())
                if wants_mean and not (wants_sum or wants_min or wants_max or wants_count):
                    return float(s.mean())
                if wants_min and not (wants_sum or wants_mean or wants_max or wants_count):
                    return float(s.min())
                if wants_max and not (wants_sum or wants_mean or wants_min or wants_count):
                    return float(s.max())
                if wants_count and not (wants_sum or wants_mean or wants_min or wants_max):
                    return int(s.count())
                # If multiple things are asked, return a dict for that one column
                return {
                    str(target_col): {
                        "sum": float(s.sum()),
                        "mean": float(s.mean()),
                        "min": float(s.min()),
                        "max": float(s.max()),
                        "count": int(s.count()),
                    }
                }

    # --- Fallback: generic stats for all numeric columns ---
    df_numeric = df_numeric.apply(pd.to_numeric, errors="ignore")
    numeric_cols = [
        c
        for c in df_numeric.columns
        if pd.to_numeric(df_numeric[c], errors="coerce").notna().any()
    ]

    if not numeric_cols:
        # No numeric columns at all
        if OPENAI_KEY:
            # Try LLM on the raw dataframe
            return llm_reason_for_df(question_text or "", df)
        return {"data_preview": df.head().to_dict()}

    stats = {}
    for col in numeric_cols:
        data = pd.to_numeric(df_numeric[col], errors="coerce").dropna()
        stats[str(col)] = {
            "sum": float(data.sum()),
            "mean": float(data.mean()),
            "min": float(data.min()),
            "max": float(data.max()),
            "count": int(data.count()),
        }

    # If we have LLM and a question, try a smarter answer
    if OPENAI_KEY and question_text:
        smart = llm_reason_for_df(question_text, df_numeric)
        return smart

    return stats


# ======================= GENERIC LLM FALLBACK (NO TABLE) =======================


def llm_reason_generic(text: str, numbers: Any) -> Any:
    if not OPENAI_KEY:
        return {"text_numbers": numbers}

    try:
        import openai
    except Exception:
        return {"text_numbers": numbers}

    openai.api_key = OPENAI_KEY

    prompt = f"""
You are a data question answering assistant.

You are given a question/page text and some numbers extracted from it.
Use them to answer the question as precisely as possible.

TEXT:
{text}

NUMBERS EXTRACTED:
{numbers}

Return ONLY the final answer, no explanation, no markdown.
The answer may be a number, boolean, or short string.
"""

    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp["choices"][0]["message"]["content"].strip()
        # Try to parse numeric answer if possible
        try:
            if "." in content:
                return float(content)
            return int(content)
        except Exception:
            # Just return the raw string otherwise
            return content
    except Exception as e:
        return {"note": f"LLM generic call failed: {e}", "numbers": numbers}


# ================================================================
# MAIN SOLVER — Handles Many Types of Questions
# ================================================================


def process_quiz_job(email: str, secret: str, start_url: str):
    current = start_url
    history = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()

        while current:
            page = ctx.new_page()
            page.goto(current, wait_until="networkidle")
            content = page.content()
            # FIXED: need a selector, so use body
            question_text = page.inner_text("body")

            payload = None

            # --- <pre> JSON: quiz directly gives us payload template ---
            try:
                block = page.query_selector("pre")
                if block:
                    obj = json.loads(block.inner_text())
                    # Respect template, but override email/secret/url
                    obj["email"] = email
                    obj["secret"] = QUIZ_SECRET
                    obj["url"] = current
                    payload = obj
            except Exception:
                pass

            # --- ATOB encoded JSON: like the sample atob(...) quiz ---
            if not payload:
                for txt in decode_atob(content):
                    try:
                        m = re.search(r"\{.*\}", txt, flags=re.S)
                        if not m:
                            continue
                        obj = json.loads(m.group())
                        obj["email"] = email
                        obj["secret"] = QUIZ_SECRET
                        obj["url"] = current
                        payload = obj
                        break
                    except Exception:
                        continue

            # --- TABLES (HTML) ---
            if not payload:
                tables = page.query_selector_all("table")
                if tables:
                    rows = []
                    for tr in tables[0].query_selector_all("tr"):
                        cells = [
                            x.inner_text().strip()
                            for x in tr.query_selector_all("td,th")
                        ]
                        if cells:
                            rows.append(cells)
                    if len(rows) > 1:
                        df = pd.DataFrame(rows[1:], columns=rows[0])
                        ans = analyze(df, question_text)
                        ans = normalize_answer(ans)
                        payload = {
                            "email": email,
                            "secret": QUIZ_SECRET,
                            "url": current,
                            "answer": ans,
                        }

            # --- CSV / EXCEL ---
            if not payload:
                for a in page.query_selector_all("a"):
                    href = a.get_attribute("href") or ""
                    file_url = urljoin(current, href)
                    if file_url.endswith((".csv", ".xlsx", ".xls")):
                        data = httpx.get(file_url).content
                        if file_url.endswith(".csv"):
                            df = pd.read_csv(io.StringIO(data.decode()))
                        else:
                            df = pd.read_excel(io.BytesIO(data))
                        ans = analyze(df, question_text)
                        ans = normalize_answer(ans)
                        payload = {
                            "email": email,
                            "secret": QUIZ_SECRET,
                            "url": current,
                            "answer": ans,
                        }
                        break

            # --- PDF ---
            if not payload:
                for a in page.query_selector_all("a"):
                    href = a.get_attribute("href") or ""
                    if href and href.lower().endswith(".pdf"):
                        pdf_url = urljoin(current, href)
                        text, tables = extract_pdf(pdf_url)
                        if tables:
                            df = tables[0]
                            ans = analyze(df, question_text)
                        else:
                            # No tables, just send text or LLM on text
                            nums = re.findall(r"\d+\.?\d*", text)
                            ans = llm_reason_generic(text, nums)
                        ans = normalize_answer(ans)
                        payload = {
                            "email": email,
                            "secret": QUIZ_SECRET,
                            "url": current,
                            "answer": ans,
                        }
                        break

            # --- FALLBACK (no obvious data structure) ---
            if not payload:
                text = question_text
                numbers = re.findall(r"\d+\.?\d*", text)
                if OPENAI_KEY:
                    ans = llm_reason_generic(text, numbers)
                else:
                    ans = numbers
                ans = normalize_answer(ans)
                payload = {
                    "email": email,
                    "secret": QUIZ_SECRET,
                    "url": current,
                    "answer": ans,
                }

            # --- FIND SUBMIT URL ---
            submit = None
            for a in page.query_selector_all("a"):
                label = (a.inner_text() or "") + " " + (a.get_attribute("href") or "")
                if "submit" in label.lower():
                    submit = urljoin(current, a.get_attribute("href"))
                    break

            if not submit:
                # No submit link: stop the quiz chain
                history.append(
                    {
                        "url": current,
                        "payload": payload,
                        "resp": {"note": "No submit URL found; stopping."},
                    }
                )
                break

            # --- SUBMIT ANSWER TO QUIZ SERVER ---
            resp = httpx.post(submit, json=payload, timeout=30)
            try:
                resp_json = resp.json()
            except Exception:
                resp_json = {"status_code": resp.status_code, "text": resp.text}

            history.append({"url": current, "payload": payload, "resp": resp_json})

            # Determine next URL, if any
            nexturl = (
                resp_json.get("url")
                or resp_json.get("next_url")
                or resp_json.get("nextTaskUrl")
            )
            if not nexturl:
                break
            current = nexturl

        browser.close()

    return {"status": "completed", "history": history}
