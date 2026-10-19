# Modelo de dados

SQLite, uma conta. Tipos abaixo em notação SQL por clareza. A fonte da verdade é `backend/app/models.py` mais as migrações Alembic. Datas em UTC.

## Tabelas

### account

Uma linha só (`CHECK id = 1`). A CLI recusa um token de outra conta.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | sempre 1 |
| ig_user_id | TEXT | `user_id` do `/me`, ID da conta profissional. É o `entry.id` dos webhooks |
| username | TEXT | |
| token_encrypted | BLOB | `v1` + nonce de 12 bytes + AES-256-GCM, com AAD de propósito |
| token_set_at | TIMESTAMP | quando o dono colou o token |
| token_expires_at | TIMESTAMP NULL | estimada em 60 dias até o primeiro refresh |
| token_expiry_confirmed | BOOLEAN | vira true quando um refresh devolve `expires_in` |
| token_refreshed_at | TIMESTAMP NULL | |
| token_refresh_attempted_at | TIMESTAMP NULL | limita as tentativas de refresh a uma por dia |
| status | TEXT | `ACTIVE` ou `TOKEN_INVALID` |
| sending_paused | BOOLEAN | interruptor geral. Com ele ligado, nenhum job envia |
| webhook_subscribed_at | TIMESTAMP NULL | |
| updated_at | TIMESTAMP | |

O Instagram expõe dois IDs para a mesma conta (um com escopo de app e um da conta profissional). Webhooks e a API de mensagens usam o da conta profissional. Guarde esse.

### campaign

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | até 80 caracteres |
| is_active | BOOLEAN | |
| post_id | TEXT NULL | mídia alvo (índice) |
| match_any_post | BOOLEAN | vale para qualquer post |
| keywords | JSON | lista de strings, até 20 |
| match_mode | TEXT | `WHOLE_WORD` ou `CONTAINS` |
| dm_message | TEXT | aceita `{username}`. Mensagem mais link até 1000 caracteres |
| link_url | TEXT NULL | somente `https`, sem credenciais. Vai no botão ou no fim do texto |
| link_button_label | TEXT NULL | máx. 20 caracteres. Sem ele, o link vai no texto |
| trigger_comments | BOOLEAN | padrão `true`. A campanha responde a comentários e precisa de `post_id` ou `match_any_post` |
| trigger_story_replies | BOOLEAN | padrão `false`. A campanha responde a respostas a stories com uma das palavras-chave |
| trigger_messages | BOOLEAN | padrão `false`. A campanha responde a uma DM comum (não resposta a story) com uma das palavras-chave. A entrega tem `trigger = MESSAGE` e `source_id` = id da mensagem |
| follow_gate_enabled | BOOLEAN | padrão `false`. Exige `link_url` |
| follow_gate_message | TEXT NULL | texto do convite, até 640 caracteres. Vazio com o gate desligado |
| follow_gate_button_label | TEXT NULL | máx. 20 caracteres. Padrão "Já segui" |
| public_reply_enabled | BOOLEAN | |
| public_reply_messages | JSON | lista, usada em rodízio pelo ID da entrega |
| created_at, updated_at | TIMESTAMP | |
| deleted_at | TIMESTAMP NULL | remoção lógica: as entregas continuam apontando para a campanha |

A validação em `app/campaigns.py` exige ao menos um gatilho. Com `trigger_comments`, exige exatamente um entre `post_id` e `match_any_post`. Sem ele, não aceita nenhum dos dois nem respostas públicas, que só fazem sentido em comentários. As colunas do follow gate entram com migração própria.

### tracked_link (fase 3)

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER FK | ON DELETE CASCADE |
| slug | TEXT UNIQUE | 7 bytes aleatórios em base64url |
| label | TEXT NULL | |
| destination_url | TEXT | somente `https` |
| position | INTEGER | ordem do botão, 0 ou 1. Leia sempre ordenando por `position` |

### link_click (fase 3)

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| tracked_link_id | INTEGER FK | |
| ip_hash | TEXT NULL | SHA-256 de `sal + ip`, sal vem de `CLICK_HASH_SALT` |
| user_agent | TEXT NULL | truncado em 300 |
| referrer | TEXT NULL | truncado em 300 |
| created_at | TIMESTAMP | índice |

