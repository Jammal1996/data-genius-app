# ─── Add to requirements.txt ────────────────────────────────────────────────
# prometheus-flask-exporter>=0.23.0

# ─── Add to app.py (after `app = Flask(__name__)`) ──────────────────────────
#
# from prometheus_flask_exporter import PrometheusMetrics
#
# metrics = PrometheusMetrics(app)
# metrics.info("app_info", "DataGenius application info", version="1.0.0")
#
# This automatically exposes GET /metrics with:
#  - flask_http_request_total          (counter, by method/path/status)
#  - flask_http_request_duration_seconds (histogram)
#  - process_cpu_seconds_total
#  - process_resident_memory_bytes
#  - python_gc_* garbage collection stats

# ─── Health endpoint (add to app.py) ────────────────────────────────────────
#
# @app.route("/health")
# def health():
#     return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}, 200
