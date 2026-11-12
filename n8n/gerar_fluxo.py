"""Gera n8n/corte-viral-postagem.json (fluxo de postagem do Corte Viral). Uso: python n8n/gerar_fluxo.py n8n/corte-viral-postagem.json"""
import json
import sys
import uuid
from pathlib import Path

OUT = Path(sys.argv[1])
G = "https://graph.facebook.com/v23.0"
nodes, conns = [], {}


def node(name, ntype, version, params, pos, **extra):
    n = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "cv/" + name)), "name": name,
         "type": ntype, "typeVersion": version, "position": pos}
    n.update(extra)
    nodes.append(n)
    return name


def link(src, dst, out=0):
    outs = conns.setdefault(src, {"main": []})["main"]
    while len(outs) <= out:
        outs.append([])
    outs[out].append({"node": dst, "type": "main", "index": 0})


def code(name, js, pos, err=False):
    extra = {"onError": "continueErrorOutput"} if err else {}
    return node(name, "n8n-nodes-base.code", 2, {"jsCode": js}, pos, **extra)


def http(name, pos, method, url, query=None, headers=None, body=None, json_body=None, binary=False,
         never_error=False, err=True):
    p = {"method": method, "url": url, "options": {"timeout": 600000}}
    if query:
        p.update(sendQuery=True, queryParameters={"parameters": [{"name": k, "value": v} for k, v in query]})
    if headers:
        p.update(sendHeaders=True, headerParameters={"parameters": [{"name": k, "value": v} for k, v in headers]})
    if body:
        p.update(sendBody=True, contentType="form-urlencoded",
                 bodyParameters={"parameters": [{"name": k, "value": v} for k, v in body]})
    if json_body:
        p.update(sendBody=True, contentType="json", specifyBody="json", jsonBody=json_body)
    if binary:
        p.update(sendBody=True, contentType="binaryData", inputDataFieldName="data")
    if never_error:
        p["options"]["response"] = {"response": {"neverError": True}}
    extra = {"onError": "continueErrorOutput"} if err else {}
    return node(name, "n8n-nodes-base.httpRequest", 4.2, p, pos, **extra)


def read_file(name, net, pos):
    return node(name, "n8n-nodes-base.readWriteFile", 1.1,
                {"operation": "read", "fileSelector": f"={{{{ {P(net)}.file_path }}}}",
                 "options": {"dataPropertyName": "data"}}, pos, onError="continueErrorOutput")


def P(net):  # dados do clipe para uma rede (vindos do nó Preparar)
    return f"$('Preparar').all().find(i => i.json.network === '{net}').json"


def cb_js(net, status_expr, url_expr, error_expr, pre=""):
    return f"""const p = $('Preparar').all().find(i => i.json.network === '{net}').json;
const r = $input.first().json;
{pre}
return [{{ json: {{ callback_url: p.callback_url, body: {{
  token: p.token, send_id: p.send_id, project: p.project, clip_id: p.clip_id, network: '{net}',
  status: {status_expr}, url: {url_expr}, error: {error_expr} }} }} }}];"""


FAIL_JS = lambda net, label: cb_js(  # noqa: E731
    net, "'error'", "''", "msg",
    pre="const e = r.error;\nconst msg = String((e && (e.description || e.message || e)) || r.message || "
        f"'falha ao postar no {label}').slice(0, 450);")

# ---------------- entrada ----------------
node("Receber do Corte Viral", "n8n-nodes-base.webhook", 2.1,
     {"httpMethod": "POST", "path": "corte-viral", "responseMode": "responseNode",
      # nenhuma página do navegador pode chamar (CORS); o Corte Viral chama direto, sem Origin
      "options": {"allowedOrigins": "https://corte-viral.invalid"}},
     [0, 400], webhookId="7c0e3f5e-2f5b-4c33-9a7e-c0a7e5e00001")

