# Corte Viral

Aplicativo desktop para Windows que transforma lives e VODs longos (YouTube,
Twitch, Kick ou arquivo local) em clipes verticais 9:16 prontos para TikTok,
YouTube Shorts, Instagram Reels e Kwai.

> **Status:** Fases 1–5 concluídas: do link da live até a pasta com clipes 9:16 legendados e os textos
> de postagem para TikTok, Shorts, Reels e Kwai.

Saída: MP4 9:16 (1080x1920), um único arquivo compatível com TikTok, YouTube
Shorts, Instagram Reels e Kwai, com legenda e gancho dentro da zona segura comum.

## Como funciona

1. Cole um link ou escolha um arquivo local.
2. O app baixa o vídeo e transcreve o áudio com marcação de tempo por palavra
   (faster-whisper, local na GPU).
3. Detecta os melhores momentos combinando picos de áudio, picos de chat e
   análise da transcrição por IA (Claude).
4. Cada momento recebe nota de 0 a 100 e uma justificativa.
5. Gera os N melhores clipes — perfil `viral` (15–60 s) ou `monetizacao`
   (61–120 s, atende o mínimo de 1 min do TikTok Creator Rewards) — com início e
   fim em pontos naturais da fala.
6. Converte para 9:16 (facecam em cima + gameplay embaixo, ou recorte seguindo o rosto).
7. Legendas animadas palavra por palavra — **opcionais**.
8. Gera gancho, título, descrição e hashtags.
9. Tela de revisão para ajustar, aprovar e exportar em lote.

## Requisitos

- Windows 10/11
- GPU NVIDIA recomendada (testado em GTX 1050 Ti 4 GB; sem GPU funciona na CPU, mais lento)
- Python 3.11 e FFmpeg
- Node.js (a partir da Fase 5)
- Chave da API Anthropic (a partir da Fase 2)

## Instalação

```powershell
# 1. Ferramentas (uma vez só; reabra o terminal depois)
winget install -e --id Python.Python.3.11
winget install -e --id Gyan.FFmpeg

# 2. Ambiente Python do projeto (dentro da pasta do repositório)
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# 3. Configuração (opcional — sem ela, valem os padrões)
copy config.example.yaml config.yaml
copy .env.example .env
```

As bibliotecas CUDA (cuBLAS/cuDNN) vêm pelo `pip`; **não** é preciso instalar o
CUDA Toolkit. Basta ter o driver NVIDIA atualizado.

Na primeira transcrição, o modelo Whisper é baixado para `models/`
(`medium` ≈ 1,5 GB).

## Uso

### Interface (recomendado)

Dê dois cliques em **`Corte Viral.bat`** (na pasta do projeto). Abre uma janela onde
você cola o link ou escolhe o arquivo, acompanha o progresso e vê a transcrição
sincronizada com o vídeo (clique numa palavra para pular até ela).

**Só um trecho do vídeo:** já assistiu e sabe onde estão os momentos? Em **Novo vídeo › Só um trecho do vídeo**,
informe onde começa e onde termina (`1:20:00` até `1:45:00`, ou `80:00`, ou `1h20m`). Só esse trecho é baixado
(link) ou separado do arquivo (sem recodificar) e transcrito — muito mais rápido que o VOD inteiro. Cada trecho
vira um projeto próprio, com o trecho no nome.

### Painel inicial (metas de monetização)

O app abre no **Painel** — e clicar em **Corte Viral** (no alto da barra lateral) volta para ele:

- **Meta de hoje**: quantos clipes você exportou hoje contra a meta diária (padrão 3, editável), quantos foram
  postados, gráfico dos últimos 7 dias e sequência de dias batendo a meta.
- **Canal do YouTube**: com uma **chave da YouTube Data API v3** (grátis, no Google Cloud) e o @ do canal, o
  painel lê inscritos, views do dia/semana e as views dos Shorts dos últimos 90 dias (atualiza a cada 30 min ou
  no botão **Atualizar**; a chave fica só em `data/goals.json`, nunca volta para a tela).
- **Metas de monetização** com barra de progresso: YouTube 1º nível (500 inscritos + 3 Shorts + 3 mi de views de
  Shorts em 90 dias), YouTube anúncios (1.000 inscritos + 10 mi de views de Shorts em 90 dias),
  TikTok (10 mil seguidores + 100 mil views em 30 dias; só vídeos com mais de 1 min), Instagram e Kwai.
  TikTok, Instagram e Kwai não têm API grátis para contas pessoais: digite os números em **Digitar os números de hoje**.

### Encontrar os melhores momentos

