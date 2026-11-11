// Rotas por hash: #/  ·  #/p/<projeto>/<etapa>. Voltar/avançar do navegador funcionam.
export const STEPS = [
  ["video", "Vídeo"], ["moments", "Momentos"], ["clips", "Clipes"], ["export", "Exportar"],
];

export function parse() {
  let parts;
  try { parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent); }
  catch (e) { return { view: "bad" }; }  // endereço malformado não deixa a tela em branco
  if (parts[0] === "contas") return { view: "accounts" };
  if (parts[0] === "p" && parts[1]) {
    const step = STEPS.some(([k]) => k === parts[2]) ? parts[2] : "video";
    return { view: "project", name: parts[1], step };
  }
  return { view: "home" };
}

export const go = (name, step = "video") => { location.hash = `#/p/${encodeURIComponent(name)}/${step}`; };
export const goHome = () => { location.hash = "#/"; };