### webhook_event

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| received_at | TIMESTAMP | |
| payload | JSON | só eventos com assinatura válida |
| status | TEXT | `PENDING`, `PROCESSED`, `FAILED` |
| error | TEXT NULL | |
| processed_at | TIMESTAMP NULL | |

Índice em `(status, received_at)`.

### delivery

O log por envio. Uma linha por gatilho.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER FK | |
| trigger | TEXT | `COMMENT` ou `STORY_REPLY` (para esta, `source_id` é o `mid` e `media_id` é o ID do story). `source_id` guarda um ID de comentário ou de mensagem, que é longo, e o SQLite não aplica o tamanho declarado |
| origin | TEXT | `WEBHOOK` ou `POLLING` (reconciliador) |
| source_id | TEXT | ID do comentário, ou `mid` no gatilho por DM |
| media_id | TEXT NULL | post do comentário |
| actor_id | TEXT | ID de quem comentou |
| actor_username | TEXT NULL | |
| text | TEXT NULL | texto do gatilho. Sujeito à retenção |
| matched_keyword | TEXT NULL | |
| status | TEXT | da DM: `PENDING`, `SENT`, `FAILED`, `UNCONFIRMED` |
| attempts | INTEGER | chamadas à Meta para a DM |
| dm_sent_at, dm_message_id | | preenchidos no envio confirmado |
| error_message | TEXT NULL | sem token nem query string |
| public_reply_status | TEXT NULL | mesmo conjunto de estados, independente da DM. NULL se a campanha não responde em público |
| public_reply_sent_at, public_reply_error | | |
| gate_state | TEXT NULL | NULL sem gate. `WAITING` (convite enviado), `CHECKING`, `PASSED` (segue), `RELEASED` (a Meta não soube dizer, link liberado), `REMINDED` (ainda não segue, pedimos de novo), `STOPPED` (toques demais) |
| gate_taps | INTEGER | toques no botão. Teto de 5 |
| gate_user_id | TEXT NULL | ID de quem tocou. Anonimizado com o resto |
| gate_tapped_at | TIMESTAMP NULL | último toque. Conta para a janela de 24 h da DM com o link |
| followup_status, followup_sent_at, followup_message_id, followup_error | | a DM com o link, separada do `status`, que é o do convite |
| anonymized_at | TIMESTAMP NULL | preenchido pela retenção |
| created_at, updated_at | TIMESTAMP | |

Idempotência: `UNIQUE(campaign_id, source_id)` e `UNIQUE(trigger, source_id)`. A segunda existe porque a Meta só aceita uma private reply por comentário. Então, se duas campanhas casarem com o mesmo comentário, só uma entrega é criada: vence a campanha do post específico e, depois dela, a mais antiga. Índices em `status` e `created_at`.

### job

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| type | TEXT | `PRIVATE_REPLY`, `DIRECT_MESSAGE` (DM por ID de usuário), `PUBLIC_REPLY`, `FOLLOW_CHECK` (consulta de seguidor, somente leitura) ou `FOLLOWUP_DM` (a DM com o link depois do gate) |
| payload | JSON NULL | dados do job. Um `FOLLOW_CHECK` guarda o número da olhada (1 a 3) |
| delivery_id | INTEGER FK | |
| dedupe_key | TEXT UNIQUE | `private:{delivery_id}` ou `public:{delivery_id}` |
| status | TEXT | `PENDING`, `RUNNING`, `DONE`, `FAILED`, `UNCONFIRMED` |
| run_at | TIMESTAMP | |
| attempts | INTEGER | |
| locked_until | TIMESTAMP NULL | lease de 2 minutos |
| send_started_at | TIMESTAMP NULL | gravado antes de chamar a Meta e limpo a cada reagendamento |
| fallback_text | BOOLEAN | ligado quando a Meta recusa o botão: o próximo envio leva o link no texto |
| last_error | TEXT NULL | |
| created_at, updated_at | TIMESTAMP | |

Índice em `(status, run_at)`.

### send_event

Livro-razão do rate limit.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| kind | TEXT | `private_reply` |
| job_id | INTEGER NULL | permite devolver a vaga quando a Meta recusa explicitamente |
| sent_at | TIMESTAMP | índice em `(kind, sent_at)` |

