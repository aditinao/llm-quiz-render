# app.py
import os
import time
import logging
from flask import Flask, request, jsonify

# import the function only (should not run anything heavy at import)
from solver.engine import run_quiz_flow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("p2-solver")

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok"})

@app.route("/start", methods=["POST"])
def start():
    payload = request.get_json(force=True)
    email = payload.get("email")
    secret = payload.get("secret")
    start_url = payload.get("url")

    if not (email and secret and start_url):
        return jsonify({"error":"email, secret and url are required"}), 400

    logger.info("Starting run for %s -> %s", email, start_url)
    t0 = time.time()
    try:
        result = run_quiz_flow(start_url, email=email, secret=secret, logger=logger)
        duration = time.time() - t0
        logger.info("Run finished in %.1fs", duration)
        return jsonify({"status":"done", "duration": duration, "result": result})
    except Exception as e:
        logger.exception("Run failed")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # only for local dev
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
