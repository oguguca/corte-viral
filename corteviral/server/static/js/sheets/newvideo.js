// Painel "Novo vídeo": link ou arquivo, opções e contas conectadas.
import { $, api, esc } from "../api.js";
import { startJob } from "../jobs.js";
import { openSheet, toast } from "../ui.js";
import { accountsSection, bindAccounts, loginChoice } from "./settings.js";

export function openNewVideo(from) {
  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `
    <div class="field"><label for="src">Link da live/VOD (YouTube, Twitch, Kick) ou arquivo do PC</label>
      <div class="row" style="flex-wrap:nowrap"><input class="input lg" id="src" placeholder="https://www.twitch.tv/videos/…" autocomplete="off">
        <button class="btn large" id="pick" title="Escolher arquivo do computador">📁 Arquivo</button></div></div>
    <div class="row" style="gap:16px">
      <div class="field"><span class="label" id="mLbl">Qualidade da transcrição</span>
        <div class="seg" role="group" aria-labelledby="mLbl">
          <button data-model="medium" aria-pressed="true">Melhor</button><button data-model="small" aria-pressed="false">Mais rápida</button>
          <button data-model="tiny" aria-pressed="false">Teste</button></div></div>
      <div class="field"><span class="label" id="lLbl">Idioma</span>
        <div class="seg" role="group" aria-labelledby="lLbl">
          <button data-lang="pt" aria-pressed="true">Português</button><button data-lang="en" aria-pressed="false">Inglês</button>
          <button data-lang="auto" aria-pressed="false">Detectar</button></div></div>
    </div>
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
    if (r && r.path) $("#src", body).value = r.path;
  };
  $("[data-cancel]", foot).onclick = s.close;
  const submit = () => {
    const source = $("#src", body).value.trim();
    if (!source) { $("#src", body).focus(); toast("Cole um link ou escolha um arquivo.", "bad"); return; }
    s.close();
    startJob({ kind: "ingest", source, model, language: lang, ...loginChoice(body) });
  };
  $("#go", foot).onclick = submit;
  $("#src", body).onkeydown = (e) => { if (e.key === "Enter") submit(); };
}
