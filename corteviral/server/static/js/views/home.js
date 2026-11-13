// Painel inicial: meta diária de clipes, números do canal do YouTube e metas de monetização de cada rede.
import { $, api, esc, nf } from "../api.js";
import { openNewVideo } from "../sheets/newvideo.js";
import { toast } from "../ui.js";

let homeTimer = null;
const ICON = { youtube: "▶", tiktok: "♪", instagram: "◎", kwai: "K" };
const compact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
const big = (n) => (n >= 10000 ? compact.format(n) : nf.format(n));
const dayName = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "");
const ago = (ts) => {
  if (!ts) return "";
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  return m < 1 ? "agora" : m < 60 ? `há ${m} min` : `há ${Math.round(m / 60)} h`;
};

function meter(pct, label, done) {
  return `<div class="meter" role="meter" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
    <i class="${done ? "done" : ""}" style="width:${Math.max(pct, pct > 0 ? 2 : 0)}%"></i></div>`;
}

// 7 dias: barras de uma cor só, linha tracejada da meta, valor no hover (title) e só o de hoje escrito
function weekChart(days, goal) {
  const W = 560, H = 130, padB = 22, padT = 18, max = Math.max(goal, ...days.map((d) => d.count), 1);
  const bw = W / days.length, y = (v) => padT + (H - padB - padT) * (1 - v / max);
  const gy = y(goal);
  return `<svg class="week-chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Clipes soltos nos últimos 7 dias: ${days.map((d) => `${dayName(d.date)} ${d.count}`).join(", ")}. Meta ${goal} por dia.">
    <line class="goal-line" x1="0" x2="${W}" y1="${gy}" y2="${gy}"></line>
    ${days.map((d, i) => {
      const x = i * bw + bw * 0.22, w = bw * 0.56, top = y(d.count), h = H - padB - top, today = i === days.length - 1;
      return `<g class="col"><title>${dayName(d.date)}: ${d.count} clipe(s)</title>
        <rect class="hit" x="${i * bw}" y="0" width="${bw}" height="${H}"></rect>
        ${d.count ? `<path class="bar ${d.count >= goal ? "met" : ""}" d="M${x},${H - padB} V${top + 4} q0,-4 4,-4 h${w - 8} q4,0 4,4 V${H - padB} Z"></path>` : ""}
        ${today ? `<text class="val" x="${x + w / 2}" y="${top - 5}" text-anchor="middle">${d.count}</text>` : ""}
        <text class="day ${today ? "today" : ""}" x="${x + w / 2}" y="${H - 6}" text-anchor="middle">${today ? "hoje" : dayName(d.date)}</text></g>`;
    }).join("")}
    <line class="base" x1="0" x2="${W}" y1="${H - padB}" y2="${H - padB}"></line></svg>
    <div class="chart-key t-small"><span class="key-dash" aria-hidden="true"></span>meta de ${goal} por dia
      <span class="key-bar" aria-hidden="true"></span>dia em que bateu</div>`;
}

function dailyCard(d) {
  const pct = Math.min(100, Math.round((d.today / d.goal) * 100)), done = d.today >= d.goal;
  const left = Math.max(0, d.goal - d.today);
  return `<section class="card pad stack">
    <div class="row"><h2 class="t-head">Meta de hoje</h2><span class="spacer"></span>
      ${d.streak ? `<span class="badge good">🔥 ${d.streak} dia(s) seguidos</span>` : ""}</div>
    <div class="row" style="align-items:baseline;gap:10px">
      <b class="t-display num">${d.today}<span class="muted" style="font-size:.55em"> / ${d.goal}</span></b>
      <span class="muted">clipes soltos hoje${d.posted_today ? ` · ${d.posted_today} publicado(s)` : ""}</span></div>
    ${meter(pct, "Meta de clipes de hoje", done)}
    <div class="${done ? "good-text" : "muted"}">${done ? "✓ Meta do dia batida. Constância é o que faz o algoritmo te mostrar." : `Faltam <b>${left}</b> clipe(s) para bater a meta.`}</div>
    ${weekChart(d.last7, d.goal)}
    <label class="row" style="gap:8px"><span class="label">Meta por dia</span>
      <input class="input num" id="dailyGoal" type="number" min="1" max="50" value="${d.goal}" style="width:72px" aria-label="Meta de clipes por dia">
      <span class="label">clipes</span></label>
  </section>`;
}

