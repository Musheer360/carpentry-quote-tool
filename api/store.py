"""
Storage abstraction for the Carpentry Quote Tool.

Two interchangeable backends, chosen automatically at runtime:

  * Vercel Blob  - used when BLOB_READ_WRITE_TOKEN is present (i.e. on Vercel,
    or locally after `vercel env pull`). Mutable JSON state (pricebook,
    projects) is stored as IMMUTABLE, timestamped versions; reads pick the
    newest version via the strongly-consistent List API. This avoids the CDN
    read-after-write staleness you'd get from overwriting a fixed blob URL.
    Item photos are stored as immutable blobs and referenced by their URL.

  * Local filesystem - used when there is no token (plain local Windows/dev
    run). State lives in data/*.json and images in data/uploads/.

Both backends expose the same API:
    load_doc(name, default)      -> dict
    save_doc(name, data)         -> None
    save_image(raw, filename)    -> str   (a path or URL the generator can read)
    read_image_bytes(ref)        -> bytes | None
"""

from __future__ import annotations

import io
import json
import os
import time
import uuid

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
SEED_PATH = os.path.join(API_DIR, "seed_pricebook.json")

STATE_PREFIX = "state/"        # blob folder for JSON docs
UPLOAD_PREFIX = "uploads/"     # blob folder for images
KEEP_VERSIONS = 3              # how many old versions of a doc to retain


def _has_blob() -> bool:
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


# ---------------------------------------------------------------------------
# Vercel Blob helpers
# ---------------------------------------------------------------------------
def _blob():
    import vercel_blob  # imported lazily so local mode needs no dependency
    return vercel_blob


def _blob_list(prefix: str):
    try:
        res = _blob().list({"prefix": prefix, "limit": 1000})
        return res.get("blobs", []) if isinstance(res, dict) else []
    except Exception:
        return []


def _blob_newest(name: str):
    """Return the newest blob descriptor for a doc, or None."""
    prefix = f"{STATE_PREFIX}{name}-"
    blobs = _blob_list(prefix)
    if not blobs:
        return None
    # pathnames embed a zero-padded ms timestamp, so lexical sort == time sort
    blobs.sort(key=lambda b: b.get("pathname", ""), reverse=True)
    return blobs[0]


def _blob_load_doc(name, default):
    import requests
    newest = _blob_newest(name)
    if not newest:
        return None  # signal "not found" so caller can seed
    url = newest.get("downloadUrl") or newest.get("url")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return json.loads(r.content.decode("utf-8"))
    except Exception:
        return default


def _blob_save_doc(name, data):
    pathname = f"{STATE_PREFIX}{name}-{int(time.time() * 1000):013d}.json"
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    _blob().put(pathname, payload, {"addRandomSuffix": False, "allowOverwrite": True,
                                    "cacheControlMaxAge": "60"})
    _blob_prune(name)


def _blob_prune(name):
    """Best-effort deletion of stale versions to limit storage growth."""
    try:
        prefix = f"{STATE_PREFIX}{name}-"
        blobs = _blob_list(prefix)
        blobs.sort(key=lambda b: b.get("pathname", ""), reverse=True)
        stale = blobs[KEEP_VERSIONS:]
        if stale:
            _blob().delete([b["url"] for b in stale])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Local filesystem helpers
# ---------------------------------------------------------------------------
def _local_path(name):
    return os.path.join(DATA_DIR, f"{name}.json")


def _local_load_doc(name, default):
    path = _local_path(name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _local_save_doc(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _local_path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Seeding (first run with an empty store)
# ---------------------------------------------------------------------------
def _seed_pricebook():
    try:
        with open(SEED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"currency": "SAR", "categories": [], "items": []}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_doc(name, default=None):
    loader = _blob_load_doc if _has_blob() else _local_load_doc
    data = loader(name, default)
    if data is not None:
        return data
    # not found -> seed sensible defaults and persist them
    if name == "pricebook":
        seeded = _seed_pricebook()
    elif name == "projects":
        seeded = {"projects": []}
    else:
        seeded = default if default is not None else {}
    try:
        save_doc(name, seeded)
    except Exception:
        pass
    return seeded


def save_doc(name, data):
    if _has_blob():
        _blob_save_doc(name, data)
    else:
        _local_save_doc(name, data)


def save_image(raw: bytes, filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower() or ".png"
    key = uuid.uuid4().hex + ext
    if _has_blob():
        res = _blob().put(f"{UPLOAD_PREFIX}{key}", raw,
                          {"addRandomSuffix": False, "allowOverwrite": True})
        return res.get("url")  # full https URL, stored on the item
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(UPLOAD_DIR, key), "wb") as f:
        f.write(raw)
    return f"uploads/{key}"


def read_image_bytes(ref: str):
    """Resolve an image reference (blob URL or local path) to raw bytes."""
    if not ref:
        return None
    if ref.startswith("http://") or ref.startswith("https://"):
        try:
            import requests
            r = requests.get(ref, timeout=15)
            r.raise_for_status()
            return r.content
        except Exception:
            return None
    path = ref if os.path.isabs(ref) else os.path.join(DATA_DIR, ref)
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None