code("Preparar", r"""// Separa: teste da ligação, conexão de conta (TikTok/Instagram) ou clipe para postar.
// O token do Corte Viral é gravado aqui na importação: sem ele, nada é aceito (nenhum site consegue postar).
const APP_TOKEN = '__CV_TOKEN__';
const body = $input.first().json.body || {};
const reply = (obj) => [{ json: { kind: 'reply', reply: obj } }];
if (APP_TOKEN.startsWith('__') || typeof body.token !== 'string' || body.token !== APP_TOKEN) {
  return reply({ ok: false, error: 'token inválido: abra o n8n pelo botão Iniciar n8n do Corte Viral' });
}
const sd = $getWorkflowStaticData('global');

if (body.test) {
  return reply({ ok: true, accounts: {
    tiktok: !!(sd.tiktok && sd.tiktok.refresh_token),
    instagram: !!(sd.instagram && sd.instagram.access_token) } });
}
if (body.setup === 'instagram') {
  if (!body.ig_user_id || !body.access_token) return reply({ ok: false, error: 'faltou o ID ou o token' });
  sd.instagram = { ig_user_id: String(body.ig_user_id), access_token: String(body.access_token) };
  return reply({ ok: true });
}
if (body.setup === 'tiktok') {
  if (!body.client_key || !body.client_secret || !body.code || !body.redirect_uri)
    return reply({ ok: false, error: 'faltaram dados do TikTok' });
  return [{ json: { kind: 'tiktok_setup', client_key: body.client_key, client_secret: body.client_secret,
                    code: body.code, redirect_uri: body.redirect_uri } }];
}
if (!body.file_path || !Array.isArray(body.networks)) return reply({ ok: false, error: 'pedido inválido' });

const common = { kind: 'upload', send_id: body.send_id, project: body.project, clip_id: body.clip_id,
                 callback_url: body.callback_url, token: body.token, file_path: body.file_path,
                 file_size: body.file_size };
return body.networks.map((network) => {
  const item = { ...common, network, post: body[network] || {} };
  if (network === 'tiktok') {
    if (!(sd.tiktok && sd.tiktok.refresh_token)) item.error = 'TikTok não conectado no n8n';
    else item.creds = sd.tiktok;
  }
  if (network === 'instagram') {
    if (!(sd.instagram && sd.instagram.access_token)) item.error = 'Instagram não conectado no n8n';
    else item.creds = sd.instagram;
  }
  return { json: item };
});""", [220, 400])

node("Tipo de pedido", "n8n-nodes-base.switch", 3.2,
     {"mode": "expression", "numberOutputs": 3,
      "output": "={{ $json.kind === 'reply' ? 0 : ($json.kind === 'tiktok_setup' ? 1 : 2) }}"},
     [440, 400])
node("Responder", "n8n-nodes-base.respondToWebhook", 1.1,
     {"respondWith": "json", "responseBody": "={{ JSON.stringify($json.reply) }}", "options": {}}, [680, 160])
node("Recebido", "n8n-nodes-base.respondToWebhook", 1.1,
     {"respondWith": "json", "responseBody": "={{ JSON.stringify({ ok: true, received: true }) }}", "options": {}},
     [680, 560])
link("Receber do Corte Viral", "Preparar")
link("Preparar", "Tipo de pedido")
link("Tipo de pedido", "Responder", 0)
link("Tipo de pedido", "TikTok: trocar código", 1)
link("Tipo de pedido", "Recebido", 2)

# ---------------- conectar TikTok (troca o código pelo refresh token) ----------------
http("TikTok: trocar código", [680, 340], "POST", "https://open.tiktokapis.com/v2/oauth/token/",
     body=[("client_key", "={{ $json.client_key }}"), ("client_secret", "={{ $json.client_secret }}"),
           ("code", "={{ $json.code }}"), ("grant_type", "authorization_code"),
           ("redirect_uri", "={{ $json.redirect_uri }}")],
     never_error=True, err=False)
code("TikTok: guardar conta", r"""const r = $input.first().json;
const s = $('Preparar').first().json;
if (!r.refresh_token) {
  return [{ json: { reply: { ok: false, error: 'TikTok recusou o código: ' + (r.error_description || r.error || 'tente de novo') } } }];
}
const sd = $getWorkflowStaticData('global');
sd.tiktok = { client_key: s.client_key, client_secret: s.client_secret, refresh_token: r.refresh_token, open_id: r.open_id };
return [{ json: { reply: { ok: true } } }];""", [900, 340])
node("Responder (TikTok)", "n8n-nodes-base.respondToWebhook", 1.1,
     {"respondWith": "json", "responseBody": "={{ JSON.stringify($json.reply) }}", "options": {}}, [1120, 340])
link("TikTok: trocar código", "TikTok: guardar conta")
link("TikTok: guardar conta", "Responder (TikTok)")

