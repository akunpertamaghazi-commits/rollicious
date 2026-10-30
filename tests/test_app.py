"""Pengujian otomatis Rollicios. Jalankan:  python -m unittest discover -s tests -v
Gateway Midtrans diganti objek palsu (FakeMidtrans) sehingga tidak butuh internet/kunci asli.
Verifikasi tanda tangan memakai rumus resmi, jadi webhook palsu benar-benar ditolak."""
import os, re, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_tmp = tempfile.mkdtemp()
os.environ["TOKO_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["SECRET_KEY"] = "kunci-uji"

import analytics, app as A, database, payment_gateway as gw, security  # noqa: E402

SERVER_KEY = "SB-Mid-server-TEST"


class FakeMidtrans:
    """Meniru perilaku Core API Midtrans (charge, status, cancel, refund) sesuai dokumentasi."""
    CODE = {"pending": "201", "settlement": "200", "expire": "202", "cancel": "202", "deny": "202", "refund": "200"}

    def __init__(self):
        self.tx, self.calls, self.refund_code, self.cancel_code, self.down = {}, [], "200", "200", False

    def payload(self, oid, status, amount=None):
        t = self.tx[oid]
        amt = t["amount"] if amount is None else amount
        code = self.CODE[status]
        p = {"order_id": oid, "status_code": code, "gross_amount": f"{amt}.00", "transaction_status": status,
             "transaction_id": t["txid"], "payment_type": "qris", "fraud_status": "accept"}
        p["signature_key"] = gw.make_signature(oid, code, f"{amt}.00")
        return p

    def __call__(self, method, path, payload=None, timeout=15):
        self.calls.append((method, path))
        if self.down:
            raise gw.GatewayError("down")
        if method == "POST" and path == "/v2/charge":
            oid = payload["transaction_details"]["order_id"]
            amt = payload["transaction_details"]["gross_amount"]
            assert sum(i["price"] * i["quantity"] for i in payload["item_details"]) == amt     # item = total
            assert payload["payment_type"] == "qris" and payload["qris"]["acquirer"] == "gopay"
            self.tx[oid] = {"status": "pending", "amount": amt, "txid": "tx-" + oid}
            return {"status_code": "201", "transaction_id": "tx-" + oid, "transaction_status": "pending",
                    "actions": [{"name": "generate-qr-code", "url": f"https://api.sandbox.midtrans.com/v2/qris/tx-{oid}/qr-code"}]}
        oid, op = re.match(r"/v2/(.+)/(status|cancel|refund)$", path).groups()
        if oid not in self.tx:
            return {"status_code": "404", "status_message": "not found"}
        t = self.tx[oid]
        if op == "status":
            return self.payload(oid, t["status"])
        if op == "cancel":
            if self.cancel_code == "200":
                t["status"] = "cancel"
            return {"status_code": self.cancel_code, "status_message": "x"}
        if self.refund_code == "200":
            t["status"] = "refund"
        return {"status_code": self.refund_code, "status_message": "Success, refund request is approved" if self.refund_code == "200" else "Merchant cannot modify the status of the transaction"}


class Base(unittest.TestCase):
    gateway = True

    def setUp(self):
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(database.DB_PATH + ext):
                os.remove(database.DB_PATH + ext)
        database.init_db()
        security.reset_rate_limits()
        A.app.config["TESTING"] = False                      # biarkan error handler asli bekerja
        self.fake = FakeMidtrans()
        self._orig = (gw._request, gw.fetch_qr_image)
        gw._request = self.fake
        gw.fetch_qr_image = lambda url: (b"\x89PNG-fake", "image/png")
        if self.gateway:
            os.environ["MIDTRANS_SERVER_KEY"] = SERVER_KEY
        else:
            os.environ.pop("MIDTRANS_SERVER_KEY", None)
        self.admin, self.cust = A.app.test_client(), A.app.test_client()

    def tearDown(self):
        gw._request, gw.fetch_qr_image = self._orig
        os.environ.pop("MIDTRANS_SERVER_KEY", None)

    # ---- helpers
    def tok(self, c):
        c.get("/login")
        with c.session_transaction() as s:
            return s["_csrf"]

    def post(self, c, url, data=None, **kw):
        d = dict(data or {}); d["csrf_token"] = self.tok(c)
        return c.post(url, data=d, **kw)

    def api(self, c, url, js):
        return c.post(url, json=js, headers={"X-CSRF-Token": self.tok(c)})

    def db(self):
        return database.get_conn()

    def q(self, sql, *a):
        c = self.db(); r = c.execute(sql, a).fetchall(); c.close(); return r

    def login_admin(self):
        r = self.post(self.admin, "/admin/login", {"username": "admin", "password": "admin123"})
        self.assertEqual(r.status_code, 302)

    def register(self, telp="08111222333", **extra):
        r = self.post(self.cust, "/register", dict(nama="Sari", no_telepon=telp, password="rahasia1", konfirmasi="rahasia1", **extra))
        self.assertEqual(r.status_code, 302)

    def pid(self, nama="Risol Lumer Mayo"):
        return self.q("SELECT id FROM products WHERE nama=?", nama)[0]["id"]

    def set_price(self, nama="Risol Lumer Mayo", harga=6000, stok=50):
        self.login_admin()
        self.post(self.admin, "/admin/produk", dict(id=self.pid(nama), nama=nama, deskripsi="d", harga=harga, stok=stok))

    def make_order(self, qty=3, nama="Risol Lumer Mayo", client=None):
        c = client or self.cust
        r = self.api(c, "/api/cart/add", {"id": self.pid(nama), "qty": qty}); self.assertEqual(r.status_code, 200, r.json)
        r = self.post(c, "/checkout", {"alamat": "Jl. Mawar No. 5, Jakarta 12345"}); self.assertEqual(r.status_code, 302)
        return int(r.headers["Location"].rstrip("/").split("/")[-1])

    def pay(self, oid):
        return self.q("SELECT * FROM payments WHERE order_id=?", oid)[0]

    def order(self, oid):
        return self.q("SELECT * FROM orders WHERE id=?", oid)[0]

    def hook(self, oid_ref, status, **kw):
        return self.cust.post("/webhook/midtrans", json=self.fake.payload(oid_ref, status, **kw))

    def stok(self, nama="Risol Lumer Mayo"):
        return self.q("SELECT stok FROM products WHERE nama=?", nama)[0]["stok"]


class TestSecurity(Base):
    def test_csrf_blocks_post_without_token(self):
        r = self.cust.post("/login", data={"no_telepon": "x", "password": "y"})
        self.assertEqual(r.status_code, 302)                                   # ditolak & dialihkan
        r = self.cust.post("/api/cart/add", json={"id": 1})
        self.assertEqual(r.status_code, 400)

    def test_csrf_token_injected_in_forms(self):
        html = self.cust.get("/login").get_data(as_text=True)
        self.assertIn('name="csrf_token"', html)

    def test_security_headers_and_cookie(self):
        r = self.cust.get("/")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("HttpOnly", r.headers.get("Set-Cookie", ""))

    def test_login_rate_limit(self):
        codes = [self.post(self.cust, "/login", {"no_telepon": "0800", "password": "salah"}).status_code for _ in range(12)]
        self.assertIn(303, codes)
        self.assertEqual(codes[:10], [200] * 10)

    def test_admin_pages_need_login(self):
        for u in ("/admin", "/admin/produk", "/admin/pembayaran", "/admin/analitik", "/admin/audit", "/admin/akun"):
            self.assertEqual(self.admin.get(u).status_code, 302, u)
        r = self.admin.post("/admin/pembayaran/rekonsiliasi")
        self.assertEqual(r.status_code, 302)

    def test_customer_cannot_use_admin_endpoints(self):
        self.register()
        r = self.post(self.cust, "/admin/pesanan/1/refund", {"alasan": "x"})
        self.assertEqual(r.status_code, 302); self.assertIn("/admin/login", r.headers["Location"])

    def test_webhook_bad_signature_and_garbage(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]
        bad = self.fake.payload(ref, "settlement"); bad["signature_key"] = "0" * 128
        self.assertEqual(self.cust.post("/webhook/midtrans", json=bad).status_code, 403)
        self.assertEqual(self.cust.post("/webhook/midtrans", data="bukan json").status_code, 400)
        self.assertEqual(self.pay(oid)["status"], "belum_bayar")


class TestProductsAndProfile(Base):
    def test_price_empty_cannot_be_bought(self):
        self.register()
        r = self.api(self.cust, "/api/cart/add", {"id": self.pid(), "qty": 1})
        self.assertEqual(r.status_code, 400); self.assertIn("belum ditentukan", r.json["msg"])

    def test_price_cannot_be_tampered_from_browser(self):
        self.set_price(harga=6000); self.register()
        self.api(self.cust, "/api/cart/add", {"id": self.pid(), "qty": 2, "harga": 1, "total": 1})
        r = self.post(self.cust, "/checkout", {"alamat": "Jl. Mawar No. 5", "total": "1"})
        oid = int(r.headers["Location"].rstrip("/").split("/")[-1])
        self.assertEqual(self.order(oid)["total"], 12000)
        self.assertEqual(self.pay(oid)["jumlah"], 12000)

    def test_register_social_media(self):
        self.register(instagram="@rollicios.id", tiktok="rolli", pengikut="2500", alamat="Jl. Melati 1")
        u = self.q("SELECT * FROM users")[0]
        self.assertEqual((u["instagram"], u["tiktok"], u["pengikut"]), ("rollicios.id", "rolli", 2500))

    def test_invalid_social_rejected(self):
        r = self.post(self.cust, "/register", dict(nama="Budi", no_telepon="08999999999", password="rahasia1", konfirmasi="rahasia1", instagram="<script>"))
        self.assertEqual(r.status_code, 200); self.assertEqual(len(self.q("SELECT * FROM users")), 0)

    def test_profile_and_admin_input_social(self):
        self.register()
        self.post(self.cust, "/akun", dict(nama="Sari", alamat="Jl. Baru 9", instagram="sari", tiktok="", pengikut="900"))
        self.assertEqual(self.q("SELECT pengikut FROM users")[0]["pengikut"], 900)
        self.login_admin()
        uid = self.q("SELECT id FROM users")[0]["id"]
        self.post(self.admin, f"/admin/pelanggan/{uid}/sosmed", dict(instagram="sari2", tiktok="sari_tt", pengikut="15000"))
        u = self.q("SELECT * FROM users")[0]
        self.assertEqual((u["instagram"], u["tiktok"], u["pengikut"]), ("sari2", "sari_tt", 15000))

    def test_last_stock_not_oversold(self):
        self.set_price(stok=1)
        b = A.app.test_client()
        self.register(); self.register_b = self.post(b, "/register", dict(nama="Budi", no_telepon="08222333444", password="rahasia1", konfirmasi="rahasia1"))
        self.api(self.cust, "/api/cart/add", {"id": self.pid(), "qty": 1}); self.api(b, "/api/cart/add", {"id": self.pid(), "qty": 1})
        self.post(self.cust, "/checkout", {"alamat": "Jl. Mawar No. 5"}); r = self.post(b, "/checkout", {"alamat": "Jl. Kenanga No. 2"})
        self.assertEqual(self.stok(), 0); self.assertEqual(len(self.q("SELECT * FROM orders")), 1)
        self.assertEqual(r.status_code, 302)


class TestGatewayFlow(Base):
    def test_dynamic_qris_created_and_status_not_faked(self):
        self.set_price(); self.register(); oid = self.make_order()
        p = self.pay(oid)
        self.assertEqual((p["gateway"], p["status"]), ("midtrans", "belum_bayar")); self.assertTrue(p["qr_url"])
        html = self.cust.get(f"/bayar/{oid}").get_data(as_text=True)
        self.assertIn(f"/bayar/{oid}/qr", html); self.assertNotIn("Saya sudah bayar", html)
        self.assertEqual(self.cust.get(f"/bayar/{oid}/qr").data, b"\x89PNG-fake")
        self.post(self.cust, f"/bayar/{oid}/konfirmasi")                                    # tombol cek status
        self.assertEqual(self.pay(oid)["status"], "belum_bayar")                           # TIDAK jadi lunas

    def test_settlement_webhook_marks_paid_once(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.fake.tx[ref]["status"] = "settlement"
        r1 = self.hook(ref, "settlement"); r2 = self.hook(ref, "settlement")
        self.assertEqual(r1.json["result"], "lunas"); self.assertEqual(r2.json["result"], "duplicate")
        self.assertEqual(self.pay(oid)["status"], "lunas"); self.assertEqual(self.order(oid)["status_order"], "lunas")
        self.assertEqual(len(self.q("SELECT * FROM cash_flows WHERE tipe='masuk'")), 1)     # kas tidak ganda
        self.assertEqual(self.stok(), 47)                                                   # stok tidak berkurang dua kali

    def test_amount_mismatch_rejected(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]
        r = self.hook(ref, "settlement", amount=1000)
        self.assertEqual(r.json["result"], "mismatch"); self.assertEqual(self.pay(oid)["status"], "belum_bayar")

    def test_unknown_order_logged(self):
        self.set_price()
        p = {"order_id": "PAY-XXXX", "status_code": "200", "gross_amount": "5000.00", "transaction_status": "settlement", "transaction_id": "t"}
        p["signature_key"] = gw.make_signature("PAY-XXXX", "200", "5000.00")
        self.assertEqual(self.cust.post("/webhook/midtrans", json=p).json["result"], "unknown")

    def test_expire_restores_stock_and_cancels(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.assertEqual(self.stok(), 47)
        self.assertEqual(self.hook(ref, "expire").json["result"], "expired")
        self.assertEqual((self.pay(oid)["status"], self.order(oid)["status_order"], self.stok()), ("kedaluwarsa", "dibatalkan", 50))

    def test_out_of_order_event_after_paid_is_ignored(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]
        self.hook(ref, "settlement"); r = self.hook(ref, "expire")
        self.assertEqual(r.json["result"], "ignored"); self.assertEqual(self.pay(oid)["status"], "lunas")
        self.assertEqual(self.stok(), 47)

    def test_late_payment_after_expiry_then_recover(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]
        self.hook(ref, "expire"); r = self.hook(ref, "settlement")
        self.assertEqual(r.json["result"], "terlambat"); self.assertEqual(self.pay(oid)["status"], "terlambat")
        self.assertEqual(len(self.q("SELECT * FROM cash_flows WHERE tipe='masuk'")), 1)
        self.login_admin(); pid = self.pay(oid)["id"]
        self.post(self.admin, f"/admin/pembayaran/{pid}/pulihkan")
        self.assertEqual((self.pay(oid)["status"], self.order(oid)["status_order"], self.stok()), ("lunas", "lunas", 47))

    def test_late_payment_recover_fails_without_stock(self):
        self.set_price(stok=3); self.register(); oid = self.make_order(qty=3)
        ref = self.pay(oid)["payment_reference"]; self.hook(ref, "expire"); self.hook(ref, "settlement")
        self.q("SELECT 1")
        c = self.db(); c.execute("UPDATE products SET stok=1 WHERE nama='Risol Lumer Mayo'"); c.commit(); c.close()
        self.login_admin(); self.post(self.admin, f"/admin/pembayaran/{self.pay(oid)['id']}/pulihkan")
        self.assertEqual(self.pay(oid)["status"], "terlambat"); self.assertEqual(self.stok(), 1)

    def test_reconcile_recovers_missed_webhook(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.fake.tx[ref]["status"] = "settlement"          # webhook tidak pernah datang
        self.login_admin(); self.post(self.admin, "/admin/pembayaran/rekonsiliasi")
        self.assertEqual(self.pay(oid)["status"], "lunas")
        self.post(self.admin, "/admin/pembayaran/rekonsiliasi")
        self.assertEqual(len(self.q("SELECT * FROM cash_flows WHERE tipe='masuk'")), 1)

    def test_polling_api_reconciles(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.fake.tx[ref]["status"] = "settlement"
        # dalam 8 detik pertama sistem tidak menanyakan gateway lagi (hemat panggilan) -> status belum berubah
        self.assertEqual(self.cust.get(f"/api/pesanan/{oid}/status").json["payment"], "belum_bayar")
        c = self.db(); c.execute("UPDATE payments SET last_check='2000-01-01 00:00:00' WHERE order_id=?", (oid,)); c.commit(); c.close()
        self.assertEqual(self.cust.get(f"/api/pesanan/{oid}/status").json["payment"], "lunas")

    def test_gateway_down_keeps_order_and_allows_retry(self):
        self.set_price(); self.register(); self.fake.down = True
        oid = self.make_order()
        self.assertIsNone(self.pay(oid)["gateway"]); self.assertEqual(self.order(oid)["status_order"], "menunggu_pembayaran")
        html = self.cust.get(f"/bayar/{oid}").get_data(as_text=True); self.assertIn("Buat QRIS", html)
        self.fake.down = False; self.post(self.cust, f"/bayar/{oid}/buat-qris")
        self.assertEqual(self.pay(oid)["gateway"], "midtrans")

    def test_cancel_by_customer_cancels_at_gateway(self):
        self.set_price(); self.register(); oid = self.make_order()
        self.post(self.cust, f"/pesanan/{oid}/batal")
        self.assertEqual(self.order(oid)["status_order"], "dibatalkan"); self.assertEqual(self.stok(), 50)
        self.assertTrue(any(c[1].endswith("/cancel") for c in self.fake.calls))

    def test_cancel_refused_if_already_paid_at_gateway(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.fake.tx[ref]["status"] = "settlement"; self.fake.cancel_code = "412"
        self.post(self.cust, f"/pesanan/{oid}/batal")
        self.assertEqual(self.pay(oid)["status"], "lunas"); self.assertEqual(self.order(oid)["status_order"], "lunas")

    def test_manual_admin_cannot_force_gateway_payment_paid(self):
        self.set_price(); self.register(); oid = self.make_order(); self.login_admin()
        c = self.db(); c.execute("UPDATE payments SET status='menunggu_verifikasi' WHERE order_id=?", (oid,)); c.commit(); c.close()
        self.post(self.admin, f"/admin/pembayaran/{self.pay(oid)['id']}/lunas")
        self.assertEqual(self.pay(oid)["status"], "menunggu_verifikasi")


class TestRefundAndShipping(Base):
    def paid_order(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.fake.tx[ref]["status"] = "settlement"; self.hook(ref, "settlement")
        return oid

    def test_gateway_refund_success(self):
        oid = self.paid_order()
        self.post(self.admin, f"/admin/pesanan/{oid}/refund", {"alasan": "Pelanggan batal", "restock": "1"})
        self.assertEqual((self.pay(oid)["status"], self.order(oid)["status_order"], self.stok()), ("refund", "refund", 50))
        self.assertEqual(self.q("SELECT jumlah FROM cash_flows WHERE tipe='keluar'")[0]["jumlah"], 18000)
        self.post(self.admin, f"/admin/pesanan/{oid}/refund", {"alasan": "lagi"})                      # tidak bisa ganda
        self.assertEqual(len(self.q("SELECT * FROM cash_flows WHERE tipe='keluar'")), 1)
        self.assertEqual(self.hook(self.pay(oid)["payment_reference"], "refund").json["result"], "already")   # notifikasi refund menyusul

    def test_gateway_refund_rejected_changes_nothing(self):
        oid = self.paid_order(); self.fake.refund_code = "412"
        self.post(self.admin, f"/admin/pesanan/{oid}/refund", {"alasan": "uji"})
        self.assertEqual((self.pay(oid)["status"], self.order(oid)["status_order"]), ("lunas", "lunas"))
        self.assertEqual(len(self.q("SELECT * FROM cash_flows WHERE tipe='keluar'")), 0)

    def test_refund_requires_reason_and_paid(self):
        self.set_price(); self.register(); oid = self.make_order(); self.login_admin()
        self.post(self.admin, f"/admin/pesanan/{oid}/refund", {"alasan": "uji refund"})
        self.assertEqual(self.pay(oid)["status"], "belum_bayar")                                      # belum lunas -> tidak bisa
        oid2 = self.paid_order if False else None
        c = self.db(); c.execute("UPDATE payments SET status='lunas' WHERE order_id=?", (oid,)); c.commit(); c.close()
        self.post(self.admin, f"/admin/pesanan/{oid}/refund", {"alasan": ""})
        self.assertEqual(self.pay(oid)["status"], "lunas")

    def test_shipping_requires_resi_and_flow(self):
        oid = self.paid_order()
        self.post(self.admin, f"/admin/pesanan/{oid}/status", {"status": "dikirim"})
        self.assertEqual(self.order(oid)["status_order"], "lunas")                                    # tanpa resi ditolak
        self.post(self.admin, f"/admin/pesanan/{oid}/kirim", {"kurir": "JNE", "no_resi": "ab"})
        self.assertEqual(self.order(oid)["status_order"], "lunas")                                    # resi pendek ditolak
        self.post(self.admin, f"/admin/pesanan/{oid}/kirim", {"kurir": "JNE", "no_resi": "jne1234567890"})
        o = self.order(oid); self.assertEqual((o["status_order"], o["kurir"], o["no_resi"]), ("dikirim", "JNE", "JNE1234567890"))
        self.assertIn("JNE1234567890", self.cust.get("/pesanan").get_data(as_text=True))              # pelanggan melihat resi
        self.post(self.admin, f"/admin/pesanan/{oid}/status", {"status": "selesai"})
        self.assertEqual(self.order(oid)["status_order"], "selesai")

    def test_cannot_ship_unpaid(self):
        self.set_price(); self.register(); oid = self.make_order(); self.login_admin()
        self.post(self.admin, f"/admin/pesanan/{oid}/kirim", {"kurir": "JNE", "no_resi": "JNE123456"})
        self.assertEqual(self.order(oid)["status_order"], "menunggu_pembayaran")

    def test_audit_log_records_actions(self):
        oid = self.paid_order()
        self.post(self.admin, f"/admin/pesanan/{oid}/kirim", {"kurir": "JNE", "no_resi": "JNE1234567"})
        self.post(self.admin, "/admin/pesanan/%d/refund" % oid, {"alasan": "uji audit"})
        aksi = {r["aksi"] for r in self.q("SELECT aksi FROM audit_logs")}
        self.assertTrue({"login", "produk_edit", "pengiriman"} <= aksi, aksi)
        self.assertEqual(self.admin.get("/admin/audit").status_code, 200)

    def test_admin_change_password(self):
        self.login_admin()
        self.post(self.admin, "/admin/akun", {"lama": "admin123", "baru": "passwordbaru1", "konfirmasi": "passwordbaru1"})
        c = A.app.test_client()
        self.assertEqual(self.post(c, "/admin/login", {"username": "admin", "password": "admin123"}).status_code, 200)
        self.assertEqual(self.post(c, "/admin/login", {"username": "admin", "password": "passwordbaru1"}).status_code, 302)


class TestManualFlowStillWorks(Base):
    gateway = False

    def test_manual_qris_flow_unchanged(self):
        self.set_price(); self.register(); oid = self.make_order()
        self.assertIsNone(self.pay(oid)["gateway"])
        html = self.cust.get(f"/bayar/{oid}").get_data(as_text=True); self.assertIn("Saya sudah bayar", html)
        self.post(self.cust, f"/bayar/{oid}/konfirmasi"); self.assertEqual(self.pay(oid)["status"], "menunggu_verifikasi")
        self.login_admin(); self.post(self.admin, f"/admin/pembayaran/{self.pay(oid)['id']}/lunas")
        self.assertEqual((self.pay(oid)["status"], self.order(oid)["status_order"]), ("lunas", "lunas"))
        self.assertEqual(self.q("SELECT jumlah FROM cash_flows")[0]["jumlah"], 18000)

    def test_webhook_disabled_without_key(self):
        self.assertEqual(self.cust.post("/webhook/midtrans", json={"a": 1}).status_code, 503)

    def test_manual_refund_records_cash(self):
        self.set_price(); self.register(); oid = self.make_order(); self.login_admin()
        self.post(self.cust, f"/bayar/{oid}/konfirmasi"); self.post(self.admin, f"/admin/pembayaran/{self.pay(oid)['id']}/lunas")
        self.post(self.admin, f"/admin/pesanan/{oid}/refund", {"alasan": "uji manual"})
        self.assertEqual(self.pay(oid)["status"], "refund"); self.assertEqual(len(self.q("SELECT * FROM cash_flows WHERE tipe='keluar'")), 1)


class TestPagesAndAnalytics(Base):
    def test_all_pages_open(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.fake.tx[ref]["status"] = "settlement"; self.hook(ref, "settlement")
        for u in ("/", "/produk", f"/produk/{self.pid()}", "/keranjang", "/pesanan", "/akun", f"/bayar/{oid}"):
            self.assertEqual(self.cust.get(u).status_code, 200, u)
        for u in ("/admin", "/admin/produk", "/admin/pesanan", "/admin/pembayaran", "/admin/pelanggan", "/admin/kas",
                  "/admin/laporan", "/admin/analitik", "/admin/audit", "/admin/akun"):
            self.assertEqual(self.admin.get(u).status_code, 200, u)
        self.assertEqual(self.cust.get("/tidak-ada").status_code, 404)

    def test_analytics_algorithms(self):
        a, b, r2 = analytics.linreg([2, 4, 6, 8, 10]); self.assertAlmostEqual(b, 2.0); self.assertAlmostEqual(r2, 1.0)
        self.assertEqual(analytics.percentile_scores([10, 20, 30, 40, 50]), [1, 2, 3, 4, 5])
        self.assertEqual(analytics.percentile_scores([1, 5, 30, 60, 90], higher_is_better=False), [5, 4, 3, 2, 1])
        self.assertAlmostEqual(analytics.stdev([2, 4, 4, 4, 5, 5, 7, 9]), 2.138, places=2)

    def test_analytics_with_data(self):
        self.set_price(); self.register(); oid = self.make_order()
        ref = self.pay(oid)["payment_reference"]; self.fake.tx[ref]["status"] = "settlement"; self.hook(ref, "settlement")
        self.post(self.cust, "/akun", dict(nama="Sari", alamat="Jl. A 1", instagram="sari", pengikut="12000"))
        c = self.db()
        cust = analytics.customer_scores(c); self.assertEqual((cust[0]["segmen"], cust[0]["freq"], cust[0]["monetary"]), ("Champion", 1, 18000))
        self.assertGreaterEqual(cust[0]["pengaruh"], 70)                                   # satu-satunya pembeli & pengikut terbanyak
        abc = analytics.abc_analysis(c); self.assertEqual((abc[0]["kelas"], abc[0]["share"]), ("A", 100.0))
        plan = {r["nama"]: r for r in analytics.restock_plan(c)}; self.assertGreater(plan["Risol Lumer Mayo"]["laju"], 0)
        fc = analytics.forecast(c); self.assertTrue(fc["ada_data"]); self.assertEqual(len(fc["pred"]), 7)
        k = analytics.kpis(c); self.assertEqual((k["aov"], k["sukses_bayar"]), (18000, 100.0)); c.close()
        self.login_admin(); html = self.admin.get("/admin/analitik").get_data(as_text=True)
        self.assertIn("Champion", html); self.assertIn("Cara hitung", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTeam(Base):
    def test_team_pages_and_footer(self):
        import team
        self.assertEqual(len(team.TEAM), 5)
        self.assertEqual(len({m["slug"] for m in team.TEAM}), 5)                         # slug unik
        self.assertEqual(len({m["tema"] for m in team.TEAM}), 5)                         # tiap anggota tema berbeda
        self.assertEqual(self.cust.get("/tim").status_code, 200)
        home = self.cust.get("/").get_data(as_text=True)
        self.assertIn("<h4>Admin</h4>", home); self.assertNotIn("<h4>Akun</h4>", home)  # kolom Akun diganti Admin
        for m in team.TEAM:
            self.assertIn(f'/tim/{m["slug"]}', home)                                     # tiap nama ada di footer & bisa diklik
            r = self.cust.get(f'/tim/{m["slug"]}')
            self.assertEqual(r.status_code, 200)
            html = r.get_data(as_text=True)
            self.assertIn(f'theme-{m["tema"]}', html); self.assertIn(f'data-fx="{m["tema"]}"', html)
            import html as _h; self.assertIn(m["nim"], html); self.assertIn(_h.escape(m["peran"]), html)
        self.assertEqual(self.cust.get("/tim/tidak-ada").status_code, 404)

    def test_admin_login_link_in_footer(self):
        self.assertIn('/admin/login', self.cust.get("/").get_data(as_text=True))


class TestPaymentPageStates(Base):
    """Tampilan halaman bayar: menunggu (loading) -> berhasil / gagal, tanpa konfirmasi manual ke admin."""

    def test_waiting_page_has_loader_and_no_manual_confirm(self):
        self.set_price(); self.register(); oid = self.make_order()
        html = self.cust.get(f"/bayar/{oid}").get_data(as_text=True)
        self.assertIn("Menunggu pembayaran", html); self.assertIn('class="wait-box"', html); self.assertIn("risol", html)
        self.assertIn("otomatis terisi", html)                                           # nominal sudah di dalam QR
        self.assertNotIn("Saya sudah bayar", html); self.assertNotIn("Cek status pembayaran", html)
        self.assertNotIn("verifikasi admin", html.lower().split("rincian")[0])

    def test_success_page_says_berhasil(self):
        self.set_price(); self.register(); oid = self.make_order()
        self.hook(self.pay(oid)["payment_reference"], "settlement")
        self.assertEqual(self.cust.get(f"/api/pesanan/{oid}/status").json["payment"], "lunas")
        html = self.cust.get(f"/bayar/{oid}").get_data(as_text=True)
        self.assertIn("Pembayaran berhasil", html); self.assertNotIn("wait-box", html)

    def test_expired_and_denied_say_gagal(self):
        self.set_price(); self.register()
        for status in ("expire", "deny"):
            oid = self.make_order(qty=1)
            self.hook(self.pay(oid)["payment_reference"], status)
            html = self.cust.get(f"/bayar/{oid}").get_data(as_text=True)
            self.assertIn("Pembayaran gagal", html, status); self.assertNotIn("wait-box", html)
        self.assertIn("kedaluwarsa", self.cust.get(f"/bayar/{self.q('SELECT MIN(order_id) m FROM payments')[0]['m']}").get_data(as_text=True))

    def test_page_updates_without_admin_action(self):
        self.set_price(); self.register(); oid = self.make_order()
        before = self.cust.get(f"/api/pesanan/{oid}/status").json
        self.hook(self.pay(oid)["payment_reference"], "settlement")
        after = self.cust.get(f"/api/pesanan/{oid}/status").json
        self.assertEqual((before["payment"], after["payment"], after["order"]), ("belum_bayar", "lunas", "lunas"))
        self.assertEqual(len(self.q("SELECT * FROM audit_logs WHERE aksi LIKE 'pembayaran%'")), 0)   # tanpa campur tangan admin
