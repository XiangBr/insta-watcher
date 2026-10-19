# Dashboard

Next.js (App Router), em container (`frontend/Dockerfile`, servido pelo gateway) ou na Vercel. O servidor do Next.js é o BFF: guarda a sessão do usuário em cookie e chama a API do backend com o token de admin. O navegador nunca recebe esse token.

## Acesso

- Uma senha de admin. O hash argon2id fica em `ADMIN_PASSWORD_HASH`. O `scripts/setup.sh` gera e grava entre aspas simples no `.env` (o Docker Compose não interpreta o `$` assim). À mão: `docker compose run --rm --no-deps web node scripts/hash-password.mjs`, ou `pnpm hash-password`. No `.env.local` do `pnpm dev`, cada `$` vira `\# Dashboard

Next.js (App Router), em container (`frontend/Dockerfile`, servido pelo gateway) ou na Vercel. O servidor do Next.js é o BFF: guarda a sessão do usuário em cookie e chama a API do backend com o token de admin. O navegador nunca recebe esse token.

## Acesso

- Uma senha de admin. . Na Vercel, o hash vai como está.
- Login em `/login` cria sessão em cookie `httpOnly`, `Secure`, `SameSite=Lax`, assinado com `SESSION_SECRET`, com expiração de 7 dias.
- `proxy.ts` (o antigo `middleware.ts`, renomeado no Next 16) manda para `/login` quem não tem sessão e define a CSP com nonce a cada request. É só uma checagem otimista do cookie: o layout, os server components e a rota do BFF conferem a sessão de novo antes de tocar em dados.
- Limite de tentativas de login com atraso crescente, contado no backend (`docs/security.md`, seção 5). Se o backend não responder, o login é recusado, porque sem contador as tentativas seriam ilimitadas.
- Toda mutação (POST, PUT, PATCH, DELETE) exige `Origin` igual ao host do painel, além do `SameSite`. Sem `Origin`, é recusada.
- O navegador só fala com `/api/bff/api/v1/...`. Essa rota confere sessão e origem, repassa só o corpo JSON e acrescenta o token de admin. Não repassa cookies nem outros cabeçalhos do navegador, não segue redirecionamentos e não expõe `login-throttle`, `/webhook` nem nada fora de `/api/v1`.
- O painel só aceita `API_BASE_URL` com `https` em produção (em dev, `localhost` por http).

## Páginas

Estado da Fase 2: todas as páginas abaixo existem, exceto o que está marcado como adiado.

### Visão geral (`/`)

Abre com uma frase de estado ("@conta. Enviando normalmente."), em verde, âmbar ou vermelho. Abaixo vem a lista **Precisa de atenção**, em ordem de urgência, onde cada item leva à tela certa: conta sem token ou com token recusado, envio pausado, token vencendo em menos de 7 dias (vermelho abaixo de 3), entregas não confirmadas e falhas, erros operacionais sem resolver, webhook mudo há mais de 3 dias com campanha ativa, e uso perto do teto por hora (90%). Por fim, a lista de medidas: DMs na última hora, fila, falhas, não confirmadas, campanhas ativas, último webhook e dias de token. A regra está em `lib/attention.ts`, com testes. Atualiza a cada 10 s. Gráfico de envios por dia e totais de 24 h e 7 dias ficaram para depois.

### Insights (`/insights`)

Painel de desempenho do perfil, lido do cache do sincronizador (nunca chama a Meta ao abrir). Botão **Atualizar agora** dispara uma leitura em segundo plano; a página se atualiza a cada 5 s enquanto ela roda. Avisa quando o token não tem `instagram_business_manage_insights`, quando a leitura parou no limite de chamadas e quais partes falharam.

- **Filtros**: período (7, 30, 90 dias, 6 meses, 1 ano, tudo ou datas personalizadas no fuso da conta), formato (Reels, carrossel, foto) e busca na legenda ou hashtag. Tudo abaixo segue os filtros.
- **KPIs**: seguidores (e ganho no período), alcance, visualizações e interações da conta (com variação contra o período anterior de mesmo tamanho), engajamento mediano por post e posts por semana.
- **O que fazer agora**: recomendações em texto (`lib/insights-advice.ts`). Cada uma vem de uma comparação nos dados e só aparece com pelo menos 3 posts em cada grupo e diferença de 15% ou mais: melhor formato, dia e faixa de horário, pico de comentários e de seguidores online, descoberta (alcance de não seguidores), tamanho de legenda e quantidade de hashtags, peso de salvamentos e compartilhamentos, perguntas nos comentários, palavras mais pedidas nas campanhas, post para repetir e frequência.
- **Crescimento e alcance**: linha de seguidores (dias antigos reconstruídos com ganhos e perdas) e uma métrica diária à escolha.
- **Posts**: os 5 que mais e os 5 que menos engajaram pela métrica escolhida, e a tabela de todos os posts do filtro, ordenável por coluna.
- **Formatos**: medianas por formato, com o melhor destacado, e tempo médio assistido dos Reels.
- **Melhores dias e horários**: engajamento pelo dia e pela faixa de 3 h em que você postou, mapa de calor de quando o público comenta e seguidores online por hora.
- **Quem é o seu público**: idade, gênero, cidades e países (seguidores ou quem interagiu no mês), alcance de seguidores contra não seguidores, alcance por tipo de conteúdo, quem comenta (únicos, recorrentes, quem mais comenta), palavras mais usadas nos comentários e palavras-chave pedidas nas campanhas.
- **Legenda e hashtags**: engajamento por tamanho de legenda, por quantidade de hashtags e por hashtag usada em 2 ou mais posts.

Engajamento é interações por conta alcançada, por post, sempre como mediana. Sem a permissão de insights, cai para interações por seguidor. As regras estão em `lib/insights.ts`, com testes. Os gráficos são SVG e CSS próprios (`components/charts.tsx`) e só desenham no navegador, porque a CSP não permite estilo inline no HTML do servidor.

### Concorrência (`/competitors`)

Compara a conta com perfis públicos parecidos (`docs/architecture.md`, seção Concorrência). Avisa quando falta `APIFY_TOKEN` ou `OPENROUTER_API_KEY`. Enquanto o Apify coleta ou há posts esperando classificação, a página se atualiza a cada 10 s.

- **Estado**: última coleta, custo dela no Apify, custo acumulado do JEV, posts na fila de classificação e o botão **Coletar de novo**.
- **Perfis acompanhados**: campo que aceita `@perfil` ou link, e a lista com foto, selo "você", "aguardando coleta" ou o erro da coleta. Remover pede confirmação.
- **Comparação**: tabela com seguidores, engajamento mediano, posts por semana, parcela de vídeos e formato principal, mais os rótulos de perfil do JEV (público, discurso, monetização, comentário para DM, profundidade). Rótulo com confiança abaixo de 0,5 aparece em itálico.
- **O que funciona no nicho**: barras com o desempenho mediano de cada formato, gancho, chamada para ação, tom e tema nos posts dos concorrentes. Pergunta em que tudo caiu numa opção só não aparece.
- **Posts que mais performaram**: os 12 posts dos concorrentes mais acima da mediana do próprio perfil, com os rótulos e link para o Instagram.
- **Relatório da I.A.**: botão **Gerar relatório**, e o último relatório com resumo, o que funciona, lacunas, ideias para gravar (cada uma com **Criar campanha**, que abre o construtor com a palavra-chave preenchida via `?keyword=`) e a análise por concorrente com as ferramentas citadas.

As regras de seleção e nomes estão em `lib/competitors.ts`, com testes. Os números já vêm calculados pela API.

### Chat (`/chat`, `/chat/flows`, `/chat/ai`)

Três abas.

- **Conversas** (`/chat`): números do Chat no topo (contatos, janelas abertas, com você, respostas da I.A., agendadas, falhas). À esquerda, a lista de conversas com busca por @, filtro por janela aberta ou modo humano, e por etiqueta; um ponto verde marca a janela de 24h aberta. À direita, a conversa em bolhas, com quem escreveu cada mensagem nossa (fluxo, campanha, acompanhamento, I.A., passagem, você) e o custo das respostas da I.A. Embaixo: o que está agendado (com Cancelar), os envios com problema, o campo de resposta (só com a janela aberta; responder pausa a automação para a pessoa) e as etiquetas. O botão **Assumir conversa** pausa o bot para essa pessoa; **Devolver ao bot** tira a pausa. A conversa se atualiza a cada 5 s.
- **Fluxos** (`/chat/flows`): cartões com nome, estado, palavras-chave, selo de boas-vindas e alcance. O editor monta os passos em lista: mensagem, depois dela só o texto, um botão de link ou botões de resposta (até 10), cada botão com uma ação (ir para um passo, mandar um link, chamar você) e etiquetas; etiquetas ao chegar no passo e acompanhamento opcional. Ao lado, uma **prévia** em que dá para tocar nos botões e seguir o fluxo.
- **I.A.** (`/chat/ai`): liga e desliga, modelo, persona, base de conhecimento, mensagem e horas de passagem, limites por pessoa e por hora, fluxo de boas-vindas e pausa ao responder pelo app. Ao lado, **Testar a I.A.**, que mostra o que a pessoa receberia com o que está no formulário, mesmo sem salvar, sem enviar nada.

Regras puras em `lib/chat.ts`, com testes. O construtor de campanha ganhou o gatilho **Mandar uma mensagem no direct**.

### Campanhas (`/campaigns`)

Lista de cartões, do mais recente para o mais antigo (pela data de modificação). Cada cartão traz o selo de estado ("No ar" ou "Pausada"), o nome, o que dispara a campanha (comentário em um post, com a miniatura e a legenda dele; comentário em qualquer post; resposta a stories; "só envia o link para quem segue"), as palavras-chave em chips, as enviadas, as falhas e quando foi modificada. Na lateral ficam o interruptor e os ícones de editar, duplicar (a cópia nasce pausada) e apagar, com confirmação. Entregas não confirmadas aparecem em violeta, com link para a lista já filtrada. No alto, uma busca (por nome ou palavra-chave, sem diferenciar acento) e filtros por gatilho e por estado. As miniaturas vêm da lista de posts da Meta, que só é pedida quando alguma campanha aponta para um post. CTR depende dos links rastreados, que não serão feitos.

### Construtor de campanha (`/campaigns/new` e `/campaigns/[id]/edit`)

Duas colunas, como o editor do ManyChat: o painel de opções à esquerda, em cartões, com o botão de salvar fixo na base, e à direita um **celular** que mostra o que o seguidor vai ver. Os passos separados do plano original foram descartados: o formulário é curto, e ver tudo de uma vez ajuda a revisar.

O celular tem abas que dependem dos gatilhos da campanha: **Publicação** (o post escolhido, com a legenda), **Comentários** (um comentário de exemplo com a primeira palavra-chave e a resposta pública sob ele), **Story** e **DM** (a conversa: com o gate, o convite, o toque da pessoa e só então o link). Ele lê o formulário enquanto se digita, então dá para conferir tudo sem enviar nada. Fica escuro nos dois temas, como a tela que imita. A montagem da conversa e das abas está em `lib/phone-preview.ts`, com testes.

- **O que ativa:** duas caixas, **Comentários em posts** e **Respostas a stories**, e ao menos uma precisa estar marcada. Com comentários, aparece o alvo: um post específico (grade de miniaturas dos últimos 12 posts) ou qualquer post. A seção de resposta pública só existe para comentários. Se a API não conseguir listar os posts (token ausente ou recusado), aparece um campo para colar o ID. Palavras-chave em chips (vírgula ou Enter), modo palavra inteira ou contém, e o campo **Teste uma frase**, que chama `POST /campaigns/match-test`. Assim a resposta vem do mesmo matcher do dispatcher e nunca diverge dele.
- **O que a pessoa recebe:** mensagem com `{username}`, link e texto do botão. Os limites (1000 caracteres, 20 do botão e os demais) vêm de `GET /limits`, então o painel não guarda cópia deles.
- **Seguir para liberar:** caixa que liga o follow gate. Com ela, a primeira DM é um convite com um botão e o link só sai depois que a Meta confirma o follow. Pede a mensagem de convite (já vem preenchida) e o texto do botão, e exige um link.
- **Resposta pública:** opcional, com várias variações em rodízio.
- **Prévia:** o balão da DM com o botão, mais a resposta pública sob o comentário. Com o gate ligado, são dois balões: "Primeiro, o convite" e "Depois que a pessoa seguir". Usa a mesma regra do worker (com rótulo, o link vai no botão; sem rótulo, no fim do texto).
- Validação rápida no cliente e, depois, a do servidor mostrada campo a campo. Ao criar, há a opção **Criar pausada**.

Adiados para a Fase 3: gatilho por DM, story reply, follow gate, destinos rastreados e modelos prontos.

### Entregas (`/logs`)

Lista filtrável por campanha, estado e período (padrão: 7 dias), com **Carregar mais** (paginação por cursor) e atualização a cada 10 s. Cada linha abre o percurso da entrega: **comentário** (quem, quando, texto e palavra casada), **DM** (estado, tentativas, erro) e **resposta pública**. Ações: **Tentar de novo** em `FAILED`; para `UNCONFIRMED`, **Reenviar DM…** abre um diálogo cujo botão só habilita depois de marcar "Conferi a caixa de entrada e a DM não chegou". Entregas já anonimizadas pela retenção não têm ações. A exportação CSV foi adiada para a Fase 3.

### Configurações (`/settings`)

Conta conectada, validade do token (marcada como estimativa até o primeiro refresh), colar token novo (o campo nunca mostra o token guardado), inscrever webhooks, pausar e retomar o envio, e **Varrer agora** (reconciliação manual). Os parâmetros (teto por hora, intervalo da varredura, retenção) aparecem como somente leitura: vêm de variáveis de ambiente do backend, e não há rota para mudá-los.

### Diagnóstico (`/diagnostics`)

Primeira tela a abrir quando "chegou comentário e a DM não saiu". Mostra a fila por estado, os eventos operacionais (com **Marcar como visto**, que tira o erro da contagem da visão geral) e os últimos webhooks recebidos, resumidos. O texto dos comentários só aparece se a pessoa marcar **Mostrar o texto dos comentários**.

## Comportamento geral

- Atualização por polling a cada 10 s (SWR), sem websocket.
- Estados de carregamento, vazio e erro em toda lista.
- Em tela estreita a barra lateral vira uma barra no topo e as colunas se empilham. **Ainda não foi verificado visualmente em celular.**
- Tipos do cliente gerados do OpenAPI do backend (`pnpm gen:api`).
- Textos pelo arquivo `lib/messages.ts`, português do Brasil como padrão. As mensagens dos eventos operacionais vêm do backend, em inglês.
- Visual: inspirado no ManyChat. Fundo cinza-claro com cartões brancos arredondados, azul como cor de ação, barra lateral com ícones e a marca no topo, cartões de opção cinza (com a escolha à esquerda) no editor. Fonte Plus Jakarta Sans, servida pelo próprio Next, então a CSP mantém `font-src 'self'`. Os selos de estado ficam em minúsculas ("No ar", "Pausada"), sem caixa alta. Cada estado de entrega tem cor própria, e **não confirmada é violeta de propósito**, para nunca se confundir com falha.
- **Tema claro e escuro**, com três opções no pé da barra lateral: Claro, Escuro e Automático (segue o sistema). A escolha fica no cookie `iwb_theme`, que o servidor lê, então a página já nasce no tema certo, sem piscar e sem script inline (a CSP não permite). O tema escuro são camadas de superfície (fundo, cartão, campo), não uma inversão do claro. Os tokens estão em `app/globals.css`.
- A CSP libera imagens só de `self` e do CDN do Instagram (`*.cdninstagram.com`, `*.fbcdn.net`), para as miniaturas do seletor de post.
