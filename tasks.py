def process_quiz_job(email: str, secret: str, start_url: str):

    history = []
    current_url = start_url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()

        while current_url:

            # --------------------------------------------------
            # ⏳ 3-MINUTE WINDOW STARTS FOR THIS QUESTION ONLY
            # --------------------------------------------------
            question_start_time = time.time()
            def within_time():
                return (time.time() - question_start_time) < 180   # <-- new rule

            attempts = []
            last_response = None

            # ==================================================
            # 🔁 Attempt loop (supports retry if within 3 minutes)
            # ==================================================
            while within_time():

                page = context.new_page()
                page.goto(current_url, wait_until="networkidle")
                time.sleep(0.4)
                content = page.content()

                # --- build answer_payload (unchanged from your logic) ---
                answer_payload = None

                # 1) Try JSON <pre>
                try:
                    pre_el = page.query_selector("pre")
                    if pre_el:
                        try:
                            obj = json.loads(pre_el.inner_text().strip())
                            obj.setdefault("email", email)
                            obj.setdefault("secret", QUIZ_SECRET)
                            obj.setdefault("url", current_url)
                            answer_payload = obj
                        except: pass
                except: pass

                # 2) Try atob() extracted JSON
                if not answer_payload:
                    for d in _decode_atob_candidates(content):
                        try:
                            m = re.search(r"\{[\s\S]*\}", d)
                            if m:
                                obj = json.loads(m.group(0))
                                obj.setdefault("email", email)
                                obj.setdefault("secret", QUIZ_SECRET)
                                obj.setdefault("url", current_url)
                                answer_payload = obj
                                break
                        except: pass

                # 3) Try HTML table → auto sum "value" column
                if not answer_payload:
                    tables = page.query_selector_all("table")
                    if tables:
                        try:
                            rows = []
                            for tr in tables[0].query_selector_all("tr"):
                                cells = [td.inner_text().strip() for td in tr.query_selector_all("td,th")]
                                if cells: rows.append(cells)

                            if rows and len(rows)>1:
                                df = pd.DataFrame(rows[1:], columns=rows[0])
                                cand=[c for c in df.columns if "value" in c.lower()]
                                if not cand:
                                    for c in df.columns:
                                        df[c]=df[c].astype(str).str.replace(r"[^\d\.\-]","",regex=True)
                                    cand=[c for c in df.columns if pd.to_numeric(df[c],errors="coerce").notna().any()]
                                if cand:
                                    col=cand[0]
                                    df[col]=pd.to_numeric(df[col],errors="coerce")
                                    total=df[col].sum()
                                    answer_payload={
                                        "email":email,
                                        "secret":QUIZ_SECRET,
                                        "url":current_url,
                                        "answer": int(total) if float(total).is_integer() else float(total)
                                    }
                        except: pass

                # 4) Try downloadable CSV/XLSX
                if not answer_payload:
                    for a in page.query_selector_all("a"):
                        href=a.get_attribute("href") or ""
                        full=urljoin(current_url,href)
                        if full.lower().endswith((".csv",".xls",".xlsx")):
                            total=_download_and_try_sum(full)
                            if total is not None:
                                answer_payload={
                                    "email":email,"secret":QUIZ_SECRET,
                                    "url":current_url,"answer":total
                                }; break

                # 5) If still nothing — submit null
                if not answer_payload:
                    answer_payload={
                        "email":email,
                        "secret":QUIZ_SECRET,
                        "url":current_url,
                        "answer":None,
                        "note":"could not auto-solve"
                    }

                # ------------------------------------------------------
                # 🔥 Locate submit URL
                # ------------------------------------------------------
                submit_url=_find_submit_url(content)
                if not submit_url:
                    for a in page.query_selector_all("a"):
                        t=(a.inner_text() or "").lower()
                        h=a.get_attribute("href") or ""
                        if "submit" in t or "submit" in h:
                            submit_url=urljoin(current_url,h); break
                if not submit_url: break


                # ------------------------------------------------------
                # 🔥 SUBMIT — and only this response determines next step
                # ------------------------------------------------------
                with httpx.Client(timeout=50) as client:
                    try:
                        resp=client.post(submit_url,json=answer_payload).json()
                    except Exception:
                        resp={"error":"invalid response", "raw":resp.text[:200]}

                attempts.append({"payload":answer_payload,"response":resp})
                last_response=resp

                # ------------------------------------------------------
                # If correct → break immediately
                # If wrong → retry allowed as long as time remains
                # ------------------------------------------------------
                if resp.get("correct")==True: break   # success → move to next
                if not within_time(): break           # retry window closed → stop

            # ==================================================
            # End of one question — use ONLY the last submission URL
            # ==================================================

            history.append({"question_url":current_url,"attempts":attempts})

            next_url = (
                last_response.get("url")
                or last_response.get("next_url")
                or last_response.get("nextTaskUrl")
            )

            if not next_url: break  # quiz finished
            current_url = next_url  # next question

        browser.close()

    return {"status":"completed","history":history}
