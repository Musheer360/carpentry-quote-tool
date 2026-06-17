"""
Carpentry Quote Tool - Flask app.

Runs identically in two environments:
  * Locally (Windows/dev): `python api/index.py` -> http://127.0.0.1:5000
  * On Vercel: exposed as a Python serverless function (see vercel.json).

Persistence is delegated to store.py (Vercel Blob in the cloud, local files
otherwise). Generated workbooks are written to a temp dir and streamed back.

Security: cookie-session auth (PBKDF2 password hashing + HMAC-signed session
cookie). Users live in Blob; an admin is bootstrapped from ADMIN_USERNAME /
ADMIN_PASSWORD. Auth is disabled when SECRET_KEY is unset (plain local dev).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import tempfile
import time
import uuid

# Ensure sibling modules (store, generator) import whether this file is loaded
# as a top-level script (local) or as a package module (Vercel's loader).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, send_file, send_from_directory, abort, make_response

import store
from generator import QuoteGenerator
from estimator import estimate as estimate_unit

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "web")
DATA_DIR = os.path.join(ROOT_DIR, "data")

app = Flask(__name__, static_folder=None)

ALLOWED_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

# ----------------------------------------------------------------------------
# Authentication (cookie sessions, Vercel-only, no third parties)
#
#   * Passwords hashed with PBKDF2-HMAC-SHA256 (per-user salt).
#   * Session = HMAC-SHA256 signed token {u, exp} in an httpOnly cookie.
#   * Users stored in Blob (doc "users"); an admin is bootstrapped once from
#     ADMIN_USERNAME / ADMIN_PASSWORD env vars.
#   * If SECRET_KEY is unset (e.g. plain local dev), auth is disabled (open).
# ----------------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "")
AUTH_ENABLED = bool(SECRET_KEY)
SESSION_COOKIE = "cqt_session"
SESSION_DAYS = 30
PBKDF2_ROUNDS = 200_000


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return salt, dk.hex()


def check_password(password: str, salt: str, expected_hex: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(dk, expected_hex)


def sign_session(username: str) -> str:
    payload = {"u": username, "exp": int(time.time()) + SESSION_DAYS * 86400}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_session(token: str):
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload.get("u")
    except Exception:
        return None


def _load_users():
    return store.load_doc("users", {"users": []})


def _ensure_admin():
    """Create the admin user once from env if the user store is empty."""
    data = _load_users()
    if data.get("users"):
        return data
    u = os.environ.get("ADMIN_USERNAME")
    p = os.environ.get("ADMIN_PASSWORD")
    if u and p:
        salt, h = hash_password(p)
        data["users"] = [{"username": u, "salt": salt, "hash": h,
                          "created": time.strftime("%Y-%m-%d %H:%M")}]
        store.save_doc("users", data)
    return data


def current_user():
    if not AUTH_ENABLED:
        return "local"
    return verify_session(request.cookies.get(SESSION_COOKIE, "") or "")


@app.before_request
def _gate():
    if not AUTH_ENABLED:
        return
    path = request.path or ""
    if not path.startswith("/api/"):
        return  # static assets are harmless; the data API is protected
    if path in ("/api/login", "/api/me", "/api/config"):
        return
    if not current_user():
        abort(401, "login required")


@app.route("/api/config")
def config():
    return jsonify({"auth": AUTH_ENABLED})


@app.route("/api/me")
def me():
    u = current_user()
    if not u:
        return jsonify({"user": None}), 401
    return jsonify({"user": u})


@app.route("/api/login", methods=["POST"])
def login():
    if not AUTH_ENABLED:
        return jsonify({"user": "local"})
    body = request.get_json(force=True, silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    data = _ensure_admin()
    user = next((x for x in data.get("users", []) if x["username"] == username), None)
    ok = bool(user) and check_password(password, user["salt"], user["hash"])
    time.sleep(0.3)  # mild brute-force friction
    if not ok:
        return jsonify({"error": "Invalid username or password"}), 401
    resp = make_response(jsonify({"user": username}))
    resp.set_cookie(SESSION_COOKIE, sign_session(username), max_age=SESSION_DAYS * 86400,
                    httponly=True, secure=request.is_secure, samesite="Lax", path="/")
    return resp


@app.route("/api/logout", methods=["POST"])
def logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


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
# parametric estimate (Smart Unit)
# ----------------------------------------------------------------------------
@app.route("/api/estimate", methods=["POST"])
def estimate():
    body = request.get_json(force=True, silent=True) or {}
    unit = body.get("unit") or body
    overrides = body.get("price_overrides") or {}
    pb = store.load_doc("pricebook")
    # allow per-project estimator price overrides to ride on top of pricebook
    if overrides:
        pb = dict(pb)
        est = dict(pb.get("estimator") or {})
        est_prices = dict(est.get("prices") or {})
        est_prices.update({k: v for k, v in overrides.items() if v not in (None, "")})
        est["prices"] = est_prices
        pb["estimator"] = est
    try:
        return jsonify(estimate_unit(unit, pb))
    except Exception as exc:
        app.logger.exception("estimate failed")
        abort(400, "estimate failed: %s" % exc)


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
