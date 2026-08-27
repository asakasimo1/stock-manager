/* ══════════════════════════════════════
   NEURAL NETWORK CANVAS — 배경 파티클 애니메이션
   personal_ai_brain(brain.html)의 neural-canvas를 이식(2026-08-27,
   사용자 요청 "brain.html 배경처럼 생동감있게"). 색상만 이 앱의 다크모드
   팔레트(보라/초록/빨강/파랑)로 교체. 다크모드가 아닐 때는 그리지 않고
   대기만 하다가, 설정에서 다크모드를 켜면(_applySettingsState가
   body.dark를 토글하는 순간) 다음 프레임부터 자동으로 그려진다 —
   새로고침 없이도 라이트↔다크 전환에 즉시 반응.
══════════════════════════════════════ */
(function () {
  const canvas = document.getElementById('neural-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const COLORS = {
    node: ['#a855f7', '#4ade80', '#fb7185', '#4da3ff', '#c084fc'],
    line: 'rgba(168,85,247,',
    pulse: ['#a855f7', '#4ade80', '#4da3ff', '#c084fc'],
  };

  const MAX_DIST = 200;
  const PULSE_SPEED = 0.0018;

  let W, H, dpr, nodes, pulses;

  function nodeCount() {
    return Math.min(60, Math.floor(window.innerWidth * window.innerHeight / 14000));
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
      vx: (Math.random() - .5) * .22,
      vy: (Math.random() - .5) * .22,
      r: Math.random() * 2.2 + 1.2,
      color: COLORS.node[Math.floor(Math.random() * COLORS.node.length)],
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: Math.random() * .015 + .006,
      opacity: Math.random() * .4 + .5,
    };
  }

  function init() {
    resize();
    nodes = Array.from({ length: nodeCount() }, mkNode);
    pulses = [];
  }

  function spawnPulse() {
    if (pulses.length >= 18) return;
    for (let tries = 0; tries < 10; tries++) {
      const i = Math.floor(Math.random() * nodes.length);
      const j = Math.floor(Math.random() * nodes.length);
      if (i === j) continue;
      const dx = nodes[i].x - nodes[j].x;
      const dy = nodes[i].y - nodes[j].y;
      if (Math.sqrt(dx * dx + dy * dy) < MAX_DIST) {
        pulses.push({
          from: i, to: j, t: 0,
          color: COLORS.pulse[Math.floor(Math.random() * COLORS.pulse.length)],
          speed: PULSE_SPEED * (Math.random() * .8 + .6),
        });
        break;
      }
    }
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

  function drawConnections() {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist > MAX_DIST) continue;
        const alpha = (1 - dist / MAX_DIST) * .22;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = COLORS.line + alpha + ')';
        ctx.lineWidth = .6;
        ctx.stroke();
      }
    }
  }

  function drawPulses() {
    const toRemove = [];
    pulses.forEach((p, idx) => {
      p.t += p.speed;
      if (p.t >= 1) { toRemove.push(idx); return; }
      const a = nodes[p.from], b = nodes[p.to];
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist > MAX_DIST) { toRemove.push(idx); return; }
      const px = a.x + dx * p.t;
      const py = a.y + dy * p.t;
      const alpha = Math.sin(p.t * Math.PI);
      const pg = ctx.createRadialGradient(px, py, 0, px, py, 5);
      pg.addColorStop(0, p.color.replace(')', ', ' + (alpha * .95) + ')').replace('rgb', 'rgba'));
      pg.addColorStop(1, 'transparent');
      ctx.beginPath();
      ctx.arc(px, py, 5, 0, Math.PI * 2);
      ctx.fillStyle = pg;
      ctx.fill();
    });
    for (let i = toRemove.length - 1; i >= 0; i--) pulses.splice(toRemove[i], 1);
  }

  function drawNodes() {
    const t = performance.now() / 1000;
    nodes.forEach(n => {
      const glow = Math.sin(t * n.phaseSpeed * 60 + n.phase) * .5 + .5;
      const r = n.r + glow * 1.5;
      const alpha = n.opacity * (.6 + glow * .4);

      const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, r * 4.5);
      g.addColorStop(0, hexToRgba(n.color, alpha * .45));
      g.addColorStop(1, 'transparent');
      ctx.beginPath();
      ctx.arc(n.x, n.y, r * 4.5, 0, Math.PI * 2);
      ctx.fillStyle = g;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(n.color, alpha);
      ctx.fill();
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

  let frame = 0;
  let started = false;
  function loop() {
    if (document.body.classList.contains('dark')) {
      if (!started) { init(); started = true; }
      ctx.clearRect(0, 0, W, H);
      drawBg();
      drawConnections();
      drawPulses();
      drawNodes();
      updateNodes();
      if (++frame % 40 === 0) spawnPulse();
    } else {
      started = false; // 다시 켜지면 새 상태로 재시작
    }
    requestAnimationFrame(loop);
  }

  loop();
  window.addEventListener('resize', () => { if (started) init(); });
})();
