# Rollicios — Toko Kelompok

Website penjualan risol lumer (Flask + SQLite) dengan sisi **konsumen** dan **admin**.
Alur: Daftar/Masuk → Produk → Keranjang → Checkout → QRIS → "Saya sudah bayar" → Menunggu verifikasi → Admin tandai lunas → Transaksi tercatat di kas.

## Menjalankan
```bash
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Buka http://127.0.0.1:5000 — database `database/toko.db` dibuat otomatis beserta 8 produk contoh.

| Peran | Alamat | Akun |
|---|---|---|
| Konsumen | `/register` lalu `/login` | daftar sendiri (no. telepon + password) |
| Admin | `/admin/login` | `admin` / `admin123` (**ganti** sebelum presentasi) |

## Produk & harga
Ada 4 produk awal: **Risol Lumer Mayo, Keju, Coklat, Matcha**. Harga sengaja dikosongkan (0) dan tampil sebagai "Harga menyusul"; produk tanpa harga belum bisa dimasukkan ke keranjang. Isi harga lewat **Admin → Produk & stok → Edit**. Warna dan ilustrasi risol otomatis mengikuti kata *mayo / keju / coklat / matcha* pada nama produk.

## Fitur
- **Konsumen:** hero 3D risol yang terbelah dan isinya mengalir (Three.js), rak produk yang bergeser ke samping saat scroll, mode geser/grid, cari & urutkan, kartu tilt 3D, ilustrasi risol yang membelah saat di-hover, keranjang AJAX, checkout, pembayaran QRIS dengan status otomatis, riwayat pesanan dengan timeline, mode terang/gelap.
- **Admin:** dashboard + grafik 7 hari, CRUD produk + upload foto, atur stok, pesanan (proses → kirim → selesai), verifikasi/tolak pembayaran, daftar pelanggan, arus kas (otomatis dari pembayaran + catatan manual), laporan bulanan yang bisa dicetak/PDF.

## Menambah QRIS asli
Simpan gambar QRIS toko sebagai `static/images/qris/qris.png`. Selama belum ada, halaman bayar menampilkan QR contoh.

## Struktur
```
app.py            semua route (konsumen & admin)
database.py       skema tabel + data awal
templates/        auth/ customer/ admin/ + base.html, _macros.html
static/           css/ js/ images/
```
Catatan: `admin/layout.html` (kerangka sidebar admin) dan `auth/404.html` ditambahkan di luar struktur awal.

## Mengisi biodata tim (5 anggota)
Footer memuat kolom **Admin** dan **Tim kami**. Klik nama untuk membuka halaman biodata masing-masing (`/tim/<slug>`), tiap anggota punya warna latar dan animasi berbeda (Mayo = gelembung krim, Keju = lelehan keju, Coklat = ombak coklat, Matcha = daun berguguran, Galaksi = bintang & grid neon).
Edit **`team.py`**: nama, NIM, prodi, kampus, peran, tentang, keahlian, hobi, kutipan, instagram, email. Foto: simpan sebagai `static/images/team/<slug>.jpg`. Data di file masih contoh ("Nama Anggota 1" dst).

## QRIS dinamis otomatis (Midtrans) — opsional
Secara bawaan pembayaran memakai QRIS manual (pelanggan menekan "Saya sudah bayar", admin memverifikasi). Untuk QRIS otomatis:
1. Daftar akun **Midtrans Sandbox**, ambil *Server Key* (Settings > Access Keys).
2. Set environment variable (lihat `.env.example`): `MIDTRANS_SERVER_KEY=SB-Mid-server-...`
3. Di Dashboard Midtrans > Settings > Payment > **Notification URL**, isi `https://DOMAIN-KAMU/webhook/midtrans` (untuk uji lokal pakai ngrok).
4. Jalankan ulang aplikasi. Checkout kini membuat QRIS dengan nominal sudah terisi otomatis (pelanggan tidak mengetik nominal, tidak ada konfirmasi ke admin). Halaman bayar menampilkan animasi loading selama menunggu, lalu tulisan **Pembayaran berhasil** atau **Pembayaran gagal** secara otomatis; status lunas hanya berubah setelah Midtrans mengonfirmasi (webhook bertanda tangan SHA-512 + cek nominal), bukan dari tombol pelanggan.

Penanganan: webhook ganda (idempoten), event tidak berurutan, nominal berbeda, tanda tangan palsu, pembayaran terlambat setelah kedaluwarsa (admin bisa *Pulihkan pesanan* atau *Refund*), rekonsiliasi otomatis saat halaman bayar dibuka + tombol *Rekonsiliasi* di admin. Refund via API perlu diaktifkan oleh tim Midtrans untuk akunmu. Gunakan sandbox untuk uji; jangan memakai kunci production sebelum disetujui.

## Fitur admin tambahan
Pengiriman & nomor resi (pelanggan melihat resi di Pesanan), refund, audit log aktivitas, ganti password admin, input sosmed pelanggan, dan halaman **Analitik** (RFM, skor pengaruh sosmed, peramalan penjualan regresi linear, titik pesan ulang stok, analisis ABC, keranjang belanja, KPI) lengkap dengan penjelasan rumus.

## Keamanan
CSRF pada semua form/API, rate limit login/daftar/admin/keranjang/webhook, header keamanan, cookie HttpOnly+SameSite, audit log, password ter-hash. Set `SECRET_KEY` dan `COOKIE_SECURE=1` (HTTPS) saat deploy. Database lama otomatis di-backup sebelum migrasi kolom baru.

## Menjalankan tes
```bash
python -m unittest discover -s tests -v
```
46 tes mencakup alur manual & gateway (dengan Midtrans tiruan), keamanan, refund, pengiriman, analitik, dan halaman tim.

## Catatan lama
Password di-hash (Werkzeug). Set `SECRET_KEY` lewat environment variable saat deploy. Belum ada proteksi CSRF; tambahkan Flask-WTF jika dosen memintanya.
