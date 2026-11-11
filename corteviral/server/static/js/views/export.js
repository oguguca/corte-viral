// Etapa ④ Exportar: resumo dos aprovados, avisos e exportação em lote.
import { $, api, esc, fmt, projectApi } from "../api.js";
import { onJobDone, startJob } from "../jobs.js";
import { go } from "../router.js";
import { openLayout } from "../sheets/layout.js";
import { confirmAction, toast } from "../ui.js";

export async function renderExport(root, name) {
  const P = projectApi(name);
  let d = null, chk = null;
  try { [d, chk] = await Promise.all([api(P("/moments")), api(P("/export-check"))]); } catch (e) { /* sem momentos */ }
  if (!d) {
    root.innerHTML = `<div class="card"><div class="empty"><div class="big">📦</div>Encontre e aprove momentos antes de exportar.
      <button class="btn primary" id="toMoments">Ir para Momentos</button></div></div>`;
    $("#toMoments", root).onclick = () => go(name, "moments");
    return;
  }
  const approved = d.moments.filter((m) => m.status === "approved").sort((a, b) => b.score - a.score);
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
            ${m.meta ? '<span class="badge info">textos ✓</span>' : '<span class="badge">sem textos</span>'}</li>`).join("")}</ul>`
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
      </section>
    </div>`;
  $("#toClips", root)?.addEventListener("click", () => go(name, "clips"));
  $("#auth", root)?.addEventListener("click", (e) => openLayout(e.currentTarget, name));
  $("#aiAll", root)?.addEventListener("click", () => startJob({ kind: "posts", project: name, ids: approved.map((m) => m.id) }));
  $("#openDir", root).onclick = () => api(P("/open-exports"), { method: "POST" });
  $("#exp", root).onclick = async () => {
    if (chk.unauthorized && !confirmAction("O streamer não está marcado como autorizado. Exportar mesmo assim?")) return;
    if (chk.duplicates.length && !confirmAction(`${chk.duplicates.length} trecho(s) já foram exportados antes. Exportar de novo?`)) return;
    const job = await startJob({ kind: "export", project: name });
    if (job && job.status === "done") showResult(job);
  };
  function showResult(job) {
    const r = job.result || {};
    $("#result", root).innerHTML = `<div class="note ${(r.errors || []).length ? "warn" : "good"}"><span>${(r.errors || []).length ? "⚠" : "✓"}</span>
      <span>${(r.files || []).length} clipe(s) exportado(s)${(r.errors || []).length ? ` · ${r.errors.length} falharam` : ""}.
      <button class="btn small" id="openRes" style="margin-top:8px">Abrir pasta</button></span></div>`;
    $("#openRes", root).onclick = () => api(P("/open-exports"), { method: "POST" });
    toast("Exportação concluída.", "good");
  }
}
