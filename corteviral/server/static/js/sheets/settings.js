// Painel "Ajustes": contas conectadas, estilo da legenda, uso da IA e sobre.
import { $, api, esc, nf } from "../api.js";
import { loadCapStyle, saveCapStyle, state } from "../state.js";
import { getTheme, setTheme, THEMES } from "../theme.js";
import { openSheet, toast } from "../ui.js";

const NAMES = { youtube: "YouTube", twitch: "Twitch", kick: "Kick" };

// ---------------- contas conectadas (também usado no "Novo vídeo") ----------------
export function accountsSection(withAlternatives = true) {
  return `<div class="stack" data-accounts>
    <div class="row"><div style="flex:1"><div class="t-head">Contas conectadas</div>
      <div class="t-small" data-acct-status>verificando…</div></div>
      <button class="btn" data-acct-connect>Conectar contas</button></div>
    <div class="t-small">Abre uma janela separada (perfil próprio do app) com YouTube, Twitch e Kick. Entre nas contas e feche a janela —
      funciona com qualquer navegador que você use, mesmo aberto.</div>
    <details ${withAlternatives ? "" : "hidden"}><summary class="t-small" style="cursor:pointer">Outras formas de login (só para este vídeo)</summary>
      <div class="row" style="margin-top:10px">
        <select class="select" data-login style="width:auto"><option value="">Usar contas conectadas</option>
          <option value="firefox">Firefox</option><option value="file">Arquivo cookies.txt…</option></select>
        <input class="input" data-cookie-file placeholder="C:\\…\\cookies.txt" hidden style="flex:1">
        <button class="btn small" data-cookie-pick hidden>📁</button></div></details></div>`;
}

export function loginChoice(root) {
  const v = $("[data-login]", root)?.value || "";
  return { cookies_from_browser: v && v !== "file" ? v : null,
           cookies_file: v === "file" ? ($("[data-cookie-file]", root).value.trim() || null) : null };
}

export function bindAccounts(root) {
  const status = $("[data-acct-status]", root), btn = $("[data-acct-connect]", root);
  const refresh = async () => {
    try {
      const s = await api("/api/session/status");
      if (!s.edge) { status.textContent = "Microsoft Edge não encontrado"; btn.disabled = true; return; }
      status.innerHTML = s.platforms?.length ? "✓ " + s.platforms.map((p) => esc(NAMES[p] || p)).join(", ") : "nenhuma conta conectada";
      btn.textContent = s.platforms?.length ? "Gerenciar" : "Conectar contas";
    } catch (e) { status.textContent = "não consegui verificar"; }
  };
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      await api("/api/session/connect", { method: "POST" });
      status.textContent = "Entre nas contas na janela que abriu e feche-a quando terminar…";
      let seen = false;
      for (let i = 0; i < 1200; i++) {
        const { open } = await api("/api/session/window-open");
        if (open) seen = true;
        if ((seen && !open) || (!seen && i > 40)) break;
        await new Promise((r) => setTimeout(r, 500));
      }
      status.textContent = "lendo sessão…";
      await api("/api/session/sync", { method: "POST" });
    } catch (e) { toast(e.message, "bad"); }
    btn.disabled = false;
    refresh();
  };
  const sel = $("[data-login]", root);
  sel.onchange = () => { const f = sel.value === "file"; $("[data-cookie-file]", root).hidden = !f; $("[data-cookie-pick]", root).hidden = !f; };
  $("[data-cookie-pick]", root).onclick = async () => {
    const r = await api("/api/pick-file?kind=cookies", { method: "POST" }).catch(() => null);
    if (r && r.path) $("[data-cookie-file]", root).value = r.path;
  };
  refresh();
}