A manutenção apaga registros com mais de 2 horas.

### login_attempt

Contador de senhas erradas no login do painel. Ele fica no backend porque o painel pode rodar em várias instâncias (na Vercel, por exemplo) que não compartilham memória.

| Coluna | Tipo | Notas |
|---|---|---|
| key | TEXT PK | HMAC do IP do cliente, calculado pelo painel (o backend nunca vê o IP), ou `global` para a soma de todos |
| failures | INTEGER | falhas seguidas |
| last_failure_at | TIMESTAMP | indexado. Sem falha por 24 h, a contagem recomeça |
| blocked_until | TIMESTAMP NULL | até quando o login fica recusado |

A manutenção apaga registros sem falha há mais de 24 horas. Um login certo apaga a linha daquele cliente, e a linha `global` só expira com o tempo.

### postback_receipt

| Coluna | Tipo | Notas |
|---|---|---|
| mid | TEXT PK | ID do evento de postback |
| created_at | TIMESTAMP | índice |

Distingue o repasse do mesmo toque de um toque novo, inclusive depois de reinício. A manutenção apaga os recibos com mais de `RETENTION_WEBHOOK_DAYS` dias (7), o que basta para cobrir as reentregas da Meta.

### ops_event

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| source | TEXT | `WORKER`, `TOKEN`, `RECONCILER`, `WEBHOOK`, `SYSTEM` |
| level | TEXT | `INFO`, `WARNING`, `ERROR` |
| message | TEXT | |
| payload | JSON NULL | sem segredos e sem texto de usuário |
| created_at | TIMESTAMP | |
| resolved_at | TIMESTAMP NULL | |

### insight_media

Uma linha por post ou reel, preenchida pelo sincronizador de insights. Cópia somente leitura do que a Meta informa.

| Coluna | Tipo | Notas |
|---|---|---|
| media_id | TEXT PK | |
| posted_at | TIMESTAMP | índice |
| media_type, product_type | TEXT | `IMAGE`, `VIDEO`, `CAROUSEL_ALBUM`; `FEED` ou `REELS` quando a Meta informa |
| caption, permalink, thumbnail_url, media_url | TEXT NULL | as URLs do CDN vencem; a próxima leitura as renova |
| like_count, comments_count | INTEGER NULL | dos campos do post, existem mesmo sem a permissão de insights |
| metrics | JSON | nome da Meta para número (`reach`, `views`, `saved`, `shares`, `total_interactions`, `follows`, `profile_visits`, `ig_reels_avg_watch_time`...). Métrica ausente não é zero. Também guarda duas contagens nossas: `comment_mentions` e `comment_questions` |
| terms | JSON | palavras usadas nos comentários (normalizadas, sem stopwords nem menções) para contagem. O texto dos comentários não é guardado |
| comments_seen_count | INTEGER NULL | `comments_count` da última leitura completa dos comentários; igual ao atual, não relê |
| insights_fetched_at, insights_error | | quando as métricas vieram e, se a Meta recusou, por quê |
| listed_at | TIMESTAMP | post ausente de uma listagem completa foi apagado no Instagram e sai daqui |

### insight_day

| Coluna | Tipo | Notas |
|---|---|---|
| day | TEXT PK | `AAAA-MM-DD` no fuso `INSIGHTS_UTC_OFFSET_MINUTES` |
| metrics | JSON | `reach`, `views`, `accounts_engaged`, `total_interactions`, `likes`, `comments`, `saves`, `shares`, `replies`, `profile_links_taps`, `follows`, `unfollows` |
| followers_count | INTEGER NULL | só nos dias em que houve leitura do perfil; o painel reconstrói os outros com `follows` e `unfollows` |
| complete | BOOLEAN | a Meta revisa um dia por até 48 h; depois disso ele não é lido de novo |
| fetched_at | TIMESTAMP | |

### insight_snapshot

Chave e valor JSON para o que a Meta só informa como um todo: `profile`, `audience` (renovado a cada 24 h), `sync_status` e `unsupported_metrics` (métricas que a Meta recusou para esta conta, para não pedir de novo).

### insight_comment

