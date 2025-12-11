# app.py
# Simple Flask app that accepts a manual POST (the curl you use) and runs the solver loop.
import os
import time
import json
import logging
from flask import Flask, request, jsonify
from solver.engine import run_quiz_flow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("p2-solver")

app = Flask(__name__)

# health
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok"})

# This is the endpoint you manually curl to trigger evaluation
@app.route("/start", methods=["POST"])  # call this URL with the curl you provided
def start():
    payload = request.get_json(force=True)
    email = payload.get("email")
    secret = payload.get("secret")
    start_url = payload.get("url")

    if not (email and secret and start_url):
        return jsonify({"error": "email, secret and url are required"}), 400

    # run in-process (blocking) — HF/Render call will wait.
    logger.info("Starting run for %s -> %s", email, start_url)
    t0 = time.time()
    try:
        result = run_quiz_flow(start_url, email=email, secret=secret, logger=logger)
        duration = time.time() - t0
        logger.info("Run finished in %.1fs", duration)
        return jsonify({"status": "done", "duration": duration, "result": result})
    except Exception as e:
        logger.exception("Run failed")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # for local debugging
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
