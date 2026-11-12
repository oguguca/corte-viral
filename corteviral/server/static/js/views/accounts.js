// Contas conectadas: as contas de YouTube, TikTok e Instagram ficam no n8n (grátis, neste PC);
// aqui o usuário liga o n8n, escolhe as redes e entrega a cada uma os dados de conexão.
import { $, api, esc } from "../api.js";
import { toast } from "../ui.js";

const ICON = { youtube: "▶", tiktok: "♪", instagram: "◎" };
const FALLBACK_NETS = { tiktok: "TikTok", youtube: "YouTube Shorts", instagram: "Instagram Reels" };
const TIKTOK_SCOPES = "user.info.basic,video.publish,video.upload";
const when = (ts) => new Date(ts * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });

function n8nBadge(n) {
  if (n.online) return '<span class="badge good">● ligado</span>';
  if (!n.installed && n.local) return '<span class="badge warn">não instalado</span>';
  return '<span class="badge">○ desligado</span>';
}

function accountBadge(reply, net) {
  const acc = reply && reply.accounts;
  if (!acc || !(net in acc)) return "";
  return acc[net] ? '<span class="badge good">✓ conectada no n8n</span>' : '<span class="badge warn">falta conectar</span>';
}

function setupHtml(net, cfg) {
  if (net === "youtube") return `
    <div class="field" style="max-width:260px"><label for="ytPriv">Visibilidade no YouTube</label>
      <select class="select" id="ytPriv">${[["private", "Privado"], ["unlisted", "Não listado"], ["public", "Público"]]
        .map(([v, l]) => `<option value="${v}" ${cfg.youtube_privacy === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <details><summary class="t-small" style="cursor:pointer">Como conectar o YouTube</summary><ol class="t-small steps-list">
      <li>No Google Cloud, crie um projeto, ative a <b>YouTube Data API v3</b> e crie um <b>ID do cliente OAuth</b> (Aplicativo da Web)
        com o redirecionamento <code>http://localhost:5678/rest/oauth2-credential/callback</code>.</li>
      <li>No n8n: <b>Credenciais › Nova › YouTube OAuth2 API</b>, cole o ID e o segredo e clique em <b>Entrar com o Google</b>.</li>
      <li>Abra o fluxo <b>Corte Viral — postagem</b>, escolha essa credencial no nó <b>YouTube: enviar</b> e salve.</li>
      <li>Sem a verificação do Google, os vídeos sobem como <b>privados</b>; a cota grátis aguenta ~6 envios por dia.</li></ol></details>`;
  if (net === "instagram") return `
    <div class="row" style="align-items:flex-end">
      <div class="field" style="width:200px"><label for="igId">ID da conta do Instagram</label><input class="input num" id="igId" autocomplete="off" placeholder="1784…"></div>
      <div class="field" style="flex:1;min-width:220px"><label for="igTok">Token de acesso</label><input class="input" id="igTok" type="password" autocomplete="off" placeholder="EAAG…"></div>
      <button class="btn" data-setup="instagram">Conectar no n8n</button></div>
    <details><summary class="t-small" style="cursor:pointer">Como conseguir o ID e o token</summary><ol class="t-small steps-list">
      <li>A conta precisa ser <b>Profissional</b> (Criador ou Empresa) e estar ligada a uma <b>Página do Facebook</b>.</li>
      <li>Em developers.facebook.com, crie um app do tipo <b>Empresa</b> e adicione o produto <b>Instagram</b>.</li>
      <li>No Gerenciador de Negócios, crie um <b>Usuário do sistema</b>, dê acesso ao app e à Página e gere um token com
        <code>instagram_basic</code>, <code>instagram_content_publish</code> e <code>pages_read_engagement</code> (esse token não expira).</li>
      <li>O ID da conta aparece em <code>/me/accounts?fields=instagram_business_account</code> no Explorador da Graph API.</li>
      <li>Os dados vão direto para o n8n — o Corte Viral não guarda.</li></ol></details>`;
  return `
    <div class="row" style="align-items:flex-end">
      <div class="field" style="width:190px"><label for="ttKey">Client key</label><input class="input" id="ttKey" autocomplete="off"></div>
      <div class="field" style="width:220px"><label for="ttSecret">Client secret</label><input class="input" id="ttSecret" type="password" autocomplete="off"></div>
      <div class="field" style="flex:1;min-width:220px"><label for="ttRedirect">Redirect URI cadastrada no app</label><input class="input" id="ttRedirect" placeholder="https://…" autocomplete="off"></div></div>
    <div class="row" style="align-items:flex-end">
      <button class="btn" id="ttLogin">1. Abrir login do TikTok</button>
      <div class="field" style="flex:1;min-width:240px"><label for="ttCode">2. Cole aqui o endereço para onde o TikTok te mandou</label>
        <input class="input" id="ttCode" autocomplete="off" placeholder="https://…?code=…"></div>
      <button class="btn" data-setup="tiktok">3. Conectar no n8n</button></div>
    <details><summary class="t-small" style="cursor:pointer">Como criar o app do TikTok</summary><ol class="t-small steps-list">
      <li>Em developers.tiktok.com, crie um app e adicione <b>Login Kit</b> e <b>Content Posting API</b> (com <i>Direct Post</i>).</li>
      <li>Cadastre uma Redirect URI <b>https</b> (pode ser qualquer página sua) e copie a <b>Client key</b> e o <b>Client secret</b>.</li>
      <li>Clique em <b>Abrir login do TikTok</b>, autorize e copie o endereço da página que abrir (ele tem <code>?code=</code>).</li>
      <li>Antes da auditoria do TikTok, os vídeos saem <b>só para você</b> (privados). Clipes acima de 64 MB não são aceitos.</li></ol></details>`;
}

export async function renderAccounts(root) {
  let d, offline = "";
  try { d = await api("/api/publish"); } catch (e) {  // servidor antigo/parado: as 3 redes aparecem mesmo assim
    offline = e.message;
    d = { config: { webhook_url: "http://127.0.0.1:5678/webhook/corte-viral", networks: {}, youtube_privacy: "private", auto_send: false },
          n8n: { installed: false, local: true, online: false, editor_url: "http://localhost:5678" }, recent: [], networks: FALLBACK_NETS };
  }
  const cfg = d.config, n = d.n8n;
  let reply = null;
  root.innerHTML = `
    <header class="page-head"><div><h1 class="t-display">Contas conectadas</h1>
      <div class="sub">As contas ficam no <b>n8n</b>, que roda grátis neste PC e posta por você. O Corte Viral entrega cada clipe
        aprovado com o título, a descrição e as hashtags de cada rede.</div></div></header>
    ${offline ? `<div class="note warn" style="margin-bottom:18px"><span>⚠</span><span>O app precisa ser reiniciado para ativar esta aba
      (feche a janela do Corte Viral e abra de novo). Detalhe: ${esc(offline)}</span></div>` : ""}
    <section class="card pad stack" style="margin-bottom:18px">
      <div class="row"><span class="thumb-lg" aria-hidden="true">⚙</span>
        <div style="flex:1;min-width:0"><h2 class="t-head">n8n</h2><div class="t-small">${esc(n.editor_url)}</div></div>
        <span id="n8nBadge">${n8nBadge(n)}</span></div>
      ${!n.installed && n.local ? `<div class="note warn"><span>⚠</span><span>O n8n ainda não está instalado nesta pasta. Veja “Postagem automática” no README.</span></div>` : ""}
      <div class="row">
        <button class="btn primary" id="n8nStart" ${n.online || !n.installed ? "hidden" : ""}>Iniciar n8n</button>
        <button class="btn" id="n8nOpen">Abrir n8n</button>
        <button class="btn" id="n8nTest">Testar ligação</button>
        <span class="spacer"></span>
        <label class="switch"><input type="checkbox" id="autoSend" ${cfg.auto_send ? "checked" : ""}><span class="track"></span>
          <span>Ao exportar, pôr na fila de postagem</span></label></div>
      <div id="testOut"></div>
      <details><summary class="t-small" style="cursor:pointer">Endereço do fluxo (avançado)</summary>
        <div class="row" style="margin-top:8px;align-items:flex-end"><div class="field" style="flex:1"><label for="hookUrl">Webhook do fluxo “Corte Viral — postagem”</label>
          <input class="input" id="hookUrl" value="${esc(cfg.webhook_url)}"></div><button class="btn" id="saveUrl">Salvar</button></div></details>
    </section>
    <div class="stack" id="nets">${Object.entries(d.networks).map(([k, label]) => `
      <section class="card pad stack" data-net="${k}">
        <div class="row"><span class="thumb-lg net-${k}" aria-hidden="true">${ICON[k]}</span>
          <div style="flex:1;min-width:0"><h2 class="t-head">${esc(label)}</h2><span class="acc" data-acc="${k}"></span></div>
          <label class="switch"><input type="checkbox" data-on="${k}" ${cfg.networks[k] ? "checked" : ""}><span class="track"></span><span>Postar nesta rede</span></label></div>
        ${setupHtml(k, cfg)}
      </section>`).join("")}</div>
    <section class="card pad stack" style="margin-top:18px"><h2 class="t-head">Últimos envios</h2>
      ${d.recent.length ? `<ul class="list">${d.recent.map((e) => `<li class="row" style="padding:8px 12px;border-radius:12px;background:var(--surface-2)">
          <span class="badge ${e.status === "published" ? "good" : e.status === "error" ? "bad" : "info"}">${
            e.status === "published" ? "✓ publicado" : e.status === "error" ? "⚠ falhou" : "→ enviado ao n8n"}</span>
          <b>${esc(d.networks[e.network] || e.network)}</b><span class="t-small" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.project)} · ${esc(e.clip_id)}${e.error ? " · " + esc(e.error) : ""}</span>
          ${e.url ? `<a class="btn small" href="${esc(e.url)}" target="_blank" rel="noopener">Ver</a>` : ""}<span class="t-small num">${when(e.ts)}</span></li>`).join("")}</ul>`
        : '<div class="t-small">Nada enviado ainda. Aprove clipes e use “Enviar para postar” na etapa Exportar.</div>'}
    </section>`;

  const save = async (change) => {
    const body = { webhook_url: $("#hookUrl", root).value.trim(), youtube_privacy: $("#ytPriv", root).value, auto_send: $("#autoSend", root).checked,
                   networks: Object.fromEntries([...root.querySelectorAll("[data-on]")].map((x) => [x.dataset.on, x.checked])), ...change };
    try { await api("/api/publish/config", { method: "PUT", body }); toast("Salvo.", "good"); }
    catch (e) { toast("Não salvou: " + e.message, "bad"); }
  };
  root.querySelectorAll("[data-on]").forEach((x) => x.onchange = () => save());
  $("#autoSend", root).onchange = () => save();
  $("#ytPriv", root).onchange = () => save();
  $("#saveUrl", root).onclick = () => save();

  const paintAccounts = () => root.querySelectorAll("[data-acc]").forEach((el) => { el.innerHTML = accountBadge(reply, el.dataset.acc); });
  const test = async (quiet = false) => {
    const out = $("#testOut", root);
    out.innerHTML = '<span class="t-small"><span class="spinner"></span> testando…</span>';
    const r = await api("/api/publish/test", { method: "POST" }).catch((e) => ({ ok: false, error: e.message }));
    if (r.ok) { reply = r.reply; out.innerHTML = '<div class="note good"><span>✓</span><span>O fluxo do n8n respondeu.</span></div>'; }
    else { reply = null; out.innerHTML = quiet ? "" : `<div class="note bad"><span>⚠</span><span>${esc(r.error)}</span></div>`; }
    paintAccounts();
  };
  $("#n8nTest", root).onclick = () => test();
  $("#n8nOpen", root).onclick = () => api("/api/n8n/open", { method: "POST" }).catch((e) => toast(e.message, "bad"));
  $("#n8nStart", root).onclick = async (e) => {
    const b = e.currentTarget;
    b.disabled = true; b.textContent = "Ligando…";
    try { await api("/api/n8n/start", { method: "POST" }); } catch (err) { toast(err.message, "bad"); b.disabled = false; b.textContent = "Iniciar n8n"; return; }
    for (let i = 0; i < 60; i++) {  // o n8n leva alguns segundos para subir
      await new Promise((r) => setTimeout(r, 2000));
      if (!document.body.contains(root)) return;
      const s = await api("/api/publish").catch(() => null);
      if (s && s.n8n.online) { $("#n8nBadge", root).innerHTML = n8nBadge(s.n8n); b.hidden = true; toast("n8n ligado.", "good"); test(true); return; }
    }
    b.disabled = false; b.textContent = "Iniciar n8n";
    toast("O n8n não respondeu. Veja data/n8n/n8n.log.", "bad");
  };

  $("#ttLogin", root).onclick = () => {
    const key = $("#ttKey", root).value.trim(), redirect = $("#ttRedirect", root).value.trim();
    if (!key || !redirect) { toast("Preencha a Client key e a Redirect URI.", "bad"); return; }
    const q = new URLSearchParams({ client_key: key, scope: TIKTOK_SCOPES, response_type: "code", redirect_uri: redirect, state: "corteviral" });
    window.open("https://www.tiktok.com/v2/auth/authorize/?" + q, "_blank", "noopener");
  };
  root.querySelectorAll("[data-setup]").forEach((b) => b.onclick = async () => {
    const net = b.dataset.setup;
    let fields;
    if (net === "instagram") {
      fields = { ig_user_id: $("#igId", root).value.trim(), access_token: $("#igTok", root).value.trim() };
      if (!fields.ig_user_id || !fields.access_token) { toast("Preencha o ID e o token.", "bad"); return; }
    } else {
      const raw = $("#ttCode", root).value.trim();
      let code = raw;
      try { code = new URL(raw).searchParams.get("code") || raw; } catch (e) { /* colou só o código */ }
      fields = { client_key: $("#ttKey", root).value.trim(), client_secret: $("#ttSecret", root).value.trim(),
                 redirect_uri: $("#ttRedirect", root).value.trim(), code };
      if (Object.values(fields).some((v) => !v)) { toast("Preencha os campos do TikTok e cole o endereço com o código.", "bad"); return; }
    }
    b.disabled = true;
    try {
      await api(`/api/publish/account/${net}`, { method: "POST", body: { fields } });
      root.querySelectorAll(`[data-net="${net}"] input[type=password], #ttCode, #igTok`).forEach((x) => { x.value = ""; });
      toast(`${d.networks[net]} conectado no n8n.`, "good");
      test(true);
    } catch (e) { toast("Não conectou: " + e.message, "bad"); }
    finally { b.disabled = false; }
  });
  if (n.online) test(true);
}
