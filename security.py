"""Keamanan tambahan: CSRF, rate limiting, header keamanan, audit log."""
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, flash, g, jsonify, redirect, request, session

# ---------------------------------------------------------------- CSRF
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_FORM_RE = re.compile(r"(<form\b[^>]*\bmethod\s*=\s*[\"']?post[\"']?[^>]*>)", re.I)


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def csrf_protect(exempt_endpoints=()):
    """before_request: tolak POST/PUT/DELETE tanpa token yang cocok."""
    if request.method in SAFE_METHODS or request.endpoint in exempt_endpoints:
        return None
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    real = session.get("_csrf", "")
    if not real or not sent or not hmac.compare_digest(sent, real):
        if request.is_json or request.path.startswith("/api/"):
            return jsonify(ok=False, msg="Sesi kedaluwarsa, muat ulang halaman."), 400
        flash("Sesi formulir kedaluwarsa. Silakan coba lagi.", "error")
        return redirect(request.referrer or "/")
    return None


def inject_csrf(response):
    """Sisipkan input token ke SEMUA <form method=post> di halaman HTML (tanpa mengubah template satu per satu)."""
    if response.mimetype == "text/html" and not response.direct_passthrough:
        html = response.get_data(as_text=True)
        tag = f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'
        new = _FORM_RE.sub(lambda m: m.group(1) + tag, html)
        if new != html:
            response.set_data(new)
    return response


def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


# ---------------------------------------------------------------- rate limit
_hits = defaultdict(deque)


def client_ip():
    return request.remote_addr or "0.0.0.0"


def rate_limited(bucket, limit, window):
    """Sliding window per IP (in-memory; cukup untuk satu proses. Untuk multi-proses pakai Redis)."""
    now, key = time.time(), (bucket, client_ip())
    q = _hits[key]
    while q and q[0] <= now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def rate_limit(bucket, limit, window, methods=("POST",)):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if request.method in methods and rate_limited(bucket, limit, window):
                if request.is_json or request.path.startswith(("/api/", "/webhook/")):
                    return jsonify(ok=False, msg="Terlalu banyak permintaan, coba lagi sebentar."), 429
                flash("Terlalu banyak percobaan. Tunggu beberapa menit lalu coba lagi.", "error")
                return redirect(request.path), 303
            return fn(*a, **kw)
        return wrapper
    return deco


def reset_rate_limits():
    _hits.clear()
