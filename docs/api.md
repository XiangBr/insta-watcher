# API

Base: o endereço público da instalação (`PUBLIC_URL`). As rotas públicas passam pelo gateway; as de admin (`/api/v1`) só pela rede interna, exceto com o painel na Vercel (`docs/deploy.md`). Corpo em JSON. O contrato completo sai do OpenAPI do FastAPI em `/openapi.json` (desligado em produção ou protegido por admin), e o frontend gera os tipos a partir dele.

## Públicos (sem autenticação de usuário)

| Método e rota | Função |
|---|---|
| `GET /webhook` | Desafio da Meta. Responde o `hub.challenge` se `hub.verify_token` for válido, senão 403 |
| `POST /webhook` | Recebe eventos. Valida `X-Hub-Signature-256` sobre o corpo bruto. 200 depois de gravar, 401 se a assinatura falhar, 400 se o JSON for inválido, 413 acima de `WEBHOOK_MAX_BODY_BYTES` |
| `GET /health` | `{"status":"ok"}` ou 503. Não expõe versão, contagens nem detalhes |
| `GET /privacy`, `GET /terms`, `GET /data-deletion` | Páginas exigidas pela Meta para o modo Live. HTML estático, sem script. Contato vem de `LEGAL_CONTACT_EMAIL`. Sem ele, 404 |

As páginas de privacidade, termos e exclusão de dados são servidas pela API, porque a Meta exige as três para publicar o app e elas precisam funcionar mesmo com o painel fora do ar. São um modelo: adapte o texto à sua situação.

## Admin

Todas exigem `Authorization: Bearer <API_ADMIN_TOKEN>`. Quem chama é o servidor do Next.js, nunca o navegador. Prefixo `/api/v1`.

### Status e conta

| Rota | Função |
|---|---|
| `GET /status` | Fila por estado, última entrega de webhook, eventos pendentes, uso da janela de rate limit (x de 750), dias até o token expirar, estado da conta, erros não resolvidos |
| `GET /account` | Dados da conta, ou `null` antes do primeiro token. Nunca devolve o token |
| `PUT /account/token` | `{"token": "..."}`. Valida o token com a Meta (`/me`), guarda criptografado e atualiza a expiração. Responde 422 se a Meta recusar e 409 se o token for de outra conta |
| `POST /account/subscribe` | Assina o app aos campos de webhook (`comments`, `messages`) e grava `webhook_subscribed_at` |
| `POST /account/pause` e `/resume` | Interruptor geral de envio. Depois do resume, os jobs parados saem em até 5 minutos |
| `GET /posts?limit=12` | Mídias recentes para o seletor de post (até 50), com miniatura. Sem cursor por enquanto |

### Campanhas

| Rota | Função |
|---|---|
| `GET /limits` | Limites de validação do formulário de campanha (nome, palavras-chave, mensagem, botão, link, respostas públicas). O painel os lê daqui em vez de guardar cópia |
| `POST /campaigns/match-test` | `{keywords, match_mode, text}`, devolve `{matched, keyword}`. Roda o matcher real, então o teste do formulário nunca difere do envio |
| `GET /campaigns` | Lista com contagens de entregas por estado. Cliques chegam na Fase 3 |
| `POST /campaigns` | Cria. Valida palavras-chave, tamanho de botão, URLs `https` e os gatilhos: `trigger_comments` (padrão `true`, exige `post_id` ou `match_any_post`) e `trigger_story_replies` (padrão `false`). Ao menos um gatilho. O follow gate usa `follow_gate_enabled`, `follow_gate_message` (até 640 caracteres) e `follow_gate_button_label` (padrão "Já segui"), e exige `link_url`. Sem comentários, nem post nem resposta pública. Campo desconhecido dá 422 |
| `GET /campaigns/{id}` | Detalhe |
| `PATCH /campaigns/{id}` | Edita só os campos enviados. A campanha resultante é validada inteira, e se for inválida nada muda |
| `DELETE /campaigns/{id}` | Apagamento lógico (`deleted_at`). As entregas continuam apontando para ela |
| `POST /campaigns/{id}/duplicate` | Copia como inativa, com o nome terminado em ` (copy)` |
| `POST /campaigns/{id}/pause` e `/resume` | Liga e desliga |
| `GET /campaigns/{id}/stats?days=30` | Entregas por dia (UTC) e por estado, mais os totais. Cliques e CTR por link chegam na Fase 3 |

