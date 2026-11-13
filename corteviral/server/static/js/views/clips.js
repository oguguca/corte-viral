// Etapa ③ Clipes: revisar cada clipe (aprovar/descartar, corte, textos por rede, legenda) e gerar os MP4.
import { $, api, enc, esc, fmt, projectApi } from "../api.js";
import { startJob } from "../jobs.js";
import { mountLivePreview } from "../livepreview.js";
import { go } from "../router.js";
import { openLayout } from "../sheets/layout.js";
import { saveCapStyle, state } from "../state.js";
import { confirmAction, scoreClass, toast } from "../ui.js";
import { aiTag, critBars } from "./moments.js";

const PLATS = [["tiktok", "TikTok"], ["shorts", "Shorts"], ["reels", "Reels"], ["kwai", "Kwai"]];
const POS = [["0.35", "Em cima"], ["0.55", "Meio"], ["0.85", "Embaixo"]];
const title = (m) => (m.meta && m.meta.hook) || m.hook || m.description || m.reason || "Momento";

export async function renderClips(root, name, project, ctx = {}) {
  const P = projectApi(name);
  let d;
  try { d = await api(P("/moments")); } catch (e) {
    root.innerHTML = `<div class="card"><div class="empty"><div class="big">✨</div>Primeiro encontre os momentos.
      <button class="btn primary" id="toMoments">Ir para Momentos</button></div></div>`;
    $("#toMoments", root).onclick = () => go(name, "moments");
    return;
  }
  const ui = state.clipsUi;  // aba, filtro e rede sobrevivem aos redesenhos após tarefas
  let live = null;           // prévia ao vivo do clipe aberto
  const refreshLive = async (id) => {  // depois de salvar algo que muda o que aparece no vídeo
    if (!live) return;
    try { live.update(await api(P("/moments/") + enc(id) + "/preview")); } catch (e) { /* a prévia fica como estava */ }
  };
  const byScore = [...d.moments].sort((a, b) => b.score - a.score);
  if (!d.moments.some((m) => m.id === state.selected)) state.selected = byScore[0]?.id;

  root.innerHTML = `
    <section class="card pad" style="margin-bottom:18px">
      <div class="row" style="gap:18px">
        <label class="switch"><input type="checkbox" id="capOn"><span class="track"></span><span>Legenda nos clipes</span></label>
        <div class="row" style="gap:8px"><span class="label" id="posLbl">Posição</span>
          <div class="seg" role="group" aria-labelledby="posLbl">${POS.map(([v, l]) => `<button data-pos="${v}">${l}</button>`).join("")}</div></div>
        <button class="btn" id="layoutBtn">▣ Layout do vertical…</button>
        <span class="spacer"></span>
        <label class="row" style="gap:6px"><span class="label">Gerar os</span>
          <input class="input" id="nBest" type="number" min="1" max="30" value="${Math.min(5, d.moments.length)}" style="width:64px" aria-label="Quantos clipes gerar">
          <span class="label">melhores</span></label>
        <button class="btn primary" id="genBest">Gerar clipes</button>
      </div>
      <div class="t-small" style="margin-top:8px">Mudanças de legenda e layout valem para os próximos clipes gerados — use “Gerar de novo” nos que já existem.</div>
    </section>
    <div class="grid-clips">
      <section class="stack">
        <div class="seg" role="group" aria-label="Filtrar">
          ${[["all", "Todos"], ["pending", "A revisar"], ["approved", "Aprovados"], ["discarded", "Descartados"]].map(([k, l]) =>
            `<button data-filter="${k}" aria-pressed="${ui.filter === k}">${l}</button>`).join("")}</div>
        <ul class="list" id="clipList" aria-label="Clipes"></ul>
      </section>
      <section class="card pad" id="detail"></section>
    </div>
    <div class="row end" style="margin-top:20px">
      <button class="btn primary large" id="next">Próximo: exportar ›</button></div>`;

  const syncQuick = () => {
    $("#capOn", root).checked = state.capStyle.enabled !== false;
    root.querySelectorAll("[data-pos]").forEach((b) => b.setAttribute("aria-pressed", Math.abs(+b.dataset.pos - (state.capStyle.position ?? 0.7)) < 0.04));
  };
  syncQuick();
  $("#capOn", root).onchange = async (e) => { try { await saveCapStyle({ enabled: e.target.checked }); syncQuick(); toast(e.target.checked ? "Legendas ligadas." : "Legendas desligadas.", "good"); } catch (err) { toast(err.message, "bad"); } };
  root.querySelectorAll("[data-pos]").forEach((b) => b.onclick = async () => {
    try { await saveCapStyle({ position: +b.dataset.pos }); syncQuick(); toast("Posição da legenda: " + b.textContent + ".", "good"); } catch (err) { toast(err.message, "bad"); }
  });
  $("#layoutBtn", root).onclick = (e) => openLayout(e.currentTarget, name, ctx.rerender);
  $("#genBest", root).onclick = () => {
    const n = Math.max(1, +$("#nBest", root).value || 1);
    const ids = byScore.filter((m) => m.status !== "discarded").slice(0, n).map((m) => m.id);
    startJob({ kind: "render", project: name, ids });
  };
  $("#next", root).onclick = () => go(name, "export");
  root.querySelectorAll("[data-filter]").forEach((b) => b.onclick = () => {
    ui.filter = b.dataset.filter;
    root.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    renderList();
  });

  const patch = async (id, body) => {
    const r = await api(`${P("/moments/")}${enc(id)}`, { method: "PUT", body });
    const m = d.moments.find((x) => x.id === id);
    Object.assign(m, r.moment);
    if (r.moment.caption_words === undefined) delete m.caption_words;
    return m;
  };

  function renderList() {
    const visible = d.moments.filter((m) => ui.filter === "all" || (m.status || "pending") === ui.filter);
    $("#clipList", root).innerHTML = visible.length ? visible.map((m) => `
      <li><div class="moment ${m.status === "discarded" ? "discarded" : ""}" role="button" tabindex="0" data-id="${esc(m.id)}"
          ${m.id === state.selected ? 'aria-current="true"' : ""} style="grid-template-columns:44px minmax(0,1fr);padding:10px 12px">
        <div class="${scoreClass(m.score)}" style="width:44px;height:44px;border-radius:12px;font-size:16px"><span class="num">${m.score}</span></div>
        <div style="min-width:0"><div class="m-title" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(title(m))}</div>
          <div class="m-meta" style="margin-top:4px"><span class="t-small num">${fmt(m.start)} · ${Math.round(m.end - m.start)} s</span>
            ${m.status === "approved" ? '<span class="badge good">✓ aprovado</span>' : m.status === "discarded" ? '<span class="badge bad">descartado</span>' : ""}
            ${(d.clips || {})[m.id]?.final ? '<span class="badge info">▶ clipe pronto</span>' : ""}</div></div></div></li>`).join("")
      : '<li class="empty">Nada aqui com esse filtro.</li>';
    root.querySelectorAll("#clipList .moment").forEach((el) => {
      const pick = () => { state.selected = el.dataset.id; renderList(); renderDetail(); };
      el.onclick = pick;
      el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
    });
  }

  function markStatus(box, m) {  // atualiza só os botões, sem recarregar os vídeos nem perder o que foi digitado
    box.querySelectorAll("[data-status]").forEach((b) => {
      const on = m.status === b.dataset.status;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on);
      b.textContent = b.dataset.status === "approved" ? (on ? "✓ Aprovado" : "Aprovar") : (on ? "Descartado" : "Descartar");
    });
  }

  // faixa "LEGENDA" arrastável sobre o vídeo: posição livre, salva só neste clipe
  function bindCapMarker(box, m) {
    const mk = $("#capMarker", box), stage = mk.parentElement, info = $("#capPosInfo", box), reset = $("#capPosReset", box);
    const LO = 0.02, HI = 0.98;
    let pos = m.caption_pos ?? state.capStyle.position ?? 0.7, saveTimer = 0;
    const paint = () => {
      const media = stage.firstElementChild;  // vídeo ou quadro vazio: a faixa segue a área do vídeo 9:16
      mk.style.left = media.offsetLeft + "px";
      mk.style.width = media.offsetWidth + "px";
      mk.style.top = media.offsetTop + pos * media.offsetHeight + "px";
      mk.setAttribute("aria-valuenow", Math.round(pos * 100));
      mk.setAttribute("aria-valuetext", `${Math.round(pos * 100)}% da altura`);
      info.textContent = `Altura: ${Math.round(pos * 100)}% ${m.caption_pos != null ? "· só neste clipe" : "· padrão de Ajustes"}`;
      reset.hidden = m.caption_pos == null;
      live?.setPosition(pos);  // a legenda da prévia anda junto com a faixa
    };
    const save = () => {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(async () => {
        try { await patch(m.id, { caption_pos: +pos.toFixed(3) }); paint(); toast("Posição salva. “Gerar de novo” aplica no vídeo.", "good"); }
        catch (err) { toast(err.message, "bad"); }
      }, 250);
    };
    mk.onpointerdown = (e) => {
      e.preventDefault();
      mk.setPointerCapture(e.pointerId);
      mk.classList.add("dragging");
      const h = stage.firstElementChild.offsetHeight, y0 = e.clientY, p0 = pos;  // 1:1 a partir de onde foi pega
      mk.onpointermove = (ev) => { pos = Math.min(HI, Math.max(LO, p0 + (ev.clientY - y0) / h)); paint(); };
      mk.onpointerup = mk.onpointercancel = () => {
        mk.onpointermove = mk.onpointerup = mk.onpointercancel = null;
        mk.classList.remove("dragging");
        if (pos !== p0) save();
      };
    };
    mk.onkeydown = (e) => {
      const step = e.shiftKey ? 0.05 : 0.01;
      if (e.key === "ArrowUp") pos = Math.max(LO, pos - step);
      else if (e.key === "ArrowDown") pos = Math.min(HI, pos + step);
      else return;
      e.preventDefault(); paint(); save();
    };
    reset.onclick = async () => {
      try { await patch(m.id, { reset_caption_pos: true }); delete m.caption_pos; pos = state.capStyle.position ?? 0.7; paint(); }
      catch (err) { toast(err.message, "bad"); }
    };
    const media = stage.firstElementChild;
    if (media.tagName === "VIDEO") media.addEventListener("loadedmetadata", paint, { once: true });
    requestAnimationFrame(paint);
  }

  function renderDetail() {
    const box = $("#detail", root);
    const m = d.moments.find((x) => x.id === state.selected);
    if (!m) { box.innerHTML = '<div class="empty">Escolha um clipe na lista.</div>'; return; }
    const ready = (d.clips || {})[m.id]?.final;
    const view = ready ? (ui.view || "clip") : "live";
    const capOn = m.captions ?? state.capStyle.enabled ?? true;
    const hookOn = m.hook_on ?? state.capStyle.hook ?? true;
    const posts = (m.meta && m.meta.posts) || {};
    const capText = m.caption_words?.length ? m.caption_words.map((w) => w.w).join(" ") : (m.text || "");
    box.innerHTML = `
      <div class="row" style="align-items:flex-start;gap:14px">
        <div class="${scoreClass(m.score)}"><span class="num">${m.score}</span><small>nota</small></div>
        <div style="flex:1;min-width:0"><h2 class="t-title">${esc(title(m))}</h2>
          <div class="m-meta"><span class="t-small num">${fmt(m.start)} – ${fmt(m.end)} · ${Math.round(m.end - m.start)} s</span>${aiTag(m)}</div></div>
      </div>
      <div class="row" style="margin:14px 0">
        <button class="btn ${m.status === "approved" ? "on" : ""}" data-status="approved" aria-pressed="${m.status === "approved"}">${m.status === "approved" ? "✓ Aprovado" : "Aprovar"}</button>
        <button class="btn ${m.status === "discarded" ? "on" : ""}" data-status="discarded" aria-pressed="${m.status === "discarded"}">${m.status === "discarded" ? "Descartado" : "Descartar"}</button>
        <span class="spacer"></span>
        <button class="btn" id="expOne" title="Gera o MP4 final e os textos de cada rede deste clipe agora">⬇ Exportar este clipe</button>
        <button class="btn primary" id="gen">${ready ? "Gerar de novo" : "Gerar clipe"}</button>
      </div>
      <div class="row" style="align-items:flex-start;gap:18px">
        <div class="stack" style="flex:none;gap:8px">
          ${ready ? `<div class="seg pv-switch" role="group" aria-label="O que mostrar">
              <button data-view="live" aria-pressed="${view === "live"}">Prévia ao vivo</button>
              <button data-view="clip" aria-pressed="${view === "clip"}">Clipe gerado</button></div>` : ""}
          <div class="cap-stage">${view === "clip"
            ? `<video class="vertical" style="height:440px" controls preload="metadata" src="${P("/clips/")}${enc(m.id)}?t=${Date.now()}"></video>`
            : `<canvas class="preview-9x16 live-pv" id="livePv" width="540" height="960" role="img"
                 aria-label="Prévia ao vivo do clipe: clique para tocar ou pausar"></canvas>`}
            <div class="cap-marker" id="capMarker" role="slider" tabindex="0" aria-label="Posição da legenda neste clipe"
                 aria-valuemin="2" aria-valuemax="98" ${ui.tab === "legenda" ? "" : "hidden"}><span>↕ LEGENDA</span></div></div>
          ${view === "live" ? `<div class="lp-bar" id="lpBar"><button class="lp-play" type="button">▶</button>
              <input class="lp-seek" type="range" min="0" step="0.05" value="0" aria-label="Posição na prévia">
              <span class="lp-time num"></span></div>
            <div class="t-small" style="width:248px">Prévia ao vivo: mostra na hora a legenda, o gancho e o layout.
              ${ready ? "Mudou algo? “Gerar de novo” aplica no MP4." : "O MP4 só é feito em “Gerar clipe”."}</div>` : ""}
        </div>
        <div style="flex:1;min-width:260px">
          <div class="tabs" role="tablist">
            ${[["corte", "Corte"], ["textos", "Textos"], ["legenda", "Legenda"], ["faixa", "Faixa"]].map(([k, l]) =>
              `<button class="tab" role="tab" data-tab="${k}" aria-selected="${ui.tab === k}">${l}</button>`).join("")}
          </div>
          <div data-pane="corte" ${ui.tab === "corte" ? "" : "hidden"} class="stack">
            <video id="cutSrc" controls preload="metadata" src="${P("/video")}#t=${m.start}"></video>
            <div class="row">
              <div class="field" style="width:110px"><label for="cs">Início (s)</label><input class="input num" id="cs" type="number" step="0.1" value="${m.start}"></div>
              <button class="btn small" data-n="cs:-1">−1 s</button><button class="btn small" data-n="cs:1">+1 s</button>
              <button class="btn small" data-here="cs">Início = aqui</button></div>
            <div class="row">
              <div class="field" style="width:110px"><label for="ce">Fim (s)</label><input class="input num" id="ce" type="number" step="0.1" value="${m.end}"></div>
              <button class="btn small" data-n="ce:-1">−1 s</button><button class="btn small" data-n="ce:1">+1 s</button>
              <button class="btn small" data-here="ce">Fim = aqui</button></div>
            <div class="row"><button class="btn primary" id="saveCut">Salvar corte</button>
              <span class="t-small">Depois, “Gerar de novo” para aplicar.</span></div>
          </div>
          <div data-pane="textos" ${ui.tab === "textos" ? "" : "hidden"} class="stack">
            <div class="field"><label for="hook">Gancho (texto nos 3 primeiros segundos)</label><input class="input" id="hook" value="${esc((m.meta && m.meta.hook) || m.hook || "")}"></div>
            <label class="switch"><input type="checkbox" id="hookOn" ${hookOn ? "checked" : ""}><span class="track"></span><span>Mostrar o gancho no vídeo</span></label>
            <div class="seg" role="group" aria-label="Rede social">${PLATS.map(([k, l]) => `<button data-plat="${k}" aria-pressed="${k === ui.plat}">${l}</button>`).join("")}</div>
            ${PLATS.map(([k]) => { const p = posts[k] || {}; return `<div class="stack" data-platpane="${k}" ${k === ui.plat ? "" : "hidden"}>
              <div class="field"><label>Título</label><input class="input" data-f="title" value="${esc(p.title || "")}"></div>
              <div class="field"><label>Descrição</label><textarea class="textarea" data-f="description">${esc(p.description || "")}</textarea></div>
              <div class="field"><label>Hashtags${k === "reels" ? " (o Instagram aceita até 5)" : ""}</label><input class="input" data-f="hashtags" value="${esc((p.hashtags || []).join(" "))}"></div>
              ${k === "shorts" ? `<div class="field"><label>Tags do YouTube (palavras-chave de busca, separadas por vírgula)</label>
                <input class="input" data-f="tags" value="${esc((p.tags || []).join(", "))}"></div>` : ""}
              <div class="row"><button class="btn small" data-copy="${k}">Copiar tudo</button></div></div>`; }).join("")}
            <div class="row"><button class="btn primary" id="saveTexts">Salvar textos</button>
              <button class="btn" id="aiTexts">✨ Escrever com IA</button>
              ${m.meta ? `<span class="badge ${m.meta.ai ? "info" : ""}">${m.meta.ai ? "textos da IA" : "textos simples"}</span>` : '<span class="t-small">sem textos ainda</span>'}</div>
          </div>
          <div data-pane="legenda" ${ui.tab === "legenda" ? "" : "hidden"} class="stack">
            <label class="switch"><input type="checkbox" id="clipCap" ${capOn ? "checked" : ""}><span class="track"></span><span>Legenda neste clipe</span></label>
            <div class="note info"><span>↕</span><span>Arraste a faixa <b>LEGENDA</b> em cima do vídeo para onde quiser (ou use ↑ ↓).
              <br><span id="capPosInfo" class="num"></span>
              <button class="btn small plain" id="capPosReset" ${m.caption_pos == null ? "hidden" : ""}>Usar o padrão de Ajustes</button></span></div>
            <div class="field"><label for="capText">Texto da legenda (corrija palavras; os tempos são reaproveitados)</label>
              <textarea class="textarea" id="capText" style="min-height:140px">${esc(capText)}</textarea></div>
            <div class="row"><button class="btn primary" id="saveCap">Salvar legenda</button>
              <button class="btn plain" id="resetCap" ${m.caption_words ? "" : "disabled"}>Voltar ao texto original</button></div>
          </div>
          <div data-pane="faixa" ${ui.tab === "faixa" ? "" : "hidden"} class="stack" id="bandPane">
            <div class="empty"><span class="spinner"></span></div></div>
        </div>
      </div>
      ${m.description || m.criteria ? `<div class="divider" style="margin:16px 0"></div>
        <div class="m-desc">${esc(m.description || "")}</div>${critBars(m)}` : ""}`;

    box.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => {  // troca de aba sem redesenhar (mantém o que foi digitado)
      ui.tab = b.dataset.tab;
      box.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", x === b));
      box.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== ui.tab; });
      $("#capMarker", box).hidden = ui.tab !== "legenda";
    });
    bindCapMarker(box, m);
    live?.destroy();
    live = null;
    if (view === "live") {
      const cv = $("#livePv", box), bar = $("#lpBar", box);
      api(P("/moments/") + enc(m.id) + "/preview").then((spec) => {
        if (!cv.isConnected) return;  // trocou de clipe antes de chegar
        live = mountLivePreview(cv, bar, P("/video"), spec);
      }).catch((err) => { $(".lp-time", bar).textContent = "Prévia indisponível: " + err.message; });
    }
    box.querySelectorAll("[data-view]").forEach((b) => b.onclick = () => { ui.view = b.dataset.view; renderDetail(); });
    box.querySelectorAll("[data-status]").forEach((b) => b.onclick = async () => {
      const status = m.status === b.dataset.status ? "pending" : b.dataset.status;
      try { await patch(m.id, { status }); renderList(); markStatus(box, m); } catch (err) { toast(err.message, "bad"); }
    });
    $("#gen", box).onclick = () => startJob({ kind: "render", project: name, ids: [m.id] });
    $("#expOne", box).onclick = async () => {  // exporta só este clipe, sem esperar os outros
      const cs = +$("#cs", box).value, ce = +$("#ce", box).value;
      if (Math.abs(cs - m.start) > 0.01 || Math.abs(ce - m.end) > 0.01) {  // corte mudado e não salvo
        if (!confirmAction("Você mudou o corte e não salvou. Salvar o corte novo e exportar?")) return;
        try { await patch(m.id, { start: cs, end: ce }); } catch (err) { toast(err.message, "bad"); return; }
      }
      let chk;
      try { chk = await api(P("/export-check")); } catch (err) { toast(err.message, "bad"); return; }
      if (chk.unauthorized && !confirmAction("O streamer não está marcado como autorizado. Exportar mesmo assim?")) return;
      const job = await startJob({ kind: "export", project: name, ids: [m.id] });
      const r = job && job.status === "done" ? job.result : null;
      if (!r) return;
      if (r.errors && r.errors.length) { toast("Não exportou: " + r.errors[0].error, "bad"); return; }
      toast("Clipe exportado: MP4 + textos de cada rede. Abrindo a pasta…", "good");
      api(P("/open-exports"), { method: "POST" }).catch(() => {});
    };
    // corte
    const src = $("#cutSrc", box);
    box.querySelectorAll("[data-n]").forEach((b) => b.onclick = () => {
      const [id, dlt] = b.dataset.n.split(":");
      $("#" + id, box).value = Math.max(0, +$("#" + id, box).value + +dlt).toFixed(1);
    });
    box.querySelectorAll("[data-here]").forEach((b) => b.onclick = () => { $("#" + b.dataset.here, box).value = src.currentTime.toFixed(1); });
    $("#saveCut", box).onclick = async () => {
      try { await patch(m.id, { start: +$("#cs", box).value, end: +$("#ce", box).value }); toast("Corte salvo. Gere o clipe de novo para aplicar.", "good"); renderList(); renderDetail(); }
      catch (err) { toast(err.message, "bad"); }
    };
    // textos
    box.querySelectorAll("[data-plat]").forEach((b) => b.onclick = () => {
      ui.plat = b.dataset.plat;
      box.querySelectorAll("[data-plat]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      box.querySelectorAll("[data-platpane]").forEach((p) => { p.hidden = p.dataset.platpane !== ui.plat; });
    });
    box.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
      const pane = box.querySelector(`[data-platpane="${b.dataset.copy}"]`);
      const v = (f) => pane.querySelector(`[data-f="${f}"]`).value;
      const tags = pane.querySelector('[data-f="tags"]');
      const parts = [v("title"), "", v("description"), "", v("hashtags")].concat(tags ? ["", "Tags: " + tags.value] : []);
      try { await navigator.clipboard.writeText(parts.join(String.fromCharCode(10))); toast("Copiado.", "good"); }
      catch (err) { toast("Não consegui copiar.", "bad"); }
    });
    $("#hookOn", box).onchange = (e) => patch(m.id, { hook_on: e.target.checked }).then(() => refreshLive(m.id))
      .catch((err) => toast(err.message, "bad"));
    $("#hook", box).oninput = (e) => { if ($("#hookOn", box).checked) live?.setHook(e.target.value); };
    $("#saveTexts", box).onclick = async () => {
      const out = {};
      box.querySelectorAll("[data-platpane]").forEach((p) => {
        const v = (f) => p.querySelector(`[data-f="${f}"]`).value;
        out[p.dataset.platpane] = { title: v("title"), description: v("description"), hashtags: v("hashtags").split(/[ ,]+/).filter(Boolean) };
        const tags = p.querySelector('[data-f="tags"]');
        if (tags) out[p.dataset.platpane].tags = tags.value.split(",").map((t) => t.trim()).filter(Boolean);
      });
      try { await patch(m.id, { meta: { hook: $("#hook", box).value, posts: out } }); toast("Textos salvos.", "good"); renderDetail(); }
      catch (err) { toast(err.message, "bad"); }
    };
    $("#aiTexts", box).onclick = () => {
      if (m.meta && !confirmAction("Este clipe já tem textos (talvez editados por você). A IA vai substituir. Continuar?")) return;
      startJob({ kind: "posts", project: name, ids: [m.id] });
    };
    // legenda
    $("#clipCap", box).onchange = (e) => patch(m.id, { captions: e.target.checked }).then(() => refreshLive(m.id))
      .catch((err) => toast(err.message, "bad"));
    $("#saveCap", box).onclick = async () => {
      try { await patch(m.id, { caption_text: $("#capText", box).value }); toast("Legenda salva. Gere o clipe de novo para aplicar.", "good"); renderDetail(); }
      catch (err) { toast(err.message, "bad"); }
    };
    $("#resetCap", box).onclick = async () => {
      try { await patch(m.id, { reset_caption: true }); delete m.caption_words; renderDetail(); } catch (err) { toast(err.message, "bad"); }
    };
    bandControls($("#bandPane", box), m);
  }

  // faixa da divisa: imagem entre a câmera e o jogo, ajustada olhando a prévia ao vivo (vale para todos os clipes)
  async function bandControls(pane, m) {
    let b;
    try { b = await api("/api/divider-band"); } catch (e) { pane.innerHTML = `<div class="note bad">⚠ ${esc(e.message)}</div>`; return; }
    if (!pane.isConnected) return;
    const pick = async () => {
      const r = await api("/api/pick-file?kind=image", { method: "POST" }).catch(() => null);
      if (!r || !r.path) return;
      try { await api("/api/divider-band/image", { method: "POST", body: { path: r.path } }); toast("Faixa pronta. Ajuste olhando a prévia.", "good"); }
      catch (e) { toast(e.message, "bad"); return; }
      await refreshLive(m.id); bandControls(pane, m);
    };
    if (!b.has_image) {
      pane.innerHTML = `<div class="note info"><span>🖼</span><span>Escolha a imagem da faixa: um <b>PNG com fundo transparente</b>.
        Pode ser do tamanho do vídeo (1080×1920) com a faixa no meio: o app usa só a parte visível e põe na divisa
        entre a câmera e o jogo.</span></div>
        <div class="row"><button class="btn primary" id="bandPick">Escolher imagem PNG…</button></div>`;
      $("#bandPick", pane).onclick = pick;
      return;
    }
    const pct = Math.round(b.scale * 100);
    pane.innerHTML = `
      <div class="band-thumb"><img src="${esc(b.image_url)}" alt="Imagem da faixa da divisa"></div>
      <label class="switch"><input type="checkbox" id="bandOn" ${b.enabled ? "checked" : ""}><span class="track"></span>
        <span>Faixa no vídeo <span class="t-small">(todos os clipes)</span></span></label>
      <div class="field"><label for="bandOff">Altura <span class="t-small num" id="bandOffV"></span></label>
        <input type="range" id="bandOff" min="-700" max="700" step="2" value="${b.offset}" ${b.enabled ? "" : "disabled"}></div>
      <div class="field"><label for="bandSc">Tamanho <span class="t-small num" id="bandScV"></span></label>
        <input type="range" id="bandSc" min="50" max="150" step="1" value="${pct}" ${b.enabled ? "" : "disabled"}></div>
      <div class="row"><button class="btn small" id="bandReset" ${b.enabled ? "" : "disabled"}>Centralizar na divisa</button>
        <button class="btn small plain" id="bandPick">Trocar imagem…</button></div>
      ${document.getElementById("livePv") ? "" : '<div class="note warn"><span>👁</span><span>Clique em <b>Prévia ao vivo</b> (ao lado do vídeo) para ver a faixa enquanto ajusta.</span></div>'}
      <div class="t-small">Ajuste olhando a <b>prévia ao vivo</b>: no layout câmera + jogo ela começa na divisa; nos outros,
        mova a <b>Altura</b> até onde quiser (o gancho desce para não ficar embaixo da faixa).
        Salva sozinho. “Gerar de novo” aplica no MP4; exportar e a fila já usam o ajuste novo.</div>`;
    const off = $("#bandOff", pane), sc = $("#bandSc", pane);
    const label = () => {
      $("#bandOffV", pane).textContent = +off.value === 0 ? "· no meio da divisa" : `· ${+off.value > 0 ? "+" : ""}${off.value} px ${+off.value > 0 ? "para baixo" : "para cima"}`;
      $("#bandScV", pane).textContent = `· ${sc.value}%`;
    };
    label();
    let timer = null;
    const save = () => {
      clearTimeout(timer);
      timer = setTimeout(() => api("/api/divider-band", { method: "PUT", body: { offset: +off.value, scale: +sc.value / 100 } })
        .catch((e) => toast(e.message, "bad")), 400);
    };
    const adjust = () => { label(); live?.setBand({ offset: +off.value, scale: +sc.value / 100 }); save(); };
    off.oninput = adjust;
    sc.oninput = adjust;
    $("#bandReset", pane).onclick = () => { off.value = 0; sc.value = 100; adjust(); };
    $("#bandPick", pane).onclick = pick;
    $("#bandOn", pane).onchange = async (e) => {
      try { await api("/api/divider-band", { method: "PUT", body: { enabled: e.target.checked } }); }
      catch (err) { e.target.checked = !e.target.checked; toast(err.message, "bad"); return; }
      off.disabled = sc.disabled = $("#bandReset", pane).disabled = !e.target.checked;
      refreshLive(m.id);
    };
  }

  renderList();
  renderDetail();
}
