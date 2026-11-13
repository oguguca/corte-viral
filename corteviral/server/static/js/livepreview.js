// Prévia ao vivo do clipe 9:16: desenha o vídeo original no layout do streamer, com legenda, gancho e
// crédito por cima, do jeito que o MP4 vai sair (mesmos números de layout.py e captions.py). Não renderiza nada:
// mudou a posição, o gancho ou a legenda, a prévia muda na hora.

const SAFE_X = 140, FRAME_PAD = 20, SAFE_BOTTOM = 1440, CREDIT_SIZE = 36;
const HOOK_SCALE = 0.8, HOOK_SECONDS = 3, FACE_ANCHOR = 0.42, LIBASS = 1.41;  // libass mede pela altura da linha
const W = 540, H = 960, K = H / 1920;

export function coverCrop([x, y, w, h], aspect, anchorY) {
  let cw, ch;
  if (w / h > aspect) { ch = h; cw = h * aspect; } else { cw = w; ch = w / aspect; }
  let cy = y + anchorY * h - ch / 2;
  cy = Math.min(Math.max(cy, y), y + h - ch);
  return [x + (w - cw) / 2, cy, cw, ch];
}

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// mesma conta de captions.py: base da legenda (em 1920) e o crédito fora do caminho dela
export const captionBottom = (pos, size) => Math.min(Math.max(pos * 1920 + size / 2, FRAME_PAD + size), 1920 - FRAME_PAD);
function creditY(band) {
  let cy = SAFE_BOTTOM;
  if (band) {
    const [ctop, cbot] = band;
    if (cy > ctop - 8 && cy - CREDIT_SIZE < cbot + 8) {
      const below = cbot + 8 + CREDIT_SIZE;
      cy = below <= 1920 - FRAME_PAD ? below : ctop - 8;
    }
  }
  return cy;
}

// mesma conta de divider_band.band_box: faixa centrada na divisa câmera/jogo (k = 1 no vídeo, 0.5 na prévia)
const even = (v) => Math.max(2, Math.round(v / 2) * 2);
export function bandBox(b, render, k) {
  const Wk = even(1080 * k), Hk = even(1920 * k), camH = even(Hk * render.facecam_ratio);
  const div = render.divider_px ? even(render.divider_px * k) : 0;
  const w = even(b.raw_w * b.scale * (Wk / 1080)), h = even(b.raw_h * b.scale * (Wk / 1080));
  return { x: Math.floor((Wk - w) / 2), y: Math.round(camH + div / 2 - h / 2 + b.offset * (Hk / 1920)), w, h };
}

let active = null;  // só uma prévia toca por vez

