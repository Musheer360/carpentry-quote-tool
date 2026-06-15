"""
Carpentry Quote Tool - Flask app.

Runs identically in two environments:
  * Locally (Windows/dev): `python api/index.py` -> http://127.0.0.1:5000
  * On Vercel: exposed as a Python serverless function (see vercel.json).

Persistence is delegated to store.py (Vercel Blob in the cloud, local files
otherwise). Generated workbooks are written to a temp dir and streamed back.

Security: if APP_PASSWORD is set, every /api/* call (except /api/auth) must
send header `X-App-Password` matching it. The UI prompts once and remembers it.
"""

from __future__ import annotations

import io
import os
import tempfile
import time
import uuid

from flask import Flask, jsonify, request, send_file, send_from_directory, abort

import store
from generator import QuoteGenerator

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "web")
DATA_DIR = os.path.join(ROOT_DIR, "data")

app = Flask(__name__, static_folder=None)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
ALLOWED_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


# ----------------------------------------------------------------------------
# auth gate
# ----------------------------------------------------------------------------
@app.before_request
def _gate():
    if not APP_PASSWORD:
        return  # open mode (local / no password configured)
    path = request.path or ""
    if not path.startswith("/api/"):
        return  # static frontend is harmless; the data API is what we protect
    if path == "/api/auth":
        return
    if request.headers.get("X-App-Password", "") != APP_PASSWORD:
        abort(401, "unauthorized")


@app.route("/api/auth", methods=["POST"])
def auth():
    body = request.get_json(force=True, silent=True) or {}
    if not APP_PASSWORD:
        return jsonify({"ok": True, "required": False})
    ok = body.get("password", "") == APP_PASSWORD
    return (jsonify({"ok": True, "required": True}) if ok
            else (jsonify({"ok": False, "required": True}), 401))


@app.route("/api/config")
def config():
    return jsonify({"password_required": bool(APP_PASSWORD)})


# ----------------------------------------------------------------------------
# frontend
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def assets(filename):
    # serve styles.css / app.js (and ignore accidental /api fallthrough)
    if filename.startswith("api/"):
        abort(404)
    full = os.path.join(FRONTEND_DIR, filename)
    if os.path.isfile(full):
        return send_from_directory(FRONTEND_DIR, filename)
    abort(404)


@app.route("/uploads/<path:filename>")
def uploads(filename):
    # only meaningful in local-file mode; blob images are served by their URL
    return send_from_directory(os.path.join(DATA_DIR, "uploads"), filename)


# ----------------------------------------------------------------------------
# price book
# ----------------------------------------------------------------------------
@app.route("/api/pricebook", methods=["GET"])
def get_pricebook():
    return jsonify(store.load_doc("pricebook"))


@app.route("/api/pricebook", methods=["PUT"])
def put_pricebook():
    body = request.get_json(force=True)
    if not isinstance(body, dict) or "items" not in body:
        abort(400, "pricebook must be an object with an 'items' list")
    for it in body.get("items", []):
        try:
            it["price"] = float(it.get("price") or 0)
        except (TypeError, ValueError):
            it["price"] = 0.0
    store.save_doc("pricebook", body)
    return jsonify({"ok": True})


# ----------------------------------------------------------------------------
# projects
# ----------------------------------------------------------------------------
def _projects():
    return store.load_doc("projects", {"projects": []})


def _find(data, pid):
    for p in data["projects"]:
        if p["id"] == pid:
            return p
    return None


@app.route("/api/projects", methods=["GET"])
def list_projects():
    data = _projects()
    return jsonify([
        {
            "id": p["id"], "unit_name": p.get("unit_name"),
            "client_name_en": p.get("client_name_en"),
            "client_name_ar": p.get("client_name_ar"),
            "items": len(p.get("items", [])), "updated": p.get("updated"),
        }
        for p in data["projects"]
    ])


@app.route("/api/projects", methods=["POST"])
def create_project():
    body = request.get_json(force=True) or {}
    data = _projects()
    project = {
        "id": uuid.uuid4().hex[:12],
        "company_name_en": body.get("company_name_en", ""),
        "company_name_ar": body.get("company_name_ar", "مستوى الإبداع للمقاولات"),
        "client_name_en": body.get("client_name_en", ""),
        "client_name_ar": body.get("client_name_ar", ""),
        "unit_name": body.get("unit_name", ""),
        "location": body.get("location", ""),
        "price_overrides": body.get("price_overrides", {}),
        "items": body.get("items", []),
        "updated": time.strftime("%Y-%m-%d %H:%M"),
    }
    data["projects"].append(project)
    store.save_doc("projects", data)
    return jsonify(project), 201


@app.route("/api/projects/<pid>", methods=["GET"])
def get_project(pid):
    project = _find(_projects(), pid)
    if not project:
        abort(404)
    return jsonify(project)


@app.route("/api/projects/<pid>", methods=["PUT"])
def update_project(pid):
    body = request.get_json(force=True) or {}
    data = _projects()
    project = _find(data, pid)
    if not project:
        abort(404)
    for key in ("company_name_en", "company_name_ar", "client_name_en",
                "client_name_ar", "unit_name", "location", "price_overrides", "items"):
        if key in body:
            project[key] = body[key]
    project["updated"] = time.strftime("%Y-%m-%d %H:%M")
    store.save_doc("projects", data)
    return jsonify(project)


@app.route("/api/projects/<pid>", methods=["DELETE"])
def delete_project(pid):
    data = _projects()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p["id"] != pid]
    if len(data["projects"]) == before:
        abort(404)
    store.save_doc("projects", data)
    return jsonify({"ok": True})


# ----------------------------------------------------------------------------
# image upload
# ----------------------------------------------------------------------------
@app.route("/api/upload", methods=["POST"])
def upload_image():
    if "file" not in request.files:
        abort(400, "no file")
    f = request.files["file"]
    if not f.filename:
        abort(400, "empty filename")
    if os.path.splitext(f.filename)[1].lower() not in ALLOWED_IMG_EXT:
        abort(400, "unsupported image type")
    ref = store.save_image(f.read(), f.filename)
    return jsonify({"path": ref})


# ----------------------------------------------------------------------------
# generate
# ----------------------------------------------------------------------------
def _slug(text, fallback="client"):
    import re
    text = (text or "").strip()
    text = re.sub(r"[^\w\u0600-\u06FF\- ]+", "", text)
    text = re.sub(r"\s+", "_", text)
    return text or fallback


@app.route("/api/projects/<pid>/generate", methods=["GET"])
def generate(pid):
    project = _find(_projects(), pid)
    if not project:
        abort(404)
    gen = QuoteGenerator(store.load_doc("pricebook"))
    fname = "%s_%s.xlsx" % (
        _slug(project.get("client_name_en") or project.get("unit_name")),
        time.strftime("%Y%m%d_%H%M%S"),
    )
    out_path = os.path.join(tempfile.gettempdir(), fname)
    try:
        gen.build(project, DATA_DIR, out_path)
    except Exception as exc:
        app.logger.exception("generation failed")
        abort(500, "generation failed: %s" % exc)
    with open(out_path, "rb") as f:
        buf = io.BytesIO(f.read())
    buf.seek(0)
    try:
        os.remove(out_path)
    except OSError:
        pass
    return send_file(
        buf, as_attachment=True, download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    print("Carpentry Quote Tool running at  http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
