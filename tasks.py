import os, time, json, re, io, base64, urllib.request
import httpx, pandas as pd
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

QUIZ_SECRET = os.getenv("QUIZ_SECRET", "261")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", None)   # Optional
WORK_DIR = "/tmp/llm_agent"
os.makedirs(WORK_DIR, exist_ok=True)

# ======================= PDF SUPPORT =======================
def extract_pdf(url):
    import pdfplumber
    file = os.path.join(WORK_DIR, f"{time.time()}.pdf")
    urllib.request.urlretrieve(url, file)
    with pdfplumber.open(file) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        tables = []
        for p in pdf.pages:
            tbl = p.extract_table()
            if tbl:
                tables.append(pd.DataFrame(tbl[1:], columns=tbl[0]))
        return text, tables

# ======================= IMAGE OCR =======================
def extract_image(url):
    import pytesseract
    from PIL import Image
    data = httpx.get(url).content
    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img)

# ======================= PLOT =======================
def make_plot(df):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    df.plot(ax=ax)
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode()

# ======================= ATOB JSON =======================
def decode_atob(html):
    out=[]
    for b in re.findall(r'atob\("([^"]+)"\)', html):
        try: out.append(base64.b64decode(b).decode())
        except: pass
    return out

# ======================= SMART COMPUTE =======================
def analyze(df):
    df = df.apply(pd.to_numeric, errors="ignore")

    numeric = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().any()]
    if not numeric: return {"data_preview": df.head().to_dict()}

    stats = {}
    for col in numeric:
        data = pd.to_numeric(df[col], errors="coerce").dropna()
        stats[col] = {
            "sum": float(data.sum()),
            "mean": float(data.mean()),
            "min": float(data.min()),
            "max": float(data.max()),
            "count": int(data.count())
        }
    return stats

# ======================= LLM REASONING (OPTIONAL) =======================
def llm_reason(text,data):
    """If question unclear, generate answer using GPT"""
    if not OPENAI_KEY: return {"note":"LLM disabled"}
    import openai
    openai.api_key = OPENAI_KEY

    prompt=f"""
    You are a data analysis agent. Solve using available data only.

    QUESTION:
    {text}

    AVAILABLE DATA:
    {data}

    ANSWER WITH JSON ONLY.
    """

    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}]
    )
    return resp["choices"][0]["message"]["content"]

# ================================================================
# MAIN SOLVER — Handles Any Type of Question
# ================================================================
def process_quiz_job(email, secret, start_url):
    current = start_url
    history=[]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()

        while current:
            page=ctx.new_page()
            page.goto(current,wait_until="networkidle")
            content=page.content()

            payload=None

            # --- <pre> JSON ---
            try:
                block=page.query_selector("pre")
                if block:
                    obj=json.loads(block.inner_text())
                    obj["email"]=email
                    obj["secret"]=QUIZ_SECRET
                    obj["url"]=current
                    payload=obj
            except: pass

            # --- ATOB encoded JSON ---
            if not payload:
                for txt in decode_atob(content):
                    try:
                        obj=json.loads(re.search(r"\{.*\}",txt,flags=re.S).group())
                        obj["email"]=email
                        obj["secret"]=QUIZ_SECRET
                        obj["url"]=current
                        payload=obj; break
                    except: pass

            # --- TABLES (HTML) ---
            if not payload and (tbl:=page.query_selector_all("table")):
                rows=[]
                for tr in tbl[0].query_selector_all("tr"):
                    cells=[x.inner_text().strip() for x in tr.query_selector_all("td,th")]
                    if cells: rows.append(cells)
                if len(rows)>1:
                    df=pd.DataFrame(rows[1:],columns=rows[0])
                    payload={"email":email,"secret":QUIZ_SECRET,"url":current,"answer":analyze(df)}

            # --- CSV / EXCEL ---
            if not payload:
                for a in page.query_selector_all("a"):
                    href=a.get_attribute("href") or ""
                    file=urljoin(current,href)
                    if file.endswith((".csv",".xlsx",".xls")):
                        data=httpx.get(file).content
                        df=pd.read_csv(io.StringIO(data.decode())) if ".csv" in file else pd.read_excel(io.BytesIO(data))
                        payload={"email":email,"secret":QUIZ_SECRET,"url":current,"answer":analyze(df)}
                        break

            # --- PDF ---
            if not payload:
                for a in page.query_selector_all("a"):
                    href=a.get_attribute("href") or ""
                    if href.endswith(".pdf"):
                        text,tables=extract_pdf(urljoin(current,href))
                        ans=analyze(tables[0]) if tables else text
                        payload={"email":email,"secret":QUIZ_SECRET,"url":current,"answer":ans}
                        break

            # --- FALLBACK (LLM or text dump) ---
            if not payload:
                text=page.inner_text()
                data=re.findall(r"\d+",text)
                payload={"email":email,"secret":QUIZ_SECRET,"url":current,"answer":data or llm_reason(text,data)}

            # --- SUBMIT ---
            submit=None
            for a in page.query_selector_all("a"):
                if "submit" in (a.inner_text()+" "+a.get_attribute("href")).lower():
                    submit=urljoin(current,a.get_attribute("href")); break

            if not submit: break  # end if no submit available

            resp=httpx.post(submit,json=payload).json()
            history.append({"url":current,"payload":payload,"resp":resp})

            nexturl=resp.get("url") or resp.get("next_url") or resp.get("nextTaskUrl")
            if not nexturl: break
            current=nexturl

        browser.close()

    return {"status":"completed","history":history}
