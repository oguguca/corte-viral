// Painel "Novo vídeo": link ou arquivo, opções e contas conectadas.
import { $, api, esc } from "../api.js";
import { startJob } from "../jobs.js";
import { openSheet, toast } from "../ui.js";
import { accountsSection, bindAccounts, loginChoice } from "./settings.js";

// "1:20:00", "80:00", "4800" ou "1h20m" → segundos (null se vazio, NaN se inválido)
export function parseTime(text) {
  const t = String(text || "").trim().toLowerCase().replace(/\s+/g, "");
  if (!t) return null;
  const hm = t.match(/^(?:(\d+)h)?(?:(\d+)m(?:in)?)?(?:(\d+)s)?$/);
  if (hm && /[hms]/.test(t)) return (+(hm[1] || 0)) * 3600 + (+(hm[2] || 0)) * 60 + (+(hm[3] || 0));
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(t)) return NaN;
  return t.split(":").reduce((acc, part) => acc * 60 + parseFloat(part), 0);
}
const hms = (s) => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; };

export function openNewVideo(from) {
  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <div class="field"><label for="nvSrc">Link da live/VOD (YouTube, Twitch, Kick) ou arquivo do PC</label>
      <div class="row" style="flex-wrap:nowrap"><input class="input lg" id="nvSrc" placeholder="https://www.twitch.tv/videos/…" autocomplete="off">
        <button class="btn large" id="pick" title="Escolher arquivo do computador">📁 Arquivo</button></div></div>
    <div class="row" style="gap:16px">
      <div class="field"><span class="label" id="mLbl">Qualidade da transcrição</span>
        <div class="seg" role="group" aria-labelledby="mLbl">
          <button data-model="medium" aria-pressed="true">Melhor</button><button data-model="small" aria-pressed="false">Mais rápida</button><button data-model="base" aria-pressed="false">Rascunho</button>
          <button data-model="tiny" aria-pressed="false">Teste</button></div></div>
      <div class="field"><span class="label" id="lLbl">Idioma</span>
        <div class="seg" role="group" aria-labelledby="lLbl">
          <button data-lang="pt" aria-pressed="true">Português</button><button data-lang="en" aria-pressed="false">Inglês</button>
          <button data-lang="auto" aria-pressed="false">Detectar</button></div></div>
    </div>
    <details id="nvPart"><summary style="cursor:pointer"><b>Só um trecho do vídeo</b>
        <span class="t-small">— já assistiu? Escolha onde estão os cortes e não transcreve o vídeo inteiro</span></summary>
      <div class="stack" style="margin-top:12px">
        <div class="row" style="align-items:flex-end">
          <div class="field" style="width:140px"><label for="nvFrom">Começa em</label>
            <input class="input num" id="nvFrom" placeholder="1:20:00" inputmode="numeric" autocomplete="off"></div>
          <div class="field" style="width:140px"><label for="nvTo">Termina em</label>
            <input class="input num" id="nvTo" placeholder="1:45:00" inputmode="numeric" autocomplete="off"></div>
          <span class="t-small" id="nvLen" aria-live="polite"></span></div>
        <div class="t-small">Use horas:minutos:segundos (ou minutos:segundos). Só esse trecho é baixado e transcrito —
          bem mais rápido. Cada trecho vira um projeto próprio.</div></div></details>
    <details><summary class="t-small" style="cursor:pointer">Vídeo com restrição de idade, privado ou de inscritos?</summary>
      <div style="margin-top:12px">${accountsSection()}</div></details>`;
  const foot = document.createElement("div");
  foot.className = "row";
  foot.innerHTML = `<button class="btn plain" data-cancel>Cancelar</button><button class="btn primary" id="go">Processar vídeo</button>`;
  const s = openSheet({ title: "Novo vídeo", body, foot, width: 620, from });
  bindAccounts(body);

  let model = "medium", lang = "pt";
  body.querySelectorAll("[data-model]").forEach((b) => b.onclick = () => { model = b.dataset.model; body.querySelectorAll("[data-model]").forEach((x) => x.setAttribute("aria-pressed", x === b)); });
  body.querySelectorAll("[data-lang]").forEach((b) => b.onclick = () => { lang = b.dataset.lang; body.querySelectorAll("[data-lang]").forEach((x) => x.setAttribute("aria-pressed", x === b)); });
  $("#pick", body).onclick = async () => {
    const r = await api("/api/pick-file", { method: "POST" }).catch(() => null);
    if (r && r.path) $("#nvSrc", body).value = r.path;
  };
  $("[data-cancel]", foot).onclick = s.close;
  const readPart = () => {  // null = vídeo inteiro; {error} = algo errado; {start, end}
    const a = parseTime($("#nvFrom", body).value), b = parseTime($("#nvTo", body).value);
    if (a === null && b === null) return null;
    if (Number.isNaN(a) || Number.isNaN(b)) return { error: "Horário inválido. Use 1:20:00 ou 80:00.", field: Number.isNaN(a) ? "#nvFrom" : "#nvTo" };
    if (a === null || b === null) return { error: "Preencha onde o trecho começa e onde termina.", field: a === null ? "#nvFrom" : "#nvTo" };
    if (b - a < 10) return { error: "O fim precisa vir pelo menos 10 s depois do começo.", field: "#nvTo" };
    return { start: a, end: b };
  };
  const showLen = () => {
    const p = readPart();
    $("#nvLen", body).textContent = p && !p.error ? `trecho de ${hms(p.end - p.start)}` : "";
  };
  $("#nvFrom", body).oninput = showLen;
  $("#nvTo", body).oninput = showLen;
  const submit = () => {
    const source = $("#nvSrc", body).value.trim();
    if (!source) { $("#nvSrc", body).focus(); toast("Cole um link ou escolha um arquivo.", "bad"); return; }
    const part = $("#nvPart", body).open ? readPart() : null;
    if (part && part.error) { $(part.field, body).focus(); toast(part.error, "bad"); return; }
    s.close();
    startJob({ kind: "ingest", source, model, language: lang, ...loginChoice(body),
               ...(part ? { section_start: part.start, section_end: part.end } : {}) });
  };
  $("#go", foot).onclick = submit;
  $("#nvSrc", body).onkeydown = (e) => { if (e.key === "Enter") submit(); };
}
