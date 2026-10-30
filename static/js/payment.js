/* Halaman pembayaran: QR contoh (jika qris.png belum ada) + polling status */
(function () {
  const panel = document.querySelector('.qris-panel'); if (!panel) return;
  const ref = panel.dataset.ref, status = panel.dataset.status, oid = panel.dataset.oid;

  // QR contoh deterministik dari kode referensi (BUKAN QRIS asli)
  const cv = document.getElementById('qrCanvas');
  if (cv) {
    const N = 29, px = cv.width / N, ctx = cv.getContext('2d');
    let h = 2166136261; for (const c of ref) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
    const rnd = () => { h ^= h << 13; h ^= h >>> 17; h ^= h << 5; return ((h >>> 0) % 1000) / 1000; };
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height); ctx.fillStyle = '#10131c';
    const finder = (x, y) => { ctx.fillRect(x * px, y * px, 7 * px, 7 * px); ctx.fillStyle = '#fff'; ctx.fillRect((x + 1) * px, (y + 1) * px, 5 * px, 5 * px); ctx.fillStyle = '#10131c'; ctx.fillRect((x + 2) * px, (y + 2) * px, 3 * px, 3 * px); };
    const inFinder = (x, y) => (x < 8 && y < 8) || (x > N - 9 && y < 8) || (x < 8 && y > N - 9);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (!inFinder(x, y) && rnd() > 0.52) ctx.fillRect(x * px, y * px, px, px);
    finder(0, 0); finder(N - 7, 0); finder(0, N - 7);
  }

  // Pembayaran otomatis: cek status tiap 3 detik. Hasil akhir (berhasil/gagal) ditampilkan begitu terdeteksi.
  const gateway = panel.dataset.gateway === '1';
  const wt = document.getElementById('waitTitle'), wx = document.getElementById('waitText'), box = document.getElementById('waitBox');
  if (status === 'menunggu_verifikasi' || (gateway && status === 'belum_bayar')) {
    let fails = 0;
    const poll = async () => {
      try {
        const r = await fetch(`/api/pesanan/${oid}/status`, { cache: 'no-store' });
        if (!r.ok) throw new Error('http');
        const d = await r.json(); fails = 0;
        if (d.payment !== status) {
          if (box) { box.classList.add(d.payment === 'lunas' ? 'ok' : 'bad'); }
          if (wt) wt.textContent = d.payment === 'lunas' ? 'Pembayaran berhasil!' : 'Pembayaran gagal';
          if (wx) wx.textContent = 'Memuat hasil…';
          setTimeout(() => location.reload(), 700);
          return;
        }
        if (wx && box && box.dataset.offline) { delete box.dataset.offline; wx.textContent = 'Jangan tutup halaman ini. Status berubah otomatis begitu pembayaranmu masuk.'; }
      } catch (e) {
        if (++fails >= 2 && wx && box) { box.dataset.offline = '1'; wx.textContent = 'Koneksi terputus, mencoba lagi…'; }
      }
      setTimeout(poll, 3000);
    };
    setTimeout(poll, 2000);
  }

  // hitung mundur masa berlaku QRIS (hanya tampilan; status resmi tetap dari gateway)
  const cd = document.getElementById('countdown'), exp = panel.dataset.expires;
  if (cd && exp) {
    const end = new Date(exp.replace(' ', 'T')).getTime();
    const tick = () => {
      const left = Math.floor((end - Date.now()) / 1000);
      if (left <= 0) { cd.textContent = 'Waktu habis, memeriksa status…'; cd.classList.add('low'); return; }
      cd.textContent = 'Sisa waktu ' + Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0');
      cd.classList.toggle('low', left < 120);
      setTimeout(tick, 1000);
    };
    tick();
  }
})();