# ---------------- qual rede ----------------
node("Qual rede?", "n8n-nodes-base.switch", 3.2,
     {"mode": "expression", "numberOutputs": 4,
      "output": "={{ $json.error ? 3 : ['tiktok', 'youtube', 'instagram'].indexOf($json.network) }}"},
     [900, 560])
link("Recebido", "Qual rede?")
code("Sem conta", """const r = $input.all();
return r.map((it) => ({ json: { callback_url: it.json.callback_url, body: {
  token: it.json.token, send_id: it.json.send_id, project: it.json.project, clip_id: it.json.clip_id,
  network: it.json.network, status: 'error', url: '', error: it.json.error } } }));""", [1120, 1240])
link("Qual rede?", "Sem conta", 3)

AV = "Avisar o Corte Viral"
http(AV, [2900, 700], "POST", "={{ $json.callback_url }}", json_body="={{ JSON.stringify($json.body) }}",
     err=False)
nodes[-1].update(retryOnFail=True, maxTries=5, waitBetweenTries=5000)  # app reiniciando: tenta de novo
link("Sem conta", AV)

# ---------------- TikTok ----------------
T = P("tiktok")
http("TikTok: renovar acesso", [1120, 480], "POST", "https://open.tiktokapis.com/v2/oauth/token/",
     body=[("client_key", "={{ $json.creds.client_key }}"), ("client_secret", "={{ $json.creds.client_secret }}"),
           ("grant_type", "refresh_token"), ("refresh_token", "={{ $json.creds.refresh_token }}")],
     never_error=True)
code("TikTok: guardar acesso", r"""const r = $input.first().json;
if (!r.access_token) throw new Error('TikTok recusou o acesso (' + (r.error_description || r.error || 'refaça a conexão') + ')');
const sd = $getWorkflowStaticData('global');
sd.tiktok = { ...sd.tiktok, refresh_token: r.refresh_token || sd.tiktok.refresh_token };
const p = $('Preparar').all().find(i => i.json.network === 'tiktok').json;
if (p.file_size > 64 * 1024 * 1024) throw new Error('clipe acima de 64 MB: o TikTok não aceita num envio só');
return [{ json: { access_token: r.access_token, size: p.file_size, caption: String(p.post.caption || '').slice(0, 2200) } }];""",
     [1340, 480], err=True)
http("TikTok: iniciar envio", [1560, 480], "POST", "https://open.tiktokapis.com/v2/post/publish/video/init/",
     headers=[("Authorization", "=Bearer {{ $json.access_token }}")],
     json_body="={{ JSON.stringify({ post_info: { title: $json.caption, privacy_level: 'SELF_ONLY', "
               "disable_comment: false, disable_duet: false, disable_stitch: false }, source_info: { "
               "source: 'FILE_UPLOAD', video_size: $json.size, chunk_size: $json.size, total_chunk_count: 1 } }) }}")
read_file("TikTok: ler vídeo", "tiktok", [1780, 480])
http("TikTok: enviar vídeo", [2000, 480], "PUT", "={{ $('TikTok: iniciar envio').first().json.data.upload_url }}",
     headers=[("Content-Type", "video/mp4"),
              ("Content-Range", f"=bytes 0-{{{{ {T}.file_size - 1 }}}}/{{{{ {T}.file_size }}}}")],
     binary=True)
code("TikTok: resultado", cb_js("tiktok", "'published'", "''", "''"), [2220, 480])
code("TikTok: falhou", FAIL_JS("tiktok", "TikTok"), [2220, 620])
link("Qual rede?", "TikTok: renovar acesso", 0)
for a, b in (("TikTok: renovar acesso", "TikTok: guardar acesso"), ("TikTok: guardar acesso", "TikTok: iniciar envio"),
             ("TikTok: iniciar envio", "TikTok: ler vídeo"), ("TikTok: ler vídeo", "TikTok: enviar vídeo"),
             ("TikTok: enviar vídeo", "TikTok: resultado")):
    link(a, b, 0)
    link(a, "TikTok: falhou", 1)
link("TikTok: resultado", AV)
link("TikTok: falhou", AV)

