"""
Instagram Bridge — FastAPI + Instagrapi
=========================================

Tiny HTTP bridge that exposes Instagrapi search/hashtag features over a
shared-secret-authenticated REST API so a Node.js site (Mention Monitor)
can pull public Instagram data.

Endpoints
---------
GET  /health                       → liveness probe
POST /login                        → warm up / validate a session
GET  /hashtag/{name}/recent        → recent medias for a hashtag
GET  /search/{keyword}             → top users + recent medias matching a keyword

Auth
----
Every request (except `/health`) must include `X-Bridge-Token: <BRIDGE_TOKEN>`
matching the env var `BRIDGE_TOKEN`. Generate a long random string and set it
on both this server and the Mention Monitor site.

Instagram credentials are passed per-request in the JSON body for `/login`
and cached in memory keyed by username. Subsequent calls reuse the cached
client. Session files are persisted to `./sessions/<username>.json` so the
server survives restarts without re-logging.

Disclaimer
----------
Instagrapi is an unofficial reverse-engineered client. Meta may rate-limit or
block accounts. Use a dedicated secondary Instagram account, never your main
one. Respect ToS at your own risk.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# instagrapi is lazy-imported inside _get_client so /health works even if
# instagrapi fails to import (e.g. dependency mismatch during boot).

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("instagram-bridge")

BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "").strip()
SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR", "./sessions"))
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Instagram Bridge", version="1.0.0")

# Permissive CORS — the bridge is meant to be called server-to-server only,
# but Railway/Fly health checks sometimes hit it from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory cache of logged-in clients, keyed by username.
_clients: Dict[str, Any] = {}
_clients_lock = threading.Lock()


# ─── Auth ────────────────────────────────────────────────────────────────────
def _require_token(x_bridge_token: Optional[str]) -> None:
    if not BRIDGE_TOKEN:
        raise HTTPException(500, "BRIDGE_TOKEN is not configured on the server")
    if not x_bridge_token or x_bridge_token != BRIDGE_TOKEN:
        raise HTTPException(401, "invalid or missing X-Bridge-Token")


# ─── Client management ───────────────────────────────────────────────────────
def _session_path(username: str) -> Path:
    safe = "".join(c for c in username if c.isalnum() or c in "._-").lower()
    return SESSIONS_DIR / f"{safe}.json"


def _get_client(username: str, password: str):
    """Return a logged-in Instagrapi Client, reusing the cached one if any."""
    with _clients_lock:
        cached = _clients.get(username)
        if cached is not None:
            return cached

        from instagrapi import Client  # lazy import

        cl = Client()
        cl.delay_range = [1, 3]  # be polite
        sess_file = _session_path(username)

        # Try to reuse a persisted session first to avoid the login challenge.
        if sess_file.exists():
            try:
                cl.load_settings(sess_file)
                cl.login(username, password)
                cl.get_timeline_feed()  # cheap sanity ping
                _clients[username] = cl
                log.info("reused session for %s", username)
                return cl
            except Exception as e:
                log.warning("session reuse failed for %s: %s — falling back to fresh login", username, e)
                try:
                    sess_file.unlink(missing_ok=True)
                except Exception:
                    pass

        # Fresh login.
        cl.login(username, password)
        try:
            cl.dump_settings(sess_file)
        except Exception as e:
            log.warning("could not persist session for %s: %s", username, e)
        _clients[username] = cl
        log.info("fresh login successful for %s", username)
        return cl


def _media_to_dict(m: Any) -> Dict[str, Any]:
    """Normalise an Instagrapi Media into a plain dict for the bridge consumer."""
    try:
        user = m.user
        return {
            "id": str(getattr(m, "pk", "")) or str(getattr(m, "id", "")),
            "code": getattr(m, "code", None),
            "url": f"https://www.instagram.com/p/{m.code}/" if getattr(m, "code", None) else None,
            "caption": getattr(m, "caption_text", "") or "",
            "media_type": getattr(m, "media_type", None),
            "like_count": getattr(m, "like_count", 0) or 0,
            "comment_count": getattr(m, "comment_count", 0) or 0,
            "taken_at": m.taken_at.isoformat() if getattr(m, "taken_at", None) else None,
            "user": {
                "username": getattr(user, "username", None),
                "full_name": getattr(user, "full_name", None),
                "pk": str(getattr(user, "pk", "")),
            },
            "thumbnail_url": str(getattr(m, "thumbnail_url", "") or ""),
        }
    except Exception as e:
        log.warning("media normalisation failed: %s", e)
        return {"id": str(getattr(m, "pk", "")), "error": str(e)}


# ─── Schemas ─────────────────────────────────────────────────────────────────
class LoginBody(BaseModel):
    username: str
    password: str


class FetchBody(BaseModel):
    username: str
    password: str
    amount: int = 20


# ─── Routes ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "instagram-bridge",
        "version": "1.0.0",
        "cached_clients": list(_clients.keys()),
        "token_configured": bool(BRIDGE_TOKEN),
    }


@app.post("/login")
def login(body: LoginBody, x_bridge_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    _require_token(x_bridge_token)
    try:
        cl = _get_client(body.username, body.password)
        info = cl.account_info()
        return {
            "ok": True,
            "username": info.username,
            "full_name": info.full_name,
            "media_count": info.media_count,
        }
    except Exception as e:
        log.exception("login failed for %s", body.username)
        raise HTTPException(401, f"login failed: {e}")


@app.post("/hashtag/recent")
def hashtag_recent(body: FetchBody, name: str = Query(...), x_bridge_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    _require_token(x_bridge_token)
    try:
        cl = _get_client(body.username, body.password)
        medias = cl.hashtag_medias_recent(name, amount=max(1, min(body.amount, 50)))
        return {"ok": True, "hashtag": name, "count": len(medias), "medias": [_media_to_dict(m) for m in medias]}
    except Exception as e:
        log.exception("hashtag fetch failed for %s", name)
        raise HTTPException(502, f"hashtag fetch failed: {e}")


@app.post("/search")
def search(body: FetchBody, keyword: str = Query(...), x_bridge_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Search Instagram by keyword: returns top users + recent medias from matching hashtags."""
    _require_token(x_bridge_token)
    try:
        cl = _get_client(body.username, body.password)
        amount = max(1, min(body.amount, 30))

        # 1. Resolve the keyword to candidate hashtags (strip spaces; Instagram hashtags are joined).
        slug = "".join(ch for ch in keyword.lower() if ch.isalnum())

        medias: List[Dict[str, Any]] = []
        seen_ids = set()

        if slug:
            try:
                hashtag_medias = cl.hashtag_medias_recent(slug, amount=amount)
                for m in hashtag_medias:
                    d = _media_to_dict(m)
                    if d["id"] not in seen_ids:
                        seen_ids.add(d["id"])
                        medias.append(d)
            except Exception as e:
                log.warning("hashtag '%s' lookup failed: %s", slug, e)

        # 2. Top users matching the keyword — return medias from the first 3.
        try:
            users = cl.search_users(keyword)[:3]
            for u in users:
                try:
                    user_medias = cl.user_medias(u.pk, amount=5)
                    for m in user_medias:
                        d = _media_to_dict(m)
                        if d["id"] not in seen_ids:
                            seen_ids.add(d["id"])
                            medias.append(d)
                except Exception as e:
                    log.warning("user_medias for %s failed: %s", u.username, e)
        except Exception as e:
            log.warning("search_users for '%s' failed: %s", keyword, e)

        return {"ok": True, "keyword": keyword, "count": len(medias), "medias": medias[: amount]}
    except Exception as e:
        log.exception("search failed for %s", keyword)
        raise HTTPException(502, f"search failed: {e}")


@app.post("/logout")
def logout(body: LoginBody, x_bridge_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    _require_token(x_bridge_token)
    with _clients_lock:
        cl = _clients.pop(body.username, None)
    try:
        if cl is not None:
            cl.logout()
    except Exception:
        pass
    try:
        _session_path(body.username).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True, "logged_out": body.username}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