| Coluna | Tipo | Notas |
|---|---|---|
| comment_id | TEXT PK | |
| media_id | TEXT | índice |
| commented_at | TIMESTAMP | índice. Base do mapa de horários |
| author_id, username | TEXT NULL | apagados depois de `RETENTION_DELIVERY_DAYS` |

Comentários da própria conta não entram. O texto nunca é guardado.

### competitor

Um perfil público acompanhado na análise de concorrência. A própria conta entra com `is_self` (no máximo uma linha).

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| username | TEXT UNIQUE | minúsculo, sem `@` |
| is_self | BOOLEAN | a conta do dono, mantida a partir de `account.username` |
| added_at | TIMESTAMP | |
| full_name, biography, category, external_url, profile_pic_url | TEXT NULL | como o Apify informa. URLs só `https` |
| followers, follows, posts_count | INTEGER NULL | contagem escondida fica NULL |
| verified, private | BOOLEAN NULL | |
| scraped_at, scrape_error | | última coleta e, se o perfil não veio ou é privado, por quê |
| labels | JSON | respostas do JEV às perguntas de perfil: `{pergunta: {value, confidence}}` |
| labels_version, classify_error | TEXT NULL | `TAXONOMY_VERSION` usada; diferente da atual reclassifica |

### competitor_post

Os posts mais recentes de cada perfil (até 50, os mais velhos saem).

| Coluna | Tipo | Notas |
|---|---|---|
| post_id | TEXT PK | ID do Instagram |
| competitor_id | INTEGER FK | `ON DELETE CASCADE`. Índice com `posted_at` |
| short_code, url, media_type, product_type | TEXT NULL | `media_type`: Image, Video ou Sidecar |
| caption | TEXT NULL | até 8000 caracteres |
| hashtags | JSON | lista, até 30 |
| posted_at | TIMESTAMP | |
| likes, comments, views | INTEGER NULL | |
| display_url | TEXT NULL | URL assinada do CDN do Instagram, expira |
| is_pinned | BOOLEAN | fixados ficam fora das medianas |
| scraped_at | TIMESTAMP | |
| labels, labels_version, classify_error | | como em `competitor`, para as perguntas de post |

### competitor_scan

Uma execução do actor no Apify. No máximo uma `RUNNING`.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| apify_run_id, dataset_id | TEXT NULL | |
| status | TEXT | `RUNNING`, `SUCCEEDED`, `FAILED` |
| usernames | JSON | o que foi pedido |
| started_at, finished_at | TIMESTAMP | |
| profiles, posts | INTEGER | o que voltou |
| cost_usd | REAL NULL | `usageTotalUsd` do Apify |
| error | TEXT NULL | |

### competitor_report

Relatório escrito pelo LLM, sob demanda. Ficam os 20 mais recentes. Um `RUNNING` no boot vira `FAILED`.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| status | TEXT | `RUNNING`, `SUCCEEDED`, `FAILED` |
| model | TEXT | |
| created_at, finished_at | TIMESTAMP | |
| content | JSON NULL | `summary`, `what_works`, `gaps`, `competitors`, `ideas` (validado contra `ReportContent`) |
| cost_usd | REAL NULL | |
| error | TEXT NULL | |

### setting

Chave e valor (JSON) para configurações editadas no painel. Hoje só a chave `chat` (`ChatConfig` em `app/flows.py`): I.A. ligada, modelo, persona, base de conhecimento, mensagem e horas de passagem, limites da I.A., fluxo de boas-vindas e pausa ao responder pelo app. Config que não valida mais volta ao padrão, sem derrubar o dispatcher.

### contact

Quem mandou mensagem direta para a conta.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| ig_user_id | TEXT UNIQUE | id da pessoa no Instagram (escopo da conta). Vira `anon:{id}` na retenção |
| username | TEXT NULL | buscado na API de perfil no primeiro envio |
| first_seen_at | TIMESTAMP | |
| last_inbound_at | TIMESTAMP NULL | índice. A janela de 24h começa aqui |
| last_outbound_at | TIMESTAMP NULL | |
| tags | JSON | etiquetas, minúsculas, até 20 |
| human_until, human_reason | | modo humano: até quando, e por quê (`manual_reply`, `panel_reply`, `handoff_option`, `ai_handoff`, `panel`) |
| messages_in, messages_out | INTEGER | |
| anonymized_at | TIMESTAMP NULL | |

