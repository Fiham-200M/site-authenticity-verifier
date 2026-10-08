#!/usr/bin/env python3
"""Flask Backend Server for End-Link Crawler Forensic Dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

app = Flask(__name__, template_folder="templates", static_folder="static")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "Output"
BRAND_FILE = BASE_DIR / "brand.json"
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)

# Active crawl jobs tracking
active_jobs: Dict[str, Dict[str, Any]] = {}
job_locks = threading.Lock()


def load_brands() -> List[str]:
    """Load brand list from brand.json."""
    if BRAND_FILE.exists():
        try:
            return json.loads(BRAND_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def get_crawl_summary(crawl_path: Path) -> Optional[Dict[str, Any]]:
    """Extract metadata summary from a crawl directory."""
    report_file = crawl_path / "report.json"
    if not report_file.exists():
        return None
    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
        pages = data.get("pages", [])
        last_page = pages[-1] if pages else {}
        money_page = next((p for p in pages if p.get("page_type") == "MONEY_SITE"), last_page)

        return {
            "crawl_id": crawl_path.name,
            "timestamp": crawl_path.stat().st_mtime,
            "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(crawl_path.stat().st_mtime)),
            "start_url": data.get("start_url", ""),
            "final_url": last_page.get("url") or last_page.get("requested_url", ""),
            "classification": data.get("classification", "UNKNOWN"),
            "status": data.get("status", "COMPLETED"),
            "brand": data.get("selected_brand") or data.get("dominant_brand") or last_page.get("selected_brand") or "UNKNOWN",
            "page_count": len(pages),
            "money_site_reached": any(p.get("page_type") == "MONEY_SITE" for p in pages),
            "has_live_chat": bool(data.get("live_chat_info") or money_page.get("live_chat_info")),
            "folder_name": crawl_path.name,
        }
    except Exception:
        return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Health check and environment status."""
    ai_server_online = False
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8000/api/verify", headers={"User-Agent": "HealthCheck"})
        # 405 Method Not Allowed or 422 Unprocessable Entity means server is responding
        try:
            with urllib.request.urlopen(req, timeout=1.5):
                ai_server_online = True
        except urllib.error.HTTPError:
            ai_server_online = True
    except Exception:
        ai_server_online = False

    return jsonify({
        "status": "online",
        "python": PYTHON_EXE,
        "ai_verifier_online": ai_server_online,
        "brands_count": len(load_brands()),
        "output_dir": str(OUTPUT_DIR),
        "total_crawls": len(list(OUTPUT_DIR.glob("crawl_*"))) if OUTPUT_DIR.exists() else 0,
    })


@app.route("/api/brands")
def api_brands():
    """Return all protected brands."""
    return jsonify(load_brands())


@app.route("/api/history")
def api_history():
    """Return past crawls sorted newest first."""
    if not OUTPUT_DIR.exists():
        return jsonify([])

    crawls = []
    for item in sorted(OUTPUT_DIR.glob("crawl_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if item.is_dir():
            summary = get_crawl_summary(item)
            if summary:
                crawls.append(summary)

    return jsonify(crawls)


@app.route("/api/report/<crawl_id>")
def api_report(crawl_id: str):
    """Return full report.json and assets manifest for a crawl."""
    target_dir = (OUTPUT_DIR / crawl_id).resolve()
    if not str(target_dir).startswith(str(OUTPUT_DIR.resolve())) or not target_dir.exists():
        return jsonify({"error": "Crawl report not found"}), 404

    report_file = target_dir / "report.json"
    if not report_file.exists():
        return jsonify({"error": "report.json not found"}), 404

    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))

        # Enrich pages with available asset URLs
        for page in data.get("pages", []):
            folder_str = page.get("folder")
            if folder_str:
                folder_path = Path(folder_str)
                # Relative path from crawl folder
                rel_parts = []
                try:
                    rel_parts = folder_path.relative_to(target_dir).parts
                except ValueError:
                    rel_parts = [folder_path.name]
                rel_sub = "/".join(rel_parts)

                page["logo_asset_url"] = f"/api/assets/{crawl_id}/{rel_sub}/logo.png" if (target_dir / rel_sub / "logo.png").exists() else None
                page["favicon_asset_url"] = f"/api/assets/{crawl_id}/{rel_sub}/favicon.png" if (target_dir / rel_sub / "favicon.png").exists() else None
                page["content_asset_url"] = f"/api/assets/{crawl_id}/{rel_sub}/content.txt" if (target_dir / rel_sub / "content.txt").exists() else None

                # Check forensics directory
                forensics_json = target_dir / rel_sub / "forensics" / "forensic_summary.json"
                if forensics_json.exists():
                    try:
                        page["forensics_summary"] = json.loads(forensics_json.read_text(encoding="utf-8"))
                    except Exception:
                        pass

        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/assets/<crawl_id>/<path:filepath>")
def api_asset(crawl_id: str, filepath: str):
    """Serve static assets from a crawl folder."""
    crawl_dir = (OUTPUT_DIR / crawl_id).resolve()
    if not str(crawl_dir).startswith(str(OUTPUT_DIR.resolve())) or not crawl_dir.exists():
        return "Not found", 404

    target_file = (crawl_dir / filepath).resolve()
    if not str(target_file).startswith(str(crawl_dir)) or not target_file.exists():
        return "Asset not found", 404

    return send_from_directory(target_file.parent, target_file.name)