function youtubeCard(yt) {
  if (!yt.connected) {
    return `<section class="card pad stack" id="ytCard">
      <div class="row"><span class="thumb-lg net-youtube" aria-hidden="true">▶</span>
        <div><h2 class="t-head">Ligar o seu canal do YouTube</h2>
        <div class="t-small">As views e os inscritos entram nas metas sozinhos (grátis, só leitura).</div></div></div>
      <ol class="steps-list t-small">
        <li>Abra <b>console.cloud.google.com</b> e crie um projeto (pode usar o mesmo da chave do Gemini).</li>
        <li>Em <b>APIs e serviços › Biblioteca</b>, ative a <b>YouTube Data API v3</b>.</li>
        <li>Em <b>Credenciais › Criar credenciais › Chave de API</b>, copie a chave e cole aqui.</li></ol>
      <div class="field"><label for="ytKey">Chave da API</label><input class="input" id="ytKey" type="password" autocomplete="off" placeholder="AIza…" value=""></div>
      <div class="field"><label for="ytChannel">Seu canal (@ ou link)</label><input class="input" id="ytChannel" placeholder="@seucanal" value="${esc(yt.channel || "")}"></div>
      <button class="btn primary" id="ytSave">Ligar canal</button></section>`;
  }
  const s = yt.stats, g = yt.gained || {};
  return `<section class="card pad stack" id="ytCard">
    <div class="row"><span class="thumb-lg net-youtube" aria-hidden="true">▶</span>
      <div style="min-width:0"><h2 class="t-head">${esc(s?.title || yt.channel)}</h2>
        <div class="t-small">${esc(yt.channel)} · atualizado ${ago(yt.updated)}</div></div>
      <span class="spacer"></span><button class="btn small" id="ytRefresh">Atualizar</button></div>
    ${yt.error ? `<div class="note warn"><span>⚠</span><span>${esc(yt.error)}</span></div>` : ""}
    ${s ? `<div class="stat-grid">
      <div class="stat"><span class="t-label">Inscritos</span><b class="num">${big(s.subscribers)}</b>
        ${g.today_subscribers ? `<span class="delta">+${nf.format(g.today_subscribers)} hoje</span>` : ""}</div>
      <div class="stat"><span class="t-label">Views hoje</span><b class="num">${big(g.today_views || 0)}</b>
        <span class="delta muted">${g.today_from_yesterday ? "desde ontem" : "desde a 1ª leitura de hoje"}${g.today_since
          ? " às " + new Date(g.today_since * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : ""}</span></div>
      <div class="stat"><span class="t-label">Views na semana</span><b class="num">${big(g.week_views || 0)}</b>
        ${g.since ? `<span class="delta muted">medindo desde ${new Date(g.since + "T12:00:00").toLocaleDateString("pt-BR")}</span>` : ""}</div>
      <div class="stat"><span class="t-label">Shorts em 90 dias</span><b class="num">${nf.format(s.shorts_90d)}</b>
        <span class="delta muted">${big(s.shorts_views_90d)} views</span></div></div>` : ""}
    <details><summary class="t-small" style="cursor:pointer">Trocar canal ou chave</summary>
      <div class="stack" style="margin-top:10px">
        <div class="field"><label for="ytChannel">Canal (@ ou link)</label><input class="input" id="ytChannel" value="${esc(yt.channel || "")}"></div>
        <div class="field"><label for="ytKey">Nova chave (deixe vazio para manter)</label><input class="input" id="ytKey" type="password" autocomplete="off"></div>
        <div class="row"><button class="btn" id="ytSave">Salvar</button><button class="btn plain" id="ytRemove">Desligar canal</button></div></div></details>
  </section>`;
}

function programCard(p) {
  return `<section class="card pad stack goal-card">
    <div class="row"><span class="thumb-lg net-${p.network}" aria-hidden="true">${ICON[p.network]}</span>
      <div style="min-width:0;flex:1"><h3 class="t-head">${esc(p.name)}</h3><div class="t-small">${esc(p.program)}</div></div>
      <b class="num goal-pct ${p.done ? "good-text" : ""}">${p.done ? "✓" : Math.floor(p.percent) + "%"}</b></div>
    ${p.requirements.map((r) => `<div class="stack" style="gap:6px">
      <div class="row" style="gap:6px"><span>${esc(r.label)}</span><span class="spacer"></span>
        <span class="num"><b>${big(r.current)}</b> <span class="muted">/ ${big(r.target)}</span></span></div>
      ${meter(r.percent, `${p.name}: ${r.label}`, r.done)}
      <span class="t-small">${r.done ? "✓ Requisito cumprido" : `Faltam ${nf.format(r.target - r.current)}`}</span></div>`).join("")}
    ${p.auto ? '<span class="badge info">atualiza sozinho pelo canal</span>'
      : `<details><summary class="t-small" style="cursor:pointer">${p.network === "youtube" ? "Ligue o canal acima ou digite os números" : "Digitar os números de hoje"}</summary>
        <div class="row" style="margin-top:8px;align-items:flex-end">${p.requirements.map((r) => `
          <div class="field" style="width:130px"><label for="m-${p.id}-${r.key}">${esc(r.label)}</label>
            <input class="input num" id="m-${p.id}-${r.key}" type="number" min="0" data-net="${p.network}" data-key="${r.key}" value="${r.current}"></div>`).join("")}
          <button class="btn small" data-save-manual="${p.id}" data-net="${p.network}">Salvar</button></div></details>`}
    <div class="t-small muted">${esc(p.note)}</div>
  </section>`;
}

export async function renderHome(root, { rerender } = {}) {
  let g;
  root.innerHTML = '<div class="empty"><span class="spinner"></span>Carregando o painel…</div>';
  try { g = await api("/api/goals"); } catch (e) {
    root.innerHTML = `<div class="note bad">⚠ Não consegui carregar o painel: ${esc(e.message)}</div>`;
    return;
  }
  const paint = () => {
    root.innerHTML = `
      <header class="page-head"><div><h1 class="t-display">Painel</h1>
        <div class="sub">Rumo à monetização: metas de cada rede, atualizadas pelas views.</div></div>
        <button class="btn primary large" id="homeNew">＋ Novo vídeo</button></header>
      <div class="grid-2">${dailyCard(g.daily)}${youtubeCard(g.youtube)}</div>
      <h2 class="t-head" style="margin:26px 0 12px">Metas de monetização</h2>
      <div class="goal-grid">${g.programs.map(programCard).join("")}</div>
      <p class="t-small muted" style="margin-top:14px">Regras públicas de cada programa conferidas em 2026; as redes mudam
        as regras de vez em quando. As views de Shorts em 90 dias somam as views dos Shorts publicados nos últimos 90 dias.</p>`;
    bind();
  };
  const save = async (body, msg) => {
    try { await api("/api/goals", { method: "PUT", body }); g = await api("/api/goals" + (body.youtube ? "?refresh=1" : ""));
      paint(); if (msg) toast(msg, "good"); }
    catch (e) { toast(e.message, "bad"); }
  };
  function bind() {
    $("#homeNew", root).onclick = (e) => openNewVideo(e.currentTarget);
    $("#dailyGoal", root).onchange = (e) => {
      const v = Math.min(50, Math.max(1, Math.round(+e.target.value || 1)));
      save({ daily_videos: v }, `Meta: ${v} clipe(s) por dia.`);
    };
    $("#ytSave", root)?.addEventListener("click", (e) => {
      const key = $("#ytKey", root).value.trim(), channel = $("#ytChannel", root).value.trim();
      if (!channel || (!g.youtube.has_key && !key)) { toast("Preencha a chave e o canal.", "bad"); return; }
      e.currentTarget.disabled = true;
      save({ youtube: { api_key: key, channel } }, "Canal ligado.");
    });
    $("#ytRemove", root)?.addEventListener("click", () => save({ youtube: { remove: true } }, "Canal desligado."));
    $("#ytRefresh", root)?.addEventListener("click", async (e) => {
      e.currentTarget.disabled = true;
      try { g = await api("/api/goals?refresh=1"); paint(); } catch (err) { toast(err.message, "bad"); e.currentTarget.disabled = false; }
    });
    root.querySelectorAll("[data-save-manual]").forEach((b) => b.onclick = () => {
      const net = b.dataset.net, vals = {};
      b.closest(".goal-card").querySelectorAll(`input[data-net="${net}"]`).forEach((i) => { vals[i.dataset.key] = Math.max(0, Math.round(+i.value || 0)); });
      save({ manual: { [net]: vals } }, "Números salvos.");
    });
  }
  paint();
  clearInterval(homeTimer);  // clipe saiu da fila: a meta de hoje sobe sozinha enquanto o painel está aberto
  homeTimer = setInterval(async () => {
    if (!root.isConnected || !["", "#", "#/"].includes(location.hash)) { clearInterval(homeTimer); return; }
    if (root.querySelector("input:focus, details[open]")) return;  // não atrapalha quem está digitando
    try { g = await api("/api/goals"); paint(); } catch (e) { /* tenta de novo no próximo ciclo */ }
  }, 30000);
}
