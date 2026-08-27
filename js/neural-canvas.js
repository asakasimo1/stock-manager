/* ══════════════════════════════════════
   NEURAL NETWORK CANVAS — 배경 라인 드로잉 애니메이션
   personal_ai_brain(brain.html)의 neural-canvas를 이식(2026-08-27,
   "brain.html 배경처럼 생동감있게") → 이후 "점보다는 라인이 그려지는
   애니메이션으로" 요청 반영해 재작업: 노드는 거의 안 보이는 작은 앵커점
   으로 줄이고, 두 노드 사이를 선이 처음부터 끝까지 자라나듯 그려졌다가
   (grow) 잠깐 유지되고(hold) 옅어지며 사라지는(fade) 트레이스로 교체.
   상시 켜진 정적 그물망(drawConnections)은 없애고 이 트레이스만 남김 —
   "라인이 그려진다"는 느낌 자체가 핵심 비주얼이 되도록.

   다크모드가 아닐 때는 그리지 않고 대기만 하다가, 설정에서 다크모드를
   켜면(body.dark 토글) 다음 프레임부터 자동으로 그려진다.
══════════════════════════════════════ */
(function () {
  const canvas = document.getElementById('neural-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const COLORS = {
    node: 'rgba(200,190,230,',
    trace: ['#a855f7', '#4ade80', '#4da3ff', '#c084fc', '#fb7185'],
  };

  const MAX_DIST     = 260;   // 이 거리 안의 노드 쌍만 트레이스로 이어짐
  const MAX_TRACES   = 16;
  const SPAWN_CHANCE = 0.10;  // 프레임당 새 트레이스 생성 확률
  const GROW_SPEED   = 0.018; // 선이 0→1 그려지는 속도
  const HOLD_FRAMES  = 55;    // 다 그려진 뒤 유지 프레임
  const FADE_SPEED   = 0.028;

  let W, H, dpr, nodes, traces;

  function nodeCount() {
    return Math.min(46, Math.floor(window.innerWidth * window.innerHeight / 16000));
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
      vx: (Math.random() - .5) * .18,
      vy: (Math.random() - .5) * .18,
      r: Math.random() * .8 + .5,
      opacity: Math.random() * .3 + .25,
    };
  }

  function init() {
    resize();
    nodes = Array.from({ length: nodeCount() }, mkNode);
    traces = [];
  }

  function mkTrace() {
    for (let tries = 0; tries < 15; tries++) {
      const i = Math.floor(Math.random() * nodes.length);
      const j = Math.floor(Math.random() * nodes.length);
      if (i === j) continue;
      const a = nodes[i], b = nodes[j];
      if (Math.hypot(a.x - b.x, a.y - b.y) < MAX_DIST) {
        return {
          from: i, to: j, t: 0, phase: 'grow', fadeA: 1,
          color: COLORS.trace[Math.floor(Math.random() * COLORS.trace.length)],
        };
      }
    }
    return null;
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
      { x: W * .85, y: H * .7, r: W * .4, c1: 'rgba(74,222,128,.05)', c2: 'transparent' },
      { x: W * .5, y: H * .9, r: W * .3, c1: 'rgba(251,113,133,.04)', c2: 'transparent' },
    ];
    spots.forEach(s => {
      const sg = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r);
      sg.addColorStop(0, s.c1); sg.addColorStop(1, s.c2);
      ctx.fillStyle = sg;
      ctx.fillRect(0, 0, W, H);
    });
  }

  function drawNodes() {
    nodes.forEach(n => {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = COLORS.node + n.opacity + ')';
      ctx.fill();
    });
  }

  function updateTraces() {
    if (traces.length < MAX_TRACES && Math.random() < SPAWN_CHANCE) {
      const t = mkTrace();
      if (t) traces.push(t);
    }
    for (let k = traces.length - 1; k >= 0; k--) {
      const tr = traces[k];
      if (tr.phase === 'grow') {
        tr.t += GROW_SPEED;
        if (tr.t >= 1) { tr.t = 1; tr.phase = 'hold'; tr.holdT = 0; }
      } else if (tr.phase === 'hold') {
        tr.holdT++;
        if (tr.holdT >= HOLD_FRAMES) tr.phase = 'fade';
      } else {
        tr.fadeA -= FADE_SPEED;
        if (tr.fadeA <= 0) { traces.splice(k, 1); }
      }
    }
  }

  function drawTraces() {
    traces.forEach(tr => {
      const a = nodes[tr.from], b = nodes[tr.to];
      if (!a || !b) return;
      const growing = tr.phase === 'grow';
      const endX = growing ? a.x + (b.x - a.x) * tr.t : b.x;
      const endY = growing ? a.y + (b.y - a.y) * tr.t : b.y;
      const alpha = (tr.phase === 'fade' ? Math.max(0, tr.fadeA) : 1) * .6;

      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(endX, endY);
      ctx.strokeStyle = hexToRgba(tr.color, alpha);
      ctx.lineWidth = 1.1;
      ctx.stroke();

      // 그려지는 중일 때 끝점에 밝은 촉(펜촉) 글로우
      if (growing) {
        const g = ctx.createRadialGradient(endX, endY, 0, endX, endY, 7);
        g.addColorStop(0, hexToRgba(tr.color, Math.min(1, alpha * 1.8)));
        g.addColorStop(1, 'transparent');
        ctx.beginPath();
        ctx.arc(endX, endY, 7, 0, Math.PI * 2);
        ctx.fillStyle = g;
        ctx.fill();
      }
    });
  }

  function hexToRgba(hex, a) {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
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
      drawNodes();
      drawTraces();
      updateTraces();
      updateNodes();
    } else {
      started = false; // 다시 켜지면 새 상태로 재시작
    }
    requestAnimationFrame(loop);
  }

  loop();
  window.addEventListener('resize', () => { if (started) init(); });
})();