# ---------------- YouTube ----------------
Y = P("youtube")
read_file("YouTube: ler vídeo", "youtube", [1120, 800])
node("YouTube: enviar", "n8n-nodes-base.youTube", 1,
     {"resource": "video", "operation": "upload", "title": f"={{{{ {Y}.post.title }}}}", "regionCode": "BR",
      "categoryId": "20", "binaryProperty": "data",
      "options": {"description": f"={{{{ {Y}.post.description }}}}", "tags": f"={{{{ ({Y}.post.tags || []).join(',') }}}}",
                  "privacyStatus": f"={{{{ {Y}.post.privacy || 'private' }}}}", "selfDeclaredMadeForKids": False,
                  "defaultLanguage": "pt"}},
     [1340, 800], onError="continueErrorOutput", disabled=True)  # religar depois de escolher a credencial
# Depois do envio: declara o que a tela do YouTube Studio pergunta (o nó do YouTube não tem esses campos).
# containsSyntheticMedia=false → "conteúdo alterado ou sintético: Não" (cortes de live reais);
# promoção paga não pode ser marcada pela API e já fica "Não" por padrão.
# Ao atualizar "status", o YouTube apaga o que não vier no pedido: por isso lê o status atual antes
# (o vídeo pode ter ficado privado à força, se o app do Google não for verificado) e manda todos os campos.
# O nó do YouTube devolve o código do vídeo em "uploadId".
VID = "($('YouTube: enviar').first().json.uploadId || $('YouTube: enviar').first().json.id)"
YT_CRED = {"authentication": "predefinedCredentialType", "nodeCredentialType": "youTubeOAuth2Api"}
node("YouTube: ver status", "n8n-nodes-base.httpRequest", 4.2,
     {"method": "GET", "url": "https://www.googleapis.com/youtube/v3/videos", **YT_CRED,
      "sendQuery": True, "queryParameters": {"parameters": [{"name": "part", "value": "status"},
                                                            {"name": "id", "value": f"={{{{ {VID} }}}}"}]},
      "options": {"timeout": 60000}},
     [1450, 640], onError="continueRegularOutput", disabled=True)  # usa a mesma credencial do nó YouTube: enviar
CUR = "(($json.items && $json.items[0] && $json.items[0].status) || {})"
node("YouTube: declarar", "n8n-nodes-base.httpRequest", 4.2,
     {"method": "PUT", "url": "https://www.googleapis.com/youtube/v3/videos", **YT_CRED,
      "sendQuery": True, "queryParameters": {"parameters": [{"name": "part", "value": "status"}]},
      "sendBody": True, "contentType": "json", "specifyBody": "json",
      "jsonBody": f"={{{{ JSON.stringify({{ id: {VID}, status: {{ "
                  f"privacyStatus: {CUR}.privacyStatus || {Y}.post.privacy || 'private', "
                  f"embeddable: {CUR}.embeddable !== false, license: {CUR}.license || 'youtube', "
                  f"publicStatsViewable: {CUR}.publicStatsViewable !== false, "
                  "selfDeclaredMadeForKids: false, containsSyntheticMedia: false } }) }}",
      "options": {"timeout": 60000}},
     [1450, 760], onError="continueRegularOutput", disabled=True)
code("YouTube: resultado", cb_js("youtube", "r.id ? 'published' : 'error'",
                                 "r.id ? 'https://youtube.com/shorts/' + r.id : ''",
                                 "r.id ? '' : 'YouTube ainda não conectado: no n8n, escolha a credencial no nó YouTube: enviar e ative-o'",
                                 pre="const r2 = $('YouTube: enviar').first().json; Object.assign(r, { id: r2.uploadId || r2.id });"),
     [1560, 800])
code("YouTube: falhou", FAIL_JS("youtube", "YouTube"), [1560, 940])
link("Qual rede?", "YouTube: ler vídeo", 1)
for a, b in (("YouTube: ler vídeo", "YouTube: enviar"), ("YouTube: enviar", "YouTube: ver status")):
    link(a, b, 0)
    link(a, "YouTube: falhou", 1)
link("YouTube: ver status", "YouTube: declarar")
link("YouTube: declarar", "YouTube: resultado")  # falhar ao declarar não desfaz o vídeo publicado
link("YouTube: resultado", AV)
link("YouTube: falhou", AV)

