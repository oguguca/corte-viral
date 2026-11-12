// Aba "Fila de postagem": todos os clipes programados (de todos os vídeos) e como está cada envio.
import { $, api, esc } from "../api.js";
import { confirmAction, toast } from "../ui.js";

const NET_SHORT = { youtube: "YT", tiktok: "TT", instagram: "IG" };
let timer = null;

function when(ts) {  // 19:05 · amanhã 08:30 · 14/11 09:00
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86400000);
  if (diff === 0) return hm;
  if (diff === 1) return `amanhã ${hm}`;
  if (diff === -1) return `ontem ${hm}`;
  return `${d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} ${hm}`;
}

function inMin(sec) {
  if (sec < 60) return "em menos de 1 min";
  const m = Math.round(sec / 60);
  return m < 60 ? `em ${m} min` : `em ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

// estado de um item: fila → enviado ao n8n → publicado / falhou
function stateOf(it, now) {
  if (it.status === "waiting") return it.at < now - 60 ? ["warn", "Atrasado"] : ["", "Na fila"];
  if (it.status === "error") return ["bad", "Falhou"];
  const pub = Object.values(it.publish || {});
  if (pub.some((e) => e.status === "error")) return ["bad", "Falhou"];
  if (pub.length && pub.every((e) => e.status === "published")) return ["good", "Publicado"];
  return ["info", "Enviando…"];
}

function errorOf(it) {
  if (it.error) return it.error;
  const e = Object.values(it.publish || {}).find((x) => x.status === "error");
  return e ? e.error : "";
}

function netBadges(it) {
  return Object.entries(it.publish || {}).map(([net, e]) => {
    const cls = e.status === "published" ? "good" : e.status === "error" ? "bad" : "info";
    const sym = e.status === "published" ? "✓" : e.status === "error" ? "⚠" : "→";
    const b = `<span class="badge ${cls}">${sym} ${NET_SHORT[net] || esc(net)}</span>`;
    return e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener" title="Abrir o vídeo">${b} ↗</a>` : b;
  }).join("");
}

function row(it, now, next) {
  const [cls, label] = stateOf(it, now);
  const err = errorOf(it);
  const isNext = next && it.id === next.id;
  const t = it.status === "waiting" ? it.at : (it.sent_at || it.at);
  return `<li class="qp-row ${isNext ? "next" : ""} ${it.status !== "waiting" ? "done" : ""}">
    <div class="qp-time num">${esc(when(t))}${isNext ? `<span class="t-small">${esc(inMin(it.at - now))}</span>` : ""}</div>
    <div class="qp-main">
      <div class="qp-title" title="${esc(it.title)}">${esc(it.title)}</div>
      <div class="qp-meta t-small">
        <a href="#/p/${encodeURIComponent(it.project)}/export" title="Abrir o vídeo no Corte Viral">${esc(it.project_title)}</a>
        ${it.duration ? ` · ${it.duration} s` : ""}</div>
      ${err && cls === "bad" ? `<div class="qp-err">${esc(err)}</div>` : ""}
    </div>
    <div class="qp-status">
      <span class="badge ${cls}">${esc(label)}</span>${netBadges(it)}
      ${it.status === "waiting" ? `<button class="btn plain small" data-cancel="${esc(it.id)}" title="Tirar da fila" aria-label="Tirar da fila">✕</button>` : ""}
      ${cls === "bad" ? `<button class="btn small" data-retry="${esc(it.id)}">Tentar de novo</button>` : ""}
    </div></li>`;
}

