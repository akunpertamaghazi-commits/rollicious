"""Rollicios - website penjualan risol lumer (Flask + SQLite)."""
import os
import random
import re
import sqlite3
import string
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import analytics
import database
import pay_flow
import payment_gateway as gw
import security as sec
import team

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ganti-kunci-rahasia-ini-sebelum-deploy")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4 MB
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",   # set 1 saat memakai HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
if app.secret_key.startswith("ganti-kunci"):
    app.logger.warning("SECRET_KEY masih default. Set environment variable SECRET_KEY sebelum deploy!")

app.jinja_env.globals["csrf_token"] = sec.csrf_token
CSRF_EXEMPT = {"webhook_midtrans"}      # webhook diverifikasi dengan tanda tangan, bukan token sesi


@app.before_request
def _csrf_guard():
    return sec.csrf_protect(CSRF_EXEMPT)


@app.after_request
def _secure_response(resp):
    return sec.security_headers(sec.inject_csrf(resp))

PRODUCT_DIR = os.path.join(app.root_path, "static", "images", "products")
QRIS_DIR = os.path.join(app.root_path, "static", "images", "qris")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}

STATUS_LABEL = {
    "menunggu_pembayaran": "Menunggu pembayaran",
    "menunggu_verifikasi": "Menunggu verifikasi",
    "lunas": "Lunas",
    "diproses": "Diproses",
    "dikirim": "Dikirim",
    "selesai": "Selesai",
    "dibatalkan": "Dibatalkan",
    "belum_bayar": "Belum bayar",
    "ditolak": "Ditolak",
    "kedaluwarsa": "Kedaluwarsa",
    "gagal": "Gagal",
    "terlambat": "Dibayar terlambat",
    "refund": "Dana dikembalikan",
}
PAID_STATUSES = ("lunas", "diproses", "dikirim", "selesai")


# --------------------------------------------------------------------------
# Database helper
# --------------------------------------------------------------------------
def db():
    if "db" not in g:
        g.db = database.get_conn()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# --------------------------------------------------------------------------
# Filter & context template
# --------------------------------------------------------------------------
@app.template_filter("rupiah")
def rupiah(value):
    return "Rp " + f"{int(value or 0):,}".replace(",", ".")


@app.template_filter("harga")
def harga_txt(value):
    """Tampilkan harga; 0 / kosong = 'Harga menyusul'."""
    return "Harga menyusul" if not value or int(value) <= 0 else rupiah(value)


@app.template_filter("flavor")
def flavor(nama):
    """Tentukan varian rasa dari nama produk (dipakai untuk warna & ilustrasi risol)."""
    n = (nama or "").lower()
    for key, out in (("mayo", "mayo"), ("keju", "keju"), ("coklat", "coklat"),
                     ("cokelat", "coklat"), ("matcha", "matcha")):
        if key in n:
            return out
    return "default"


@app.template_filter("tgl")
def tgl(value):
    if not value:
        return "-"
    try:
        return datetime.strptime(value[:16], "%Y-%m-%d %H:%M").strftime("%d %b %Y, %H:%M")
    except ValueError:
        return value


@app.template_filter("status")
def status_label(value):
    return STATUS_LABEL.get(value, value)


@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    user = None
    if session.get("user_id"):
        user = db().execute("SELECT id, nama FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return dict(
        cart_count=sum(cart.values()),
        cur_user=user,
        is_admin=bool(session.get("admin_id")),
        admin_name=session.get("admin_name"),
        team_members=team.TEAM,
    )


# --------------------------------------------------------------------------
# Decorator login
# --------------------------------------------------------------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            flash("Masuk dulu untuk melanjutkan.", "info")
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin_id"):
            flash("Halaman ini khusus admin.", "info")
            return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrapper


def audit(aksi, detail=""):
    """Catat aktivitas admin (ikut ter-commit bersama perubahan datanya)."""
    db().execute("INSERT INTO audit_logs (admin_id, admin_nama, aksi, detail, ip) VALUES (?,?,?,?,?)",
                 (session.get("admin_id"), session.get("admin_name"), aksi, str(detail)[:500], sec.client_ip()))


HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._]{1,30}$")


def parse_profile(f):
    """Validasi data profil + sosmed. Return (dict, error)."""
    alamat = f.get("alamat", "").strip()[:300]
    ig, tt = f.get("instagram", "").strip(), f.get("tiktok", "").strip()
    for val, label in ((ig, "Instagram"), (tt, "TikTok")):
        if val and not HANDLE_RE.match(val):
            return None, f"Username {label} tidak valid (hanya huruf, angka, titik, garis bawah)."
    try:
        peng = int(f.get("pengikut") or 0)
    except ValueError:
        return None, "Jumlah pengikut harus berupa angka."
    if not 0 <= peng <= 2_000_000_000:
        return None, "Jumlah pengikut di luar batas wajar."
    return {"alamat": alamat or None, "instagram": ig.lstrip("@") or None,
            "tiktok": tt.lstrip("@") or None, "pengikut": peng}, None


# --------------------------------------------------------------------------
# Util keranjang & stok
# --------------------------------------------------------------------------
def cart_detail():
    cart = session.get("cart", {})
    items, total = [], 0
    for pid, qty in list(cart.items()):
        p = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p or p["stok"] <= 0 or p["harga"] <= 0:
            cart.pop(pid, None)
            continue
        qty = min(int(qty), p["stok"])
        cart[pid] = qty
        sub = p["harga"] * qty
        items.append({"p": p, "qty": qty, "subtotal": sub})
        total += sub
    session["cart"] = cart
    return items, total


def restore_stock(order_id):
    for it in db().execute("SELECT product_id, jumlah FROM order_items WHERE order_id=?", (order_id,)).fetchall():
        db().execute("UPDATE products SET stok = stok + ? WHERE id=?", (it["jumlah"], it["product_id"]))


def touch_order(order_id, status):
    db().execute(
        "UPDATE orders SET status_order=?, updated_at=datetime('now','localtime') WHERE id=?",
        (status, order_id),
    )