// ---------------- estilo da legenda ----------------
function captionSection() {
  const s = state.capStyle;
  return `<div class="row" style="align-items:flex-start;gap:20px">
    <div class="stack" style="flex:1;min-width:260px">
      <label class="switch"><input type="checkbox" id="cOn" ${s.enabled ? "checked" : ""}><span class="track"></span><span>Legendas ligadas por padrão</span></label>
      <div class="field"><label for="cFont">Fonte</label><select class="select" id="cFont">${(state.capFonts.length ? state.capFonts : [s.font]).map((f) => `<option ${f === s.font ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></div>
      <div class="field"><label for="cSize">Tamanho · <span id="cSizeV" class="num"></span></label><input type="range" id="cSize" min="50" max="140" step="2" value="${s.size}"></div>
      <div class="field"><label for="cPos">Altura na tela · <span id="cPosV" class="num"></span></label><input type="range" id="cPos" min="0.11" max="0.75" step="0.01" value="${s.position}"></div>
      <div class="row"><div class="field"><label for="cWords">Palavras por vez</label><select class="select" id="cWords">${[1, 2, 3, 4, 5, 6].map((n) => `<option ${n === s.max_words ? "selected" : ""}>${n}</option>`).join("")}</select></div>
        <div class="field"><label for="cColor">Texto</label><input type="color" id="cColor" value="${s.color}"></div>
        <div class="field"><label for="cHl">Palavra falada</label><input type="color" id="cHl" value="${s.highlight}"></div></div>
      <label class="switch"><input type="checkbox" id="cUpper" ${s.uppercase ? "checked" : ""}><span class="track"></span><span>Tudo em MAIÚSCULAS</span></label>
      <label class="switch"><input type="checkbox" id="cPop" ${s.pop ? "checked" : ""}><span class="track"></span><span>Efeito “pop” na palavra falada</span></label>
      <label class="switch"><input type="checkbox" id="cHook" ${s.hook ? "checked" : ""}><span class="track"></span><span>Gancho nos 3 primeiros segundos</span></label>
      <label class="switch"><input type="checkbox" id="cCredit" ${s.credit ? "checked" : ""}><span class="track"></span><span>Crédito “@streamer” no canto</span></label>
      <div class="row"><button class="btn primary" id="cSave">Salvar estilo</button></div>
    </div>
    <div class="stack" style="align-items:center"><canvas class="preview-9x16" id="cPv" width="360" height="640" aria-label="Prévia da legenda"></canvas>
      <span class="t-small">Prévia · área segura tracejada</span></div></div>`;
}

function readCaption(b) {
  return { ...state.capStyle, enabled: $("#cOn", b).checked, font: $("#cFont", b).value, size: +$("#cSize", b).value,
           position: +$("#cPos", b).value, max_words: +$("#cWords", b).value, color: $("#cColor", b).value.toUpperCase(),
           highlight: $("#cHl", b).value.toUpperCase(), uppercase: $("#cUpper", b).checked, pop: $("#cPop", b).checked,
           hook: $("#cHook", b).checked, credit: $("#cCredit", b).checked };
}

function drawCaption(b) {
  const s = readCaption(b), cv = $("#cPv", b), g = cv.getContext("2d"), W = cv.width, H = cv.height, k = H / 1920;
  $("#cSizeV", b).textContent = s.size + " px";
  $("#cPosV", b).textContent = Math.round(s.position * 100) + "%";
  const gr = g.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, "#2b3240"); gr.addColorStop(1, "#11141a");
  g.fillStyle = gr; g.fillRect(0, 0, W, H);
  g.fillStyle = "#3a4354"; g.fillRect(0, 0, W, H * 0.3);
  let words = ["olha", "essa", "explosão", "mano"].slice(0, s.max_words);
  if (s.uppercase) words = words.map((w) => w.toUpperCase());
  let px = (s.size * k) / 1.41;  // o libass mede pela altura da linha (~1,41× a fonte do navegador)
  const setFont = () => { g.font = `900 ${px}px "${s.font}", sans-serif`; };
  setFont();
  g.textBaseline = "bottom"; g.lineJoin = "round";
  let space = g.measureText(" ").width, widths = words.map((w) => g.measureText(w).width);
  let total = widths.reduce((a, c) => a + c, 0) + space * (words.length - 1);
  const safeW = (1080 - 200) * k;
  if (total > safeW) { px *= safeW / total; setFont(); space = g.measureText(" ").width; widths = words.map((w) => g.measureText(w).width);
                       total = widths.reduce((a, c) => a + c, 0) + space * (words.length - 1); }
  const y = Math.min(Math.max(s.position * 1920 + s.size / 2, 210 + s.size), 1440) * k;
  let x = ((60 + 940) / 2) * k - total / 2;
  words.forEach((w, i) => {
    g.lineWidth = Math.max(2, 18 * k); g.strokeStyle = "#000"; g.strokeText(w, x, y);
    g.fillStyle = i === Math.min(1, words.length - 1) ? s.highlight : s.color; g.fillText(w, x, y);
    x += widths[i] + space;
  });
  g.setLineDash([4, 4]); g.strokeStyle = "rgba(255,255,255,.3)"; g.lineWidth = 1;
  g.strokeRect(60 * k, 210 * k, 880 * k, 1230 * k); g.setLineDash([]);
}

// ---------------- uso da IA ----------------
function usageSection() {
  return `<div class="stack"><div class="row" style="align-items:baseline"><b class="t-display num" id="uPct">—</b><span class="muted">do limite diário</span></div>
    <div class="meter" role="meter" aria-label="Uso do limite diário" aria-valuemin="0" aria-valuemax="100" id="uMeter"><i></i></div>
    <div class="row" id="uStatus"></div><div class="t-small num" id="uNums"></div>
    <div class="t-label" style="margin-top:6px">Tokens por minuto — última hora</div>
    <svg class="chart" id="uChart" role="img" aria-label="Tokens por minuto na última hora"></svg>
    <details><summary class="t-small" style="cursor:pointer">Ver em tabela</summary><div class="t-small num" id="uTable" style="margin-top:8px"></div></details>
    <div class="t-small">O Google não informa a cota grátis pela API: confira em AI Studio → Rate limits e ajuste
      <code>llm.daily_request_limit</code>/<code>llm.daily_token_limit</code> no config.yaml.</div></div>`;
}

async function fillUsage(b) {
  let u;
  try { u = await api("/api/usage"); } catch (e) { return; }
  const st = u.rate_limited ? ["var(--bad)", "⛔", "limite da IA atingido — tente mais tarde", "bad"]
    : u.percent >= 90 ? ["var(--bad)", "⚠", "quase no limite", "bad"]
    : u.percent >= 70 ? ["var(--warn)", "⚠", "uso alto", "warn"] : ["var(--good)", "✓", "dentro do limite", "good"];
  $("#uPct", b).textContent = (u.percent < 10 ? u.percent.toFixed(1) : Math.round(u.percent)) + "%";
  Object.assign($("#uMeter i", b).style, { width: Math.min(100, u.percent) + "%", background: st[0] });
  $("#uMeter", b).setAttribute("aria-valuenow", u.percent);
  $("#uStatus", b).innerHTML = `<span class="badge ${st[3]}">${st[1]} ${st[2]}</span>`;
  $("#uNums", b).textContent = `${nf.format(u.total_tokens)} de ${nf.format(u.limits.tokens)} tokens · ${nf.format(u.requests)} de ${nf.format(u.limits.requests)} requisições · ${u.provider}`;
  $("#uTable", b).innerHTML = u.per_minute.filter((x) => x.tokens).map((x) => `${esc(x.t)} — ${nf.format(x.tokens)} tokens`).join("<br>") || "Nenhum uso na última hora.";
  const svg = $("#uChart", b), W = svg.clientWidth || 560, H = 110, padL = 40, padB = 16, padT = 8, series = u.per_minute;
  const max = Math.max(1, ...series.map((x) => x.tokens)), step = Math.pow(10, Math.floor(Math.log10(max))), top = Math.ceil(max / step) * step;
  const bw = (W - padL) / series.length, y = (v) => padT + (H - padB - padT) * (1 - v / top);
  let out = `<line class="grid" x1="${padL}" x2="${W}" y1="${y(top)}" y2="${y(top)}" stroke-dasharray="2 3"/>
    <text x="${padL - 6}" y="${y(top) + 3}" text-anchor="end">${nf.format(top)}</text><text x="${padL - 6}" y="${y(0) + 3}" text-anchor="end">0</text>
    <text x="${padL}" y="${H - 2}">−60 min</text><text x="${W}" y="${H - 2}" text-anchor="end">agora</text>`;
  series.forEach((p, i) => {
    const x = padL + i * bw, h = y(0) - y(p.tokens);
    if (p.tokens > 0) { const w = Math.max(1, bw - 2), r = Math.min(2, w / 2, h);
      out += `<path class="bar" d="M${x + 1},${y(0)} v${-(h - r)} q0,${-r} ${r},${-r} h${w - 2 * r} q${r},0 ${r},${r} v${h - r} z"/>`; }
    out += `<rect data-i="${i}" x="${x}" y="${padT}" width="${bw}" height="${H - padB - padT}" fill="transparent"/>`;
  });
  out += `<line class="grid" x1="${padL}" x2="${W}" y1="${y(0)}" y2="${y(0)}"/>`;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = out;
  const tip = $("#tip");
  svg.querySelectorAll("rect[data-i]").forEach((rc) => {
    rc.onmousemove = (e) => { const p = series[+rc.dataset.i]; Object.assign(tip.style, { display: "block", left: e.clientX + 12 + "px", top: e.clientY - 30 + "px" });
                              tip.textContent = `${p.t} — ${nf.format(p.tokens)} tokens`; };
    rc.onmouseleave = () => { tip.style.display = "none"; };
  });
}

// ---------------- painel ----------------
let settingsOpen = false;

export async function openSettings(from, start = "appearance") {
  if (settingsOpen) return;  // clique duplo não abre dois painéis
  settingsOpen = true;
  await loadCapStyle();
  const body = document.createElement("div");
  const TABS = [["appearance", "Aparência"], ["accounts", "Contas"], ["captions", "Legenda"], ["usage", "Uso da IA"], ["about", "Sobre"]];
  body.innerHTML = `<div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab" role="tab" data-t="${k}">${l}</button>`).join("")}</div>
    <div data-p="appearance" class="stack"><div class="theme-cards">${THEMES.map(([k, l]) => `
      <button class="theme-card" data-theme-card="${k}" aria-pressed="${getTheme() === k}"><span class="sw sw-${k}"></span>
        <b>${l}</b><span class="t-small">${{ auto: "Segue o claro/escuro do Windows", light: "Leve, estilo Fotos/Finder",
          dark: "Combina com vídeo e games", gta: "Pôr do sol neon de Vice" }[k]}</span></button>`).join("")}</div>
      <div class="t-small">Também dá para trocar pelo atalho no rodapé da barra lateral.</div></div>
    <div data-p="accounts">${accountsSection(false)}</div><div data-p="captions">${captionSection()}</div>
    <div data-p="usage">${usageSection()}</div>
    <div data-p="about" class="stack"><div class="t-head">Corte Viral</div>
      <div class="muted">Do link da live até a pasta com clipes 9:16 legendados e textos para TikTok, Shorts, Reels e Kwai.</div>
      <div class="t-small">Seus projetos, clipes e ajustes ficam nesta pasta do computador; nada é apagado pela interface.</div></div>`;
  const s = openSheet({ title: "Ajustes", body, width: 860, from, onClose: () => {
    settingsOpen = false;
    clearInterval(usageTimer);
    document.removeEventListener("themechange", paintCards);
  } });
  let usageTimer = null;
  const show = (k) => {
    body.querySelectorAll("[data-t]").forEach((b) => b.setAttribute("aria-selected", b.dataset.t === k));
    body.querySelectorAll("[data-p]").forEach((p) => { p.hidden = p.dataset.p !== k; });
    clearInterval(usageTimer);
    if (k === "usage") { fillUsage(body); usageTimer = setInterval(() => document.body.contains(body) ? fillUsage(body) : clearInterval(usageTimer), 5000); }
    if (k === "captions") drawCaption(body);
  };
  body.querySelectorAll("[data-t]").forEach((b) => b.onclick = () => show(b.dataset.t));
  const paintCards = () => body.querySelectorAll("[data-theme-card]").forEach((c) => c.setAttribute("aria-pressed", c.dataset.themeCard === getTheme()));
  body.querySelectorAll("[data-theme-card]").forEach((c) => c.onclick = () => { setTheme(c.dataset.themeCard); paintCards(); });
  document.addEventListener("themechange", paintCards);
  bindAccounts(body);
  ["cOn", "cFont", "cSize", "cPos", "cWords", "cColor", "cHl", "cUpper", "cPop", "cHook", "cCredit"].forEach((id) => {
    $("#" + id, body).addEventListener("input", () => drawCaption(body));
    $("#" + id, body).addEventListener("change", () => drawCaption(body));
  });
  $("#cSave", body).onclick = async () => {
    try {
      await saveCapStyle(readCaption(body));
      toast("Estilo salvo. Vale para os próximos clipes gerados.", "good");
      document.dispatchEvent(new Event("capstylechange"));  // a barra rápida da etapa Clipes se atualiza
    }
    catch (e) { toast("Não salvou: " + e.message, "bad"); }
  };
  show(start);
}