### Entregas e operação

| Rota | Função |
|---|---|
| `GET /deliveries` | Filtros `campaign_id`, `status`, `since` e `until` (com fuso, senão 422). Paginação por cursor: mande `next_cursor` como `before` |
| `POST /deliveries/{id}/retry` | Para `FAILED`. Para `UNCONFIRMED`, exige `{"confirm_not_delivered": true}`, senão 422. Outro estado dá 409 |
| `GET /deliveries/export.csv` | Fase 3. CSV com neutralização de células que começam com `=`, `+`, `-`, `@` |
| `GET /ops-events?level=&unresolved=` | Eventos operacionais recentes |
| `POST /ops-events/{id}/resolve` | Marca o evento como visto, e ele sai da contagem de `/status` |
| `GET /webhook-events?with_text=false` | Últimos eventos brutos, resumidos por mudança. O texto do comentário só vem com `with_text=true` |
| `POST /reconcile` | Dispara uma varredura agora (`{"queued": n}`). 409 sem conta ativa |

### Insights

Só leem as tabelas que o sincronizador de insights preenche (`docs/architecture.md`, seção Insights). Abrir a página nunca chama a Meta.

| Rota | Função |
|---|---|
| `GET /insights` | Tudo da última leitura: estado da sincronização (`sync`), perfil, todos os posts com métricas e palavras dos comentários, métricas diárias da conta, público (demografia, alcance por seguidor e não seguidor, por tipo de conteúdo, seguidores online por hora) e `utc_offset_minutes`. Os filtros de período, formato e busca rodam no painel |
| `GET /insights/activity?since=&until=` | Comentários por hora (UTC), total, pessoas únicas, quantas voltaram a comentar em 2 ou mais posts, quem mais comenta e as palavras-chave de campanha mais acionadas. Padrão: últimos 30 dias. Datas sem fuso ou invertidas dão 422 |
| `POST /insights/sync` | 202. Começa uma leitura agora, em segundo plano (`{"started": false}` se já houver uma rodando). 409 sem conta ativa |

### Concorrência

Leem as tabelas `competitor*` (`docs/architecture.md`, seção Concorrência). Abrir a página nunca chama o Apify nem o OpenRouter.

| Rota | Função |
|---|---|
| `GET /competitors` | `collect_enabled` e `classify_enabled` (se `APIFY_TOKEN` e `OPENROUTER_API_KEY` existem), `running`, última coleta, posts esperando classificação, custo acumulado do JEV, a taxonomia (perguntas e rótulos), cada perfil com números, rótulos e até 24 posts (legenda cortada em 500 caracteres, engajamento e desempenho), e `niche` (desempenho mediano por opção) |
| `POST /competitors` | 201. Corpo `{"username"}`: aceita `@user`, `user` ou o link do perfil. 422 se não for um username, 409 se já existe ou passou de `COMPETITORS_MAX` |
| `DELETE /competitors/{id}` | 204. Remove o perfil e os posts dele. 404 se não existe |
| `POST /competitors/scan` | 202. Coleta todos os perfis de novo agora e classifica o que for novo (`{"started": false}` se já houver uma rodando). 409 sem `APIFY_TOKEN` |
| `GET /competitors/report` | `enabled`, `running` e o relatório mais recente (ou `null`) |
| `POST /competitors/report` | 202. Começa um relatório com `COMPETITORS_REPORT_MODEL`. 409 sem `OPENROUTER_API_KEY` ou sem nenhum concorrente coletado |

### Chat

Leem e gravam as tabelas do Chat (`docs/architecture.md`, seção Chat). Quem envia é o loop `chat`; as rotas só enfileiram.

