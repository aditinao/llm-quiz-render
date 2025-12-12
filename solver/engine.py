# solver/engine.py
import os
import time
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

# --- NEW IMPORTS FOR GEMINI API ---
try:
    from google import genai
    # ResourceExhaustedError is the specific error for hitting quota limits.
    from google.genai.errors import ResourceExhaustedError, APIError 
    
    # Initialize the Gemini Client. It will automatically pick up the GEMINI_API_KEY
    # from the environment variables, which should be set in your Hugging Face space secrets.
    GEMINI_CLIENT = genai.Client()
    GEMINI_MODEL = "gemini-2.5-flash" # Recommended for fast, simple quiz solving
except ImportError:
    # This block ensures the script can still run if the genai library isn't installed.
    # In a production environment, you should ensure the library is in requirements.txt
    GEMINI_CLIENT = None
    ResourceExhaustedError = Exception 
    APIError = Exception 

# --- CONFIGURATION FOR FALLBACK ---
# Fetch API details for the secondary fallback from environment variables
AIPIPE_URL = os.environ.get("AIPIPE_API_URL")
AIPIPE_KEY = os.environ.get("AIPIPE_API_KEY")

# Simple fetch helper using requests
def fetch_url_text(url, timeout=20):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text, r.headers

def post_json(url, payload, timeout=20, logger=None):
    headers = {"Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    # If status is not 2xx, include the body in the exception for debugging
    if not r.ok:
        body = r.text
        msg = f"POST {url} returned {r.status_code}. Body: {body}"
        if logger:
            logger.error(msg)
        # raise requests.HTTPError with body included
        r.raise_for_status()
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
            return urljoin(base_url, action)
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
    """
    Heuristic answer generator. Used as a type converter for LLM output,
    and as a final safety fallback if all APIs fail.
    """
    # very simple heuristics; extend per test patterns
    low = text.lower()
    # Return string "True" to be converted by the final answer processing
    if "true or false" in low or "true/false" in low or "boolean" in low:
        return "True" 
    import re
    m = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if m:
        num = m.group(1)
        return float(num) if "." in num else int(num)
    # default: short string
    return text.strip()[:500]


# --- NEW LLM AND FALLBACK LOGIC IMPLEMENTATION ---

def call_gemini_api(prompt, logger):
    """Calls the primary Gemini API."""
    if not GEMINI_CLIENT:
        raise ImportError("Gemini client not initialized. Check 'google-genai' install and API Key.")
        
    logger.info("Calling primary Gemini API...")
    
    # Simple System Instruction to ensure the model responds with just the answer
    system_instruction = (
        "You are an expert quiz solver. Analyze the quiz question and provide only "
        "the final answer without any explanation, preamble, or extra text. "
        "The answer must be a single string, number, or boolean ('True' or 'False')."
    )
    
    response = GEMINI_CLIENT.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "system_instruction": system_instruction,
            "temperature": 0.1, # Low temperature for deterministic answers
            "max_output_tokens": 50 
        }
    )
    
    answer_text = response.text.strip()
    logger.info(f"Gemini response: {answer_text[:100]}")
    return answer_text

def call_aipipe_fallback(prompt, logger):
    """Calls the secondary Aipipe endpoint for fallback."""
    if not AIPIPE_URL:
        raise RuntimeError("AIPIPE_API_URL environment variable is not set for fallback.")
        
    logger.warning("Calling secondary Aipipe Fallback API...")
    
    headers = {}
    if AIPIPE_KEY:
        # Assuming the fallback API uses an Authorization header
        headers["Authorization"] = f"Bearer {AIPIPE_KEY}"
        
    payload = {
        "model": "fallback-solver-model",
        "prompt": prompt
    }
    
    # Use requests.post directly for the external API call
    r = requests.post(AIPIPE_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    
    resp_data = r.json()
    # ASSUMPTION: The Aipipe response has a field like 'answer' or 'generated_text'
    answer_text = resp_data.get("answer") or resp_data.get("generated_text")
    
    if not answer_text:
        raise ValueError("Fallback API response did not contain a valid 'answer' field.")
        
    answer_text = str(answer_text).strip()
    logger.info(f"Aipipe Fallback response: {answer_text[:100]}")
    return answer_text


def get_llm_answer_with_fallback(quiz_text, logger):
    """
    Primary logic to get an answer, implementing the Gemini -> Aipipe fallback.
    """
    # 1. Primary Attempt: Gemini API
    try:
        raw_llm_answer = call_gemini_api(quiz_text, logger)
        return raw_llm_answer
        
    # Catch specific Gemini quota error (429) or generic API error
    except (ResourceExhaustedError, APIError) as e:
        logger.warning(f"Gemini API failed (Quota/API error: {type(e).__name__}). Initiating fallback.")
        
        # 2. Fallback Attempt: Aipipe
        try:
            raw_llm_answer = call_aipipe_fallback(quiz_text, logger)
            return raw_llm_answer
        except Exception as e_fallback:
            logger.error(f"Aipipe Fallback failed completely: {type(e_fallback).__name__}: {e_fallback}")
            return None # Signal a complete failure
            
    except Exception as e:
        logger.error(f"Gemini API failed with unhandled error: {type(e).__name__}: {e}. Initiating fallback.")
        
        # 2. Fallback Attempt: Aipipe for any other failure
        try:
            raw_llm_answer = call_aipipe_fallback(quiz_text, logger)
            return raw_llm_answer
        except Exception as e_fallback:
            logger.error(f"Aipipe Fallback failed completely: {type(e_fallback).__name__}: {e_fallback}")
            return None # Signal a complete failure

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
        soup = BeautifulSoup(html, "html.parser")
        
        # try a <pre id="quiz-data">
        pre = soup.find("pre", id="quiz-data")
        if pre:
            quiz_text = pre.get_text()
        else:
            # otherwise full page text
            quiz_text = soup.get_text(separator=" ", strip=True)

        # --- MODIFIED: Use the LLM with fallback first ---
        raw_llm_answer = get_llm_answer_with_fallback(quiz_text, logger)
        
        if raw_llm_answer is not None:
            # Convert the LLM's string output to the proper quiz submission type (number/string/boolean)
            answer = answer_from_text(raw_llm_answer)
            if logger:
                 logger.info("LLM provided answer, converted to type: %s", type(answer).__name__)
        else:
            # Final fallback to the simple heuristic on the raw quiz text if all LLM attempts failed
            answer = answer_from_text(quiz_text)
            if logger:
                logger.warning("All LLM attempts failed. Using final simple heuristic.")

        payload = {"email": email, "secret": secret, "url": current_url, "answer": answer}

        
        # find submit URL
        submit_url = extract_submit_url_from_html(html, base_url=current_url)
        if not submit_url:
            # fallback assumption: same host + /submit
            parsed = urlparse(current_url)
            submit_url = f"{parsed.scheme}://{parsed.netloc}/submit"
            
        if logger:
            logger.info("Submitting to: %s (answer type=%s, value=%s)", submit_url, type(answer).__name__, str(answer)[:50])
            
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
