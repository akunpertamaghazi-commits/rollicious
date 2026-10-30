(() => {
  const layer = document.getElementById('hobiLayer');
  if (!layer) return;
  const hobi = layer.dataset.hobi;
  const W = () => window.innerWidth;
  const H = () => window.innerHeight;
  const rnd = (a, b) => a + Math.random() * (b - a);

  /* ---------- Partikel: kpop, anime, coding ---------- */
  const PARTICLES = {
    // naik ke atas saat scroll turun (dir = -1), seperti konser
    kpop: {
      items: ['💜', '⭐', '🎤', '✨', '🪄', '💖'], count: 22, cls: 'kp', dir: -1, size: [22, 44],
    },
    // jatuh seperti kelopak sakura + teks jepang (dir = +1)
    anime: {
      items: ['🌸', '✨', 'アニメ', 'すごい!', 'ドドド', '⚡', '🌸'], count: 24, cls: 'jp', dir: 1, size: [18, 40],
    },
    // hujan kode ala Matrix
    coding: {
      items: ['{ }', '</>', 'const', '=>', '01', 'print()', 'git push', '404', 'if (x)', '<div>', 'npm run', ';'],
      count: 26, cls: 'code', dir: 1, size: [14, 20],
    },
  };

  let tick = null;

  if (PARTICLES[hobi]) {
    const cfg = PARTICLES[hobi];
    const nodes = [];
    for (let i = 0; i < cfg.count; i++) {
      const el = document.createElement('span');
      el.className = 'ptc ' + cfg.cls;
      el.textContent = cfg.items[i % cfg.items.length];
      el.style.fontSize = rnd(cfg.size[0], cfg.size[1]) + 'px';
      el.style.opacity = rnd(0.35, 0.9);
      layer.appendChild(el);
      nodes.push({
        el, x: rnd(0, 100), y0: rnd(0, 1000),
        speed: rnd(0.25, 0.9), spin: rnd(-0.15, 0.15), sway: rnd(10, 40),
      });
    }
    if (hobi === 'anime') {
      const sl = document.createElement('div');
      sl.className = 'speedlines';
      layer.appendChild(sl);
    }
    tick = (sy, vel) => {
      const h = H() + 120;
      nodes.forEach((n) => {
        // posisi vertikal bergerak sesuai scroll, berputar terus (loop)
        let y = (n.y0 + sy * n.speed * cfg.dir) % h;
        if (y < 0) y += h;
        const x = (n.x / 100) * W() + Math.sin(sy / 120 + n.y0) * n.sway;
        n.el.style.transform = `translate3d(${x}px, ${y - 60}px, 0) rotate(${sy * n.spin}deg)`;
      });
      if (hobi === 'anime') {
        layer.querySelector('.speedlines').style.opacity = Math.min(vel / 45, 0.55);
      }
    };
  }

  /* ---------- Gunung: pendaki naik ke puncak ---------- */
  if (hobi === 'gunung') {
    layer.innerHTML = `
      <span class="cloud" style="top:12%">☁️</span>
      <span class="cloud" style="top:26%">☁️</span>
      <svg class="mtn mtn-1" viewBox="0 0 1200 400" preserveAspectRatio="none"><polygon points="0,400 0,230 180,110 340,220 520,60 720,230 900,120 1060,230 1200,160 1200,400"/></svg>
      <svg class="mtn mtn-2" viewBox="0 0 1200 400" preserveAspectRatio="none"><polygon points="0,400 0,290 140,200 300,280 480,170 660,290 840,210 1020,300 1200,240 1200,400"/></svg>
      <svg class="mtn mtn-3" viewBox="0 0 1200 400" preserveAspectRatio="none"><polygon points="0,400 0,340 200,270 380,330 600,250 820,335 1000,280 1200,340 1200,400"/></svg>
      <span class="flag">🚩</span>
      <span class="climber">🧗</span>`;
    const m1 = layer.querySelector('.mtn-1');
    const m2 = layer.querySelector('.mtn-2');
    const m3 = layer.querySelector('.mtn-3');
    const clouds = layer.querySelectorAll('.cloud');
    const climber = layer.querySelector('.climber');
    const flag = layer.querySelector('.flag');
    tick = (sy, vel, p) => {
      // gunung naik pelan (parallax) saat scroll turun
      m1.style.transform = `translateY(${(1 - p) * 90}px)`;
      m2.style.transform = `translateY(${(1 - p) * 55}px)`;
      m3.style.transform = `translateY(${(1 - p) * 25}px)`;
      clouds.forEach((c, i) => {
        c.style.transform = `translateX(${(i ? W() - sy * 0.4 : sy * 0.6) % W()}px)`;
      });
      // pendaki bergerak dari kaki ke puncak
      const cx = W() * (0.08 + p * 0.5);
      const cy = H() * (0.88 - p * 0.45);
      climber.style.transform = `translate3d(${cx}px, ${cy}px, 0) rotate(${Math.sin(sy / 25) * 6}deg)`;
      flag.style.transform = `translate3d(${cx + 28}px, ${cy - 30}px, 0)`;
      flag.classList.toggle('on', p > 0.96);
    };
  }

  /* ---------- Loop scroll ---------- */
  if (!tick) return;
  let last = window.scrollY, ticking = false;
  const run = () => {
    const sy = window.scrollY;
    const max = Math.max(1, document.documentElement.scrollHeight - H());
    const p = Math.min(1, Math.max(0, sy / max));
    layer.style.setProperty('--p', p);
    tick(sy, Math.abs(sy - last), p);
    last = sy;
    ticking = false;
  };
  window.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; requestAnimationFrame(run); }
  }, { passive: true });
  window.addEventListener('resize', run);
  run();
})();
