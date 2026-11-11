// Componentes de interface: sheets (painéis que nascem do gatilho), toasts e confirmação.
import { $, esc } from "./api.js";

const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Abre um painel modal. `from` = elemento que abriu (a animação nasce dele e o foco volta para ele). */
export function openSheet({ title, body, foot, width = 640, from = null, onClose = null }) {
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  const sheet = document.createElement("section");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", title);
  sheet.style.setProperty("--sheet-w", width + "px");
  sheet.innerHTML = `
    <header class="sheet-head"><h2 class="t-title">${esc(title)}</h2><span class="spacer"></span>
      <button class="btn plain icon-btn" data-close aria-label="Fechar">✕</button></header>
    <div class="sheet-body"></div>${foot ? '<footer class="sheet-foot"></footer>' : ""}`;
  const bodyEl = $(".sheet-body", sheet);
  typeof body === "string" ? (bodyEl.innerHTML = body) : bodyEl.append(body);
  if (foot) typeof foot === "string" ? ($(".sheet-foot", sheet).innerHTML = foot) : $(".sheet-foot", sheet).append(foot);

  // origem espacial: o painel cresce a partir do botão que o abriu
  if (from && !reduced()) {
    const r = from.getBoundingClientRect();
    sheet.style.transformOrigin = `${r.left + r.width / 2 - innerWidth / 2 + width / 2}px ${r.top - innerHeight / 2 + 200}px`;
  }
  document.body.append(scrim, sheet);
  requestAnimationFrame(() => { scrim.classList.add("show"); sheet.classList.add("show"); });

  const close = () => {
    scrim.classList.remove("show");
    sheet.classList.remove("show");
    document.removeEventListener("keydown", onKey);
    setTimeout(() => { scrim.remove(); sheet.remove(); }, reduced() ? 0 : 260);
    from?.focus?.();
    onClose?.();
  };
  const onKey = (e) => {
    if (e.key === "Escape") close();
    if (e.key === "Tab") {  // foco preso dentro do painel
      const f = [...sheet.querySelectorAll("button, input, select, textarea, [tabindex]:not([tabindex='-1'])")]
        .filter((x) => !x.disabled && x.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
      else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
    }
  };
  document.addEventListener("keydown", onKey);
  scrim.onclick = close;
  $("[data-close]", sheet).onclick = close;
  setTimeout(() => (sheet.querySelector("input, select, textarea, .btn.primary") || $("[data-close]", sheet)).focus(), 30);
  return { sheet, body: bodyEl, close };
}

export function toast(message, kind = "") {
  const t = document.createElement("div");
  t.className = "toast";
  t.setAttribute("role", kind === "bad" ? "alert" : "status");
  t.innerHTML = (kind === "good" ? "✓ " : kind === "bad" ? "⚠ " : "") + esc(message);
  $("#toasts").append(t);
  setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, kind === "bad" ? 7000 : 3500);
}

/** Confirmação só para ações caras ou que substituem trabalho (princípio de Agência: sem excesso). */
export const confirmAction = (text) => window.confirm(text);

export function scoreClass(score) { return score >= 80 ? "score hi" : "score"; }