def save_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        return None
    os.makedirs(PRODUCT_DIR, exist_ok=True)
    name = f"{int(time.time())}_{secure_filename(file_storage.filename)}"
    file_storage.save(os.path.join(PRODUCT_DIR, name))
    return name


# ==========================================================================
# AUTH KONSUMEN
# ==========================================================================
@app.route("/register", methods=["GET", "POST"])
@sec.rate_limit("register", 8, 3600)
def register():
    if request.method == "POST":
        nama = request.form.get("nama", "").strip()
        telp = request.form.get("no_telepon", "").strip().replace(" ", "")
        pw = request.form.get("password", "")
        pw2 = request.form.get("konfirmasi", "")
        prof, perr = parse_profile(request.form)
        if len(nama) < 2:
            flash("Nama minimal 2 huruf.", "error")
        elif not telp.isdigit() or not 9 <= len(telp) <= 15:
            flash("Nomor telepon harus berupa angka (9–15 digit).", "error")
        elif len(pw) < 6:
            flash("Password minimal 6 karakter.", "error")
        elif pw != pw2:
            flash("Konfirmasi password tidak sama.", "error")
        elif perr:
            flash(perr, "error")
        elif db().execute("SELECT 1 FROM users WHERE no_telepon=?", (telp,)).fetchone():
            flash("Nomor ini sudah terdaftar. Silakan masuk.", "error")
        else:
            cur = db().execute(
                "INSERT INTO users (nama, no_telepon, password, alamat, instagram, tiktok, pengikut) VALUES (?,?,?,?,?,?,?)",
                (nama, telp, generate_password_hash(pw), prof["alamat"], prof["instagram"], prof["tiktok"], prof["pengikut"]),
            )
            db().commit()
            session.pop("_csrf", None)
            session["user_id"] = cur.lastrowid
            flash(f"Akun dibuat. Selamat datang, {nama}!", "success")
            return redirect(url_for("home"))
    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
@sec.rate_limit("login", 10, 300)
def login():
    if request.method == "POST":
        telp = request.form.get("no_telepon", "").strip().replace(" ", "")
        pw = request.form.get("password", "")
        u = db().execute("SELECT * FROM users WHERE no_telepon=?", (telp,)).fetchone()
        if u and check_password_hash(u["password"], pw):
            session.pop("_csrf", None)     # token baru setelah login
            session["user_id"] = u["id"]
            flash(f"Halo lagi, {u['nama']}!", "success")
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        flash("Nomor telepon atau password salah.", "error")
    return render_template("auth/login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Kamu sudah keluar.", "info")
    return redirect(url_for("home"))


# ==========================================================================
# KONSUMEN: HOME, PRODUK
# ==========================================================================
@app.route("/")
def home():
    products = db().execute("SELECT * FROM products ORDER BY id").fetchall()
    stats = {
        "produk": len(products),
        "pelanggan": db().execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "pesanan": db().execute(
            "SELECT COUNT(*) FROM orders WHERE status_order IN ('lunas','diproses','dikirim','selesai')"
        ).fetchone()[0],
    }
    return render_template("customer/home.html", products=products, stats=stats)


@app.route("/produk")
def products():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "baru")
    order = {
        "baru": "id DESC",
        "murah": "harga ASC",
        "mahal": "harga DESC",
        "nama": "nama ASC",
    }.get(sort, "id DESC")
    rows = db().execute(
        f"SELECT * FROM products WHERE nama LIKE ? OR deskripsi LIKE ? ORDER BY {order}",
        (f"%{q}%", f"%{q}%"),
    ).fetchall()
    return render_template("customer/products.html", products=rows, q=q, sort=sort)


@app.route("/produk/<int:pid>")
def product_detail(pid):
    p = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    related = db().execute("SELECT * FROM products WHERE id != ? ORDER BY RANDOM() LIMIT 6", (pid,)).fetchall()
    return render_template("customer/detail.html", p=p, related=related)


# ==========================================================================
# KERANJANG (API JSON)
# ==========================================================================
@app.route("/api/cart/add", methods=["POST"])
@sec.rate_limit("cart", 150, 60)
def api_cart_add():
    data = request.get_json(silent=True) or {}
    pid = str(data.get("id", ""))
    qty = max(1, int(data.get("qty", 1) or 1))
    p = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        return jsonify(ok=False, msg="Produk tidak ditemukan"), 404
    if p["harga"] <= 0:
        return jsonify(ok=False, msg=f"Harga {p['nama']} belum ditentukan, belum bisa dipesan",
                       count=sum(session.get("cart", {}).values())), 400
    cart = session.get("cart", {})
    now = cart.get(pid, 0)
    if now + qty > p["stok"]:
        return jsonify(ok=False, msg=f"Stok {p['nama']} tinggal {p['stok']}", count=sum(cart.values())), 400
    cart[pid] = now + qty
    session["cart"] = cart
    return jsonify(ok=True, msg=f"{p['nama']} masuk keranjang", count=sum(cart.values()))


@app.route("/api/cart/update", methods=["POST"])
@sec.rate_limit("cart", 150, 60)
def api_cart_update():
    data = request.get_json(silent=True) or {}
    pid = str(data.get("id", ""))
    qty = int(data.get("qty", 1) or 0)
    cart = session.get("cart", {})
    p = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p or pid not in cart:
        return jsonify(ok=False), 404
    if qty <= 0:
        cart.pop(pid)
    else:
        cart[pid] = min(qty, p["stok"])
    session["cart"] = cart
    items, total = cart_detail()
    sub = next((i["subtotal"] for i in items if str(i["p"]["id"]) == pid), 0)
    return jsonify(ok=True, qty=cart.get(pid, 0), subtotal=rupiah(sub), total=rupiah(total),
                   count=sum(session["cart"].values()))


@app.route("/keranjang")
def cart():
    items, total = cart_detail()
    return render_template("customer/cart.html", items=items, total=total)


