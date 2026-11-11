// Etapa ① Vídeo: player + transcrição sincronizada (clique numa palavra pula até ela).
import { $, api, esc, fmt, projectApi } from "../api.js";
import { go } from "../router.js";

export async function renderVideo(root, name, project) {
  const P = projectApi(name);
  let t = null;
  try { t = await api(P("/transcript")); } catch (e) { /* ainda sem transcrição */ }
  root.innerHTML = `
    <div class="grid-2">
      <section class="card pad stack">
        <video id="v" controls preload="metadata" src="${P("/video")}"></video>
        <div class="row">
          ${t ? `<span class="badge">idioma: ${esc(t.language)}</span><span class="badge num">${t.words.length} palavras</span>
                 <span class="badge">${esc(t.model || "?")} · ${esc(t.device || "?")}</span>` : ""}
          <span class="spacer"></span><button class="btn small" id="folder">Abrir pasta</button>
        </div>
      </section>
      <section class="card pad stack">
        <div class="row"><h2 class="t-head">Transcrição</h2><span class="spacer"></span>
          <input class="input" id="q" type="search" placeholder="Buscar…" style="width:200px" aria-label="Buscar na transcrição"></div>
        <div class="words" id="words">${t ? "" : '<div class="empty"><div class="big">⏳</div>A transcrição ainda não terminou.</div>'}</div>
      </section>
    </div>
    <div class="row end" style="margin-top:20px">
      <button class="btn primary large" id="next">Próximo: encontrar os melhores momentos ›</button>
    </div>`;
  $("#folder", root).onclick = () => api(P("/open-folder"), { method: "POST" });
  $("#next", root).onclick = () => go(name, "moments");
  if (!t) return;

  const video = $("#v", root), box = $("#words", root), words = t.words, els = [];
  const frag = document.createDocumentFragment();
  let wi = 0;
  for (const seg of t.segments) {
    const div = document.createElement("div");
    div.className = "tline";
    const ts = document.createElement("span");
    ts.className = "ts"; ts.textContent = fmt(seg.start);
    ts.onclick = () => { video.currentTime = seg.start; video.play(); };
    div.append(ts);
    while (wi < words.length && words[wi].start < seg.end + 0.01) {
      const w = words[wi], span = document.createElement("span");
      span.className = "w" + (w.p < 0.5 ? " low" : "");
      span.textContent = w.w;
      span.title = `${w.start.toFixed(2)} s · confiança ${Math.round(w.p * 100)}%`;
      span.onclick = () => { video.currentTime = w.start; video.play(); };
      div.append(span, " ");
      els.push(span);
      wi++;
    }
    frag.append(div);
  }
  box.innerHTML = "";
  box.append(frag);

  let last = -1;
  video.ontimeupdate = () => {
    const time = video.currentTime;
    let lo = 0, hi = words.length - 1, idx = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (words[mid].start <= time) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    if (idx === last) return;
    els[last]?.classList.remove("now");
    if (idx >= 0 && time <= words[idx].end + 0.3) { els[idx].classList.add("now"); els[idx].scrollIntoView({ block: "nearest" }); }
    last = idx;
  };
  let searchTimer = 0, marked = [];
  $("#q", root).oninput = (e) => {  // espera a digitação parar e só mexe nas palavras que mudam (VODs têm dezenas de milhares)
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const q = e.target.value.trim().toLowerCase();
      marked.forEach((i) => { els[i].textContent = words[i].w; });
      marked = [];
      if (!q) return;
      words.forEach((w, i) => { if (w.w.toLowerCase().includes(q)) { els[i].innerHTML = `<mark>${esc(w.w)}</mark>`; marked.push(i); } });
      if (marked.length) els[marked[0]].scrollIntoView({ block: "center" });
    }, 180);
  };
}
