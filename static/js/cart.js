/* Interaksi umum: tema, toast, keranjang, tilt 3D, rail geser, scroll horizontal */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- toast ---------- */
  function toast(msg, type) {
    const box = $('#toasts'); if (!box) return;
    const t = document.createElement('div');
    t.className = 'toast ' + (type || 'info'); t.textContent = msg; t.setAttribute('role', 'status');
    box.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, 3600);
  }
  window.toast = toast;
  try { JSON.parse($('#toasts').dataset.flash || '[]').forEach(([cat, msg]) => toast(msg, cat)); } catch (e) {}

  /* ---------- tema ---------- */
  $$('#themeBtn').forEach(b => b.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch (e) {}
    window.dispatchEvent(new Event('themechange'));
  }));

  /* ---------- tampil/sembunyi password ---------- */
  $$('.pw-toggle').forEach(b => b.addEventListener('click', () => {
    const i = b.previousElementSibling; const show = i.type === 'password';
    i.type = show ? 'text' : 'password'; b.textContent = show ? 'sembunyi' : 'tampil';
  }));

  /* ---------- keranjang ---------- */
  function setBadge(n) {
    const b = $('#cartBadge'); if (!b) return;
    b.textContent = n; b.hidden = !n; b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop');
  }
  async function post(url, data) {
    const tok = (document.querySelector('meta[name=csrf-token]') || {}).content || '';
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': tok }, body: JSON.stringify(data) });
    return { ok: r.ok, data: await r.json().catch(() => ({})) };
  }
  document.addEventListener('click', async e => {
    const btn = e.target.closest('.add-cart'); if (!btn || btn.disabled) return;
    const src = btn.dataset.qtyFrom ? $(btn.dataset.qtyFrom) : null;
    const qty = src ? Math.max(1, parseInt(src.value, 10) || 1) : 1;
    btn.disabled = true;
    const res = await post('/api/cart/add', { id: btn.dataset.id, qty });
    btn.disabled = false;
    if (res.data.count !== undefined) setBadge(res.data.count);
    toast(res.data.msg || 'Gagal menambahkan', res.ok ? 'success' : 'error');
    if (res.ok && btn.dataset.go) location.href = btn.dataset.go;
  });

  // stepper jumlah di halaman detail
  $$('.qty [data-q]').forEach(b => b.addEventListener('click', () => {
    const wrap = b.closest('.qty'), i = $('input', wrap), max = +wrap.dataset.max || 99;
    i.value = Math.min(max, Math.max(1, (+i.value || 1) + +b.dataset.q));
  }));

  // halaman keranjang
  const list = $('#cartList');
  if (list) {
    async function update(row, qty) {
      const res = await post('/api/cart/update', { id: row.dataset.id, qty });
      if (!res.ok) return;
      setBadge(res.data.count);
      if (qty <= 0) { row.remove(); if (!$('.cart-row')) return location.reload(); }
      else { $('input', row).value = res.data.qty; $('.line-total', row).textContent = res.data.subtotal; }
      $('#cartTotal').textContent = res.data.total;
    }
    list.addEventListener('click', e => {
      const row = e.target.closest('.cart-row'); if (!row) return;
      const q = e.target.closest('[data-cq]');
      if (q) update(row, Math.max(1, (+$('input', row).value || 1) + +q.dataset.cq));
      if (e.target.closest('.rm')) update(row, 0);
    });
    list.addEventListener('change', e => { if (e.target.matches('input')) update(e.target.closest('.cart-row'), +e.target.value || 1); });
  }

  /* ---------- tilt 3D ---------- */
  if (!reduce && matchMedia('(hover: hover)').matches) {
    $$('[data-tilt]').forEach(el => {
      el.addEventListener('mousemove', e => {
        const r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
        el.style.transition = 'transform .08s';
        el.style.transform = `perspective(900px) rotateY(${x * 12}deg) rotateX(${-y * 12}deg) translateY(-4px)`;
      });
      el.addEventListener('mouseleave', () => { el.style.transition = 'transform .5s cubic-bezier(.2,.8,.2,1)'; el.style.transform = ''; });
    });
    const stage = $('#stage .art');
    if (stage) {
      const host = $('#stage');
      host.addEventListener('mousemove', e => {
        const r = host.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
        stage.style.transform = `rotateY(${x * 22}deg) rotateX(${-y * 22}deg)`;
      });
      host.addEventListener('mouseleave', () => { stage.style.transform = ''; });
    }
  }

  /* ---------- rail geser (drag, panah, tombol grid) ---------- */
  $$('.rail').forEach(rail => {
    let down = false, sx = 0, sl = 0, moved = 0;
    rail.addEventListener('pointerdown', e => { if (e.pointerType !== 'mouse' || rail.classList.contains('as-grid')) return; down = true; moved = 0; sx = e.clientX; sl = rail.scrollLeft; });
    window.addEventListener('pointermove', e => { if (!down) return; const dx = e.clientX - sx; moved = Math.max(moved, Math.abs(dx)); if (moved > 6) rail.classList.add('dragging'); rail.scrollLeft = sl - dx; });
    window.addEventListener('pointerup', () => { down = false; setTimeout(() => rail.classList.remove('dragging'), 0); });
  });
  const mainRail = $('#rail');
  if (mainRail) {
    $$('#railArrows [data-dir]').forEach(b => b.addEventListener('click', () => mainRail.scrollBy({ left: +b.dataset.dir * 300, behavior: 'smooth' })));
    $$('.seg [data-view]').forEach(b => b.addEventListener('click', () => {
      $$('.seg [data-view]').forEach(x => x.classList.toggle('on', x === b));
      const grid = b.dataset.view === 'grid';
      mainRail.classList.toggle('as-grid', grid); $('#railArrows').classList.toggle('hide', grid);
    }));
  }

  /* ---------- scroll vertikal → geser horizontal (beranda) ---------- */
  const sec = $('#hscroll') || $('.hscroll');
  if (sec) {
    const track = $('.hs-track', sec), bar = $('#hsBar');
    const on = matchMedia('(min-width: 900px)').matches && !reduce;
    if (!on) { sec.classList.add('native'); }
    else {
      let dist = 0;
      const cards = $$('.pcard:not(.see-all)', track);
      const tints = cards.map(c => getComputedStyle(c).getPropertyValue('--g').trim() || 'transparent');
      const measure = () => { dist = Math.max(0, track.scrollWidth - innerWidth + 40); sec.style.height = (dist + innerHeight) + 'px'; draw(); };
      const draw = () => {
        const span = sec.offsetHeight - innerHeight;
        const p = span > 0 ? Math.min(1, Math.max(0, -sec.getBoundingClientRect().top / span)) : 0;
        track.style.transform = `translate3d(${-p * dist}px,0,0)`;
        if (cards.length) sec.style.setProperty('--tint', tints[Math.min(cards.length - 1, Math.floor(p * cards.length))]); if (bar) bar.style.width = (p * 100) + '%';
      };
      addEventListener('scroll', () => requestAnimationFrame(draw), { passive: true });
      addEventListener('resize', measure); addEventListener('load', measure); measure();
    }
  }

  /* ---------- hitung naik angka statistik ---------- */
  if (!reduce && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(en => {
      if (!en.isIntersecting) return; io.unobserve(en.target);
      const to = +en.target.dataset.count || 0, t0 = performance.now();
      (function tick(t) { const k = Math.min(1, (t - t0) / 1200); en.target.textContent = Math.round(to * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(tick); })(t0);
    }), { threshold: .6 });
    $$('[data-count]').forEach(el => io.observe(el));
  }
})();
