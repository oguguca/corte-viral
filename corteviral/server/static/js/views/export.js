// Etapa ④ Exportar: resumo dos aprovados, avisos e exportação em lote.
import { $, api, esc, projectApi } from "../api.js";
import { startJob } from "../jobs.js";
import { go } from "../router.js";
import { openLayout } from "../sheets/layout.js";
import { state } from "../state.js";
import { confirmAction, toast } from "../ui.js";

const NET_SHORT = { youtube: "YT", tiktok: "TT", instagram: "IG" };

function pubBadges(entries) {  // status por rede: publicado / enviado / falhou
  return Object.entries(entries || {}).map(([net, e]) => {
    const cls = e.status === "published" ? "good" : e.status === "error" ? "bad" : "info";
    const sym = e.status === "published" ? "✓" : e.status === "error" ? "⚠" : "→";
    const tip = e.status === "error" ? e.error : e.status === "published" ? "publicado" : "enviado ao n8n";
    const badge = `<span class="badge ${cls}" title="${esc(tip || "")}">${sym} ${NET_SHORT[net] || esc(net)}</span>`;
    return e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${badge}</a>` : badge;
  }).join("");
}

export async function renderExport(root, name, project, ctx = {}) {
  const P = projectApi(name);
  let d = null, chk = null, pub = {}, pcfg = null;
  try { d = await api(P("/moments")); } catch (e) { /* sem momentos */ }
  try { [pub, pcfg] = await Promise.all([api(P("/publish")), api("/api/publish")]); } catch (e) { /* sem postagem */ }
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
  root.innerHTML = `
    <div class="grid-2">
      <section class="card pad stack">
        <div class="row"><h2 class="t-head">Aprovados para postar</h2><span class="spacer"></span>
          <span class="badge ${approved.length ? "good" : ""}">${approved.length} clipe(s)</span></div>
        ${approved.length ? `<ul class="list">${approved.map((m, i) => `<li class="row" style="padding:10px 12px;border-radius:12px;background:var(--surface-2)">
            <b class="num" style="width:26px">${String(i + 1).padStart(2, "0")}</b>
            <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((m.meta && m.meta.posts && m.meta.posts.tiktok && m.meta.posts.tiktok.title) || (m.meta && m.meta.hook) || m.description || m.reason || m.id)}</span>
            <span class="t-small num">${Math.round(m.end - m.start)} s</span>
            ${m.meta ? '<span class="badge info">textos ✓</span>' : '<span class="badge">sem textos</span>'}
            ${pubBadges(pub[m.id])}</li>`).join("")}</ul>`
          : `<div class="empty"><div class="big">☑︎</div>Nenhum clipe aprovado ainda.<button class="btn" id="toClips">Revisar clipes</button></div>`}
      </section>
      <section class="stack">
        ${chk && chk.unauthorized ? `<div class="note warn"><span>⚠</span><span>Este streamer não está marcado como <b>autorizado</b>.
          Postar cortes sem autorização pode derrubar o vídeo ou desmonetizar o canal.
          <button class="btn small" id="auth" style="margin-top:8px">Marcar autorização no layout…</button></span></div>` : ""}
        ${chk && chk.duplicates.length ? `<div class="note warn"><span>⚠</span><span>${chk.duplicates.length} trecho(s) já foram exportados antes.</span></div>` : ""}
        ${noTexts ? `<div class="note info"><span>ℹ</span><span>${noTexts} clipe(s) sem textos de postagem — vão com textos simples.
          <button class="btn small" id="aiAll" style="margin-top:8px">✨ Escrever com IA para os aprovados</button></span></div>` : ""}
        <section class="card pad stack">
          <h2 class="t-head">O que vai sair</h2>
          <div class="muted">Para cada clipe: um <b>MP4 vertical 1080×1920</b> e um <b>.txt</b> com título, descrição e hashtags para
            TikTok, Shorts, Reels e Kwai. Pasta: <span class="num">${esc(chk ? chk.folder : "exports")}</span></div>
          <button class="btn primary large" id="exp" ${approved.length ? "" : "disabled"}>Exportar ${approved.length} clipe(s)</button>
          <button class="btn plain" id="openDir">Abrir pasta de exportação</button>
          <div id="result"></div>
        </section>
        <section class="card pad stack">
          <h2 class="t-head">Postar nas redes</h2>
          ${netsOn.length ? `<div class="muted">Envia os aprovados ao n8n, que posta em <b>${esc(netsOn.join(", "))}</b>.
              ${pcfg.config.auto_send ? "Também acontece sozinho a cada exportação." : ""}</div>
            <button class="btn primary" id="pubBtn" ${approved.length ? "" : "disabled"}>Enviar ${approved.length} clipe(s) para postar</button>`
          : `<div class="muted">Nenhuma rede ligada ainda. Conecte YouTube, TikTok e Instagram em <b>Contas conectadas</b>.</div>`}
          <a class="btn plain" href="#/contas">Contas conectadas ›</a>
        </section>
      </section>
    </div>`;
  $("#toClips", root)?.addEventListener("click", () => go(name, "clips"));
  $("#auth", root)?.addEventListener("click", (e) => openLayout(e.currentTarget, name, ctx.rerender));
  $("#aiAll", root)?.addEventListener("click", () => startJob({ kind: "posts", project: name, ids: approved.map((m) => m.id) }));
  const openDir = () => api(P("/open-exports"), { method: "POST" }).catch((e) => toast(e.message, "bad"));
  $("#openDir", root).onclick = openDir;
  $("#exp", root).onclick = async () => {
    let now;
    try { now = await api(P("/export-check")); } catch (e) { toast(e.message, "bad"); return; }  // sempre a checagem atual
    if (now.unauthorized && !confirmAction("O streamer não está marcado como autorizado. Exportar mesmo assim?")) return;
    if (now.duplicates.length && !confirmAction(`${now.duplicates.length} trecho(s) já foram exportados antes. Exportar de novo?`)) return;
    delete state.exportResults[name];
    await startJob({ kind: "export", project: name });  // ao terminar, a tela é redesenhada e mostra o resultado
  };
  $("#pubBtn", root)?.addEventListener("click", async () => {
    const already = approved.filter((m) => Object.values(pub[m.id] || {}).some((e) => e.status === "published")).length;
    let force = false;
    if (already) force = confirmAction(`${already} clipe(s) já foram publicados em alguma rede. Publicar de novo nessas redes também?\n(OK = publicar de novo · Cancelar = só onde ainda não saiu)`);
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
