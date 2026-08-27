/* ══════════════════════════════════════
   NEURAL NETWORK CANVAS — 배경 네트워크 메시 애니메이션
   변천사(2026-08-27): brain.html의 neural-canvas 이식(무작위 노드+점
   강조) → "점보다는 라인이 그려지는 애니메이션으로" → "너무 막 그려지는
   것 같다, 네트워크 이미지처럼 정돈되게"(참고 이미지: 파란 단색 저폴리곤
   삼각망) 최종 반영: 무작위로 아무 데나 잇던 걸 버리고, 각 노드가 가장
   가까운 이웃 몇 개하고만 이어지는 삼각망(트라이앵귤레이션에 가까운
   형태)으로 매 프레임 다시 그림 — 노드가 서서히 떠다니며 망 자체가
   천천히 재구성되는 게 애니메이션이라 "정돈되면서도 생동감있게" 둘 다
   충족. 개별 선이 튀어나왔다 사라지는 grow/fade 트레이스는 제거.

   다크모드가 아닐 때는 그리지 않고 대기만 하다가, 설정에서 다크모드를
   켜면(body.dark 토글) 다음 프레임부터 자동으로 그려진다.
══════════════════════════════════════ */
(function () {
  const canvas = document.getElementById('neural-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  // 참고 이미지처럼 단일 색 계열(보라) 위주 + 아주 가끔 밝은 파랑 포인트
  const NODE_COLORS = ['#a855f7', '#a855f7', '#a855f7', '#c084fc', '#4da3ff'];
  const LINE_RGB = '168,85,247';

  const MAX_DIST  = 190;  // 이 거리 안의 이웃만 후보
  const K_NEAREST = 3;    // 각 노드가 잇는 가장 가까운 이웃 수(삼각망 느낌의 핵심)

  let W, H, dpr, nodes;

  function nodeCount() {
    return Math.min(48, Math.floor(window.innerWidth * window.innerHeight / 15000));
  }

  function resize() {
    dpr = window.devicePixelRatio || 1;
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
  }

  function mkNode() {
    return {
      x: Math.random() * W,
      y: Math.random() * H,
      vx: (Math.random() - .5) * .12,   // 천천히 떠다니게(전보다 느긋하게)
      vy: (Math.random() - .5) * .12,
      r: Math.random() * 1.6 + 1.6,
      color: NODE_COLORS[Math.floor(Math.random() * NODE_COLORS.length)],
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: Math.random() * .01 + .004,
    };
  }

  function init() {
    resize();
    nodes = Array.from({ length: nodeCount() }, mkNode);
  }

  function hexToRgb(hex) {
    const h = hex.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  function drawBg() {
    const g = ctx.createRadialGradient(W * .3, H * .2, 0, W * .5, H * .5, Math.max(W, H) * .8);
    g.addColorStop(0, '#171c30');
    g.addColorStop(.45, '#0f1424');
    g.addColorStop(1, '#0a0d1a');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    const spots = [
      { x: W * .15, y: H * .25, r: W * .35, c1: 'rgba(168,85,247,.08)', c2: 'transparent' },
      { x: W * .85, y: H * .7, r: W * .4, c1: 'rgba(74,222,128,.04)', c2: 'transparent' },
      { x: W * .5, y: H * .9, r: W * .3, c1: 'rgba(77,163,255,.04)', c2: 'transparent' },
    ];
    spots.forEach(s => {
      const sg = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r);
      sg.addColorStop(0, s.c1); sg.addColorStop(1, s.c2);
      ctx.fillStyle = sg;
      ctx.fillRect(0, 0, W, H);
    });
  }

  /* 각 노드마다 가장 가까운 K개 이웃만 후보로 골라 저폴리곤 삼각망처럼
     정돈된 형태를 만든다(전 버전처럼 반경 안 모든 쌍을 잇던 것 대신) —
     같은 변을 두 번 그리지 않도록 seen 집합으로 중복 제거. */
  function drawMesh() {
    const seen = new Set();
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      const dists = [];
      for (let j = 0; j < nodes.length; j++) {
        if (i === j) continue;
        const b = nodes[j];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < MAX_DIST) dists.push([d, j]);
      }
      dists.sort((p, q) => p[0] - q[0]);
      for (let k = 0; k < Math.min(K_NEAREST, dists.length); k++) {
        const j = dists[k][1];
        const key = i < j ? i + '_' + j : j + '_' + i;
        if (seen.has(key)) continue;
        seen.add(key);
        const b = nodes[j];
        const alpha = (1 - dists[k][0] / MAX_DIST) * .38;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = `rgba(${LINE_RGB},${alpha})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }
  }

  function drawNodes() {
    const t = performance.now() / 1000;
    nodes.forEach(n => {
      const glow = Math.sin(t * n.phaseSpeed * 60 + n.phase) * .5 + .5;
      const r = n.r + glow * 1;
      const [cr, cg, cb] = hexToRgb(n.color);

      const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, r * 4);
      g.addColorStop(0, `rgba(${cr},${cg},${cb},${.5 + glow * .3})`);
      g.addColorStop(1, 'transparent');
      ctx.beginPath();
      ctx.arc(n.x, n.y, r * 4, 0, Math.PI * 2);
      ctx.fillStyle = g;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${cr},${cg},${cb},${.75 + glow * .25})`;
      ctx.fill();
    });
  }

  function updateNodes() {
    nodes.forEach(n => {
      n.x += n.vx; n.y += n.vy;
      if (n.x < -10) n.x = W + 10;
      if (n.x > W + 10) n.x = -10;
      if (n.y < -10) n.y = H + 10;
      if (n.y > H + 10) n.y = -10;
    });
  }

  let started = false;
  function loop() {
    if (document.body.classList.contains('dark')) {
      if (!started) { init(); started = true; }
      ctx.clearRect(0, 0, W, H);
      drawBg();
      drawMesh();
      drawNodes();
      updateNodes();
    } else {
      started = false; // 다시 켜지면 새 상태로 재시작
    }
    requestAnimationFrame(loop);
  }

  loop();
  window.addEventListener('resize', () => { if (started) init(); });
})();