Abra um projeto transcrito, escolha o perfil (**Viral** 15–60 s ou **Monetização**
61–120 s), quantos momentos e a IA, e clique em **Encontrar momentos**. Cada momento
mostra nota de 0 a 100, justificativa e uma sugestão de gancho; clicar toca o trecho.

- **Duas etapas:** a triagem lê a transcrição de todos os candidatos; depois a IA **assiste**
  (vídeo + áudio, em baixa resolução) os 15 melhores e dá nota por critério — **Gancho**,
  **Emoção**, **Visual**, **Final** e **Contexto** — sugere onde cortar e explica o que acontece,
  por que funciona e o risco (ex.: violência que pode ser moderada). Selo **"IA assistiu"**.
- **👍 / 👎** em cada momento: a IA usa seus exemplos (até 6) para calibrar as próximas avaliações.
  **Reavaliar do zero** ignora o resultado salvo.
- Custo: cerca de 6–8 mil tokens por trecho assistido (~120 mil por VOD de 30 min).
- A nota combina sinais locais (picos de volume, palavras de reação como "MEU DEUS"
  e "kkkk", ritmo de fala, picos de chat) com a avaliação da IA.
- **IA grátis:** crie uma chave em aistudio.google.com e coloque no `.env` como
  `GEMINI_API_KEY=...`. Sem chave (ou com "Sem IA"), funciona só com os sinais.
- O card **Uso da IA hoje** mostra tokens, requisições e a % do limite diário, com
  gráfico por minuto. O Google não informa a cota grátis pela API: confira em
  AI Studio → *Rate limits* e ajuste `llm.daily_request_limit` / `llm.daily_token_limit`
  no `config.yaml`.

### Gerar os clipes verticais (9:16)

Em cada momento, **Gerar clipe** cria o MP4 1080x1920 em `projects/<projeto>/clips/`;
**Gerar todos os clipes** faz a lista inteira. **Ver clipe** abre o player vertical.

Abra **Layout do vertical** para conferir o recorte:

- O app detecta sozinho as câmeras (rostos que ficam parados no mesmo lugar) e a
  janela da gameplay (a área que se mexe). Com várias câmeras, escolha qual usar.
- Arraste os retângulos (o canto branco redimensiona); a prévia 9:16 atualiza na hora.
- Layouts: **Câmera + gameplay** (câmera em cima, 30 %), **Seguir o rosto** (recorte
  vertical acompanhando o rosto) e **Vídeo com fundo desfocado** (sem câmera).
- **Salvar layout deste streamer** grava em `presets/streamers/<streamer>.json`; os
  próximos VODs do mesmo canal já saem com esse recorte.

Codificação: tenta a GPU (NVENC) e cai para a CPU (libx264) se não der. Placas da linha
Pascal (GTX 10xx) com driver 58x não rodam o NVENC do FFmpeg 9 (exige driver ≥ 610).

### Legendas animadas

Os clipes saem com legenda no estilo TikTok: 1 a 3 palavras por vez, a palavra falada
destacada em amarelo com um "pop", contorno preto e sempre dentro da área segura das
plataformas (não fica atrás dos botões do TikTok/Shorts/Reels).

- **Legendas** (no projeto): liga/desliga padrão, fonte (Arial Black, Impact, Segoe UI
  Black…), tamanho, altura na tela, palavras por vez, cores e maiúsculas — com prévia.
- Em cada momento: chave **Legenda** (só aquele clipe) e **Editar legenda** para corrigir
  palavras que a transcrição errou (os tempos são reaproveitados). Depois, **Gerar de novo**.
- Linha de comando: `render ... --no-captions` / `--captions`.

### Revisar e exportar

1. Em cada momento: **Aprovar** / **Descartar** (clicar de novo desfaz) e **Revisar corte e textos**:
   - ajuste início/fim (±1 s ou "= player", usando o ponto atual do vídeo) e **Salvar corte**;
   - **gancho** — frase escrita nos 3 primeiros segundos do vídeo (desligável por clipe);
   - abas **TikTok / Shorts / Reels / Kwai** com título, descrição e hashtags editáveis e **Copiar tudo**
     (no Shorts também as **tags do YouTube**; no Reels no máximo 5 hashtags, limite do Instagram).
   - **Prévia ao vivo**: ao lado, o clipe aparece em 9:16 já montado (câmera + gameplay), com legenda, gancho e
     crédito, tocando direto do vídeo original — sem gerar nada. Arrastar a faixa LEGENDA, mudar o gancho ou
     ligar/desligar a legenda muda a prévia na hora. Com o clipe gerado, alterne entre **Prévia ao vivo** e
     **Clipe gerado**.
   - **Faixa da divisa** (aba **Faixa** do clipe): escolha um PNG com fundo transparente (pode ser 1080×1920 com a
     faixa no meio — o app usa só a parte visível) e ele vai por cima da divisa entre a câmera e o jogo. Ajuste
     **Altura** e **Tamanho** olhando a prévia ao vivo; o gancho desce para não ficar embaixo da faixa. Vale para
     todos os clipes (no layout câmera + jogo começa na divisa; nos outros, ajuste a altura) e salva sozinho
     em `data/` (fora do git).
