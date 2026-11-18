# Cortes de live ao vivo — design

## Intenção
O usuário cola o link de uma live **em andamento** (YouTube, Twitch, Kick) e o app acompanha a transmissão:
a cada poucos minutos encontra os momentos fortes do trecho novo e **já gera o clipe pronto** (mesmo ritmo de
Shorts, legenda e faixa de sempre), que aparece na grade **Clipes prontos** enquanto a live continua. O usuário
ajusta (corte, legenda, textos) e exporta/posta como hoje. Objetivo: postar o momento enquanto o assunto ainda
está quente.

## Objetivos
- Link de live em andamento → projeto "ao vivo" que grava e processa em ciclos.
- Cada clipe aparece na grade poucos minutos depois de acontecer (meta: ≤ 10 min do momento até o clipe pronto).
- Clipes gerados ao vivo são clipes normais: mesma tela, mesmos ajustes, mesma exportação/fila.
- Status visível: "🔴 Ao vivo · gravando 1:23:10 · 4 clipes" com botão **Parar**.
- Ao fim (live acabou ou Parar), sobra o vídeo inteiro gravado no projeto.

## Não-objetivos
- Detecção em tempo real (segundos). Latência de minutos é aceita.
- Postar automaticamente sem o usuário (continua a fila/aprovação de hoje).
- Gravar o chat ao vivo nesta versão (sinais = áudio + reações faladas + IA).

## Anti-objetivos
- Não pode travar o resto do app: exportar, postar e ajustar clipes continuam funcionando durante a live.
- Não pode reavaliar com a IA trechos já avaliados (gasto de cota).
- Não pode perder o que já foi gravado/processado se a internet cair ou o app fechar.

## Abordagens consideradas
1. **Gravação contínua + leitura do arquivo crescendo (escolhida).** yt-dlp grava a live num arquivo
   MPEG-TS (formato que pode ser lido enquanto cresce); um laço lê só a parte nova em janelas.
   A favor: mesmo download das 3 plataformas, vídeo inteiro no final. Contra: tratar queda/retomada.
2. Pedaços HLS direto do servidor: URL do YouTube expira em horas, cada plataforma difere, não sobra o vídeo
   inteiro. Descartada.

## Como funciona
### Gravação
- `ingest` hoje recusa live (`IngestError("live em andamento…")`). Novo caminho: se `is_live`, cria o projeto
  (`data["live"] = {...}`) e inicia o gravador em vez de recusar.
- Gravador: yt-dlp grava a partir de agora (não do começo da live) em `projects/<p>/live/part-NNN.ts`
  (container MPEG-TS). Se o processo cair (queda de rede), retoma num `part-(N+1).ts`; tentativas com espera
  crescente; se a plataforma disser que a live acabou, encerra.
- Uma linha do tempo única: cada parte registra seu início no relógio da live (offset acumulado), salvo em
  `live.json` do projeto, para que tempos de momentos sejam contínuos entre partes.

### Ciclo de processamento (a cada `live.window_seconds`, padrão 300 s)
1. Quando houver ≥ 300 s novos gravados, recorta a janela `[fim_anterior − 60 s, agora]` (60 s de sobreposição
   para não perder momento na divisa) para `live/win-NNN.wav` (+ vídeo para render).
2. Transcreve a janela (Groq se houver chave; senão local) e **anexa** palavras/segmentos ao `transcript.json`
   com o deslocamento da janela (palavras da sobreposição que já existem são descartadas).
3. Sinais + candidatos **só da janela**; IA pontua e assiste (deep review) só os candidatos novos.
4. Seleção pela regra atual (nota ≥ `analysis.min_score`, sem sobreposição com momentos já existentes).
5. Novos momentos entram em `moments.json` (append) e são renderizados na hora (`render_clip`, mesmo ritmo de
   Shorts). A grade mostra os novos sozinha (polling).

### Fora da fila de tarefas
- Hoje só existe uma tarefa por vez. A live roda num **monitor próprio** (thread por projeto ao vivo, registro
  em memória + `live.json` em disco), separado de `jobs.running`. Exportar/postar/gerar de novo continuam.
- Concorrência: ao mesmo tempo o gravador (rede) + processamento de uma janela por vez. Se o processamento
  atrasar, as janelas acumulam e são processadas em ordem (nunca em paralelo, para não estourar CPU).

