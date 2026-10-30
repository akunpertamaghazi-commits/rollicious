"""Logika status pembayaran dari gateway (webhook + pengecekan status/rekonsiliasi).

Prinsip:
  * Pembayaran dianggap LUNAS hanya jika notifikasi/status dari gateway lolos verifikasi tanda tangan
    DAN nominalnya sama dengan tagihan. Tombol "Saya sudah bayar" tidak pernah mengubah status jadi lunas.
  * Idempoten: event yang sama tidak diproses dua kali (tabel payment_events + UPDATE bersyarat).
  * Event tidak berurutan aman: status akhir (lunas/refund) tidak ditimpa event lama (pending/expire).
  * Semua perubahan dalam satu transaksi database (BEGIN IMMEDIATE), rollback jika ada kegagalan.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

import payment_gateway as gw

OPEN = ("belum_bayar", "menunggu_verifikasi", "ditolak")      # status pembayaran yang masih bisa lunas
NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def restore_stock(conn, order_id):
    for it in conn.execute("SELECT product_id, jumlah FROM order_items WHERE order_id=?", (order_id,)).fetchall():
        conn.execute("UPDATE products SET stok = stok + ? WHERE id=?", (it["jumlah"], it["product_id"]))


def touch_order(conn, order_id, status):
    conn.execute("UPDATE orders SET status_order=?, updated_at=datetime('now','localtime') WHERE id=?", (status, order_id))


def _log(conn, key, ref, source, status, code, valid, note, raw):
    cur = conn.execute(
        "INSERT OR IGNORE INTO payment_events(event_key,order_ref,source,transaction_status,status_code,valid,note,payload) "
        "VALUES (?,?,?,?,?,?,?,?)", (key, ref, source, status, code, valid, note, raw))
    return cur.rowcount == 1


def _cash(conn, tipe, jumlah, ket):
    conn.execute("INSERT INTO cash_flows(tipe,jumlah,keterangan) VALUES (?,?,?)", (tipe, int(jumlah), ket))


def apply_event(conn, payload, source="webhook"):
    """Proses satu notifikasi/status dari gateway. Return salah satu:
    invalid | unknown | mismatch | duplicate | lunas | terlambat | expired | gagal | refund | pending | ignored | already
    """
    ref = str(payload.get("order_id", ""))
    status = str(payload.get("transaction_status", ""))
    code = str(payload.get("status_code", ""))
    raw = json.dumps(payload, ensure_ascii=False)[:4000]

    if not gw.verify_signature(payload):
        _log(conn, "invalid|" + hashlib.sha256(raw.encode()).hexdigest(), ref, source, status, code, 0, "tanda tangan tidak valid", raw)
        conn.commit()
        return "invalid"

    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        pay = conn.execute("SELECT * FROM payments WHERE payment_reference=?", (ref,)).fetchone()
        if not pay:
            _log(conn, f"unknown|{ref}|{status}|{code}", ref, source, status, code, 1, "order_id tidak dikenal", raw)
            conn.commit()
            return "unknown"

        try:
            amount = int(Decimal(str(payload.get("gross_amount"))))
        except (InvalidOperation, TypeError):
            amount = -1
        if amount != int(pay["jumlah"]):
            _log(conn, f"mismatch|{ref}|{status}|{code}", ref, source, status, code, 0,
                 f"nominal {amount} != tagihan {pay['jumlah']}", raw)
            conn.execute("UPDATE payments SET gateway_status='nominal_tidak_cocok', last_check=? WHERE id=?", (NOW(), pay["id"]))
            conn.commit()
            return "mismatch"

        key = f"{ref}|{status}|{code}|{payload.get('transaction_id', '')}"
        fresh = _log(conn, key, ref, source, status, code, 1, "diproses", raw)
        if not fresh and source == "webhook":
            conn.commit()
            return "duplicate"

        order = conn.execute("SELECT * FROM orders WHERE id=?", (pay["order_id"],)).fetchone()
        outcome = _transition(conn, pay, order, payload, status)
        conn.execute("UPDATE payments SET gateway_status=?, last_check=?, gateway_ref=COALESCE(gateway_ref,?) WHERE id=?",
                     (status, NOW(), payload.get("transaction_id"), pay["id"]))
        conn.execute("UPDATE payment_events SET note=? WHERE event_key=?", (outcome, key))
        conn.commit()
        return outcome
    except Exception:
        conn.rollback()
        raise


def _transition(conn, pay, order, payload, status):
    oid, st = pay["order_id"], pay["status"]
    fraud = str(payload.get("fraud_status", "accept"))

    if status == "settlement" or (status == "capture" and fraud == "accept"):
        if st in ("lunas", "refund", "terlambat"):
            return "already"
        if st in ("kedaluwarsa", "gagal", "dibatalkan") or order["status_order"] == "dibatalkan":
            conn.execute("UPDATE payments SET status='terlambat', paid_at=? WHERE id=?", (NOW(), pay["id"]))
            _cash(conn, "masuk", pay["jumlah"], f"Pembayaran pesanan #{oid} (TERLAMBAT, perlu tinjauan) {pay['payment_reference']}")
            return "terlambat"
        cur = conn.execute("UPDATE payments SET status='lunas', paid_at=? WHERE id=? AND status IN ('belum_bayar','menunggu_verifikasi','ditolak')",
                           (NOW(), pay["id"]))
        if cur.rowcount == 1:
            touch_order(conn, oid, "lunas")
            _cash(conn, "masuk", pay["jumlah"], f"Pembayaran pesanan #{oid} ({pay['payment_reference']})")
            return "lunas"
        return "already"

    if status in ("expire", "deny", "cancel", "failure"):
        if st in OPEN:
            conn.execute("UPDATE payments SET status=? WHERE id=?", ("kedaluwarsa" if status == "expire" else "gagal", pay["id"]))
            if order["status_order"] != "dibatalkan":
                restore_stock(conn, oid)
                touch_order(conn, oid, "dibatalkan")
            return "expired" if status == "expire" else "gagal"
        return "ignored"                                   # event lama setelah lunas/refund -> abaikan

    if status in ("refund", "partial_refund"):
        if st not in ("lunas", "terlambat", "refund"):
            return "ignored"
        try:
            total = int(Decimal(str(payload.get("refund_amount", pay["jumlah"] if status == "refund" else 0))))
        except InvalidOperation:
            total = 0
        delta = total - int(pay["refund_amount"] or 0)
        if delta > 0:
            conn.execute("UPDATE payments SET refund_amount=?, refund_at=? WHERE id=?", (total, NOW(), pay["id"]))
            _cash(conn, "keluar", delta, f"Refund pesanan #{oid} ({pay['payment_reference']})")
        if status == "refund" and st != "refund":
            conn.execute("UPDATE payments SET status='refund' WHERE id=?", (pay["id"],))
            touch_order(conn, oid, "refund")
            return "refund"
        return "already" if delta <= 0 else "refund"

    return "pending" if status == "pending" else "ignored"