/** Liga a prévia num <canvas> (540×960) com barra de controle. Devolve { update, setPosition, setHook, destroy }. */
export function mountLivePreview(canvas, bar, videoUrl, spec) {
  active?.destroy();
  const g = canvas.getContext("2d");
  const v = document.createElement("video");
  v.preload = "auto"; v.playsInline = true; v.src = videoUrl;
  let S = spec, raf = 0, dead = false;
  const play = bar.querySelector(".lp-play"), seek = bar.querySelector(".lp-seek"), time = bar.querySelector(".lp-time");
  seek.max = S.duration;

  const rel = () => Math.max(0, v.currentTime - S.start);
  let bandImg = null, bandVer = null;
  function loadBand() {  // a imagem da faixa da divisa (troca quando o usuário escolhe outra)
    if (!S.band || S.band.version === bandVer) return;
    bandVer = S.band.version;
    bandImg = new Image();
    bandImg.onload = () => { if (v.paused) draw(); };
    bandImg.src = `/api/divider-band/image?v=${S.band.version}`;
  }
  function drawBand() {
    if (!S.band || !bandImg || !bandImg.complete || !bandImg.naturalWidth) return;
    g.drawImage(bandImg, S.band.x, S.band.y, S.band.w, S.band.h);
  }
  loadBand();
  const font = (px) => { g.font = `900 ${px}px "${S.style.font}", "Arial Black", sans-serif`; };

  function drawVideo() {
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    if (v.readyState < 2 || !v.videoWidth) return;
    const iw = v.videoWidth, ih = v.videoHeight;
    const px = (a) => (a ? [a[0] * iw, a[1] * ih, a[2] * iw, a[3] * ih] : [0, 0, iw, ih]);
    if (S.layout === "split") {
      const camH = Math.round(H * S.render.facecam_ratio), div = Math.round(S.render.divider_px * K), gameH = H - camH - div;
      g.drawImage(v, ...coverCrop(px(S.facecam), W / camH, FACE_ANCHOR), 0, 0, W, camH);
      g.fillStyle = S.render.divider_color; g.fillRect(0, camH, W, div);
      g.drawImage(v, ...coverCrop(px(S.gameplay), W / gameH, 0.5), 0, camH + div, W, gameH);
    } else if (S.layout === "face-follow") {
      const cw = (ih * 9) / 16;  // a prévia fica no centro; o vídeo final segue o rosto
      g.drawImage(v, (iw - cw) / 2, 0, cw, ih, 0, 0, W, H);
    } else {
      const [x, y, w, h] = px(S.gameplay), sc = Math.max(W / w, H / h);
      g.filter = "blur(10px)"; g.drawImage(v, x, y, w, h, (W - w * sc) / 2, (H - h * sc) / 2, w * sc, h * sc); g.filter = "none";
      const fh = (W * h) / w; g.drawImage(v, x, y, w, h, 0, (H - fh) / 2, W, fh);
    }
  }

  function drawCaption(t) {
    const st = S.style, ev = S.events.find((e) => t >= e.start && t < e.end);
    if (!ev) return;
    let px = (st.size * K) / LIBASS;
    font(px);
    const space = () => g.measureText(" ").width;
    let widths = ev.words.map((w) => g.measureText(w).width);
    let total = widths.reduce((a, c) => a + c, 0) + space() * (ev.words.length - 1);
    const safeW = (1080 - 2 * SAFE_X) * K;
    if (total > safeW) {  // grupo largo demais: encolhe para caber (igual ao \fscx do .ass)
      px *= safeW / total; font(px);
      widths = ev.words.map((w) => g.measureText(w).width);
      total = widths.reduce((a, c) => a + c, 0) + space() * (ev.words.length - 1);
    }
    const y = captionBottom(st.position, st.size) * K;
    const pop = st.pop ? 1.15 - 0.15 * Math.min(1, (t - ev.start) / 0.08) : 1;
    let x = 540 * K - total / 2;
    g.textBaseline = "bottom"; g.lineJoin = "round"; g.textAlign = "left";
    ev.words.forEach((w, i) => {
      g.save();
      if (i === ev.hl && pop !== 1) {  // "pop" da palavra falada
        const cx = x + widths[i] / 2;
        g.translate(cx, y); g.scale(pop, pop); g.translate(-cx, -y);
      }
      g.shadowColor = "rgba(0,0,0,.5)"; g.shadowOffsetX = g.shadowOffsetY = st.shadow * K;
      g.lineWidth = Math.max(1, st.outline * 2 * K); g.strokeStyle = st.outline_color;
      if (st.outline) g.strokeText(w, x, y);
      g.shadowColor = "transparent";
      g.fillStyle = i === ev.hl ? st.highlight : st.color; g.fillText(w, x, y);
      g.restore();
      x += widths[i] + space();
    });
  }

  function drawHook(t) {
    if (!S.hook || !S.hook.trim() || t >= Math.min(HOOK_SECONDS, S.duration)) return;
    const st = S.style, text = st.uppercase ? S.hook.trim().toUpperCase() : S.hook.trim();
    const safeW = (1080 - 2 * SAFE_X) * K;
    let px = (st.size * HOOK_SCALE * K) / LIBASS;
    font(px);
    const full = g.measureText(text).width;
    if (full > 2 * safeW) { px *= (2 * safeW) / full; font(px); }  // cabe em até 2 linhas
    const lines = [];
    let cur = "";
    for (const w of text.split(/\s+/)) {
      const next = cur ? cur + " " + w : w;
      if (cur && g.measureText(next).width > safeW) { lines.push(cur); cur = w; } else cur = next;
    }
    if (cur) lines.push(cur);
    const pad = 14 * K, lh = px * 1.2;
    let y = S.hook_top * K;
    g.textBaseline = "top"; g.textAlign = "center";
    for (const line of lines) {
      const lw = g.measureText(line).width;
      g.fillStyle = "#000"; g.fillRect(540 * K - lw / 2 - pad, y - pad, lw + 2 * pad, lh + 2 * pad);
      g.fillStyle = "#fff"; g.fillText(line, 540 * K, y);
      y += lh;
    }
    g.textAlign = "left";
  }

  function drawCredit() {
    if (!S.credit) return;
    const st = S.style, handle = S.credit.startsWith("@") ? S.credit : "@" + S.credit;
    const band = S.events.length ? [captionBottom(st.position, st.size) - st.size * 1.25, captionBottom(st.position, st.size)] : null;
    font((CREDIT_SIZE * K) / LIBASS);
    g.textBaseline = "bottom"; g.textAlign = "right"; g.lineJoin = "round";
    const x = (1080 - SAFE_X) * K, y = creditY(band) * K;
    g.lineWidth = 4 * K; g.strokeStyle = "rgba(0,0,0,.8)"; g.strokeText(handle, x, y);
    g.fillStyle = "rgba(255,255,255,.81)"; g.fillText(handle, x, y);
    g.textAlign = "left";
  }

  function draw() {
    if (dead) return;
    if (!canvas.isConnected) { destroy(); return; }  // saiu da tela: para o som também
    const t = rel();
    drawVideo(); drawBand(); drawCaption(t); drawHook(t); drawCredit();
    seek.value = t; time.textContent = `${clock(t)} / ${clock(S.duration)}`;
  }
  function loop() {
    if (v.currentTime >= S.end) { v.pause(); v.currentTime = S.start; }
    draw();
    if (!v.paused && !dead) raf = requestAnimationFrame(loop);
  }
  function syncBtn() {
    play.textContent = v.paused ? "▶" : "❚❚";
    play.setAttribute("aria-label", v.paused ? "Tocar a prévia" : "Pausar a prévia");
  }
  const toggle = () => { if (v.paused) { if (rel() >= S.duration - 0.05) v.currentTime = S.start; v.play().catch(() => {}); } else v.pause(); };

  v.addEventListener("loadedmetadata", () => { v.currentTime = S.start; });
  v.addEventListener("seeked", draw);
  v.addEventListener("loadeddata", draw);
  v.addEventListener("play", () => { syncBtn(); cancelAnimationFrame(raf); raf = requestAnimationFrame(loop); });
  v.addEventListener("pause", () => { syncBtn(); draw(); });
  v.addEventListener("timeupdate", () => { if (!canvas.isConnected) destroy(); });
  v.addEventListener("error", () => { g.fillStyle = "#000"; g.fillRect(0, 0, W, H); time.textContent = "vídeo original indisponível"; });
  play.onclick = toggle;
  canvas.onclick = toggle;
  seek.oninput = () => { v.currentTime = S.start + +seek.value; };
  syncBtn();
  g.fillStyle = "#000"; g.fillRect(0, 0, W, H);

  function destroy() {
    dead = true; cancelAnimationFrame(raf);
    v.pause(); v.removeAttribute("src"); v.load();
    if (active === api) active = null;
  }
  const api = {
    update(spec) {  // novos dados do servidor (corte, legenda, gancho…)
      const moved = spec.start !== S.start;
      S = spec; seek.max = S.duration; loadBand();
      if (moved) v.currentTime = S.start; else if (v.paused) draw();
    },
    setBand(adj) {  // ajuste da faixa (altura/tamanho/ligada) na hora, sem esperar o servidor
      if (!S.band && !adj.base) return;
      if (adj.enabled === false) { S = { ...S, band: null, hook_top: S.hook_top_nob ?? S.hook_top }; if (v.paused) draw(); return; }
      const b = { ...(S.band || adj.base), ...adj };
      const at = bandBox(b, S.render, 0.5), full = bandBox(b, S.render, 1);
      const base = even(1920 * S.render.facecam_ratio) + (S.render.divider_px || 0) + 40;
      S = { ...S, band: { ...b, ...at }, hook_top_nob: S.hook_top_nob ?? (S.band ? null : S.hook_top),
            hook_top: S.layout === "split" ? Math.max(base, full.y + full.h + 30) : S.hook_top };
      loadBand();
      if (v.paused) draw();
    },
    setPosition(p) { S = { ...S, style: { ...S.style, position: p } }; if (v.paused) draw(); },
    setHook(text) {  // o gancho só aparece nos 3 primeiros segundos: volta para lá para mostrar
      S = { ...S, hook: text };
      if (v.paused) { if (rel() >= HOOK_SECONDS) v.currentTime = S.start; else draw(); }
    },
    destroy,
  };
  active = api;
  return api;
}