2. **Gerar textos com IA** escreve gancho, títulos (feitos para ganhar views), descrições, hashtags e tags de
   cada rede (para os aprovados; sem nenhum aprovado, para todos). Sem IA, o app gera textos simples.
3. **Exportar aprovados** cria em `exports/<projeto>/` **uma pasta por clipe** com o MP4 final (1080x1920) e
   **um .txt por rede**, cada um no formato do envio daquela rede, e abre a pasta:
   - `TikTok.txt`, `Instagram Reels.txt` e `Kwai.txt`: título + descrição + hashtags juntos, prontos para colar
     no único campo de texto dessas redes;
   - `YouTube Shorts.txt`: título, descrição e **tags** (separadas por vírgula) em blocos, um para cada campo.
   - Clipe aprovado ainda sem textos: a IA escreve antes de exportar (sem apagar textos que você editou).
   - Só um clipe, agora: na tela **Clipes**, **⬇ Exportar este clipe** exporta o clipe aberto (aprovado ou não)
     e abre a pasta. Se você mudou o corte e não salvou, ele pergunta e salva antes.
   - Avisa se o streamer não está marcado como autorizado (Layout do vertical → "Tenho autorização") e se o
     trecho já foi exportado antes (`data/history.json`).
   - Com **Crédito** preenchido no layout, o vídeo mostra "@streamer" discreto no canto e a descrição termina
     com "Créditos: @streamer".

Linha de comando: `.venv\Scripts\python -m corteviral export projects\<pasta-do-projeto>`

### Login de download (vídeos com restrição de idade, privados ou de inscritos)

Em **Ajustes › Login de download**, clique em **"Conectar contas"**. Abre uma janela do Edge com abas do YouTube,
Twitch e Kick, num perfil separado só do app. Entre nas contas que quiser e
feche a janela. Pronto: antes de cada download o app renova essa sessão sozinho.

- Funciona com **qualquer navegador** que você usa (Brave, Chrome, Firefox…),
  mesmo aberto — o app não mexe no seu navegador principal.
- Por quê assim: Chrome/Brave/Edge recentes criptografam os cookies de um jeito
  que nenhum programa externo consegue ler. No perfil do app, o próprio Edge
  entrega a sessão ao Corte Viral.
- A sessão fica em `data/` (fora do git). Para desconectar, apague essa pasta.

Alternativas em "Outras formas de login": Firefox (lê direto do perfil) ou um
arquivo `cookies.txt` exportado com a extensão "Get cookies.txt LOCALLY".

### Postagem automática (Contas conectadas + n8n)

