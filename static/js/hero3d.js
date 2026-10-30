/* Hero 3D Rollicios (Three.js r128)
   Empat risol 3D — mayo, keju, coklat, matcha — yang terbelah di tengah, lalu isinya
   mengalir keluar dan menetes, berulang. Jika WebGL / Three.js gagal, tampil versi SVG. */
(function () {
  const host = document.getElementById('hero3d');
  if (!host) return;
  const fail = () => host.classList.add('fallback');
  if (!window.THREE) return fail();
  const T = THREE, reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  let renderer;
  try { renderer = new T.WebGLRenderer({ alpha: true, antialias: true }); } catch (e) { return fail(); }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  host.appendChild(renderer.domElement);

  try { build(); } catch (e) { console.error(e); renderer.domElement.remove(); fail(); }

  function build() {
    const R = 0.42, L = 1.1, GAP = 1.5;           // jari-jari, panjang tiap belahan, jarak belahan maksimum
    const FLAVORS = [
      { color: 0xfff0b0, glow: 0xffe27a, rough: 0.45 },   // mayo
      { color: 0xffbf2e, glow: 0xffa62b, rough: 0.35 },   // keju
      { color: 0x5b2f1a, glow: 0xc0703f, rough: 0.18 },   // coklat (mengkilap)
      { color: 0x9fd45a, glow: 0x9ccc3c, rough: 0.5 }     // matcha
    ];

    const scene = new T.Scene();
    const cam = new T.PerspectiveCamera(38, 1, 0.1, 100);

    /* ---- bahan ---- */
    const crustMat = new T.MeshStandardMaterial({ color: 0xdf9433, roughness: 0.85, metalness: 0, flatShading: true, side: T.DoubleSide });
    const doughMat = new T.MeshStandardMaterial({ color: 0xf3cd7c, roughness: 0.9, side: T.DoubleSide });

    /* ---- kulit renyah: getar acak berbasis posisi supaya tidak retak di sambungan ---- */
    function crisp(geo, amt) {
      const p = geo.attributes.position;
      for (let i = 0; i < p.count; i++) {
        const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
        const n = Math.sin(x * 91.7 + y * 47.3 + z * 63.1) * 43758.5453;
        const f = (n - Math.floor(n)) - 0.5, len = Math.hypot(x, z);
        if (len > 1e-4) { p.setX(i, x + (x / len) * f * amt); p.setZ(i, z + (z / len) * f * amt); }
      }
      geo.computeVertexNormals();
      return geo;
    }

    /* ---- satu belahan risol (dir = +1 bagian atas, -1 bagian bawah) ---- */
    function makeHalf(dir, fillMat) {
      const g = new T.Group();
      const bodyGeo = new T.CylinderGeometry(R, R, L, 30, 8, true); bodyGeo.translate(0, dir * L / 2, 0);
      g.add(new T.Mesh(crisp(bodyGeo, 0.03), crustMat));

      const capGeo = new T.SphereGeometry(R, 30, 12, 0, Math.PI * 2, 0, Math.PI / 2);
      if (dir < 0) capGeo.rotateX(Math.PI);
      capGeo.translate(0, dir * L, 0);
      g.add(new T.Mesh(crisp(capGeo, 0.03), crustMat));

      const faceRot = dir > 0 ? Math.PI / 2 : -Math.PI / 2;   // bidang potong menghadap ke celah
      const ring = new T.Mesh(new T.RingGeometry(R * 0.8, R, 30), doughMat); ring.rotation.x = faceRot; g.add(ring);
      const disc = new T.Mesh(new T.CircleGeometry(R * 0.8, 30), fillMat); disc.rotation.x = faceRot; disc.position.y = dir * -0.002; g.add(disc);

      // gundukan isi yang menyembul dari bidang potong
      const dome = new T.Mesh(new T.SphereGeometry(R * 0.8, 28, 12, 0, Math.PI * 2, 0, Math.PI / 2), fillMat);
      dome.rotation.x = dir > 0 ? Math.PI : 0; g.add(dome);
      g.userData.dome = dome;
      return g;
    }

    /* ---- benang isi yang melar di antara dua belahan (bentuk jam pasir) ---- */
    function makeString(fillMat) {
      const pts = [];
      for (let i = 0; i <= 18; i++) {
        const t = i / 18, rad = R * 0.8 * (0.3 + 0.7 * Math.pow(Math.abs(2 * t - 1), 1.7));
        pts.push(new T.Vector2(rad, t - 0.5));
      }
      return new T.Mesh(new T.LatheGeometry(pts, 24), fillMat);
    }

    /* ---- satu risol utuh ---- */
    function makeRisol(f) {
      const root = new T.Group(), spin = new T.Group(); root.add(spin);
      const fillMat = new T.MeshStandardMaterial({ color: f.color, roughness: f.rough, metalness: 0.05, emissive: f.glow, emissiveIntensity: 0.18, side: T.DoubleSide });
      const top = makeHalf(1, fillMat), bot = makeHalf(-1, fillMat), str = makeString(fillMat);
      const drops = [];
      for (let k = 0; k < 3; k++) {
        const d = new T.Mesh(new T.SphereGeometry(0.075, 12, 10), fillMat); d.userData.k = k; drops.push(d); spin.add(d);
      }
      spin.add(top, bot, str);
      root.userData = { spin, top, bot, str, drops, fillMat };
      return root;
    }

    const risols = FLAVORS.map(makeRisol);
    risols.forEach(r => scene.add(r));

    /* ---- remah warna-warni melayang ---- */
    const N = 360, pos = new Float32Array(N * 3), col = new Float32Array(N * 3), c = new T.Color();
    for (let i = 0; i < N; i++) {
      pos[i * 3] = (Math.random() - 0.5) * 16; pos[i * 3 + 1] = (Math.random() - 0.5) * 11; pos[i * 3 + 2] = (Math.random() - 0.5) * 8 - 1;
      c.setHex(FLAVORS[i % 4].glow); col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    }
    const pg = new T.BufferGeometry();
    pg.setAttribute('position', new T.BufferAttribute(pos, 3)); pg.setAttribute('color', new T.BufferAttribute(col, 3));
    const crumbs = new T.Points(pg, new T.PointsMaterial({ size: 0.06, vertexColors: true, transparent: true, opacity: 0.85, depthWrite: false }));
    scene.add(crumbs);

    /* ---- cahaya: hangat dari kiri, hijau dari kanan, merah muda dari bawah ---- */
    scene.add(new T.AmbientLight(0xfff2e0, 0.55));
    const key = new T.DirectionalLight(0xffffff, 1.15); key.position.set(3, 6, 6); scene.add(key);
    const warm = new T.PointLight(0xffa62b, 1.8, 24); warm.position.set(-6, 1, 4); scene.add(warm);
    const green = new T.PointLight(0x9ccc3c, 1.4, 24); green.position.set(6, 2, 3); scene.add(green);
    const pink = new T.PointLight(0xff5d7a, 1.1, 22); pink.position.set(0, -5, 5); scene.add(pink);

    /* ---- tata letak: 2×2 untuk layar kotak/sempit, 1 baris untuk layar lebar ---- */
    let layoutY = [0, 0, 0, 0];
    function layout() {
      const w = host.clientWidth || 1, h = host.clientHeight || 1, a = w / h;
      renderer.setSize(w, h, false); cam.aspect = a; cam.updateProjectionMatrix();
      const row = a >= 1.45;
      const P = row ? [[-3.45, 0.35], [-1.15, -0.35], [1.15, 0.35], [3.45, -0.35]]
                    : [[-1.3, 1.55], [1.3, 1.2], [-1.3, -1.6], [1.3, -1.95]];
      P.forEach((p, i) => { risols[i].position.x = p[0]; layoutY[i] = p[1]; });
      const tan = Math.tan((cam.fov / 2) * Math.PI / 180);
      const needW = row ? 4.9 : 2.7, needH = row ? 2.9 : 3.9;
      cam.position.set(0, 1.4, Math.max(needW / (tan * a), needH / tan) + 1.2);
      cam.lookAt(0, 0, 0);
    }

    /* ---- animasi satu risol: terbelah → isi mengalir → menutup ---- */
    const PERIOD = 6.5, ease = x => x * x * (3 - 2 * x);
    function pose(i, t) {
      const r = risols[i], d = r.userData;
      const u = (t / PERIOD + i * 0.21) % 1;
      const e = ease(Math.min(1, Math.max(0, Math.sin(Math.PI * u) * 1.8)));   // 0 tertutup → 1 terbuka penuh
      const gap = GAP * e;

      d.top.position.y = gap / 2; d.bot.position.y = -gap / 2;
      d.top.rotation.z = e * 0.10 * (i % 2 ? -1 : 1); d.bot.rotation.z = -e * 0.07 * (i % 2 ? -1 : 1);
      d.top.userData.dome.scale.y = 0.15 + e * 1.0; d.bot.userData.dome.scale.y = 0.15 + e * 1.1;

      d.str.visible = gap > 0.05;
      const s = 1 / (1 + gap * 0.45);                       // makin renggang makin tipis
      d.str.scale.set(s, Math.max(gap, 0.001), s);

      d.drops.forEach(dp => {
        const ph = (t * 0.55 + dp.userData.k / 3 + i * 0.13) % 1;
        dp.visible = e > 0.55;
        dp.position.set((dp.userData.k - 1) * 0.2, -gap / 2 - 0.1 - ph * 1.25, (dp.userData.k % 2 - 0.5) * 0.2);
        dp.scale.setScalar(1 - ph * 0.55);
      });

      r.position.y = layoutY[i] + Math.sin(t * 1.1 + i * 1.7) * 0.07;
      r.rotation.z = (i % 2 ? -1 : 1) * 0.3; r.rotation.x = 0.42;
      d.spin.rotation.y = t * 0.35 + i * 1.3;
    }

    /* ---- loop ---- */
    let mx = 0, my = 0, visible = true, t0 = performance.now();
    addEventListener('pointermove', e => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; });
    new IntersectionObserver(es => { visible = es[0].isIntersecting; }).observe(host);
    function render(t) {
      for (let i = 0; i < 4; i++) pose(i, t);
      scene.rotation.y += (mx * 0.5 - scene.rotation.y) * 0.05;
      scene.rotation.x += (my * 0.25 - scene.rotation.x) * 0.05;
      crumbs.rotation.y = t * 0.02; crumbs.position.y = Math.sin(t * 0.3) * 0.3;
      renderer.render(scene, cam);
    }
    addEventListener('resize', () => { layout(); if (reduce) render(PERIOD * 0.5); });
    layout();
    if (reduce) { render(PERIOD * 0.5 / 1); return; }       // satu bingkai statis (terbelah)
    (function loop() { requestAnimationFrame(loop); if (visible) render((performance.now() - t0) / 1000); })();
  }
})();