### chat_message

Uma mensagem da conversa, nos dois sentidos.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| contact_id | INTEGER FK | `ON DELETE CASCADE`. Índice com `created_at` |
| direction | TEXT | `IN` ou `OUT` |
| source | TEXT | `USER`, `OWNER`, `CAMPAIGN`, `FLOW`, `FOLLOWUP`, `AI`, `HANDOFF` |
| mid | TEXT UNIQUE NULL | id da mensagem na Meta. É a idempotência do webhook |
| text | TEXT NULL | até 4000 caracteres. Apagado na retenção |
| payload | TEXT NULL | payload da resposta rápida tocada |
| flow_id, step_id | NULL | de qual fluxo e passo saiu |
| cost_usd | REAL NULL | custo da resposta da I.A. |
| created_at | TIMESTAMP | índice |

### flow

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | até 80 caracteres |
| is_active | BOOLEAN | |
| keywords | JSON | até 20. Pode ser vazio (fluxo só de boas-vindas ou de botão) |
| match_mode | TEXT | `WHOLE_WORD` ou `CONTAINS` |
| steps | JSON | lista de passos, validada por `FlowInput`. O primeiro é o início |
| created_at, updated_at | TIMESTAMP | |
| deleted_at | TIMESTAMP NULL | remoção lógica |

### chat_outbox

O que o Chat ainda vai mandar, ou já tentou.

| Coluna | Tipo | Notas |
|---|---|---|
| id | INTEGER PK | |
| contact_id | INTEGER FK | `ON DELETE CASCADE`. Índice |
| kind | TEXT | `STEP`, `LINK`, `TEXT`, `FOLLOWUP`, `AI_REPLY` |
| payload | JSON | fluxo e passo, ou o texto. Na I.A., o texto gerado é gravado antes do envio |
| dedupe_key | TEXT UNIQUE | derivado do id da mensagem que causou o envio |
| status | TEXT | `PENDING`, `RUNNING`, `SENT`, `FAILED`, `UNCONFIRMED`, `CANCELLED`. Índice com `run_at` |
| run_at | TIMESTAMP | |
| attempts | INTEGER | |
| locked_until, send_started_at | TIMESTAMP NULL | lease e marca de que a Meta foi chamada |
| sent_at, message_id | NULL | |
| error | TEXT NULL | motivo de falha ou cancelamento |
| created_at, updated_at | TIMESTAMP | `created_at` é quando o envio foi decidido (o acompanhamento compara com a última mensagem da pessoa) |

## Retenção

Texto de comentário e nome de usuário são dados pessoais. A manutenção aplica:

| Dado | Padrão | Variável |
|---|---|---|
| `webhook_event.payload` | 7 dias | `RETENTION_WEBHOOK_DAYS` |
| `delivery.text` e `actor_username` | 90 dias, depois anonimizados | `RETENTION_DELIVERY_DAYS` |
| `link_click` | 365 dias | `RETENTION_CLICK_DAYS` |
| `ops_event` | 90 dias | `RETENTION_OPS_DAYS` |
| `insight_comment.author_id` e `username` | 90 dias, depois apagados (o horário fica) | `RETENTION_DELIVERY_DAYS` |
| `chat_message.text` e `payload` | 90 dias, depois apagados (a linha fica para as contagens) | `RETENTION_DELIVERY_DAYS` |
| `contact` sem mensagem há 90 dias | `ig_user_id` vira `anon:{id}`, `username` e etiquetas apagados | `RETENTION_DELIVERY_DAYS` |
| `chat_outbox` | 90 dias, exceto o que ainda está pendente | `RETENTION_DELIVERY_DAYS` |
| `competitor_post` | os 50 mais recentes por perfil; tudo sai ao remover o perfil | fixo |
| `competitor_report` | os 20 mais recentes | fixo |

Os dados de concorrentes são públicos e de perfis profissionais, não de quem interage com a conta. Mesmo assim saem por completo quando o perfil é removido do painel.

Anonimizar significa limpar `text` e `actor_username`, trocar `actor_id` por `anon` e `source_id` por `anon:{id}`, mantendo a linha para os totais continuarem corretos. A política de privacidade publicada em `/privacy` promete estes prazos.