### Interface
- Novo vídeo detecta live (resposta do servidor) e mostra "Live ao vivo: os clipes vão aparecendo enquanto
  ela acontece".
- Projeto ao vivo: faixa no topo da tela Clipes "🔴 Ao vivo · gravando 1:23:10 · 4 clipes · próxima análise
  em 2 min" + **Parar**; barra lateral marca o projeto com 🔴.
- Grade atualiza a cada ~20 s enquanto ao vivo; toast "Novo clipe: <título>".
- Ao parar/terminar: "Live encerrada — vídeo completo gravado" + botão **Analisar a live inteira** (análise
  normal sobre o vídeo completo, para pegar o que escapou).

### Reinício do app no meio da live
- `live.json` guarda partes, janelas processadas e estado. Ao abrir o app, projeto com `live.active` e live
  ainda no ar → oferece **Retomar** (grava parte nova a partir de agora; o buraco entre partes fica registrado).

## Configuração (`live:`)
- `window_seconds: 300`, `overlap_seconds: 60`, `max_retries: 20`, `retry_wait: 10` (s, cresce até 60),
  `max_clips_per_window: 3`, `review_per_window: 3`.

## Respostas da revisão (decididas por mim — confira)
1. **Arquivo de vídeo dos clipes.** A linha do tempo da live é contínua (tempo desde o início da gravação).
   Durante a live, cada momento guarda a parte de origem (`moment["source"] = {"part": "part-002.ts",
   "offset": 3600.0}`) e `render_clip`/prévia usam essa parte quando existir (uma janela nunca atravessa duas
   partes: a sobreposição de 60 s só vale dentro da mesma parte). Ao terminar, o app junta as partes num
   `video.mp4` único, **preenchendo os buracos com tela preta e silêncio** para que todos os tempos continuem
   valendo; aí `video_path` aponta para ele e os `source` são removidos. Ajustes feitos durante a live seguem
   valendo depois.
2. **"Analisar a live inteira"** mantém tudo que já existe (aprovados, editados, exportados, na fila — mesmos
   ids) e só **acrescenta** momentos novos que não se sobrepõem aos existentes. Reaproveita a transcrição; a
   IA avalia de novo só candidatos fora dos já avaliados ao vivo. Gasta cota porque o usuário pediu.
3. **Falhas numa janela.** Groq sem cota/rede → transcreve a janela local. IA sem cota/erro → a janela fica
   `pending_ai` com os candidatos já salvos e é tentada de novo a cada ciclo (sem refazer transcrição);
   enquanto isso os candidatos com nota só de sinais ≥ `min_score` **não** viram clipe (evita clipe ruim).
   Se o atraso passar de 3 janelas, a transcrição usa o modelo local menor e a IA assiste só o melhor
   candidato por janela, para alcançar a live.
4. **Limite de clipes.** Por janela: no máximo `live.max_clips_per_window` (padrão 3) e a IA assiste no
   máximo `live.review_per_window` (padrão 3) candidatos. Sem limite por live (não desperdiçar momento bom).
5. **Opções da análise** (duração do clipe, perfil) vêm do Novo vídeo e ficam fixas durante a live;
   provedor e streamer/preset são os da configuração e do projeto, como hoje.
6. **Fila de tarefas.** O Novo vídeo continua chamando `/api/jobs` (ingest). Se o link for live, a tarefa só
   cria o projeto, entrega para o monitor ao vivo e **termina** (libera a vaga); a live segue fora da fila.
   Com outra tarefa rodando, a live espera como qualquer tarefa (é só o início, segundos).

## Decisões minhas (confira)
- Grava a partir de agora, não do começo da live (lives longas: começo inteiro custaria horas de processamento).
- Chat ao vivo fica fora desta versão.
- Uma live ao vivo por vez.
- Cota: cada janela de 5 min ≈ 5 min de áudio no Groq e ~1–3 trechos assistidos pela IA; uma live de 5 h ≈ 5 h de
  áudio (dentro das ~8 h/dia grátis do Groq).

## Para o construtor decidir
- Formato exato do yt-dlp para live por plataforma (`--hls-use-mpegts`, `--no-part`, formato até 1080p).
- Como recortar a janela de um `.ts` que cresce (ffmpeg `-ss/-t` no arquivo em escrita) e de duas partes
  quando a janela atravessa uma retomada.
- Estrutura do teste: gravador e plataforma falsos (gera `.ts` sintético que cresce) para testar o ciclo.
