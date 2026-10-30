"""Koneksi & inisialisasi database SQLite untuk Toko Kelompok."""
import os
import sqlite3
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("TOKO_DB_PATH") or os.path.join(BASE_DIR, "database", "toko.db")
DB_DIR = os.path.dirname(DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT NOT NULL,
    no_telepon TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT NOT NULL,
    deskripsi TEXT,
    harga INTEGER NOT NULL,
    stok INTEGER NOT NULL DEFAULT 0,
    gambar TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    total INTEGER NOT NULL,
    status_order TEXT NOT NULL DEFAULT 'menunggu_pembayaran',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    jumlah INTEGER NOT NULL,
    harga INTEGER NOT NULL,
    subtotal INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    metode TEXT NOT NULL DEFAULT 'QRIS',
    jumlah INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'belum_bayar',
    payment_reference TEXT,
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS cash_flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipe TEXT NOT NULL CHECK (tipe IN ('masuk','keluar')),
    jumlah INTEGER NOT NULL,
    keterangan TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

SEED_PRODUCTS = [
    # (nama, deskripsi, harga, stok)  -- harga 0 = "harga menyusul" (belum bisa dipesan)
    ("Risol Lumer Mayo", "Kulit renyah berlapis tepung roti, isi mayo creamy yang lumer saat digigit.", 0, 50),
    ("Risol Lumer Keju", "Kulit renyah dengan keju yang meleleh dan menarik panjang di setiap gigitan.", 0, 50),
    ("Risol Lumer Coklat", "Kulit renyah dengan coklat pekat yang mengalir hangat dari dalam.", 0, 50),
    ("Risol Lumer Matcha", "Kulit renyah dengan isi matcha lumer yang lembut, harum, dan sedikit pahit.", 0, 50),
]

# Produk contoh versi lama (gadget) -> dibersihkan otomatis agar tidak tercampur
OLD_DEMO = ("Aero Headphones", "Pulse Smartwatch", "Orbit Speaker", "Halo Desk Lamp",
            "Drift Backpack", "Echo Earbuds", "Slate Keyboard", "Mono Bottle")


# ---------------------------------------------------------------------------
# TAMBAHAN (v2): hanya menambah tabel/kolom, tidak mengubah data yang sudah ada
# ---------------------------------------------------------------------------
SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS payment_events (          -- log webhook/status gateway (anti-duplikat & jejak audit)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE,
    order_ref TEXT,
    source TEXT,
    transaction_status TEXT,
    status_code TEXT,
    valid INTEGER DEFAULT 0,
    note TEXT,
    payload TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS audit_logs (              -- jejak aktivitas admin
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    admin_nama TEXT,
    aksi TEXT NOT NULL,
    detail TEXT,
    ip TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

# (tabel, kolom, definisi)
NEW_COLUMNS = [
    ("users", "alamat", "TEXT"),
    ("users", "instagram", "TEXT"),
    ("users", "tiktok", "TEXT"),
    ("users", "pengikut", "INTEGER DEFAULT 0"),       # jumlah pengikut sosmed (nilai untuk skor pengaruh)
    ("orders", "alamat", "TEXT"),
    ("orders", "kurir", "TEXT"),
    ("orders", "no_resi", "TEXT"),
    ("orders", "dikirim_at", "TEXT"),
    ("payments", "gateway", "TEXT"),                  # 'midtrans' atau NULL (manual)
    ("payments", "gateway_ref", "TEXT"),              # transaction_id dari gateway
    ("payments", "qr_url", "TEXT"),
    ("payments", "expires_at", "TEXT"),
    ("payments", "last_check", "TEXT"),
    ("payments", "gateway_status", "TEXT"),
    ("payments", "refund_amount", "INTEGER DEFAULT 0"),
    ("payments", "refund_at", "TEXT"),
    ("payments", "refund_note", "TEXT"),
]


def _missing_columns(conn):
    out = []
    for tbl, col, ddl in NEW_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        if col not in have:
            out.append((tbl, col, ddl))
    return out


def migrate(conn):
    """Tambah kolom/tabel baru. Backup otomatis jika DB lama perlu diubah."""
    import shutil
    from datetime import datetime
    missing = _missing_columns(conn)
    has_data = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0]
    if missing and has_data and os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        # backup sebelum perubahan struktur (tidak dilakukan untuk DB yang baru dibuat)
        has_rows = conn.execute("SELECT (SELECT COUNT(*) FROM users)+(SELECT COUNT(*) FROM orders)+(SELECT COUNT(*) FROM products)").fetchone()[0]
        if has_rows:
            dst = os.path.join(DB_DIR, "backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
            conn.commit()
            shutil.copy2(DB_PATH, dst)
    for tbl, col, ddl in missing:
        conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
    conn.executescript(SCHEMA_EXTRA)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_ref ON payments(payment_reference) WHERE payment_reference IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_orders_user ON orders(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_payments_order ON payments(order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs(created_at)")


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    migrate(conn)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass

    # Admin bawaan (ganti passwordnya setelah login pertama!)
    if conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO admins (nama, username, password) VALUES (?,?,?)",
            ("Administrator", "admin", generate_password_hash("admin123")),
        )

    # Bersihkan produk demo lama (yang belum pernah dipesan)
    marks = ",".join("?" * len(OLD_DEMO))
    conn.execute(
        f"DELETE FROM products WHERE nama IN ({marks}) AND id NOT IN (SELECT product_id FROM order_items)",
        OLD_DEMO,
    )

    # Produk Rollicios
    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO products (nama, deskripsi, harga, stok) VALUES (?,?,?,?)",
            SEED_PRODUCTS,
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database siap:", DB_PATH)
