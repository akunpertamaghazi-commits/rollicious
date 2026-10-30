/* Animasi latar halaman anggota — satu gaya berbeda untuk tiap tema */
(function () {
  'use strict';
  document.documentElement.classList.add('t-js');
  const fx = document.querySelector('.t-fx'); if (!fx) return;
  const type = fx.dataset.fx, reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const rnd = (a, b) => a + Math.random() * (b - a);
  const add = (cls, style) => { const el = document.createElement('i'); el.className = cls; el.style.cssText = style || ''; fx.appendChild(el); return el; };

  if (!reduce) {
    if (type === 'mayo') {                                   // gelembung krim
      for (let i = 0; i < 26; i++) add('bub', `--s:${rnd(14, 92)}px;--dx:${rnd(-70, 70)}px;left:${rnd(0, 100)}%;animation-duration:${rnd(10, 24)}s;animation-delay:${-rnd(0, 24)}s`);
    }
    if (type === 'keju') {                                   // lelehan keju
      add('melt-edge');
      for (let i = 0; i < 14; i++) add('drip', `--w:${rnd(12, 26)}px;--d:${rnd(6, 11)}s;left:${(i + rnd(.1, .9)) * (100 / 14)}%;animation-delay:${-rnd(0, 10)}s`);
      for (let i = 0; i < 9; i++) add('hole', `--s:${rnd(40, 130)}px;left:${rnd(0, 95)}%;top:${rnd(12, 90)}%;animation-delay:${-rnd(0, 6)}s`);
    }
    if (type === 'coklat') {                                 // ombak coklat + kilau emas
      add('wave w1'); add('wave w2'); add('wave w3');
      for (let i = 0; i < 30; i++) add('spark', `--s:${rnd(8, 22)}px;--d:${rnd(2.5, 6)}s;left:${rnd(0, 98)}%;top:${rnd(0, 70)}%;animation-delay:${-rnd(0, 6)}s`);
    }
    if (type === 'matcha') {                                 // daun berguguran
      for (let i = 0; i < 22; i++) add('leaf', `--s:${rnd(16, 38)}px;--d:${rnd(12, 26)}s;--x0:${rnd(-5, 95)}vw;--r0:${rnd(0, 360)}deg;animation-delay:${-rnd(0, 26)}s`);
    }
  } else if (type === 'coklat') { add('wave w2'); add('wave w3'); }

  if (type === 'galaksi') {                                  // bintang parallax + meteor
    add('planet'); add('neon-grid');
    const cv = document.createElement('canvas'); fx.prepend(cv);
    const ctx = cv.getContext('2d'); let W, H, stars = [], mx = 0, my = 0, shoot = null, vis = true;
    const resize = () => { W = cv.width = innerWidth; H = cv.height = innerHeight;
      stars = Array.from({ length: Math.round(W * H / 6500) }, () => ({ x: rnd(0, W), y: rnd(0, H), z: rnd(.2, 1), p: rnd(0, 6.28), c: Math.random() < .25 ? '#ff9ec7' : (Math.random() < .4 ? '#a9b4ff' : '#ffffff') })); };
    addEventListener('resize', resize); resize();
    addEventListener('pointermove', e => { mx = e.clientX / W - .5; my = e.clientY / H - .5; });
    document.addEventListener('visibilitychange', () => { vis = !document.hidden; });
    (function draw(t) {
      requestAnimationFrame(draw); if (!vis) return;
      ctx.clearRect(0, 0, W, H);
      for (const s of stars) {
        if (!reduce) { s.y += s.z * .25; if (s.y > H) { s.y = 0; s.x = rnd(0, W); } }
        const x = s.x - mx * 40 * s.z, y = s.y - my * 40 * s.z, a = .45 + .55 * Math.sin(t / 700 + s.p);
        ctx.globalAlpha = reduce ? .8 : a; ctx.fillStyle = s.c; ctx.beginPath(); ctx.arc(x, y, s.z * 1.7, 0, 6.283); ctx.fill();
      }
      if (!reduce) {
        if (!shoot && Math.random() < .006) shoot = { x: rnd(W * .2, W), y: rnd(0, H * .4), l: 0 };
        if (shoot) { ctx.globalAlpha = 1; const g = ctx.createLinearGradient(shoot.x, shoot.y, shoot.x - shoot.l, shoot.y - shoot.l * .5);
          g.addColorStop(0, '#fff'); g.addColorStop(1, 'rgba(255,255,255,0)'); ctx.strokeStyle = g; ctx.lineWidth = 2; ctx.beginPath();
          ctx.moveTo(shoot.x, shoot.y); ctx.lineTo(shoot.x - shoot.l, shoot.y - shoot.l * .5); ctx.stroke();
          shoot.x -= 14; shoot.y += 7; shoot.l = Math.min(shoot.l + 10, 140); if (shoot.x < -150 || shoot.y > H) shoot = null; }
      }
    })(0);
  }

  // munculkan panel & isi bar keahlian saat terlihat
  const rv = document.querySelectorAll('.rv');
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { threshold: .18 });
    rv.forEach(el => io.observe(el));
  } else rv.forEach(el => el.classList.add('in'));
})();
