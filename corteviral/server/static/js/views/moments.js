// Etapa ② Momentos: encontrar, ver nota/critérios, 👍/👎 e tocar o trecho.
import { $, api, enc, esc, fmt, projectApi } from "../api.js";
import { startJob } from "../jobs.js";
import { go } from "../router.js";
import { confirmAction, scoreClass, toast } from "../ui.js";

const DURATIONS = [["15-30", "15–30 s · curto"], ["15-60", "15–60 s · viral"], ["30-60", "30–60 s"],
                   ["61-90", "61–90 s · monetização"], ["61-120", "61–120 s · monetização"], ["custom", "Personalizado…"]];
const CRIT = [["gancho", "Gancho"], ["emocao", "Emoção"], ["visual", "Visual"], ["final", "Final"], ["contexto", "Contexto"]];

export const aiTag = (m) => m.ai === "video" ? '<span class="badge info">👁 IA assistiu</span>'
  : m.ai ? '<span class="badge">IA (texto)</span>' : '<span class="badge">sinais</span>';

export function critBars(m) {
  if (!m.criteria) return "";
  return `<div class="crit">${CRIT.map(([k, l]) => `<div><div class="lbl">${l}<b class="num">${m.criteria[k] ?? "–"}</b></div>
    <div class="bar"><i style="width:${Math.max(0, Math.min(100, m.criteria[k] || 0))}%"></i></div></div>`).join("")}</div>`;
}

