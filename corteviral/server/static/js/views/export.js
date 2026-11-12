// Etapa ④ Exportar: aprovados, fila de postagem programada, exportação em lote e envio imediato.
import { $, api, esc, projectApi } from "../api.js";
import { startJob } from "../jobs.js";
import { go } from "../router.js";
import { openLayout } from "../sheets/layout.js";
import { state } from "../state.js";
import { confirmAction, toast } from "../ui.js";

const NET_SHORT = { youtube: "YT", tiktok: "TT", instagram: "IG" };
const INTERVAL_KEY = "cv.queueInterval";
let queueTimer = null;

function pubBadges(entries) {  // status por rede: publicado / enviado / falhou
  return Object.entries(entries || {}).map(([net, e]) => {
    const cls = e.status === "published" ? "good" : e.status === "error" ? "bad" : "info";
    const sym = e.status === "published" ? "✓" : e.status === "error" ? "⚠" : "→";
    const tip = e.status === "error" ? e.error : e.status === "published" ? `publicado${e.note ? " · " + e.note : ""}` : "enviado ao n8n";
    const badge = `<span class="badge ${cls}" title="${esc(tip || "")}">${sym} ${NET_SHORT[net] || esc(net)}</span>`;
    return e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${badge}</a>` : badge;
  }).join("");
}

// 19:05 · amanhã 08:30 · 14/11 09:00
function when(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86400000);
  if (diff === 0) return hm;
  if (diff === 1) return `amanhã ${hm}`;
  return `${d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} ${hm}`;
}

const titleOf = (m) => (m.meta && m.meta.posts && ((m.meta.posts.shorts && m.meta.posts.shorts.title) ||
  (m.meta.posts.tiktok && m.meta.posts.tiktok.title))) || (m.meta && m.meta.hook) || m.description || m.reason || m.id;

function readInterval() {
  try { const v = parseInt(localStorage.getItem(INTERVAL_KEY), 10); return v >= 1 && v <= 1440 ? v : 15; } catch (e) { return 15; }
}

export async function renderExport(root, name, project, ctx = {}) {
  const P = projectApi(name);
  let d = null, chk = null, pub = {}, pcfg = null, queue = null;
  try { d = await api(P("/moments")); } catch (e) { /* sem momentos */ }
  try { [pub, pcfg] = await Promise.all([api(P("/publish")), api("/api/publish")]); } catch (e) { /* sem postagem */ }
  try { queue = await api(`/api/schedule?project=${encodeURIComponent(name)}`); } catch (e) { /* sem fila */ }
  try { chk = await api(P("/export-check")); } catch (e) { if (d) toast("Não consegui checar a exportação: " + e.message, "bad"); }
  if (!d) {
    root.innerHTML = `<div class="card"><div class="empty"><div class="big">📦</div>Encontre e aprove momentos antes de exportar.
      <button class="btn primary" id="toMoments">Ir para Momentos</button></div></div>`;
    $("#toMoments", root).onclick = () => go(name, "moments");
    return;
  }
  const approved = d.moments.filter((m) => m.status === "approved").sort((a, b) => b.score - a.score);
  const netsOn = pcfg ? Object.entries(pcfg.config.networks).filter(([, on]) => on).map(([k]) => pcfg.networks[k]) : [];
  const noTexts = approved.filter((m) => !m.meta).length;
  const items = (queue && queue.items) || [];
  const waitingBy = Object.fromEntries(items.filter((i) => i.status === "waiting").map((i) => [i.clip_id, i]));
  const wentOut = (m) => Object.values(pub[m.id] || {}).some((e) => e.status === "published" || e.status === "sent");
  const toQueue = approved.filter((m) => !waitingBy[m.id] && !wentOut(m));
  // ordem da lista: os que estão na fila, do que sai primeiro ao último; depois os demais (melhor nota antes)
  approved.sort((a, b) => {
    const ta = waitingBy[a.id] ? waitingBy[a.id].at : Infinity, tb = waitingBy[b.id] ? waitingBy[b.id].at : Infinity;
    return ta !== tb ? ta - tb : b.score - a.score;
  });
  const sum = queue ? queue.summary : null;

  root.innerHTML = `
    <div class="exp-grid">
      <section class="card pad stack exp-list">
        <div class="row"><h2 class="t-head">Aprovados para postar</h2><span class="spacer"></span>
          <span class="badge ${approved.length ? "good" : ""}">${approved.length} clipe(s)</span></div>
        ${approved.length ? `<ol class="exp-rows">${approved.map((m, i) => `<li class="exp-row">
            <b class="num exp-n">${String(i + 1).padStart(2, "0")}</b>
            <div class="exp-main">
              <div class="exp-title" title="${esc(titleOf(m))}">${esc(titleOf(m))}</div>
              <div class="exp-meta">
                <span class="t-small num">${Math.round(m.end - m.start)} s</span>
                ${m.meta ? '<span class="badge info">textos ✓</span>' : '<span class="badge">sem textos</span>'}
                ${waitingBy[m.id] ? `<span class="badge accent" title="na fila de postagem">⏱ ${esc(when(waitingBy[m.id].at))}</span>` : ""}
                ${pubBadges(pub[m.id])}
              </div>
            </div></li>`).join("")}</ol>`
          : `<div class="empty"><div class="big">☑︎</div>Nenhum clipe aprovado ainda.<button class="btn" id="toClips">Revisar clipes</button></div>`}
      </section>

      <aside class="stack exp-side">
        ${chk && chk.unauthorized ? `<div class="note warn"><span>⚠</span><span>Este streamer não está marcado como <b>autorizado</b>.
          Postar cortes sem autorização pode derrubar o vídeo ou desmonetizar o canal.
          <button class="btn small" id="auth" style="margin-top:8px">Marcar autorização no layout…</button></span></div>` : ""}
        ${chk && chk.duplicates.length ? `<div class="note warn"><span>⚠</span><span>${chk.duplicates.length} trecho(s) já foram exportados antes.</span></div>` : ""}
        ${noTexts ? `<div class="note info"><span>ℹ</span><span>${noTexts} clipe(s) sem textos de postagem. A IA escreve antes de programar ou exportar.
          <button class="btn small" id="aiAll" style="margin-top:8px">✨ Escrever agora com IA</button></span></div>` : ""}

        <section class="card pad stack">
          <div class="row"><h2 class="t-head">⏱ Programar postagem</h2><span class="spacer"></span>
            ${sum && sum.waiting ? `<span class="badge accent">${sum.waiting} na fila</span>` : ""}</div>
          ${netsOn.length ? `
            <div class="muted">Solta <b>1 clipe por vez</b> em <b>${esc(netsOn.join(", "))}</b>, com título, descrição,
              hashtags e tags prontos.</div>
            <div class="row" style="align-items:flex-end">
              <div class="field" style="width:120px"><label for="qInt">A cada (min)</label>
                <input class="input num" id="qInt" type="number" min="1" max="1440" step="1" value="${readInterval()}"></div>
              <div class="seg" role="group" aria-label="Intervalos rápidos">
                ${[15, 30, 60].map((v) => `<button data-int="${v}" aria-pressed="${readInterval() === v}">${v} min</button>`).join("")}</div>
            </div>
            <div class="t-small" id="qWhen" aria-live="polite"></div>
            <button class="btn primary large" id="qGo" ${toQueue.length ? "" : "disabled"}>Programar ${toQueue.length} clipe(s)</button>
            ${items.length ? `<ol class="q-list">${items.map((it) => {
              const m = d.moments.find((x) => x.id === it.clip_id) || d.all?.find((x) => x.id === it.clip_id) || { id: it.clip_id };
              const late = it.status === "waiting" && it.at < (queue.now || Date.now() / 1000) - 60;
              const st = it.status === "waiting" ? (late ? '<span class="badge warn">atrasado</span>' : "")
                : it.status === "sent" ? '<span class="badge good">enviado</span>'
                : `<span class="badge bad" title="${esc(it.error || "")}">falhou</span>`;
              return `<li class="q-row ${it.status !== "waiting" ? "done" : ""}">
                <span class="num q-time">${esc(when(it.status === "waiting" ? it.at : (it.sent_at || it.at)))}</span>
                <span class="q-title" title="${esc(titleOf(m))}">${esc(titleOf(m))}</span>${st}
                ${it.status === "waiting" ? `<button class="btn plain small q-x" data-cancel="${esc(it.id)}" aria-label="Tirar da fila" title="Tirar da fila">✕</button>` : ""}
              </li>`; }).join("")}</ol>
              ${items.some((i) => i.status === "waiting") ? '<button class="btn plain small" id="qClear">Cancelar a fila deste vídeo</button>' : ""}` : ""}
            <div class="t-small">Deixe o Corte Viral aberto: na hora marcada ele liga o n8n sozinho e envia.
              Se o app ficar fechado, os atrasados saem um por vez quando você abrir.</div>`
          : `<div class="muted">Nenhuma rede ligada ainda. Conecte YouTube, TikTok e Instagram em <b>Contas conectadas</b>.</div>
             <a class="btn" href="#/contas">Contas conectadas ›</a>`}
        </section>

        <section class="card pad stack">
          <h2 class="t-head">📦 Exportar arquivos</h2>
          <div class="muted">Uma pasta por clipe com o <b>MP4 1080×1920</b> e <b>um .txt por rede</b> (título, descrição,
            hashtags e tags), prontos para postar à mão.</div>
          <div class="t-small exp-folder num" title="${esc(chk ? chk.folder : "exports")}">${esc(chk ? chk.folder : "exports")}</div>
          <div class="row">
            <button class="btn primary" id="exp" ${approved.length ? "" : "disabled"}>Exportar ${approved.length} clipe(s)</button>
            <button class="btn" id="openDir">Abrir pasta</button></div>
          <div id="result"></div>
        </section>

        ${netsOn.length ? `<section class="card pad stack">
          <h2 class="t-head">🚀 Postar agora</h2>
          <div class="muted">Manda todos os aprovados de uma vez para o n8n.
            ${pcfg.config.auto_send ? "Também acontece sozinho a cada exportação." : ""}</div>
          <div class="row"><button class="btn" id="pubBtn" ${approved.length ? "" : "disabled"}>Enviar ${approved.length} clipe(s) agora</button>
            <a class="btn plain" href="#/contas">Contas conectadas ›</a></div>
        </section>` : ""}
      </aside>
    </div>`;

  $("#toClips", root)?.addEventListener("click", () => go(name, "clips"));
  $("#auth", root)?.addEventListener("click", (e) => openLayout(e.currentTarget, name, ctx.rerender));
  $("#aiAll", root)?.addEventListener("click", () => startJob({ kind: "posts", project: name, ids: approved.map((m) => m.id) }));
  const openDir = () => api(P("/open-exports"), { method: "POST" }).catch((e) => toast(e.message, "bad"));
  $("#openDir", root).onclick = openDir;

  // --- fila programada ---
  const qInt = $("#qInt", root);
  const showWhen = () => {
    if (!qInt) return;
    const v = parseInt(qInt.value, 10);
    const ok = v >= 1 && v <= 1440;
    root.querySelectorAll("[data-int]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.int === v)));
    const el = $("#qWhen", root);
    if (!ok) { el.textContent = "Use de 1 a 1440 minutos."; return; }
    if (!toQueue.length) { el.textContent = approved.length ? "Todos os aprovados já estão na fila ou já saíram." : ""; return; }
    const nowS = Date.now() / 1000;
    let start = nowS;
    if (sum && sum.ends_at) start = Math.max(start, sum.ends_at + v * 60);
    else if (sum && sum.last_sent_at) start = Math.max(start, sum.last_sent_at + v * 60);
    const end = start + (toQueue.length - 1) * v * 60;
    el.innerHTML = (sum && sum.ends_at ? `A fila atual termina às <b>${esc(when(sum.ends_at))}</b>. ` : "") +
      `${start - nowS < 60 ? "O primeiro sai <b>agora</b>" : `O primeiro sai às <b>${esc(when(start))}</b>`}` +
      (toQueue.length > 1 ? ` e o último às <b>${esc(when(end))}</b>.` : ".");
  };
  if (qInt) {
    qInt.oninput = () => { try { localStorage.setItem(INTERVAL_KEY, qInt.value); } catch (e) { /* sem storage */ } showWhen(); };
    root.querySelectorAll("[data-int]").forEach((b) => b.onclick = () => { qInt.value = b.dataset.int; qInt.oninput(); });
    showWhen();
  }
  $("#qGo", root)?.addEventListener("click", async () => {
    const v = parseInt(qInt.value, 10);
    if (!(v >= 1 && v <= 1440)) { qInt.focus(); toast("Intervalo inválido: use de 1 a 1440 minutos.", "bad"); return; }
    if (chk && chk.unauthorized && !confirmAction("O streamer não está marcado como autorizado. Programar mesmo assim?")) return;
    await startJob({ kind: "schedule", project: name, ids: toQueue.map((m) => m.id), interval_min: v });
  });
  root.querySelectorAll("[data-cancel]").forEach((b) => b.onclick = async () => {
    try { await api(`/api/schedule/${encodeURIComponent(b.dataset.cancel)}`, { method: "DELETE" }); ctx.rerender?.(); }
    catch (e) { toast(e.message, "bad"); }
  });
  $("#qClear", root)?.addEventListener("click", async () => {
    if (!confirmAction("Tirar da fila todos os clipes deste vídeo que ainda não saíram?")) return;
    try { await api(`/api/schedule?project=${encodeURIComponent(name)}`, { method: "DELETE" }); ctx.rerender?.(); }
    catch (e) { toast(e.message, "bad"); }
  });
  clearInterval(queueTimer);  // um só timer, mesmo redesenhando a tela várias vezes
  if (items.some((i) => i.status === "waiting")) {  // atualiza a fila enquanto a tela está aberta
    queueTimer = setInterval(() => {
      if (!root.isConnected || !root.querySelector(".exp-grid")) { clearInterval(queueTimer); return; }
      if (!root.querySelector(".exp-side input:focus")) ctx.rerender?.();
    }, 60000);
  }

  // --- exportar / postar agora ---
  $("#exp", root).onclick = async () => {
    let now;
    try { now = await api(P("/export-check")); } catch (e) { toast(e.message, "bad"); return; }  // sempre a checagem atual
    if (now.unauthorized && !confirmAction("O streamer não está marcado como autorizado. Exportar mesmo assim?")) return;
    if (now.duplicates.length && !confirmAction(`${now.duplicates.length} trecho(s) já foram exportados antes. Exportar de novo?`)) return;
    delete state.exportResults[name];
    await startJob({ kind: "export", project: name });  // ao terminar, a tela é redesenhada e mostra o resultado
  };
  $("#pubBtn", root)?.addEventListener("click", async () => {
    const already = approved.filter(wentOut).length;
    let force = false;
    if (already) force = confirmAction(`${already} clipe(s) já foram enviados ou publicados em alguma rede. Mandar de novo nessas redes também?\n(OK = publicar de novo · Cancelar = só onde ainda não saiu)`);
    await startJob({ kind: "publish", project: name, ids: approved.map((m) => m.id), force });
  });
  if (state.exportResults[name]) showResult(state.exportResults[name]);
  function showResult(job) {
    const r = job.result || {};
    $("#result", root).innerHTML = `<div class="note ${(r.errors || []).length ? "warn" : "good"}"><span>${(r.errors || []).length ? "⚠" : "✓"}</span>
      <span>${(r.files || []).length} clipe(s) exportado(s)${(r.errors || []).length ? ` · ${r.errors.length} falharam:` : "."}
      ${(r.errors || []).map((e) => `<br>• ${esc(e.id)}: ${esc(e.error)}`).join("")}
      ${r.publish ? `<br>Postagem: ${r.publish.sent || 0} clipe(s) entregues ao n8n${(r.publish.errors || []).length
        ? ` · ${r.publish.errors.length} falharam: ${esc(r.publish.errors[0].error)}` : ""}.` : ""}
      <br><button class="btn small" id="openRes" style="margin-top:8px">Abrir pasta</button></span></div>`;
    $("#openRes", root).onclick = openDir;
  }
}