# ==========================================================================
# CHECKOUT & PEMBAYARAN QRIS
# ==========================================================================
@app.route("/checkout", methods=["GET", "POST"])
@login_required
@sec.rate_limit("checkout", 20, 600)
def checkout():
    items, total = cart_detail()
    if not items:
        flash("Keranjangmu masih kosong.", "info")
        return redirect(url_for("products"))
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

    if request.method == "POST":
        alamat = request.form.get("alamat", "").strip()[:300]
        if len(alamat) < 8:
            flash("Isi alamat pengiriman dengan lengkap (min. 8 karakter).", "error")
            return render_template("customer/checkout.html", items=items, total=total, user=user, alamat=alamat)
        conn = db()
        try:
            cur = conn.execute("INSERT INTO orders (user_id, total, status_order, alamat) VALUES (?,?,?,?)",
                               (user["id"], total, "menunggu_pembayaran", alamat))
            oid = cur.lastrowid
            for it in items:
                ok = conn.execute("UPDATE products SET stok = stok - ? WHERE id=? AND stok >= ?",
                                  (it["qty"], it["p"]["id"], it["qty"]))
                if ok.rowcount == 0:
                    raise ValueError(f"Stok {it['p']['nama']} tidak cukup")
                conn.execute(
                    "INSERT INTO order_items (order_id, product_id, jumlah, harga, subtotal) VALUES (?,?,?,?,?)",
                    (oid, it["p"]["id"], it["qty"], it["p"]["harga"], it["subtotal"]))
            ref = "PAY-" + datetime.now().strftime("%y%m%d") + "-" + str(oid).zfill(4) + "-" + \
                  "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
            conn.execute("INSERT INTO payments (order_id, metode, jumlah, status, payment_reference) VALUES (?,?,?,?,?)",
                         (oid, "QRIS", total, "belum_bayar", ref))
            conn.execute("UPDATE users SET alamat=? WHERE id=?", (alamat, user["id"]))
            conn.commit()
        except ValueError as e:
            conn.rollback()
            flash(str(e), "error")
            return redirect(url_for("cart"))
        session["cart"] = {}
        if gw.enabled() and not start_gateway_charge(oid):
            flash("QRIS otomatis belum bisa dibuat. Tekan tombol 'Buat QRIS' di halaman pembayaran.", "info")
        return redirect(url_for("payment", oid=oid))
    return render_template("customer/checkout.html", items=items, total=total, user=user, alamat=user["alamat"] or "")