export async function renderMoments(root, name) {
  const P = projectApi(name);
  let d = null;
  try { d = await api(P("/moments")); } catch (e) { /* ainda não analisado */ }
  const range = d?.range || [15, 60];
  const key = `${range[0]}-${range[1]}`;
  const preset = DURATIONS.some(([k]) => k === key);

  root.innerHTML = `
    <section class="card pad stack" style="margin-bottom:18px">
      <div class="row" style="align-items:flex-end;gap:14px">
        <div class="field" style="width:220px"><label for="dur">Duração do clipe</label>
          <select class="select" id="dur">${DURATIONS.map(([k, l]) => `<option value="${k}" ${(preset ? k === key : k === "custom") ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="row" id="custom" ${preset ? "hidden" : ""} style="gap:8px">
          <div class="field" style="width:84px"><label for="mn">Mín. (s)</label><input class="input" id="mn" type="number" min="5" max="175" value="${range[0]}"></div>
          <div class="field" style="width:84px"><label for="mx">Máx. (s)</label><input class="input" id="mx" type="number" min="10" max="180" value="${range[1]}"></div>
        </div>
        <div class="field" style="width:110px"><label for="n">Quantos</label><input class="input" id="n" type="number" min="1" max="30" value="${d?.n || 10}"></div>
        <div class="field"><span class="label" id="aiLbl">Inteligência</span>
          <div class="seg" role="group" aria-labelledby="aiLbl">
            <button data-ai="gemini" aria-pressed="${(d?.provider || "gemini") === "gemini"}">Gemini grátis</button>
            <button data-ai="none" aria-pressed="${d?.provider === "none"}">Sem IA</button>
            <button data-ai="anthropic" aria-pressed="${d?.provider === "anthropic"}">Claude</button></div></div>
        <span class="spacer"></span>
        <button class="btn plain" id="force" title="Ignora o resultado salvo e avalia tudo de novo">Reavaliar do zero</button>
        <button class="btn primary large" id="find">${d ? "Atualizar momentos" : "Encontrar momentos"}</button>
      </div>
      <div id="stats"></div>
    </section>
    <div class="grid-2">
      <ul class="list" id="list" aria-label="Momentos"></ul>
      <section class="card pad stack" style="position:sticky;top:64px">
        <video id="v" controls preload="metadata" src="${P("/video")}"></video>
        <div class="t-small" id="nowPlaying">Clique num momento para assistir ao trecho.</div>
      </section>
    </div>
    <div class="row end" style="margin-top:20px">
      <button class="btn primary large" id="next" ${d ? "" : "disabled"}>Próximo: revisar os clipes ›</button>
    </div>`;

  let provider = d?.provider || "gemini";
  root.querySelectorAll("[data-ai]").forEach((b) => b.onclick = () => {
    provider = b.dataset.ai;
    root.querySelectorAll("[data-ai]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  });
  $("#dur", root).onchange = (e) => { $("#custom", root).hidden = e.target.value !== "custom"; };
  const body = (force) => {
    const v = $("#dur", root).value;
    const [lo, hi] = v === "custom" ? [+$("#mn", root).value, +$("#mx", root).value] : v.split("-").map(Number);
    return { kind: "analyze", project: name, min_seconds: lo, max_seconds: hi, profile: lo >= 61 ? "monetizacao" : "viral",
             n: +$("#n", root).value || 10, provider, force };
  };
  $("#find", root).onclick = () => startJob(body(false));
  $("#force", root).onclick = () => {
    if (confirmAction("Reavaliar do zero refaz a análise inteira, incluindo a IA assistindo os vídeos (alguns minutos e ~120 mil tokens). " +
                      "Aprovações e edições deste projeto voltam ao início. Continuar?")) startJob(body(true));
  };
  $("#next", root).onclick = () => go(name, "clips");
  if (!d) {
    $("#list", root).innerHTML = `<li class="card"><div class="empty"><div class="big">✨</div>
      Escolha a duração e clique em <b>Encontrar momentos</b>.<br>A IA procura picos de reação, assiste aos melhores trechos e dá nota.</div></li>`;
    return;
  }

  const s = d.stats || {}, dv = s.deep || {};
  const warn = s.rate_limited || s.errors || dv.rate_limited || dv.errors;
  $("#stats", root).innerHTML = `<div class="note ${warn ? "warn" : "info"}"><span>${warn ? "⚠" : "ℹ"}</span><span>
    ${d.moments.length} momentos de ${d.candidates ?? "?"} candidatos${dv.video ? ` · IA assistiu ${dv.video}` : ""}
    ${d.has_chat ? " · com chat" : ""}${s.rate_limited || dv.rate_limited ? " · cota da IA atingida: parte ficou só com sinais" : ""}
    ${dv.errors ? ` · IA não conseguiu assistir ${dv.errors}` : ""}${d.complete === false ? " · clique em Atualizar para completar" : ""}</span></div>`;

  const video = $("#v", root);
  let until = null;
  video.ontimeupdate = () => { if (until !== null && video.currentTime >= until) { video.pause(); until = null; } };
  const fb = d.feedback || {};
  $("#list", root).innerHTML = d.moments.map((m) => `
    <li><div class="moment ${m.status === "discarded" ? "discarded" : ""}" role="button" tabindex="0" data-id="${esc(m.id)}">
      <div class="${scoreClass(m.score)}"><span class="num">${m.score}</span><small>nota</small></div>
      <div>
        <div class="row" style="gap:8px"><span class="m-title num">${fmt(m.start)} – ${fmt(m.end)}</span>
          <span class="t-small num">${Math.round(m.end - m.start)} s</span>${aiTag(m)}
          ${m.status === "approved" ? '<span class="badge good">✓ aprovado</span>' : ""}<span class="spacer"></span>
          <button class="btn plain small" data-fb="good" aria-pressed="${fb[m.id] === "good"}" aria-label="Bom momento" title="Bom — a IA aprende com isso">${fb[m.id] === "good" ? "👍 bom" : "👍"}</button>
          <button class="btn plain small" data-fb="bad" aria-pressed="${fb[m.id] === "bad"}" aria-label="Momento ruim" title="Ruim — a IA aprende com isso">${fb[m.id] === "bad" ? "👎 ruim" : "👎"}</button></div>
        ${m.description ? `<div class="m-desc">${esc(m.description)}</div>` : ""}
        <div class="muted" style="margin-top:2px">${esc(m.reason || "")}</div>
        ${m.hook || m.triage_score != null ? `<div class="m-meta">${m.hook ? `<span class="t-small">Gancho: “${esc(m.hook)}”</span>` : ""}
          ${m.triage_score != null && m.triage_score !== m.score ? `<span class="t-small num">triagem ${m.triage_score}</span>` : ""}</div>` : ""}
        ${m.risk && !/^nenhum/i.test(m.risk) ? `<div class="m-meta"><span class="badge warn wrap">⚠ ${esc(m.risk)}</span></div>` : ""}
        ${critBars(m)}
      </div></div></li>`).join("");

  const play = (el) => {
    const m = d.moments.find((x) => x.id === el.dataset.id);
    root.querySelectorAll(".moment").forEach((x) => x.setAttribute("aria-current", x === el));
    video.currentTime = m.start; until = m.end; video.play();
    $("#nowPlaying", root).textContent = `Tocando ${fmt(m.start)} – ${fmt(m.end)} · ${m.description || m.reason || ""}`;
  };
  root.querySelectorAll(".moment").forEach((el) => {
    el.onclick = () => play(el);
    el.onkeydown = (e) => {
      if (e.target !== el) return;  // Enter/Espaço nos botões 👍/👎 não disparam o vídeo
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); play(el); }
    };
  });
  root.querySelectorAll("[data-fb]").forEach((b) => b.onclick = async (e) => {
    e.stopPropagation();
    const id = b.closest(".moment").dataset.id;
    const label = fb[id] === b.dataset.fb ? null : b.dataset.fb;
    try {
      await api(`${P("/moments/")}${enc(id)}/feedback`, { method: "POST", body: { label } });
      fb[id] = label;
      b.closest(".moment").querySelectorAll("[data-fb]").forEach((x) => {
        const on = x.dataset.fb === label;
        x.setAttribute("aria-pressed", on);
        x.textContent = x.dataset.fb === "good" ? (on ? "👍 bom" : "👍") : (on ? "👎 ruim" : "👎");
      });
      toast(label ? "Anotado — a IA vai considerar na próxima avaliação." : "Marcação removida.", "good");
    } catch (err) { toast(err.message, "bad"); }
  });
}
