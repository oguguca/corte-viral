// Painel "Layout do vertical": câmeras/gameplay detectadas, caixas arrastáveis 1:1 e prévia 9:16.
import { $, api, esc, projectApi } from "../api.js";
import { openSheet, toast } from "../ui.js";

const R = (a) => (a ? a.map((v) => Math.round(v * 10000) / 10000) : null);

function coverCrop([x, y, w, h], aspect, anchorY) {
  let cw, ch;
  if (w / h > aspect) { ch = h; cw = h * aspect; } else { cw = w; ch = w / aspect; }
  let cy = y + anchorY * h - ch / 2;
  cy = Math.min(Math.max(cy, y), y + h - ch);
  return [x + (w - cw) / 2, cy, cw, ch];
}

export async function openLayout(from, name) {
  const P = projectApi(name);
  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = '<div class="empty"><span class="spinner"></span>Detectando câmeras e a janela da gameplay…</div>';
  const foot = document.createElement("div");
  foot.className = "row";
  foot.innerHTML = '<button class="btn plain" data-cancel>Cancelar</button><button class="btn primary" id="save" disabled>Salvar para este streamer</button>';
  const s = openSheet({ title: "Layout do vertical", body, foot, width: 980, from });
  $("[data-cancel]", foot).onclick = s.close;

  let r;
  try { r = await api(P("/layout")); } catch (e) { body.innerHTML = `<div class="note bad">⚠ ${esc(e.message)}</div>`; return; }
  const st = { cam: r.preset.facecam || r.detected.cams[0]?.rect || [0.7, 0.05, 0.25, 0.3],
               game: r.preset.gameplay || r.detected.gameplay || [0, 0, 1, 1], layout: r.preset.layout };
  const origin = { preset: "Salvo para este streamer", detected: "Detectado automaticamente", fallback: "Nenhuma câmera encontrada" }[r.origin] || "";
  const dur = r.duration || 600;
  body.innerHTML = `
    <div class="row"><span class="badge info">${origin}</span><span class="t-small">Arraste os retângulos; a bolinha branca redimensiona.</span></div>
    <div class="row" style="align-items:flex-start;gap:18px">
      <div style="flex:1;min-width:300px" class="stack">
        <div class="ly-stage" id="stage"><img id="img" alt="Quadro do vídeo">
          <div class="box game" id="bGame"><b>Gameplay</b><i></i></div><div class="box cam" id="bCam"><b>Câmera</b><i></i></div></div>
        <div class="row" id="cams">${r.detected.cams.map((c, i) => `<button class="btn small" data-cam="${i}">Câmera ${i + 1}</button>`).join("")
          || '<span class="t-small">Nenhuma câmera detectada — desenhe a área ou use outro layout.</span>'}</div>
      </div>
      <div class="stack" style="align-items:center"><canvas class="preview-9x16" id="pv" width="360" height="640" aria-label="Prévia do vertical"></canvas>
        <span class="t-small">Prévia 9:16</span></div>
    </div>
    <div class="row" style="align-items:flex-end;gap:14px">
      <div class="field"><span class="label" id="lyLbl">Layout</span><div class="seg" role="group" aria-labelledby="lyLbl">
        <button data-ly="split">Câmera + gameplay</button><button data-ly="face-follow">Seguir o rosto</button><button data-ly="fallback">Fundo desfocado</button></div></div>
      <div class="field" style="width:200px"><label for="streamer">Streamer</label><input class="input" id="streamer" value="${esc(r.streamer || "")}" placeholder="nome do canal"></div>
      <div class="field" style="width:170px"><label for="credit">Crédito no vídeo</label><input class="input" id="credit" value="${esc(r.preset.credit_handle || "")}" placeholder="@canal"></div>
    </div>
    <label class="switch"><input type="checkbox" id="auth" ${r.preset.authorized ? "checked" : ""}><span class="track"></span>
      <span>Tenho autorização deste streamer para postar cortes</span></label>`;

  const img = $("#img", body), bCam = $("#bCam", body), bGame = $("#bGame", body);
  const markLayout = () => body.querySelectorAll("[data-ly]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.ly === st.layout));
  const place = () => {
    const set = (el, [x, y, w, h]) => Object.assign(el.style, { left: x * 100 + "%", top: y * 100 + "%", width: w * 100 + "%", height: h * 100 + "%" });
    set(bCam, st.cam); set(bGame, st.game);
    bCam.hidden = st.layout !== "split";
    bGame.hidden = st.layout === "face-follow";
    body.querySelectorAll("[data-cam]").forEach((b) => {
      const c = r.detected.cams[+b.dataset.cam].rect;
      b.classList.toggle("on", c.every((v, i) => Math.abs(v - st.cam[i]) < 0.002));
    });
    draw();
  };
  const draw = () => {
    const cv = $("#pv", body), g = cv.getContext("2d");
    if (!img.naturalWidth) return;
    const W = cv.width, H = cv.height, iw = img.naturalWidth, ih = img.naturalHeight;
    const px = (a) => [a[0] * iw, a[1] * ih, a[2] * iw, a[3] * ih];
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    if (st.layout === "split") {
      const camH = Math.round(H * 0.3), div = 2, gameH = H - camH - div;
      g.drawImage(img, ...coverCrop(px(st.cam), W / camH, 0.42), 0, 0, W, camH);
      g.drawImage(img, ...coverCrop(px(st.game), W / gameH, 0.5), 0, camH + div, W, gameH);
    } else if (st.layout === "face-follow") {
      const cw = (ih * 9) / 16;
      g.drawImage(img, (iw - cw) / 2, 0, cw, ih, 0, 0, W, H);
    } else {
      const [x, y, w, h] = px(st.game), sc = Math.max(W / w, H / h);
      g.filter = "blur(8px)"; g.drawImage(img, x, y, w, h, (W - w * sc) / 2, (H - h * sc) / 2, w * sc, h * sc); g.filter = "none";
      const fh = (W * h) / w; g.drawImage(img, x, y, w, h, 0, (H - fh) / 2, W, fh);
    }
  };
  // arrastar 1:1 a partir do ponto onde a caixa foi pega (Pointer Events + captura)
  const drag = (el, key) => {
    el.onpointerdown = (e) => {
      e.preventDefault();
      const rect = $("#stage", body).getBoundingClientRect(), resize = e.target.tagName === "I";
      const start = { x: e.clientX, y: e.clientY, r: st[key].slice() };
      el.setPointerCapture(e.pointerId);
      el.onpointermove = (ev) => {
        const dx = (ev.clientX - start.x) / rect.width, dy = (ev.clientY - start.y) / rect.height;
        let [x, y, w, h] = start.r;
        if (resize) { w = Math.min(1 - x, Math.max(0.03, w + dx)); h = Math.min(1 - y, Math.max(0.03, h + dy)); }
        else { x = Math.min(1 - w, Math.max(0, x + dx)); y = Math.min(1 - h, Math.max(0, y + dy)); }
        st[key] = [x, y, w, h];
        place();
      };
      el.onpointerup = () => { el.onpointermove = null; el.onpointerup = null; };
    };
  };
  drag(bCam, "cam"); drag(bGame, "game");
  body.querySelectorAll("[data-cam]").forEach((b) => b.onclick = () => { st.cam = r.detected.cams[+b.dataset.cam].rect.slice(); place(); });
  body.querySelectorAll("[data-ly]").forEach((b) => b.onclick = () => { st.layout = b.dataset.ly; markLayout(); place(); });
  img.onload = () => { place(); $("#save", foot).disabled = false; };
  img.onerror = () => { $("#save", foot).disabled = false; toast("Não consegui carregar um quadro do vídeo.", "bad"); };
  img.src = P(`/frame?t=${Math.max(0, Math.min(dur * 0.4, dur - 1)).toFixed(1)}`);
  markLayout();

  $("#save", foot).onclick = async () => {
    const streamer = $("#streamer", body).value.trim();
    try {
      if (streamer) await api(P("/streamer"), { method: "PUT", body: { streamer } });
      const res = await api(P("/layout"), { method: "PUT", body: {
        layout: st.layout, facecam: st.layout === "split" ? R(st.cam) : null, gameplay: R(st.game),
        credit_handle: $("#credit", body).value.trim(), authorized: $("#auth", body).checked } });
      toast(`Layout salvo para “${res.streamer}”. Próximos VODs desse canal já saem assim.`, "good");
      s.close();
    } catch (e) { toast("Não salvou: " + e.message, "bad"); }
  };
}
