"""Perhitungan analitik untuk panel admin (murni Python, tanpa library tambahan).

Algoritma yang dipakai:
  1. RFM scoring + segmentasi pelanggan      (Recency, Frequency, Monetary, peringkat persentil 1-5)
  2. Skor Pengaruh sosmed                    (0.6 x log-skala pengikut + 0.4 x belanja ternormalisasi)
  3. Peramalan penjualan                     (regresi linear kuadrat terkecil + rata-rata bergerak 7 hari)
  4. Titik pesan ulang stok                  (ROP = laju jual x lead time + z x simpangan baku x akar lead time)
  5. Analisis ABC / Pareto produk            (kumulatif omzet 80% / 95%)
  6. Analisis keranjang belanja              (support, confidence, lift untuk pasangan produk)
  7. KPI: AOV, repeat rate, tingkat sukses pembayaran, rata-rata waktu bayar
"""
import math
from datetime import datetime, timedelta
from itertools import combinations

PAID = ("lunas", "diproses", "dikirim", "selesai")
PAID_SQL = "('lunas','diproses','dikirim','selesai')"


# ---------------------------------------------------------------- util statistik
def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def linreg(ys):
    """Kuadrat terkecil y = a + b*x untuk x = 0..n-1. Return (a, b, r2)."""
    n = len(ys)
    if n < 2:
        return (ys[0] if ys else 0.0), 0.0, 0.0
    xs = list(range(n))
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return a, b, max(0.0, r2)