def get_my_order(oid):
    o = db().execute("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, session["user_id"])).fetchone()
    if not o:
        abort(404)
    return o


@app.route("/bayar/<int:oid>")
@login_required
def payment(oid):
    o = get_my_order(oid)
    pay = db().execute("SELECT * FROM payments WHERE order_id=?", (oid,)).fetchone()
    items = db().execute(
        "SELECT oi.*, p.nama FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE order_id=?", (oid,)
    ).fetchall()
    qris_file = next((f for f in ("qris.png", "qris.jpg", "qris.jpeg", "qris.webp")
                      if os.path.exists(os.path.join(QRIS_DIR, f))), None)
    return render_template("customer/payment.html", o=o, pay=pay, items=items, qris_file=qris_file,
                           gw_enabled=gw.enabled(), sandbox=not gw.is_production())


@app.route("/bayar/<int:oid>/konfirmasi", methods=["POST"])
@login_required
def payment_confirm(oid):
    o = get_my_order(oid)
    pay = db().execute("SELECT * FROM payments WHERE order_id=?", (oid,)).fetchone()
    if pay["gateway"] == "midtrans":
        # Pembayaran otomatis: tombol ini HANYA memeriksa status ke gateway, tidak pernah menandai lunas.
        res = reconcile_payment(pay)
        flash({"lunas": "Pembayaran diterima. Terima kasih!", "pending": "Pembayaran belum terdeteksi. Selesaikan pembayaran lalu cek lagi.",
               "error": "Gagal menghubungi gateway, coba lagi sebentar."}.get(res, "Status pembayaran diperbarui."), "info")
        return redirect(url_for("payment", oid=oid))
    if o["status_order"] == "menunggu_pembayaran" and pay["status"] in ("belum_bayar", "ditolak"):
        db().execute("UPDATE payments SET status='menunggu_verifikasi' WHERE id=?", (pay["id"],))
        touch_order(oid, "menunggu_verifikasi")
        db().commit()
        flash("Terima kasih! Admin akan memeriksa pembayaranmu.", "success")
    return redirect(url_for("payment", oid=oid))


@app.route("/api/pesanan/<int:oid>/status")
@login_required
def api_order_status(oid):
    get_my_order(oid)
    pay = db().execute("SELECT * FROM payments WHERE order_id=?", (oid,)).fetchone()
    if pay["gateway"] == "midtrans" and pay["status"] in pay_flow.OPEN:
        reconcile_payment(pay, min_interval=4)          # jaga-jaga webhook terlambat
    o = db().execute("SELECT status_order FROM orders WHERE id=?", (oid,)).fetchone()
    pay = db().execute("SELECT status FROM payments WHERE order_id=?", (oid,)).fetchone()
    return jsonify(order=o["status_order"], payment=pay["status"])


@app.route("/pesanan")
@login_required
def orders():
    rows = db().execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    data = []
    for o in rows:
        items = db().execute(
            "SELECT oi.*, p.nama, p.id AS pid, p.gambar FROM order_items oi JOIN products p ON p.id=oi.product_id "
            "WHERE order_id=?", (o["id"],)).fetchall()
        pay = db().execute("SELECT * FROM payments WHERE order_id=?", (o["id"],)).fetchone()
        data.append({"o": o, "items": items, "pay": pay})
    return render_template("customer/orders.html", data=data)


@app.route("/pesanan/<int:oid>/batal", methods=["POST"])
@login_required
def order_cancel(oid):
    o = get_my_order(oid)
    if o["status_order"] == "menunggu_pembayaran":
        ok, msg = cancel_gateway_payment(oid)
        if not ok:
            flash(msg, "error")
            return redirect(request.referrer or url_for("orders"))
        restore_stock(oid)
        touch_order(oid, "dibatalkan")
        db().execute("UPDATE payments SET status='dibatalkan' WHERE order_id=?", (oid,))
        db().commit()
        flash(f"Pesanan #{oid} dibatalkan.", "info")
    return redirect(url_for("orders"))


# ==========================================================================
# ADMIN
# ==========================================================================
@app.route("/admin/login", methods=["GET", "POST"])
@sec.rate_limit("adminlogin", 6, 300)
def admin_login():
    if request.method == "POST":
        a = db().execute("SELECT * FROM admins WHERE username=?", (request.form.get("username", "").strip(),)).fetchone()
        if a and check_password_hash(a["password"], request.form.get("password", "")):
            session.pop("_csrf", None)
            session.permanent = True
            session["admin_id"], session["admin_name"] = a["id"], a["nama"]
            audit("login")
            db().commit()
            return redirect(url_for("admin_dashboard"))
        db().execute("INSERT INTO audit_logs (aksi, detail, ip) VALUES ('login_gagal', ?, ?)",
                     (request.form.get("username", "")[:60], sec.client_ip()))
        db().commit()
        flash("Username atau password admin salah.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    if session.get("admin_id"):
        audit("logout")
        db().commit()
    session.pop("admin_id", None)
    session.pop("admin_name", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    q = lambda sql, *a: db().execute(sql, a).fetchone()[0]
    stat = {
        "pendapatan": q("SELECT COALESCE(SUM(jumlah),0) FROM payments WHERE status='lunas'"),
        "saldo": q("SELECT COALESCE(SUM(CASE tipe WHEN 'masuk' THEN jumlah ELSE -jumlah END),0) FROM cash_flows"),
        "verifikasi": q("SELECT COUNT(*) FROM payments WHERE status='menunggu_verifikasi'"),
        "pesanan_baru": q("SELECT COUNT(*) FROM orders WHERE status_order IN ('lunas','diproses')"),
        "pelanggan": q("SELECT COUNT(*) FROM users"),
        "produk": q("SELECT COUNT(*) FROM products"),
        "terlambat": q("SELECT COUNT(*) FROM payments WHERE status='terlambat'"),
    }
    chart = []
    for i in range(6, -1, -1):
        d = datetime.now() - timedelta(days=i)
        v = q("SELECT COALESCE(SUM(jumlah),0) FROM payments WHERE status='lunas' AND date(paid_at)=?",
              d.strftime("%Y-%m-%d"))
        chart.append({"label": d.strftime("%d/%m"), "v": v})
    peak = max([c["v"] for c in chart] + [1])
    low = db().execute("SELECT * FROM products WHERE stok <= 5 ORDER BY stok").fetchall()
    recent = db().execute(
        "SELECT o.*, u.nama FROM orders o JOIN users u ON u.id=o.user_id ORDER BY o.id DESC LIMIT 6").fetchall()
    return render_template("admin/dashboard.html", stat=stat, chart=chart, peak=peak, low=low, recent=recent)


# ----- produk & stok -----
@app.route("/admin/produk", methods=["GET", "POST"])
@admin_required
def admin_products():
    if request.method == "POST":
        pid = request.form.get("id")
        nama = request.form.get("nama", "").strip()
        desk = request.form.get("deskripsi", "").strip()
        try:
            harga = int(request.form.get("harga") or 0)
            stok = int(request.form.get("stok", 0))
        except ValueError:
            flash("Harga dan stok harus angka.", "error")
            return redirect(url_for("admin_products"))
        if not nama or harga < 0 or stok < 0:
            flash("Data produk belum lengkap.", "error")
            return redirect(url_for("admin_products"))
        img = save_image(request.files.get("gambar"))
        if pid:
            if img:
                db().execute("UPDATE products SET nama=?, deskripsi=?, harga=?, stok=?, gambar=? WHERE id=?",
                             (nama, desk, harga, stok, img, pid))
            else:
                db().execute("UPDATE products SET nama=?, deskripsi=?, harga=?, stok=? WHERE id=?",
                             (nama, desk, harga, stok, pid))
            audit("produk_edit", f"id={pid} {nama} harga={harga} stok={stok}")
            flash("Produk diperbarui.", "success")
        else:
            db().execute("INSERT INTO products (nama, deskripsi, harga, stok, gambar) VALUES (?,?,?,?,?)",
                         (nama, desk, harga, stok, img))
            audit("produk_tambah", f"{nama} harga={harga} stok={stok}")
            flash("Produk ditambahkan.", "success")
        db().commit()
        return redirect(url_for("admin_products"))
    rows = db().execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    return render_template("admin/products.html", products=rows)


@app.route("/admin/produk/<int:pid>/stok", methods=["POST"])
@admin_required
def admin_stock(pid):
    try:
        delta = int(request.form.get("delta", 0))
    except ValueError:
        delta = 0
    db().execute("UPDATE products SET stok = MAX(0, stok + ?) WHERE id=?", (delta, pid))
    audit("stok_ubah", f"produk={pid} delta={delta}")
    db().commit()
    flash("Stok diperbarui.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/produk/<int:pid>/hapus", methods=["POST"])
@admin_required
def admin_product_delete(pid):
    try:
        db().execute("DELETE FROM products WHERE id=?", (pid,))
        audit("produk_hapus", f"id={pid}")
        db().commit()
        flash("Produk dihapus.", "success")
    except sqlite3.IntegrityError:
        db().rollback()
        flash("Produk sudah pernah dipesan, jadi tidak bisa dihapus. Set stoknya ke 0 saja.", "error")
    return redirect(url_for("admin_products"))


# ----- pesanan -----
@app.route("/admin/pesanan")
@admin_required
def admin_orders():
    st = request.args.get("status", "")
    sql = ("SELECT o.*, u.nama, u.no_telepon, p.status AS pay_status FROM orders o "
           "JOIN users u ON u.id=o.user_id LEFT JOIN payments p ON p.order_id=o.id")
    rows = db().execute(sql + (" WHERE o.status_order=?" if st else "") + " ORDER BY o.id DESC",
                        (st,) if st else ()).fetchall()
    data = []
    for o in rows:
        items = db().execute(
            "SELECT oi.*, p.nama FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE order_id=?",
            (o["id"],)).fetchall()
        data.append({"o": o, "items": items})
    return render_template("admin/orders.html", data=data, st=st, labels=STATUS_LABEL, kurir_list=KURIR)


@app.route("/admin/pesanan/<int:oid>/status", methods=["POST"])
@admin_required
def admin_order_status(oid):
    new = request.form.get("status")
    o = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        abort(404)
    if new == "dikirim" and not (o["no_resi"] or o["kurir"] == "Antar sendiri"):
        flash("Isi kurir dan nomor resi lewat form pengiriman terlebih dulu.", "error")
    elif new in ("diproses", "dikirim", "selesai") and o["status_order"] in PAID_STATUSES:
        touch_order(oid, new)
        flash(f"Pesanan #{oid} → {STATUS_LABEL[new]}.", "success")
    elif new == "dibatalkan" and o["status_order"] in ("menunggu_pembayaran", "menunggu_verifikasi"):
        ok, msg = cancel_gateway_payment(oid)
        if not ok:
            flash(msg, "error")
        else:
            restore_stock(oid)
            touch_order(oid, "dibatalkan")
            db().execute("UPDATE payments SET status='dibatalkan' WHERE order_id=?", (oid,))
            flash(f"Pesanan #{oid} dibatalkan dan stok dikembalikan.", "info")
    else:
        flash("Perubahan status tidak diizinkan untuk pesanan ini.", "error")
    audit("pesanan_status", f"#{oid} -> {new}")
    db().commit()
    return redirect(request.referrer or url_for("admin_orders"))


# ----- pembayaran -----
@app.route("/admin/pembayaran")
@admin_required
def admin_payments():
    st = request.args.get("status", "")
    sql = ("SELECT p.*, o.user_id, u.nama FROM payments p JOIN orders o ON o.id=p.order_id "
           "JOIN users u ON u.id=o.user_id")
    rows = db().execute(sql + (" WHERE p.status=?" if st else "") + " ORDER BY "
                        "CASE p.status WHEN 'terlambat' THEN 0 WHEN 'menunggu_verifikasi' THEN 1 ELSE 2 END, p.id DESC",
                        (st,) if st else ()).fetchall()
    events = db().execute("SELECT * FROM payment_events ORDER BY id DESC LIMIT 30").fetchall()
    return render_template("admin/payments.html", payments=rows, st=st, events=events, gw_enabled=gw.enabled())


@app.route("/admin/pembayaran/<int:pid>/<aksi>", methods=["POST"])
@admin_required
def admin_payment_action(pid, aksi):
    pay = db().execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not pay or pay["status"] != "menunggu_verifikasi":
        flash("Pembayaran ini tidak sedang menunggu verifikasi.", "error")
        return redirect(url_for("admin_payments"))
    if pay["gateway"]:
        flash("Pembayaran otomatis: status hanya ditentukan oleh gateway, tidak bisa diubah manual.", "error")
        return redirect(url_for("admin_payments"))
    if aksi == "lunas":
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db().execute("UPDATE payments SET status='lunas', paid_at=? WHERE id=?", (now, pid))
        touch_order(pay["order_id"], "lunas")
        db().execute("INSERT INTO cash_flows (tipe, jumlah, keterangan) VALUES ('masuk',?,?)",
                     (pay["jumlah"], f"Pembayaran pesanan #{pay['order_id']} ({pay['payment_reference']})"))
        flash(f"Pesanan #{pay['order_id']} lunas dan masuk ke kas.", "success")
    elif aksi == "tolak":
        db().execute("UPDATE payments SET status='ditolak' WHERE id=?", (pid,))
        touch_order(pay["order_id"], "menunggu_pembayaran")
        flash("Pembayaran ditolak. Pelanggan bisa mengonfirmasi ulang.", "info")
    audit("pembayaran_" + aksi, f"payment={pid} pesanan=#{pay['order_id']} {pay['jumlah']}")
    db().commit()
    return redirect(url_for("admin_payments"))


# ----- pelanggan -----
@app.route("/admin/pelanggan")
@admin_required
def admin_customers():
    rows = db().execute(
        "SELECT u.*, COUNT(o.id) AS jml_order, "
        "COALESCE(SUM(CASE WHEN o.status_order IN ('lunas','diproses','dikirim','selesai') THEN o.total END),0) AS belanja "
        "FROM users u LEFT JOIN orders o ON o.user_id=u.id GROUP BY u.id ORDER BY belanja DESC, u.id DESC"
    ).fetchall()
    return render_template("admin/customers.html", customers=rows)


# ----- kas -----
@app.route("/admin/kas", methods=["GET", "POST"])
@admin_required
def admin_cashflow():
    if request.method == "POST":
        tipe = request.form.get("tipe")
        ket = request.form.get("keterangan", "").strip()
        try:
            jml = int(request.form.get("jumlah", 0))
        except ValueError:
            jml = 0
        if tipe in ("masuk", "keluar") and jml > 0 and ket:
            db().execute("INSERT INTO cash_flows (tipe, jumlah, keterangan) VALUES (?,?,?)", (tipe, jml, ket))
            audit("kas_tambah", f"{tipe} {jml} {ket}")
            db().commit()
            flash("Catatan kas ditambahkan.", "success")
        else:
            flash("Isi tipe, jumlah (> 0), dan keterangan.", "error")
        return redirect(url_for("admin_cashflow"))
    rows = db().execute("SELECT * FROM cash_flows ORDER BY id DESC").fetchall()
    masuk = sum(r["jumlah"] for r in rows if r["tipe"] == "masuk")
    keluar = sum(r["jumlah"] for r in rows if r["tipe"] == "keluar")
    return render_template("admin/cashflow.html", rows=rows, masuk=masuk, keluar=keluar)


@app.route("/admin/kas/<int:cid>/hapus", methods=["POST"])
@admin_required
def admin_cash_delete(cid):
    db().execute("DELETE FROM cash_flows WHERE id=? AND keterangan NOT LIKE 'Pembayaran pesanan #%' AND keterangan NOT LIKE 'Refund pesanan #%'", (cid,))
    audit("kas_hapus", f"id={cid}")
    db().commit()
    flash("Catatan manual dihapus (catatan otomatis dari pembayaran tidak bisa dihapus).", "info")
    return redirect(url_for("admin_cashflow"))


# ----- laporan -----
@app.route("/admin/laporan")
@admin_required
def admin_reports():
    bulan = request.args.get("bulan") or datetime.now().strftime("%Y-%m")
    one = lambda sql, *a: db().execute(sql, a).fetchone()[0]
    rep = {
        "penjualan": one("SELECT COALESCE(SUM(jumlah),0) FROM payments WHERE status='lunas' AND strftime('%Y-%m',paid_at)=?", bulan),
        "transaksi": one("SELECT COUNT(*) FROM payments WHERE status='lunas' AND strftime('%Y-%m',paid_at)=?", bulan),
        "masuk": one("SELECT COALESCE(SUM(jumlah),0) FROM cash_flows WHERE tipe='masuk' AND strftime('%Y-%m',created_at)=?", bulan),
        "keluar": one("SELECT COALESCE(SUM(jumlah),0) FROM cash_flows WHERE tipe='keluar' AND strftime('%Y-%m',created_at)=?", bulan),
        "batal": one("SELECT COUNT(*) FROM orders WHERE status_order='dibatalkan' AND strftime('%Y-%m',created_at)=?", bulan),
    }
    rep["laba"] = rep["masuk"] - rep["keluar"]
    top = db().execute(
        "SELECT p.nama, SUM(oi.jumlah) AS qty, SUM(oi.subtotal) AS omzet FROM order_items oi "
        "JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id "
        "WHERE o.status_order IN ('lunas','diproses','dikirim','selesai') AND strftime('%Y-%m',o.created_at)=? "
        "GROUP BY p.id ORDER BY qty DESC LIMIT 8", (bulan,)).fetchall()
    harian = db().execute(
        "SELECT date(paid_at) AS tgl, COUNT(*) AS n, SUM(jumlah) AS total FROM payments "
        "WHERE status='lunas' AND strftime('%Y-%m',paid_at)=? GROUP BY date(paid_at) ORDER BY tgl", (bulan,)).fetchall()
    return render_template("admin/reports.html", bulan=bulan, rep=rep, top=top, harian=harian)


# ==========================================================================
# TAMBAHAN v2 — QRIS dinamis (Midtrans), webhook, rekonsiliasi
# ==========================================================================
def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def start_gateway_charge(oid, new_ref=False):
    """Buat QRIS dinamis untuk pesanan. True jika berhasil. Gagal -> order tetap aman, bisa dicoba lagi."""
    if not gw.enabled():
        return False
    pay = db().execute("SELECT * FROM payments WHERE order_id=?", (oid,)).fetchone()
    o = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not pay or not o or pay["status"] != "belum_bayar" or o["status_order"] != "menunggu_pembayaran":
        return False
    if pay["gateway"] == "midtrans" and pay["qr_url"]:
        return True                                    # sudah punya QR aktif
    user = db().execute("SELECT nama, no_telepon FROM users WHERE id=?", (o["user_id"],)).fetchone()
    items = db().execute("SELECT oi.product_id AS id, oi.harga AS price, oi.jumlah AS qty, p.nama AS name "
                         "FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?", (oid,)).fetchall()
    ref = pay["payment_reference"]
    if new_ref:                                        # percobaan ulang: order_id baru agar tidak bentrok di gateway
        ref = ref.split("-R")[0] + "-R" + datetime.now().strftime("%H%M%S")
        db().execute("UPDATE payments SET payment_reference=? WHERE id=?", (ref, pay["id"]))
        db().commit()
    try:
        r = gw.create_qris(ref, pay["jumlah"], items, user["nama"], user["no_telepon"])
    except gw.GatewayError as e:
        app.logger.warning("Charge QRIS gagal untuk pesanan %s: %s", oid, e)
        return False
    exp = (datetime.now() + timedelta(minutes=gw.expiry_minutes())).strftime("%Y-%m-%d %H:%M:%S")
    db().execute("UPDATE payments SET gateway='midtrans', gateway_ref=?, qr_url=?, expires_at=?, gateway_status=?, last_check=? WHERE id=?",
                 (r["transaction_id"], r["qr_url"], exp, r["status"], _now(), pay["id"]))
    db().commit()
    return True


def reconcile_payment(pay, min_interval=0):
    """Tanyakan status resmi ke gateway lalu terapkan lewat jalur yang sama dengan webhook."""
    if pay["gateway"] != "midtrans" or not gw.enabled():
        return None
    if min_interval and pay["last_check"]:
        try:
            if (datetime.now() - datetime.strptime(pay["last_check"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds() < min_interval:
                return None
        except ValueError:
            pass
    try:
        r = gw.get_status(pay["payment_reference"])
    except gw.GatewayError:
        return "error"
    if str(r.get("status_code")) == "404":             # belum ada transaksi di sisi gateway
        db().execute("UPDATE payments SET last_check=? WHERE id=?", (_now(), pay["id"]))
        db().commit()
        return "pending"
    return pay_flow.apply_event(db(), r, "status")


def cancel_gateway_payment(oid):
    """Batalkan QRIS yang masih pending di gateway sebelum pesanan dibatalkan. Return (ok, pesan)."""
    pay = db().execute("SELECT * FROM payments WHERE order_id=?", (oid,)).fetchone()
    if not pay or pay["gateway"] != "midtrans":
        return True, ""
    try:
        r = gw.cancel(pay["payment_reference"])
        if str(r.get("status_code")) in ("200", "201", "202", "404"):
            return True, ""
        res = reconcile_payment(pay)                   # gateway menolak (mis. sudah dibayar): cek status sebenarnya
        if res in ("lunas", "already"):
            return False, "Pembayaran sudah diterima, pesanan tidak bisa dibatalkan."
        return False, "Gateway menolak pembatalan. Coba lagi atau hubungi admin."
    except gw.GatewayError:
        return False, "Tidak dapat menghubungi gateway pembayaran. Coba lagi sebentar."


@app.route("/webhook/midtrans", methods=["POST"])
@sec.rate_limit("webhook", 300, 60)
def webhook_midtrans():
    """Endpoint notifikasi Midtrans. Atur URL ini di Dashboard Midtrans > Settings > Payment > Notification URL."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False), 400
    if not gw.enabled():
        return jsonify(ok=False, msg="gateway nonaktif"), 503
    res = pay_flow.apply_event(db(), data, "webhook")
    if res == "invalid":
        return jsonify(ok=False), 403
    return jsonify(ok=True, result=res)


@app.route("/bayar/<int:oid>/qr")
@login_required
def payment_qr(oid):
    get_my_order(oid)
    pay = db().execute("SELECT * FROM payments WHERE order_id=?", (oid,)).fetchone()
    if not gw.enabled() or not pay["qr_url"] or pay["status"] != "belum_bayar":
        abort(404)
    try:
        data, ctype = gw.fetch_qr_image(pay["qr_url"])
    except gw.GatewayError:
        abort(502)
    return Response(data, mimetype=ctype, headers={"Cache-Control": "private, max-age=60"})


@app.route("/bayar/<int:oid>/buat-qris", methods=["POST"])
@login_required
def payment_create_qris(oid):
    get_my_order(oid)
    if start_gateway_charge(oid, new_ref=True):
        flash("QRIS dibuat. Silakan scan dan bayar.", "success")
    else:
        flash("QRIS belum bisa dibuat saat ini. Coba lagi sebentar.", "error")
    return redirect(url_for("payment", oid=oid))


# ----- profil pelanggan + data sosmed -----
@app.route("/akun", methods=["GET", "POST"])
@login_required
def account():
    user = db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if request.method == "POST":
        prof, err = parse_profile(request.form)
        nama = request.form.get("nama", "").strip()
        if err:
            flash(err, "error")
        elif len(nama) < 2:
            flash("Nama minimal 2 huruf.", "error")
        else:
            db().execute("UPDATE users SET nama=?, alamat=?, instagram=?, tiktok=?, pengikut=? WHERE id=?",
                         (nama, prof["alamat"], prof["instagram"], prof["tiktok"], prof["pengikut"], user["id"]))
            db().commit()
            flash("Profil diperbarui.", "success")
            return redirect(url_for("account"))
    return render_template("customer/account.html", user=user)


# ==========================================================================
# TAMBAHAN v2 — ADMIN: pengiriman, refund, rekonsiliasi, analitik, audit
# ==========================================================================
KURIR = ["JNE", "J&T Express", "SiCepat", "AnterAja", "Pos Indonesia", "GoSend", "Grab Express", "Antar sendiri"]


@app.route("/admin/pesanan/<int:oid>/kirim", methods=["POST"])
@admin_required
def admin_ship(oid):
    o = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        abort(404)
    kurir = request.form.get("kurir", "")
    resi = request.form.get("no_resi", "").strip().upper()
    if o["status_order"] not in ("lunas", "diproses", "dikirim"):
        flash("Pesanan harus sudah lunas sebelum dikirim.", "error")
    elif kurir not in KURIR:
        flash("Pilih kurir yang valid.", "error")
    elif kurir != "Antar sendiri" and not re.fullmatch(r"[A-Z0-9\-]{5,40}", resi):
        flash("Nomor resi tidak valid (5–40 karakter huruf/angka/strip).", "error")
    else:
        first = o["status_order"] != "dikirim"
        db().execute("UPDATE orders SET kurir=?, no_resi=?, status_order='dikirim', "
                     "dikirim_at=COALESCE(dikirim_at, ?), updated_at=datetime('now','localtime') WHERE id=?",
                     (kurir, resi or None, _now(), oid))
        audit("pengiriman", f"#{oid} {kurir} {resi}")
        db().commit()
        flash(f"Pesanan #{oid} {'dikirim' if first else 'resi diperbarui'} via {kurir}.", "success")
    return redirect(request.referrer or url_for("admin_orders"))


@app.route("/admin/pesanan/<int:oid>/refund", methods=["POST"])
@admin_required
def admin_refund(oid):
    o = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    pay = db().execute("SELECT * FROM payments WHERE order_id=?", (oid,)).fetchone()
    reason = request.form.get("alasan", "").strip()
    restock = request.form.get("restock") == "1"
    if not o or not pay:
        abort(404)
    if pay["status"] not in ("lunas", "terlambat"):
        flash("Hanya pembayaran yang sudah lunas yang bisa direfund.", "error")
    elif len(reason) < 3:
        flash("Isi alasan refund.", "error")
    else:
        if pay["gateway"] == "midtrans":
            try:
                r = gw.refund(pay["payment_reference"], pay["jumlah"], "RF-" + pay["payment_reference"], reason)
            except gw.GatewayError:
                flash("Tidak dapat menghubungi gateway. Refund belum diproses, coba lagi.", "error")
                return redirect(request.referrer or url_for("admin_orders"))
            if str(r.get("status_code")) != "200":
                audit("refund_ditolak_gateway", f"#{oid} {r.get('status_code')} {r.get('status_message')}")
                db().commit()
                flash(f"Refund ditolak gateway: {r.get('status_message', 'tidak diketahui')}", "error")
                return redirect(request.referrer or url_for("admin_orders"))
        cur = db().execute("UPDATE payments SET status='refund', refund_amount=?, refund_at=?, refund_note=? "
                           "WHERE id=? AND status IN ('lunas','terlambat')", (pay["jumlah"], _now(), reason, pay["id"]))
        if cur.rowcount == 1:
            if restock and o["status_order"] != "dibatalkan":
                restore_stock(oid)
            touch_order(oid, "refund")
            db().execute("INSERT INTO cash_flows (tipe, jumlah, keterangan) VALUES ('keluar',?,?)",
                         (pay["jumlah"], f"Refund pesanan #{oid} ({pay['payment_reference']}): {reason[:80]}"))
            audit("refund", f"#{oid} {pay['jumlah']} {'gateway' if pay['gateway'] else 'manual'} {reason[:80]}")
            db().commit()
            flash(f"Refund pesanan #{oid} dicatat ({'via gateway' if pay['gateway'] else 'manual: pastikan dana sudah ditransfer'}).", "success")
    return redirect(request.referrer or url_for("admin_orders"))


@app.route("/admin/pembayaran/<int:pid>/pulihkan", methods=["POST"])
@admin_required
def admin_payment_recover(pid):
    """Pembayaran datang setelah pesanan dibatalkan/kedaluwarsa: aktifkan kembali pesanan jika stok masih cukup."""
    pay = db().execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not pay or pay["status"] != "terlambat":
        flash("Tidak ada pembayaran terlambat yang bisa dipulihkan.", "error")
        return redirect(url_for("admin_payments"))
    oid = pay["order_id"]
    ok = True
    for it in db().execute("SELECT product_id, jumlah FROM order_items WHERE order_id=?", (oid,)).fetchall():
        if db().execute("UPDATE products SET stok = stok - ? WHERE id=? AND stok >= ?", (it["jumlah"], it["product_id"], it["jumlah"])).rowcount == 0:
            ok = False
            break
    if not ok:
        db().rollback()
        flash("Stok tidak cukup untuk memulihkan pesanan. Gunakan Refund.", "error")
    else:
        db().execute("UPDATE payments SET status='lunas' WHERE id=?", (pid,))
        touch_order(oid, "lunas")
        audit("pulihkan_pesanan", f"#{oid} payment={pid}")
        db().commit()
        flash(f"Pesanan #{oid} dipulihkan dan ditandai lunas.", "success")
    return redirect(url_for("admin_payments"))


@app.route("/admin/pembayaran/rekonsiliasi", methods=["POST"])
@admin_required
def admin_reconcile():
    """Cek ulang semua pembayaran gateway yang belum final (untuk webhook yang terlambat/hilang)."""
    rows = db().execute("SELECT * FROM payments WHERE gateway='midtrans' AND status IN ('belum_bayar','menunggu_verifikasi')").fetchall()
    hasil = {}
    for p in rows:
        r = reconcile_payment(p) or "-"
        hasil[r] = hasil.get(r, 0) + 1
    audit("rekonsiliasi", f"{len(rows)} transaksi: {hasil}")
    db().commit()
    flash(f"Rekonsiliasi selesai ({len(rows)} transaksi): " + (", ".join(f"{k}={v}" for k, v in hasil.items()) or "tidak ada yang perlu dicek"), "success")
    return redirect(url_for("admin_payments"))


@app.route("/admin/pembayaran/<int:pid>/cek", methods=["POST"])
@admin_required
def admin_payment_check(pid):
    pay = db().execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not pay:
        abort(404)
    res = reconcile_payment(pay) or "tidak ada gateway"
    audit("cek_status", f"payment={pid} hasil={res}")
    db().commit()
    flash(f"Hasil pengecekan: {res}", "info")
    return redirect(url_for("admin_payments"))


@app.route("/admin/pelanggan/<int:uid>/sosmed", methods=["POST"])
@admin_required
def admin_customer_social(uid):
    prof, err = parse_profile(request.form)
    if err:
        flash(err, "error")
    else:
        db().execute("UPDATE users SET instagram=?, tiktok=?, pengikut=? WHERE id=?",
                     (prof["instagram"], prof["tiktok"], prof["pengikut"], uid))
        audit("sosmed_pelanggan", f"user={uid} ig={prof['instagram']} tt={prof['tiktok']} pengikut={prof['pengikut']}")
        db().commit()
        flash("Data sosmed pelanggan disimpan.", "success")
    return redirect(url_for("admin_customers"))


@app.route("/admin/analitik")
@admin_required
def admin_analytics():
    conn = db()
    cust = analytics.customer_scores(conn)
    pairs, n_orders = analytics.basket_pairs(conn)
    return render_template("admin/analytics.html", kpi=analytics.kpis(conn), fc=analytics.forecast(conn),
                           customers=cust, segs=analytics.segment_summary(cust), restock=analytics.restock_plan(conn),
                           abc=analytics.abc_analysis(conn), pairs=pairs, n_orders=n_orders)


@app.route("/admin/audit")
@admin_required
def admin_audit():
    page = max(1, request.args.get("p", 1, type=int))
    per = 25
    total = db().execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    rows = db().execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?", (per, (page - 1) * per)).fetchall()
    return render_template("admin/audit.html", rows=rows, page=page, pages=max(1, -(-total // per)), total=total)


@app.route("/admin/akun", methods=["GET", "POST"])
@admin_required
@sec.rate_limit("adminpw", 6, 600)
def admin_account():
    a = db().execute("SELECT * FROM admins WHERE id=?", (session["admin_id"],)).fetchone()
    if request.method == "POST":
        old, new, conf = (request.form.get(k, "") for k in ("lama", "baru", "konfirmasi"))
        if not check_password_hash(a["password"], old):
            flash("Password lama salah.", "error")
        elif len(new) < 8:
            flash("Password baru minimal 8 karakter.", "error")
        elif new != conf:
            flash("Konfirmasi password tidak sama.", "error")
        else:
            db().execute("UPDATE admins SET password=? WHERE id=?", (generate_password_hash(new), a["id"]))
            audit("ganti_password")
            db().commit()
            flash("Password admin diganti.", "success")
            return redirect(url_for("admin_account"))
    return render_template("admin/account.html", a=a)


# ==========================================================================
# TIM / BIODATA ANGGOTA
# ==========================================================================
@app.route("/tim")
def team_index():
    return render_template("team/index.html", members=team.TEAM)


@app.route("/tim/<slug>")
def team_member(slug):
    idx = next((i for i, m in enumerate(team.TEAM) if m["slug"] == slug), None)
    if idx is None:
        abort(404)
    n = len(team.TEAM)
    return render_template("team/member.html", m=team.TEAM[idx], prev=team.TEAM[idx - 1], nxt=team.TEAM[(idx + 1) % n],
                           foto=team.photo_for(slug, os.path.join(app.root_path, "static")), idx=idx + 1, total=n)


# ----- error -----
@app.errorhandler(404)
def not_found(_e):
    return render_template("auth/404.html"), 404


@app.errorhandler(413)
def too_large(_e):
    flash("Ukuran file terlalu besar (maks 4 MB).", "error")
    return redirect(request.referrer or url_for("home"))


@app.errorhandler(500)
def server_error(e):
    app.logger.exception("Kesalahan server: %s", e)          # detail hanya di log server
    return render_template("auth/error.html"), 500


database.init_db()  # pastikan tabel & data awal ada

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