| Rota | Função |
|---|---|
| `GET /chat/contacts?search=&tag=&view=all\|open\|human&offset=&limit=` | Resumo (contatos, janelas abertas, em modo humano, respostas e custo da I.A. em 24 h, agendadas, falhas), a página de contatos, da conversa mais recente para a mais antiga, com a última mensagem e o fim da janela de 24h, e todas as etiquetas em uso |
| `GET /chat/contacts/{id}` | O contato, as últimas 200 mensagens, o que está agendado e os envios com problema. 404 se não existe ou foi anonimizado |
| `PATCH /chat/contacts/{id}` | `{"tags": [...]}` troca as etiquetas. `{"human": true}` pausa a automação por `handoff_hours` e cancela o agendado; `false` devolve ao bot |
| `POST /chat/contacts/{id}/messages` | 202. `{"text"}` (até 1000). Enfileira a resposta digitada no painel e pausa a automação para a pessoa. 409 com a janela de 24h fechada |
| `DELETE /chat/outbox/{id}` | 204. Cancela uma mensagem que ainda não saiu. 409 se já saiu ou está saindo |
| `GET /chat/flows` | Fluxos não apagados, com mensagens enviadas e pessoas alcançadas |
| `POST /chat/flows` | 201. Corpo `FlowInput`. 422 se um botão aponta para passo inexistente, se um passo tem link e botões juntos, se um link não é `https`. 409 se outro fluxo ativo já usa a palavra-chave |
| `GET /chat/flows/{id}` | |
| `PUT /chat/flows/{id}` | Mesmo corpo e regras do `POST` |
| `DELETE /chat/flows/{id}` | 204. Remoção lógica. Se era o fluxo de boas-vindas, a config deixa de apontar para ele |
| `GET /chat/config` | `ChatConfig`, `ai_available` (se `OPENROUTER_API_KEY` existe), o modelo padrão (`CHAT_AI_MODEL`) e o uso da I.A. em 24 h |
| `PUT /chat/config` | Corpo `ChatConfig`. 409 ao ligar a I.A. sem `OPENROUTER_API_KEY`, 404 com fluxo de boas-vindas inexistente. Desligar a I.A. cancela as respostas dela que estavam esperando |
| `POST /chat/ai/test` | `{"message", "config"?}`. O que a I.A. responderia, com a config salva ou a enviada. Não envia nem grava nada; o OpenRouter cobra normalmente. 409 sem a chave, 502 se o OpenRouter falhar |

Campanhas aceitam também `trigger_messages` (DM comum com a palavra-chave).

### Login do painel

O Next.js confere a senha. Estas rotas só contam as falhas (`docs/security.md`, seção 5). `key` é um HMAC do IP feito pelo painel, com 16 a 128 caracteres `[A-Za-z0-9_-]`.

| Rota | Função |
|---|---|
| `POST /login-throttle/check` | `{"retry_after": s}`. Se for maior que zero, recuse sem conferir a senha |
| `POST /login-throttle/failure` | Conta uma senha errada e devolve a espera até a próxima tentativa. Só gera evento operacional quando o cliente é bloqueado, para um erro de digitação não virar ruído |
| `POST /login-throttle/success` | 204. Zera o contador daquele cliente |

## Erros

```json
{ "error": { "code": "validation_failed", "message": "texto legível", "details": [] } }
```

Códigos: `unauthorized` (401), `forbidden` (403), `not_found` (404), `validation_failed` (422), `conflict` (409), `rate_limited` (429), `upstream_error` (502, falha na Meta).

Em `validation_failed`, `details` traz `loc`, `msg` e `type` de cada campo e nunca o valor recebido. O envelope só vale para `/api/`. O webhook e as páginas públicas mantêm as respostas padrão.

## Limites da própria API

- Login do dashboard (no Next.js) com limite de tentativas e atraso crescente.
- Rotas admin com limite por IP de origem e por token. As públicas (`/webhook`, `/r`) têm limite próprio e mais folgado.
- Tamanho máximo de corpo no `/webhook` (por exemplo 256 KB).