@app.route("/api/crawl/start", methods=["POST"])
def api_crawl_start():
    """Start a crawl subprocess and register a live streaming job."""
    req_data = request.get_json() or {}
    start_url = (req_data.get("url") or "").strip()
    if not start_url:
        return jsonify({"error": "URL is required"}), 400

    if not (start_url.startswith("http://") or start_url.startswith("https://")):
        start_url = "https://" + start_url

    headless = bool(req_data.get("headless", True))
    challenge_timeout = int(req_data.get("challenge_timeout", 15))
    max_pages = int(req_data.get("max_pages", 0))
    expected_brand = (req_data.get("expected_brand") or "").strip() or None

    job_id = str(uuid.uuid4())[:8]
    log_q: queue.Queue = queue.Queue()

    # Build command line
    cmd = [
        PYTHON_EXE,
        "crawler.py",
        start_url,
        "--challenge-timeout", str(challenge_timeout),
    ]
    if headless:
        cmd.append("--headless")
    if max_pages > 0:
        cmd.extend(["--max-pages", str(max_pages)])

    # Record crawl start time to identify newly generated folder
    start_timestamp = time.time()

    def run_worker():
        proc = None
        try:
            log_q.put({"type": "info", "message": f"[System] Launching crawler: {' '.join(cmd)}"})
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            with job_locks:
                active_jobs[job_id]["process"] = proc

            output_folder_found = None

            for line in iter(proc.stdout.readline, ""):
                line_clean = line.rstrip()
                if not line_clean:
                    continue

                # Detect output directory from output lines
                m = re.search(r'(crawl_\d{8}_\d{6}_\d+)', line_clean)
                if m:
                    output_folder_found = m.group(1)

                log_q.put({"type": "log", "message": line_clean})

            proc.stdout.close()
            return_code = proc.wait()

            # Attempt to locate output directory if not found in stdout
            if not output_folder_found:
                candidates = [p for p in OUTPUT_DIR.glob("crawl_*") if p.stat().st_mtime >= start_timestamp - 2]
                if candidates:
                    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    output_folder_found = candidates[0].name

            # Read report if available
            report_summary = None
            if output_folder_found:
                crawl_dir = OUTPUT_DIR / output_folder_found
                report_summary = get_crawl_summary(crawl_dir)

            log_q.put({
                "type": "finished",
                "return_code": return_code,
                "crawl_id": output_folder_found,
                "summary": report_summary,
            })

            with job_locks:
                if job_id in active_jobs:
                    active_jobs[job_id]["status"] = "completed"
                    active_jobs[job_id]["crawl_id"] = output_folder_found
                    active_jobs[job_id]["summary"] = report_summary

        except Exception as exc:
            log_q.put({"type": "error", "message": str(exc)})
            with job_locks:
                if job_id in active_jobs:
                    active_jobs[job_id]["status"] = "failed"
                    active_jobs[job_id]["error"] = str(exc)

    thread = threading.Thread(target=run_worker, daemon=True)
    with job_locks:
        active_jobs[job_id] = {
            "job_id": job_id,
            "url": start_url,
            "status": "running",
            "log_queue": log_q,
            "started_at": time.time(),
            "thread": thread,
            "process": None,
            "crawl_id": None,
            "summary": None,
        }
    thread.start()

    return jsonify({"job_id": job_id, "status": "started", "url": start_url})


@app.route("/api/crawl/stream/<job_id>")
def api_crawl_stream(job_id: str):
    """Server-Sent Events endpoint streaming live log lines and job status."""
    with job_locks:
        job = active_jobs.get(job_id)

    if not job:
        return jsonify({"error": "Job not found"}), 404

    log_q: queue.Queue = job["log_queue"]

    def event_stream():
        yield f"data: {json.dumps({'type': 'connected', 'job_id': job_id})}\n\n"
        while True:
            try:
                item = log_q.get(timeout=25.0)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") in ("finished", "error"):
                    break
            except queue.Empty:
                # Keep-alive heartbeat
                yield f": heartbeat\n\n"

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/api/crawl/cancel/<job_id>", methods=["POST"])
def api_crawl_cancel(job_id: str):
    """Cancel a running crawl process."""
    with job_locks:
        job = active_jobs.get(job_id)

    if not job:
        return jsonify({"error": "Job not found"}), 404

    proc = job.get("process")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            job["log_queue"].put({"type": "info", "message": "[System] Crawl job was cancelled by user."})
            job["status"] = "cancelled"
            return jsonify({"status": "cancelled"})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return jsonify({"status": "not_running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n==========================================================")
    print(f"🚀 End-Link Crawler Forensic Web Dashboard")
    print(f"🌐 Access URL: http://localhost:{port}")
    print(f"🐍 Python Executable: {PYTHON_EXE}")
    print(f"📁 Output Directory: {OUTPUT_DIR}")
    print(f"==========================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
