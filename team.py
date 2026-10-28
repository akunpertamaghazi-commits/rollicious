"""Data anggota kelompok (5 orang). EDIT FILE INI untuk mengisi nama & biodata asli.

Cara mengisi:
  * nama, nim, prodi, kampus, kelas, kota, tentang, kutipan, tugas, keahlian, hobi, instagram, email
  * slug   : bagian alamat halaman, mis. "budi" -> /tim/budi  (huruf kecil, tanpa spasi)
  * tema   : mayo | keju | coklat | matcha | galaksi  (menentukan warna latar + animasi halaman)
  * foto   : simpan gambar di static/images/team/<slug>.jpg (atau .png/.webp). Jika tidak ada, tampil inisial.
  * keahlian: daftar (nama, nilai 0-100)
Simpan file lalu jalankan ulang aplikasi.
"""
import os

TEAM = [
    dict(slug="fahri dwi hariyanto", tema="mayo", nama="Fahri Dwi Hariyanto", peran="Ketua Kelompok & Koordinator",
         nim="00000000001", prodi="sistem informasi", kampus="universita bina sarana informatika", kelas="19-1B-26", kota="Jakarta, slipi",
         tentang="Tulis perkenalan singkat bae.",
         tugas=["Mengoordinasi jadwal dan pembagian tugas kelompok", "Menyusun laporan akhir dan presentasi"],
         keahlian=[("Kepemimpinan", 90), ("Komunikasi", 85), ("Manajemen waktu", 80)],
         hobi=["Membaca", "Kuliner", "Traveling"], kutipan="Kerja sama membuat yang sulit terasa ringan.",
         instagram="", email=""),
    dict(slug="keyla ainurista", tema="keju", nama="Keyla Ainurista", peran="produksi",
         nim="00000000002", prodi="sistem informasi", kampus="universita bina sarana informatika", kelas="19-1B-26", kota="Jakarta, slipi",
         tentang="perkenalan singkat ",
         tugas=["singkat"],
         keahlian=[("-", 88), ("-", 82), ("-", 78)],
         hobi=["TIDUR, MAKAN, NONTON KPOP"], kutipan="-",
         instagram="", email=""),
    dict(slug="m.faizal afandi", tema="coklat", nama="Muhammad Faizal Afandi", peran="marketing & promosi",
         nim="00000000003", prodi="sistem informasi", kampus="universita bina sarana informatika", kelas="19-1B-26", kota="Jakarta, slipi",
         tentang="Tulis perkenalan singkat tentang ",
         tugas=["-"],
         keahlian=[("-", 92), ("=", 84), ("-", 88)],
         hobi=["-"], kutipan="-",
         instagram="", email=""),
    dict(slug="rayhan saputra", tema="matcha", nama="Rayhan Saputra", peran="Keuangan & Administrasi",
         nim="00000000004", prodi="sistem informasi", kampus="universita bina sarana informatika", kelas="19-1B-26", kota="Jakarta, slipi",
         tentang="Tulis perkenalan singkat.",
         tugas=["Menentukan harga jual dan menghitung modal", "Mencatat kas masuk dan keluar", "Menyusun laporan keuangan"],
         keahlian=[("Akuntansi dasar", 86), ("Excel", 90), ("Ketelitian", 94)],
         hobi=["-"], kutipan="Angka yang jujur membuat usaha bertahan.",
         instagram="", email=""),
    dict(slug="Ahmad Ghazi Al-ghifary", tema="galaksi", nama="Ahmad Ghazi Al-ghifary", peran="Developer & oprasional",
         nim="00000000005", prodi="sistem informasi", kampus="universita bina sarana informatika", kelas="19-1B-26", kota="Jakarta, slipi",
         tentang="perkenalkan saya ahmad ghazi al-ghifary, saya adalah seorang mahasiswa sistem informasi di universitas bina sarana informatika. saya meiliki minat bakat dalam sebuah pengembangan teknologi",
         tugas=["Membuat konten dan jadwal posting", "Mengelola akun sosial media toko", "Merancang promo dan kemasan"],
         keahlian=[("HTML & CSS", 100), ("Python & Flask", 100), ("Basis data", 100)],
         hobi=["futsal, game, tidur"], kutipan="kalau berak sekebon cebok nya selaut",
         instagram="ahmdghzee", email="ghazialghifary12@Gmail.com"),
]

_EXT = ("jpg", "jpeg", "png", "webp")


def photo_for(slug, static_dir):
    """Nama file foto di static/images/team/ jika ada, selain itu None."""
    for ext in _EXT:
        name = f"{slug}.{ext}"
        if os.path.exists(os.path.join(static_dir, "images", "team", name)):
            return name
    return None