export async function renderQueue(root) {
  clearInterval(timer);
  let d;
  try { d = await api("/api/queue"); } catch (e) {
    root.innerHTML = `<div class="empty"><div class="big">⚠</div>Não consegui ler a fila: ${esc(e.message)}</div>`;
    return;
  }
  const now = d.now;
  const waiting = d.items.filter((i) => i.status === "waiting").sort((a, b) => a.at - b.at);
  const done = d.items.filter((i) => i.status !== "waiting").sort((a, b) => (b.sent_at || 0) - (a.sent_at || 0));
  const next = waiting[0];
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const doneToday = done.filter((i) => (i.sent_at || 0) * 1000 >= today.getTime());
  const published = doneToday.filter((i) => stateOf(i, now)[1] === "Publicado").length;
  const failed = doneToday.filter((i) => stateOf(i, now)[0] === "bad").length;

  root.innerHTML = `
    <header class="page-head"><div>
      <h1 class="t-display">Fila de postagem</h1>
      <div class="sub">Todos os clipes programados, de todos os vídeos. Atualiza sozinha.</div></div></header>
    ${!d.networks.length ? `<div class="note warn"><span>⚠</span><span>Nenhuma rede ligada. Ligue o YouTube em <a href="#/contas">Contas conectadas</a>.</span></div>` : ""}
    <section class="stat-grid qp-stats">
      <div class="stat"><span class="t-small">Próximo envio</span><b class="num">${next ? esc(when(next.at)) : "—"}</b>
        <span class="t-small">${next ? esc(inMin(next.at - now)) : "fila vazia"}</span></div>
      <div class="stat"><span class="t-small">Na fila</span><b class="num">${waiting.length}</b>
        <span class="t-small">${d.summary.ends_at ? `termina ${esc(when(d.summary.ends_at))}` : "—"}</span></div>
      <div class="stat"><span class="t-small">Publicados hoje</span><b class="num good-text">${published}</b>
        <span class="t-small">${failed ? `${failed} falharam` : "nenhuma falha"}</span></div>
      <div class="stat"><span class="t-small">n8n</span><b>${d.n8n.online ? '<span class="good-text">ligado</span>' : "desligado"}</b>
        <span class="t-small">${d.n8n.online ? esc(d.networks.join(", ") || "sem rede") : "liga sozinho na hora"}</span></div>
    </section>

    <section class="card pad stack">
      <div class="row"><h2 class="t-head">Próximos</h2><span class="spacer"></span>
        ${waiting.length ? '<button class="btn plain small" id="qpClear">Cancelar a fila toda</button>' : ""}</div>
      ${waiting.length ? `<ol class="qp-list">${waiting.map((it) => row(it, now, next)).join("")}</ol>`
        : `<div class="empty"><div class="big">⏱</div>Nada programado. Na etapa <b>Exportar</b> de um vídeo, use <b>Programar postagem</b>.</div>`}
    </section>

    ${done.length ? `<section class="card pad stack">
      <h2 class="t-head">Já saíram</h2>
      <ol class="qp-list">${done.slice(0, 60).map((it) => row(it, now, null)).join("")}</ol>
    </section>` : ""}

    <div class="t-small">Deixe o Corte Viral aberto: na hora marcada ele liga o n8n sozinho e envia.
      Os vídeos chegam ao YouTube Studio em alguns minutos (privados até o app do Google ser verificado).</div>`;

  root.querySelectorAll("[data-cancel]").forEach((b) => b.onclick = async () => {
    try { await api(`/api/schedule/${encodeURIComponent(b.dataset.cancel)}`, { method: "DELETE" }); renderQueue(root); }
    catch (e) { toast(e.message, "bad"); }
  });
  root.querySelectorAll("[data-retry]").forEach((b) => b.onclick = async () => {
    try {
      const r = await api(`/api/schedule/${encodeURIComponent(b.dataset.retry)}/retry`, { method: "POST" });
      if (r.item) toast(`Voltou para a fila: sai ${when(r.item.at)}.`, "good");
      renderQueue(root);
    } catch (e) { toast(e.message, "bad"); }
  });
  $("#qpClear", root)?.addEventListener("click", async () => {
    if (!confirmAction(`Tirar da fila os ${waiting.length} clipe(s) que ainda não saíram?`)) return;
    const projects = [...new Set(waiting.map((i) => i.project))];
    try {
      for (const p of projects) await api(`/api/schedule?project=${encodeURIComponent(p)}`, { method: "DELETE" });
      renderQueue(root);
    } catch (e) { toast(e.message, "bad"); }
  });
  timer = setInterval(() => {  // atualiza enquanto a aba está aberta
    if (!root.isConnected || location.hash !== "#/fila") { clearInterval(timer); return; }
    renderQueue(root);
  }, 15000);
}
