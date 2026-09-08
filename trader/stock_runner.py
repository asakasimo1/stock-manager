"""
주식 자동매매 HTTP 서버 — Oracle VM (고정 IP)에서 실행
GitHub Actions에서 curl로 각 job 호출
"""
import subprocess, sys, os, logging
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SECRET = os.getenv("STOCK_RUNNER_SECRET", "")

JOB_MAP = {
    "signals":          "job_signals.py",
    "buy":              "job_buy.py",
    "monitor":          "job_monitor.py",
    "close":            "job_close.py",
    "report":           "job_report.py",
    "profit_sell":      "job_profit_sell_cloud.py",
    "profit_buy":       "job_profit_buy_cloud.py",
    "balance":          "job_balance.py",
    "factor_rebalance": "job_factor_rebalance.py",
    "monitor_036030":   "monitor_036030.py",
}

WORK_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route("/api/stock-runner", methods=["GET"])
def stock_runner():
    if SECRET and request.args.get("secret") != SECRET:
        return jsonify({"error": "인증 실패"}), 401

    job = request.args.get("job", "")
    if not job:
        return jsonify({"error": "job 파라미터 필요", "available": list(JOB_MAP.keys())}), 400

    script = JOB_MAP.get(job)
    if not script:
        return jsonify({"error": f"알 수 없는 job: {job}", "available": list(JOB_MAP.keys())}), 400

    script_path = os.path.join(WORK_DIR, script)
    logger.info("실행: %s", script)

    result = subprocess.run(
        [sys.executable, script_path],
        capture_output=True, text=True, cwd=WORK_DIR, timeout=300
    )

    return jsonify({
        "ok":      result.returncode == 0,
        "job":     job,
        "script":  script,
        "stdout":  result.stdout[-3000:] if result.stdout else "",
        "stderr":  result.stderr[-1000:] if result.stderr else "",
        "code":    result.returncode,
    })

@app.route("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3001)
