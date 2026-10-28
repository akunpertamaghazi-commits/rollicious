"""Integrasi Midtrans Core API — QRIS dinamis (sandbox/production).

Sumber: dokumentasi resmi Midtrans (docs.midtrans.com):
  * Charge QRIS      : POST {BASE}/v2/charge  (payment_type=qris, qris.acquirer=gopay)
  * Autentikasi      : HTTP Basic, username = Server Key, password kosong
  * Status           : GET  {BASE}/v2/{order_id}/status
  * Batalkan (pending): POST {BASE}/v2/{order_id}/cancel
  * Refund           : POST {BASE}/v2/{order_id}/refund  (refund_key, amount, reason)
  * Notifikasi       : signature_key = SHA512(order_id + status_code + gross_amount + server_key)
BASE sandbox = https://api.sandbox.midtrans.com , production = https://api.midtrans.com

Konfigurasi lewat environment variable (jangan ditulis di kode / frontend):
  MIDTRANS_SERVER_KEY, MIDTRANS_IS_PRODUCTION (default false), MIDTRANS_EXPIRY_MINUTES (default 15)
Jika MIDTRANS_SERVER_KEY kosong, gateway nonaktif dan alur QRIS manual lama tetap dipakai.
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse


class GatewayError(Exception):
    pass


def server_key():
    return os.environ.get("MIDTRANS_SERVER_KEY", "").strip()


def enabled():
    return bool(server_key())


def is_production():
    return os.environ.get("MIDTRANS_IS_PRODUCTION", "false").strip().lower() in ("1", "true", "yes")


def base_url():
    return "https://api.midtrans.com" if is_production() else "https://api.sandbox.midtrans.com"


def expiry_minutes():
    try:
        return max(2, int(os.environ.get("MIDTRANS_EXPIRY_MINUTES", "15")))
    except ValueError:
        return 15


def _auth_header():
    token = base64.b64encode((server_key() + ":").encode()).decode()
    return {"Authorization": "Basic " + token, "Accept": "application/json", "Content-Type": "application/json"}


def _request(method, path, payload=None, timeout=15):
    """Panggil API Midtrans. Dipisah supaya mudah di-mock saat pengujian."""
    if not enabled():
        raise GatewayError("Gateway pembayaran belum dikonfigurasi")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method, headers=_auth_header())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode() or "{}")
        except ValueError:
            raise GatewayError(f"HTTP {e.code} dari gateway")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise GatewayError("Tidak dapat menghubungi gateway pembayaran") from e


# ---------------------------------------------------------------- tanda tangan
def make_signature(order_id, status_code, gross_amount):
    raw = f"{order_id}{status_code}{gross_amount}{server_key()}"
    return hashlib.sha512(raw.encode()).hexdigest()


def verify_signature(payload):
    """True hanya jika signature_key pada payload cocok (perbandingan waktu-konstan)."""
    try:
        expected = make_signature(str(payload["order_id"]), str(payload["status_code"]), str(payload["gross_amount"]))
        given = str(payload.get("signature_key", ""))
    except KeyError:
        return False
    return bool(server_key()) and hmac.compare_digest(expected, given)


# ------------------------------------------------------------------- operasi
def create_qris(order_ref, amount, items, customer_name, phone):
    """Buat transaksi QRIS dinamis. Return dict: transaction_id, qr_url, status."""
    body = {
        "payment_type": "qris",
        "transaction_details": {"order_id": order_ref, "gross_amount": int(amount)},
        "item_details": [{"id": str(i["id"]), "price": int(i["price"]), "quantity": int(i["qty"]),
                          "name": str(i["name"])[:50]} for i in items],
        "customer_details": {"first_name": str(customer_name)[:50], "phone": str(phone)[:20]},
        "qris": {"acquirer": "gopay"},
        "custom_expiry": {"expiry_duration": expiry_minutes(), "unit": "minute"},
    }
    r = _request("POST", "/v2/charge", body)
    if str(r.get("status_code")) not in ("200", "201"):
        raise GatewayError(r.get("status_message") or "Gagal membuat transaksi QRIS")
    qr = next((a.get("url") for a in r.get("actions", []) if a.get("name") == "generate-qr-code"), None)
    return {"transaction_id": r.get("transaction_id"), "qr_url": qr, "status": r.get("transaction_status", "pending")}


def get_status(order_ref):
    return _request("GET", f"/v2/{order_ref}/status")


def cancel(order_ref):
    return _request("POST", f"/v2/{order_ref}/cancel")


def refund(order_ref, amount, refund_key, reason):
    return _request("POST", f"/v2/{order_ref}/refund",
                    {"refund_key": refund_key, "amount": int(amount), "reason": str(reason)[:255]})


def qr_host_allowed(url):
    """Cegah SSRF: hanya host milik Midtrans yang boleh diambil gambarnya."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host.endswith(".midtrans.com") or host.endswith(".veritrans.co.id"))


def fetch_qr_image(url):
    """Ambil gambar QR dari Midtrans lewat server (kunci tidak pernah sampai ke browser)."""
    if not qr_host_allowed(url):
        raise GatewayError("URL QR tidak diizinkan")
    req = urllib.request.Request(url, headers={"Authorization": _auth_header()["Authorization"]})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(), r.headers.get("Content-Type", "image/png")
    except Exception as e:
        raise GatewayError("Gagal mengambil gambar QR") from e