As contas de **YouTube, TikTok e Instagram** ficam no [n8n](https://n8n.io), gratuito e rodando neste PC.
O Corte Viral entrega cada clipe aprovado (caminho do MP4 + título, descrição e hashtags de cada rede) ao
fluxo **"Corte Viral — postagem"**, que posta e avisa de volta o resultado.

**Instalar o n8n (uma vez só, precisa do Node.js 24 ou mais novo):**

```powershell
mkdir tools\n8n -Force; cd tools\n8n
npm install n8n --ignore-scripts
```

`--ignore-scripts` evita compilar o conector de Kafka (não usado aqui), que exige Visual Studio.
O programa (`tools/`) e os dados do n8n (`data/n8n`: login, contas, chaves) nunca vão para o GitHub.

**Ligar e preparar:**

1. Barra lateral › **Contas conectadas** › **Iniciar n8n** e depois **Abrir n8n** (`http://localhost:5678`).
   Na primeira vez, crie o usuário dono do n8n.
2. O botão **Iniciar n8n** já importa e publica o fluxo `n8n/corte-viral-postagem.json` (na primeira vez e
   quando o fluxo mudar). O nó **YouTube: enviar** vem desligado até você escolher a credencial do YouTube nele.
3. Em cada rede, siga o passo a passo da própria tela (botão "Como conectar…"):
   - **YouTube:** credencial **YouTube OAuth2 API** dentro do n8n (projeto no Google Cloud com a YouTube Data API v3).
     Sem a verificação do Google, os vídeos sobem **privados**; a cota grátis aceita até 100 envios por dia.
     Depois de cada envio, o nó **YouTube: declarar** marca *conteúdo alterado ou sintético: Não* e *conteúdo
     para crianças: Não* (usa a mesma credencial; promoção paga já fica *Não* por padrão). Ao atualizar o fluxo,
     a credencial escolhida é mantida.
     O título do Shorts leva o máximo de hashtags que cabe em 100 caracteres (`#shorts` primeiro).
   - **Instagram:** conta **Profissional** ligada a uma Página do Facebook + app da Meta. Cole o ID da conta e o
     token de um *Usuário do sistema* na tela e clique em **Conectar no n8n**.
   - **TikTok:** app em developers.tiktok.com com *Login Kit* e *Content Posting API*. Preencha client key,
     secret e redirect URI, faça o login e cole o endereço de volta. Antes da auditoria do TikTok os vídeos
     saem **só para você**; clipes acima de 64 MB não são aceitos.
4. Ligue as redes desejadas e clique em **Testar ligação**.

**Programar postagem (fila):** na etapa **Exportar**, card **⏱ Programar postagem** — escolha o intervalo
(ex.: 1 clipe a cada 15 min) e clique em **Programar N clipe(s)**. Antes, a IA escreve título, descrição, hashtags
e tags de quem ainda não tem, e os vídeos já ficam prontos. Uma leva nova começa sozinha depois da última que
está na fila. Cada clipe na fila mostra a hora (⏱) e pode ser tirado com ✕. O Corte Viral precisa ficar aberto:
na hora marcada ele liga o n8n se estiver desligado. Com o app fechado, os atrasados saem um por vez ao abrir.
A fila fica em `data/publish_queue.json`.

A barra lateral tem a aba **⏱ Fila de postagem**: todos os clipes programados, de todos os vídeos, por ordem
de horário. Mostra o próximo envio (com contagem), quantos faltam, os publicados hoje (com link ↗) e o erro de
quem falhou, com **Tentar de novo** (volta para o fim da fila). Atualiza sozinha a cada 15 s.

**Postar agora:** na etapa **Exportar**, **Enviar N clipe(s) agora** manda todos de uma vez.
Com **"Enviar para postar ao exportar"** ligado (na etapa Exportar ou em Contas conectadas), cada exportação
**pergunta antes** se deve pôr os clipes na fila (só os aprovados). Sem o "OK", só exporta os arquivos — nada sobe
sem confirmação. Na fila, eles saem um por vez, no intervalo da fila.
Cada clipe mostra o status por rede (→ enviado, ✓ publicado com link, ⚠ falhou); clipes já publicados numa rede
não são reenviados sem confirmação. O histórico fica em `data/publish_log.json`.

### Linha de comando

```powershell
# Baixa/importa e transcreve
.venv\Scripts\python -m corteviral ingest "https://www.twitch.tv/videos/123456789"
.venv\Scripts\python -m corteviral ingest "C:\Videos\live.mp4"
# Só um trecho (baixa e transcreve apenas de 1:20:00 a 1:45:00)
.venv\Scripts\python -m corteviral ingest "https://www.twitch.tv/videos/123456789" --inicio 1:20:00 --fim 1:45:00

# Opções
#   --model small        modelo mais rápido (menos preciso)
#   --language pt        força o idioma (padrão: detecção automática)
#   --force              refaz tudo, ignorando o cache
#   --cookies-from-browser firefox   usa o login do navegador (vídeos com restrição)

# Melhores momentos (perfil viral, 10 momentos, IA Gemini)
.venv\Scripts\python -m corteviral analyze projects\<pasta-do-projeto> --n 10 --profile viral --provider gemini

# Detectar layout e gerar os clipes
.venv\Scripts\python -m corteviral detect projects\<pasta-do-projeto>
.venv\Scripts\python -m corteviral render projects\<pasta-do-projeto> [--ids m00012,m01618] [--preview]

# Estado de um projeto
.venv\Scripts\python -m corteviral info projects\<pasta-do-projeto>
```

Cada vídeo vira uma pasta em `projects/` com `audio.wav`, `transcript.json`
(palavras com tempo de início/fim) e `project.json`. Rodar o mesmo link ou
arquivo de novo reaproveita o que já foi feito. Arquivos locais não são copiados
(hardlink quando estão no mesmo disco).

Lives em andamento são recusadas — aguarde virar VOD.

## Configuração

Todos os parâmetros ficam em `config.yaml` (veja `config.example.yaml`, comentado).

## Testes

```powershell
.venv\Scripts\pytest            # testes rápidos
.venv\Scripts\pytest -m slow    # inclui transcrição real com o modelo tiny
```

## Documentação

- [Especificação de design](docs/superpowers/specs/2026-11-11-corte-viral-design.md)
- [Plano da Fase 1](docs/superpowers/plans/2026-11-11-fase-1-download-transcricao.md)