def percentile_scores(values, higher_is_better=True):
    """Beri skor 1..5 berdasarkan peringkat persentil (adil walau data sedikit)."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i], reverse=higher_is_better)   # urutan terbaik dulu
    scores = [0] * n
    i = 0
    while i < n:                       # nilai kembar mendapat skor yang sama
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        pos = (i + j) / 2 + 1          # posisi tengah kelompok (1..n)
        s = max(1, min(5, math.ceil(5 * (n - pos + 1) / n))) if n else 1
        for k in range(i, j + 1):
            scores[order[k]] = s
        i = j + 1
    return scores


# ------------------------------------------------------------ 1 & 2. pelanggan
def customer_scores(conn, today=None):
    today = today or datetime.now()
    rows = conn.execute(f"""
        SELECT u.id, u.nama, u.no_telepon, u.instagram, u.tiktok, COALESCE(u.pengikut,0) AS pengikut,
               COUNT(o.id) AS freq, COALESCE(SUM(o.total),0) AS monetary, MAX(o.created_at) AS last_order
        FROM users u LEFT JOIN orders o ON o.user_id=u.id AND o.status_order IN {PAID_SQL}
        GROUP BY u.id ORDER BY u.id""").fetchall()
    buyers = [r for r in rows if r["freq"] > 0]

    def days_since(s):
        try:
            return max(0, (today - datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")).days)
        except (TypeError, ValueError):
            return 9999

    rec = [days_since(r["last_order"]) for r in buyers]
    r_sc = percentile_scores(rec, higher_is_better=False)          # makin baru makin tinggi
    f_sc = percentile_scores([r["freq"] for r in buyers])
    m_sc = percentile_scores([r["monetary"] for r in buyers])
    rfm = {r["id"]: (rec[i], r_sc[i], f_sc[i], m_sc[i]) for i, r in enumerate(buyers)}

    max_f = max([r["pengikut"] for r in rows] + [0])
    max_m = max([r["monetary"] for r in rows] + [0])
    out = []
    for r in rows:
        nf = math.log10(1 + r["pengikut"]) / math.log10(1 + max_f) if max_f > 0 else 0.0
        nm = r["monetary"] / max_m if max_m > 0 else 0.0
        pengaruh = round(100 * (0.6 * nf + 0.4 * nm))
        if r["id"] in rfm:
            d, R, F, M = rfm[r["id"]]
            if R >= 4 and F >= 4:
                seg = "Champion"
            elif R >= 3 and F >= 3:
                seg = "Loyal"
            elif R >= 4 and F <= 2:
                seg = "Pelanggan baru"
            elif R <= 2 and F >= 3:
                seg = "Berisiko pergi"
            elif R <= 2:
                seg = "Tidur"
            else:
                seg = "Menengah"
        else:
            d, R, F, M, seg = None, 0, 0, 0, "Belum beli"
        label = "Calon brand ambassador" if pengaruh >= 70 else ("Potensial" if pengaruh >= 40 else "Biasa")
        out.append({**dict(r), "recency": d, "R": R, "F": F, "M": M, "segmen": seg, "pengaruh": pengaruh, "label_pengaruh": label})
    out.sort(key=lambda x: (x["R"] + x["F"] + x["M"], x["monetary"]), reverse=True)
    return out


def segment_summary(customers):
    seg = {}
    for c in customers:
        s = seg.setdefault(c["segmen"], {"n": 0, "omzet": 0})
        s["n"] += 1
        s["omzet"] += c["monetary"]
    return sorted(seg.items(), key=lambda kv: -kv[1]["n"])


# ---------------------------------------------------------- 3. peramalan penjualan
def daily_revenue(conn, days=28, today=None):
    today = (today or datetime.now()).date()
    start = today - timedelta(days=days - 1)
    got = {r["d"]: r["s"] for r in conn.execute(
        "SELECT date(paid_at) d, SUM(jumlah) s FROM payments WHERE status IN ('lunas','refund') AND paid_at IS NOT NULL "
        "AND date(paid_at) >= ? GROUP BY date(paid_at)", (start.isoformat(),))}
    return [((start + timedelta(days=i)).isoformat(), int(got.get((start + timedelta(days=i)).isoformat(), 0))) for i in range(days)]


def forecast(conn, days=28, horizon=7, today=None):
    series = daily_revenue(conn, days, today)
    ys = [v for _, v in series]
    a, b, r2 = linreg(ys)
    last = datetime.strptime(series[-1][0], "%Y-%m-%d")
    pred = [(
        (last + timedelta(days=i + 1)).strftime("%Y-%m-%d"),
        int(max(0, round(a + b * (len(ys) + i)))),
    ) for i in range(horizon)]
    ma7 = round(mean(ys[-7:])) if ys else 0
    m = mean(ys)
    rel = (b * len(ys)) / m if m else 0
    trend = "naik" if rel > 0.15 else ("turun" if rel < -0.15 else "stabil")
    return {"series": series, "pred": pred, "slope": b, "intercept": a, "r2": r2, "ma7": ma7,
            "total_pred": sum(v for _, v in pred), "trend": trend, "ada_data": sum(ys) > 0}


# ------------------------------------------------------------ 4. stok & restock
def restock_plan(conn, window=14, lead_time=3, z=1.65, today=None):
    today = (today or datetime.now()).date()
    start = today - timedelta(days=window - 1)
    sold = {}
    for r in conn.execute(f"""
        SELECT oi.product_id pid, date(p.paid_at) d, SUM(oi.jumlah) q FROM order_items oi
        JOIN orders o ON o.id=oi.order_id JOIN payments p ON p.order_id=o.id
        WHERE o.status_order IN {PAID_SQL} AND p.status='lunas' AND date(p.paid_at) >= ?
        GROUP BY oi.product_id, date(p.paid_at)""", (start.isoformat(),)):
        sold.setdefault(r["pid"], {})[r["d"]] = r["q"]
    plan = []
    for pr in conn.execute("SELECT id, nama, stok FROM products ORDER BY nama"):
        daily = [sold.get(pr["id"], {}).get((start + timedelta(days=i)).isoformat(), 0) for i in range(window)]
        v, sd = mean(daily), stdev(daily)
        rop = math.ceil(v * lead_time + z * sd * math.sqrt(lead_time))
        days_left = round(pr["stok"] / v, 1) if v > 0 else None
        target = math.ceil(v * (lead_time + 7) + z * sd * math.sqrt(lead_time + 7))   # cukup untuk lead time + 1 minggu
        saran = max(0, target - pr["stok"])
        if pr["stok"] <= 0:
            st = "Habis"
        elif v > 0 and pr["stok"] <= rop:
            st = "Restock sekarang"
        elif days_left is not None and days_left <= lead_time + 4:
            st = "Segera"
        else:
            st = "Aman"
        plan.append({"id": pr["id"], "nama": pr["nama"], "stok": pr["stok"], "laju": round(v, 2), "sd": round(sd, 2),
                     "rop": rop, "hari_habis": days_left, "saran": saran if v > 0 else 0, "status": st})
    return plan


# -------------------------------------------------------------- 5. ABC / Pareto
def abc_analysis(conn):
    rows = conn.execute(f"""
        SELECT p.nama, SUM(oi.subtotal) omzet, SUM(oi.jumlah) qty FROM order_items oi
        JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id
        WHERE o.status_order IN {PAID_SQL} GROUP BY p.id ORDER BY omzet DESC""").fetchall()
    total = sum(r["omzet"] for r in rows)
    out, cum = [], 0.0
    for r in rows:
        before = cum / total if total else 0
        cum += r["omzet"]
        kelas = "A" if before < 0.80 else ("B" if before < 0.95 else "C")
        out.append({"nama": r["nama"], "omzet": r["omzet"], "qty": r["qty"],
                    "share": round(100 * r["omzet"] / total, 1) if total else 0,
                    "kum": round(100 * cum / total, 1) if total else 0, "kelas": kelas})
    return out


# ------------------------------------------------------- 6. analisis keranjang
def basket_pairs(conn, min_orders=1):
    orders = {}
    for r in conn.execute(f"""SELECT oi.order_id oid, p.nama FROM order_items oi JOIN orders o ON o.id=oi.order_id
                              JOIN products p ON p.id=oi.product_id WHERE o.status_order IN {PAID_SQL}"""):
        orders.setdefault(r["oid"], set()).add(r["nama"])
    n = len(orders)
    single, pair = {}, {}
    for items in orders.values():
        for it in items:
            single[it] = single.get(it, 0) + 1
        for a, b in combinations(sorted(items), 2):
            pair[(a, b)] = pair.get((a, b), 0) + 1
    out = []
    for (a, b), c in pair.items():
        if c < min_orders:
            continue
        sup = c / n
        out.append({"a": a, "b": b, "n": c, "support": round(100 * sup, 1),
                    "conf_ab": round(100 * c / single[a], 1), "conf_ba": round(100 * c / single[b], 1),
                    "lift": round(sup / ((single[a] / n) * (single[b] / n)), 2)})
    return sorted(out, key=lambda x: (-x["lift"], -x["n"])), n


# ------------------------------------------------------------------- 7. KPI
def kpis(conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]
    omzet = one("SELECT COALESCE(SUM(jumlah),0) FROM payments WHERE status IN ('lunas')")
    trx = one("SELECT COUNT(*) FROM payments WHERE status='lunas'")
    buyers = one(f"SELECT COUNT(DISTINCT user_id) FROM orders WHERE status_order IN {PAID_SQL}")
    repeat = one(f"SELECT COUNT(*) FROM (SELECT user_id FROM orders WHERE status_order IN {PAID_SQL} GROUP BY user_id HAVING COUNT(*)>=2)")
    ok = one("SELECT COUNT(*) FROM payments WHERE status IN ('lunas','refund')")
    bad = one("SELECT COUNT(*) FROM payments WHERE status IN ('gagal','kedaluwarsa','ditolak','dibatalkan')")
    waits = []
    for r in conn.execute("SELECT o.created_at c, p.paid_at t FROM payments p JOIN orders o ON o.id=p.order_id WHERE p.status='lunas' AND p.paid_at IS NOT NULL"):
        try:
            d = (datetime.strptime(r["t"][:19], "%Y-%m-%d %H:%M:%S") - datetime.strptime(r["c"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            waits.append(max(0, d))
        except (TypeError, ValueError):
            pass
    return {"aov": round(omzet / trx) if trx else 0, "repeat_rate": round(100 * repeat / buyers, 1) if buyers else 0,
            "sukses_bayar": round(100 * ok / (ok + bad), 1) if (ok + bad) else 0,
            "rata_menit_bayar": round(mean(waits), 1) if waits else None, "pembeli": buyers, "transaksi": trx}
