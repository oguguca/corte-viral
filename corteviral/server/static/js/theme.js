// Aparência: Automático (segue o Windows), Claro, Escuro e GTA 6. Fica salva neste computador.
export const THEMES = [
  ["auto", "Automático", "◐"], ["light", "Claro", "☀"], ["dark", "Escuro", "☾"], ["gta", "GTA 6", "🌴"],
];
const KEY = "cv-theme";

export function getTheme() {
  try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; }
}

export function setTheme(theme) {
  try { localStorage.setItem(KEY, theme); } catch (e) { /* sem armazenamento: vale só nesta sessão */ }
  applyTheme(theme);
  document.dispatchEvent(new CustomEvent("themechange", { detail: theme }));
}

export function applyTheme(theme = getTheme()) {
  const root = document.documentElement;
  if (theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

/** Controle segmentado de tema (usado na barra lateral e em Ajustes). */
export function themeSwitcher({ labels = false } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "seg theme-seg";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "Aparência");
  const paint = () => {
    const cur = getTheme();
    wrap.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.theme === cur));
  };
  wrap.innerHTML = THEMES.map(([k, l, icon]) =>
    `<button data-theme="${k}" title="${l}" aria-label="${l}">${icon}${labels ? " " + l : ""}</button>`).join("");
  wrap.querySelectorAll("button").forEach((b) => b.onclick = () => setTheme(b.dataset.theme));
  document.addEventListener("themechange", paint);
  paint();
  return wrap;
}