# ---------------- Instagram (Graph API, envio resumable) ----------------
I = P("instagram")
TOK = f"={{{{ {I}.creds.access_token }}}}"
CONT = "$('Instagram: criar envio').first().json.id"
http("Instagram: criar envio", [1120, 1080], "POST", f"={G}/{{{{ $json.creds.ig_user_id }}}}/media",
     query=[("media_type", "REELS"), ("upload_type", "resumable"), ("share_to_feed", "true"),
            ("caption", "={{ String($json.post.caption || '').slice(0, 2200) }}"),
            ("access_token", "={{ $json.creds.access_token }}")])
read_file("Instagram: ler vídeo", "instagram", [1340, 1080])
http("Instagram: enviar vídeo", [1560, 1080], "POST", f"=https://rupload.facebook.com/ig-api-upload/v23.0/{{{{ {CONT} }}}}",
     headers=[("Authorization", f"=OAuth {{{{ {I}.creds.access_token }}}}"), ("offset", "0"),
              ("file_size", f"={{{{ {I}.file_size }}}}")],
     binary=True)
node("Instagram: esperar", "n8n-nodes-base.wait", 1.1, {"amount": 20, "unit": "seconds"}, [1780, 1080],
     webhookId="7c0e3f5e-2f5b-4c33-9a7e-c0a7e5e00002")
http("Instagram: status", [2000, 1080], "GET", f"={G}/{{{{ {CONT} }}}}",
     query=[("fields", "status_code,status"), ("access_token", TOK)])
node("Instagram: pronto?", "n8n-nodes-base.switch", 3.2,
     {"mode": "expression", "numberOutputs": 3,
      # espera no máximo 30 × 20 s = 10 min; depois desiste e avisa
      "output": "={{ $json.status_code === 'FINISHED' ? 0 : ($json.status_code === 'IN_PROGRESS' && $runIndex < 30 ? 1 : 2) }}"},
     [2220, 1080])
http("Instagram: publicar", [2440, 1000], "POST", f"={G}/{{{{ {I}.creds.ig_user_id }}}}/media_publish",
     query=[("creation_id", f"={{{{ {CONT} }}}}"), ("access_token", TOK)])
http("Instagram: link", [2660, 1000], "GET", f"={G}/{{{{ $json.id }}}}",
     query=[("fields", "permalink"), ("access_token", TOK)])
code("Instagram: resultado", cb_js("instagram", "'published'", "r.permalink || ''", "''"), [2880, 1000])
code("Instagram: falhou", FAIL_JS("instagram", "Instagram"), [2660, 1240])
code("Instagram: recusado", cb_js("instagram", "'error'", "''",
                                  "r.status_code === 'IN_PROGRESS' ? 'o Instagram demorou mais de 10 min para processar o vídeo'"
                                  " : 'o Instagram recusou o vídeo (' + (r.status || r.status_code || 'sem detalhe') + ')'"),
     [2440, 1180])
link("Qual rede?", "Instagram: criar envio", 2)
for a, b in (("Instagram: criar envio", "Instagram: ler vídeo"), ("Instagram: ler vídeo", "Instagram: enviar vídeo"),
             ("Instagram: enviar vídeo", "Instagram: esperar")):
    link(a, b, 0)
    link(a, "Instagram: falhou", 1)
link("Instagram: esperar", "Instagram: status")
link("Instagram: status", "Instagram: pronto?", 0)
link("Instagram: status", "Instagram: falhou", 1)
link("Instagram: pronto?", "Instagram: publicar", 0)
link("Instagram: pronto?", "Instagram: esperar", 1)
link("Instagram: pronto?", "Instagram: recusado", 2)
for a, b in (("Instagram: publicar", "Instagram: link"), ("Instagram: link", "Instagram: resultado")):
    link(a, b, 0)
    link(a, "Instagram: falhou", 1)
link("Instagram: resultado", AV)
link("Instagram: falhou", AV)
link("Instagram: recusado", AV)

wf = {"id": "CorteViralPost01", "name": "Corte Viral — postagem", "active": False, "nodes": nodes,
      # execuções não são guardadas: elas teriam tokens e segredos em texto puro no banco do n8n
      "connections": conns, "settings": {"executionOrder": "v1", "saveManualExecutions": False,
                                         "saveDataSuccessExecution": "none", "saveDataErrorExecution": "none",
                                         "saveExecutionProgress": False},
      "pinData": {}, "tags": []}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(len(nodes), "nós")
