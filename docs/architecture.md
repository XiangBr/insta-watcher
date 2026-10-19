# Arquitetura

## Objetivo

Receber eventos de comentário e mensagem do Instagram, decidir se casam com alguma campanha, e responder com private reply dentro dos limites da Meta, sem enviar duas vezes a mesma DM. Para uma conta, rodando barato em uma VPS.

## Processos

Um container, um processo Uvicorn (`--workers 1`). Dentro dele, o FastAPI atende HTTP e o lifespan inicia os loops asyncio abaixo:

| Loop | Função | Intervalo |
|---|---|---|
| dispatcher | lê `webhook_event` pendentes, aplica filtros e matching, cria `delivery` e `job` | acordado a cada webhook, mais um varrimento a cada 5 s |
| worker | pega jobs vencidos, reserva vaga no rate limit, chama a Meta, grava resultado | contínuo, concorrência 3 |
| reconciler | busca comentários recentes que o webhook não entregou | 5 min (configurável) |
| maintenance | refresh de token, limpeza de retenção, limpeza do ledger de envios | 1 h |
| insights | copia métricas da conta, dos posts, do público e o horário dos comentários para o SQLite (só leitura na Meta) | 3 h (configurável) |
| chat | envia o outbox do Chat: passos de fluxo, links, acompanhamentos, passagens, respostas do painel e da I.A. (seção Chat) | acordado pelo dispatcher, mais um varrimento a cada 2 s |
| competitors | só com `APIFY_TOKEN` ou `OPENROUTER_API_KEY`: acompanha a coleta no Apify e classifica os posts com o JEV (seção Concorrência) | 2 min (configurável) |

Um processo só porque o SQLite tem um escritor e a fila depende de claims atômicos. Dois processos exigiriam outro mecanismo de lock. Para uma conta, isso não compensa.

A CLI (`python -m app.cli`) roda como um segundo processo, mas só grava campanha, conta e pausa. Não toca na fila nem no rate limit, então não conflita com o processo principal (o WAL e o `busy_timeout` cobrem as escritas).

## Insights

O sincronizador (`app/insights.py`) só lê da Meta e grava em `insight_*`. A página de insights lê dessas tabelas, então abrir o painel não gasta chamadas. Cada rodada tem um teto de chamadas (`INSIGHTS_MAX_CALLS_PER_SYNC`, 200); as primeiras completam o histórico aos poucos. Ordem: perfil, lista de posts, últimos 7 dias da conta, métricas dos posts (os nunca lidos primeiro, depois os mais desatualizados: posts com menos de 7 dias a cada 2 h, até 60 dias a cada 24 h, os mais velhos a cada 14 dias), público (a cada 24 h), comentários dos posts dos últimos `INSIGHTS_COMMENT_DAYS` dias cujo total mudou, e o resto do histórico diário até `INSIGHTS_BACKFILL_DAYS`.

- Sem `instagram_business_manage_insights` (erro 10 ou 200 com "permission"), as partes de insights param e o resto continua: posts, curtidas, comentários e horários vêm das permissões básica e de comentários.
- Métricas que a Meta recusa para esta conta ou tipo de mídia são pedidas uma a uma e lembradas em `insight_snapshot.unsupported_metrics`, para as próximas rodadas não pedirem de novo.
- O botão do painel e o loop dividem um lock, então nunca rodam juntos.
- Os dias usam um deslocamento fixo de UTC (`INSIGHTS_UTC_OFFSET_MINUTES`, padrão -180, Brasília), porque a imagem não tem base de fusos garantida e o Brasil não tem horário de verão. `online_followers` vem em horário do Pacífico segundo a Meta **(verificar)** e é convertido.

## Concorrência (opcional)

Compara a conta com perfis públicos parecidos. Três peças, cada uma paga pela chave do dono:

1. **Coleta** (`app/apify.py`): o actor `apify~instagram-profile-scraper` (`APIFY_PROFILE_ACTOR`) recebe os usernames e devolve perfil e os ~12 posts mais recentes de cada um. Sem login no Instagram e sem tocar na conta do dono. `maxItems` igual ao número de perfis limita o que o Apify cobra.
2. **Classificação em massa** (`app/competitors.py`, `app/competitor_taxonomy.py`): o JEV (modelo de decisão da TypeSafe, `JEV_MODEL`, padrão `typesafe/jev-1.14` fixado para os limiares não mudarem sozinhos) responde perguntas tipadas pela Decisions API do OpenRouter (`POST /api/alpha/decisions`). Ele não gera texto: devolve a opção escolhida, a confiança e as probabilidades. Por post: formato, gancho, chamada para ação, tom, tema e nível. Por perfil, lendo bio e 12 legendas: público, discurso, monetização, se usa comentário para DM e profundidade. As perguntas ficam num arquivo de configuração para cada pessoa ajustar ao nicho; mudar `TAXONOMY_VERSION` reclassifica tudo.
3. **Relatório** (sob demanda): um LLM no OpenRouter (`COMPETITORS_REPORT_MODEL`) recebe o resumo já calculado (números, rótulos, melhores e piores posts, o que performa no nicho) e devolve JSON com resumo, o que funciona, lacunas, análise por concorrente com ferramentas citadas, e ideias de post com palavra-chave para uma campanha. O painel transforma a palavra-chave em atalho para criar a campanha. O pedido usa saída estruturada (`json_schema` gerado de `ReportContent`); o OpenRouter ignora o parâmetro nos modelos que não o suportam, então o prompt também descreve o formato. A leitura aceita quebra de linha crua dentro de strings. Se a resposta vier cortada (`finish_reason: length`, limite de 12 mil tokens) ou fora do formato, o modelo recebe a própria resposta e o problema, e tem uma segunda chance; o custo das duas chamadas é somado.

O loop `competitors`, a cada `COMPETITORS_INTERVAL_SECONDS`:

- Se há uma coleta `RUNNING` em `competitor_scan`, consulta o run no Apify. Terminou bem: grava perfis e posts (mantém os 50 mais recentes por perfil). Falhou, ou passou de 45 min: marca `FAILED` com o motivo.
- Senão, abre uma coleta para todo perfil nunca coletado ou mais velho que `COMPETITORS_REFRESH_HOURS` (padrão 7 dias). A própria conta entra sozinha (`is_self`), coletada do mesmo jeito para a comparação ser justa.
- Classifica até `COMPETITORS_CLASSIFY_PER_RUN` posts (4 chamadas em paralelo) e os perfis pendentes. Post sem legenda não é enviado. Legenda editada volta para a fila.

**Desempenho de um post** é o engajamento dele, (curtidas + comentários) ÷ seguidores, dividido pela mediana do próprio perfil. Assim um perfil de 10 mil e um de 200 mil entram na mesma régua. "O que funciona no nicho" é a mediana desse desempenho por opção de cada pergunta, só com posts dos concorrentes, sem fixados e com no mínimo 3 posts por opção.

Custos guardados: `competitor_scan.cost_usd` (do Apify), o acumulado do JEV em `insight_snapshot.competitors_status` e `competitor_report.cost_usd` (do OpenRouter, com `usage.include`).

**Decisão (2026-10-19):** o `CLAUDE.md` excluía scraping. A exceção foi aberta só para dados **públicos** de perfis de terceiros, coletados por um serviço externo, sem login e sem automação de navegador na conta do dono. O risco de termos de uso fica com o Apify, não com a conta do Instagram de quem hospeda. O módulo é opcional e desligado sem as chaves, então quem publica o projeto não precisa usá-lo.

## Tratamento do webhook

1. `GET /webhook` responde ao desafio da Meta se `hub.verify_token` bater com `WEBHOOK_VERIFY_TOKEN` (comparação em tempo constante).
2. `POST /webhook` lê o corpo bruto (até 256 KB, senão 413), valida `X-Hub-Signature-256` com HMAC-SHA256 usando o segredo do app (aceita os dois segredos, do app Facebook e do app Instagram, porque a doc não deixa claro qual a Meta usa), grava em `webhook_event` e responde 200. Assinatura inválida responde 401 e registra um aviso no log, sem o corpo. JSON inválido com assinatura válida responde 400.
3. O dispatcher faz o resto fora do request.

Gravar antes de processar evita perder evento se o processamento falhar, e evita que a Meta reenvie por causa de erro nosso.

## Fluxo: comentário para DM

Código em `app/dispatcher.py` e `app/worker.py`.

1. O dispatcher lê `entry[].changes[]` com `field == "comments"` e, em separado, as respostas a story (seção abaixo). Os demais campos viram evento processado sem efeito.
2. Descarta o comentário se o autor é a própria conta (`from.id` igual ao `entry.id` ou ao `ig_user_id` guardado).
3. Escolhe **uma** campanha: primeiro as do post específico, depois as de "qualquer post", e entre elas a mais antiga. A primeira que casar a palavra-chave vence. Comentário em anúncio chega com o ID da mídia do anúncio e um `original_media_id`, e o match considera os dois.
4. Insere `delivery` com `INSERT .. ON CONFLICT DO NOTHING`, sobre `UNIQUE(campaign_id, source_id)` e `UNIQUE(trigger, source_id)`. Se nada for inserido, o comentário já foi tratado (webhook repetido ou reconciliador) e não entra nada na fila.
5. Cria o job `PRIVATE_REPLY` e, se a campanha responde em público, um job `PUBLIC_REPLY` 10 s depois. Cada um tem estado e retry próprios: a falha de um não bloqueia nem repete o outro.
6. O worker confere se a entrega ainda está `PENDING`, reserva a vaga no rate limit, grava `send_started_at`, chama a Meta e grava o resultado.

Se o evento quebrar no dispatcher, ele vira `FAILED` guardando só o tipo da exceção, porque a mensagem poderia citar o texto do comentário.

## Matching

Normalização aplicada nos dois lados da comparação: minúsculas, remoção de diacríticos (`preço` e `preco` casam), remoção de emoji e pontuação. Modos: palavra inteira (fronteira de palavra Unicode) ou contém. Várias palavras funcionam como OR. Usar propriedades Unicode (`\p{L}` via `regex`, ou `unicodedata` no Python), não `\w` ASCII.

## Rate limit

Meta documenta 750 private replies por hora por conta profissional, para comentários em posts e reels. A implementação usa janela deslizante sobre a tabela `send_event`:

1. Sob um `asyncio.Lock` do processo, conta os `send_event` com `kind = 'private_reply'` e `sent_at` nos últimos 3600 s. O lock basta porque só existe um processo. Com dois, seria preciso um lock no banco.
2. Se a contagem é menor que `PRIVATE_REPLY_HOURLY_LIMIT` (padrão 750), insere uma linha reservando a vaga e segue.
3. Se atingiu, reagenda o job para `sent_at do evento mais antigo da janela + 3600 s`. Não descarta.
4. Se a Meta rejeitar explicitamente o envio, ou se a conexão nem abrir, remove a reserva daquele job. Se o resultado for ambíguo, a reserva fica, porque a mensagem pode ter saído.

A checagem acontece no worker, logo antes de cada envio, e não no agendamento. Um bug de laço não consegue furar o teto.

Como a janela vive no SQLite, reiniciar o container não zera o contador. O limite pode ser compartilhado com outras chamadas da conta, então a variável existe para baixar o teto (por exemplo 700) se a Meta começar a devolver erro de limite antes dos 750.

Consequência prática: um reel com 3.000 comentários com a palavra-chave leva cerca de 4 horas para ser respondido por completo.

## Fila

Tabela `job` (`app/queue.py`). O claim é um único statement, atômico no SQLite:

```sql
UPDATE job SET status = 'RUNNING', locked_until = :now + :lease
 WHERE status = 'PENDING' AND id IN (
   SELECT id FROM job WHERE status = 'PENDING' AND run_at <= :now
    ORDER BY run_at, id LIMIT :n)
RETURNING id;
```

`attempts` só sobe quando a Meta vai ser chamada. Esperar por vaga no rate limit ou por um token novo não conta como tentativa.

PRAGMAs na conexão: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`.

Recuperação no boot (todo `RUNNING` está órfão, porque só existe um processo) e a cada ciclo do worker (jobs com lease vencido):

- Se `send_started_at` está vazio, a Meta nunca foi chamada. O job volta a `PENDING`.
- Se `send_started_at` está preenchido, não dá para saber se a DM saiu. O job vira `UNCONFIRMED`.

O worker grava `send_started_at` imediatamente antes da chamada à Meta e limpa o campo a cada reagendamento, para que um crash antes da próxima chamada não seja confundido com envio ambíguo.

## Classificação de erros e retry

| Resultado da chamada | Classe | Ação |
|---|---|---|
| Código 190 (token inválido ou expirado) | Rejeição confirmada | Conta vai para `TOKEN_INVALID`, a tentativa é devolvida, os jobs esperam um token novo, `ops_event` ERROR |
| Códigos 4, 17, 32, 368, 613 (limite) | Rejeição confirmada | Reagenda com backoff 5, 15 e 45 min |
| Código 100 numa mensagem com botão | Rejeição confirmada | Uma nova tentativa imediata com o link dentro do texto (`fallback_text`) |
| Outros erros 4xx com corpo de erro da Graph (10, 100, 200, 551...) | Rejeição confirmada | `FAILED`, sem retry, grava o motivo |
| Conexão recusada ou timeout de conexão | Não enviado | Reagenda com backoff, como limite |
| Código 1 ou 2, qualquer 5xx, timeout depois de enviar, resposta sem `message_id`, erro sem corpo da Graph | Ambíguo | `UNCONFIRMED`, sem retry automático |

Os códigos acima vêm do tratamento que o OpenReply dá a cada um e da doc da Meta. O significado exato do 551 não foi confirmado, então a regra para ele deve ser revista quando aparecer em log real.

`UNCONFIRMED` aparece no painel com a orientação de olhar a caixa de entrada do Instagram antes de reenviar manualmente. O motivo é evitar DM duplicada, que é pior do que DM perdida.

Máximo de 4 tentativas por job (uma inicial mais três com backoff).

## Reconciliação

Webhook da Meta é best effort. Há comentários que nunca disparam evento. O reconciler existe para isso, com regras estreitas:

1. Para cada post com campanha ativa, lista os comentários recentes (janela de 72 h, configurável, até 3 páginas). Campanhas de "qualquer post" fazem a varredura nas 10 mídias mais recentes.
2. Age só se o comentário casa a palavra-chave, não existe `delivery` para ele, e o dono ainda não respondeu (checagem das respostas ao comentário).
3. Considera só comentários feitos **depois** da criação da campanha. Sem isso, criar uma campanha para um post antigo mandaria DM para quem comentou antes.
4. Teto de 30 comentários novos por post em cada varredura, para não esgotar o limite da API de comentários.
5. Usa a mesma função do dispatcher, com `origin = POLLING`. Rate limit, idempotência e log são os mesmos.

Limitação conhecida: comentários filtrados pelo recurso de palavras ocultas do Instagram podem não aparecer na API.

## Token do Instagram

- Entrada: `python -m app.cli set-token`, que lê o token com `getpass`. Pelo painel, `PUT /api/v1/account/token`. O backend consulta `/me` para validar e guarda o `user_id` e o username. Um token de outra conta é recusado.
- Armazenamento: criptografado com AES-256-GCM, chave em `ENCRYPTION_KEY`. Nunca devolvido por API.
- Renovação: a Meta só renova tokens com mais de 24 h. O primeiro refresh acontece assim que possível, para descobrir a validade real (até lá ela é estimada em 60 dias). Depois disso, a renovação acontece quando faltam menos de `TOKEN_REFRESH_WHEN_DAYS` (20). Tentativas que falham se repetem no máximo uma vez por dia, cada uma com um `ops_event` ERROR que informa os dias restantes. Token vencido não é renovável, e o dono gera outro.
- O endpoint de refresh só aceita o token na query string. A chamada não é registrada em log: o log do httpx fica desligado e os erros nunca carregam a URL.
- Cabeçalho: chamadas à Meta enviam o token em `Authorization: Bearer`, não na query string, para não vazar em log de proxy.

## Follow gate (Fase 3, implementado)

A campanha pode guardar o link atrás de um follow. Funciona para comentário e para story reply.

1. **Convite.** A primeira DM não leva o link. Leva o texto de convite da campanha e um botão de postback ("Já segui"), com payload `bw:gate:<id da entrega>`. Para story reply é uma mensagem direta. Para comentário é a private reply. A entrega nasce com `gate_state = WAITING`.
2. **Toque.** A Meta envia um evento `messaging_postbacks`. O dispatcher só aceita um payload exatamente nesse formato (outros botões, como a sonda de diagnóstico, são ignorados), grava o `mid` em `postback_receipt` (um repasse do mesmo toque não conta de novo, nem depois de reiniciar), e confere quem tocou: em story reply precisa ser a pessoa da entrega, e em comentário a primeira pessoa que tocar passa a ser a dona do convite. Cada pessoa pode tocar até 5 vezes (`MAX_TAPS`). Passado disso a entrega vira `STOPPED`. O toque cria um job `FOLLOW_CHECK`.
3. **Checagem.** O job consulta `is_user_follow_business` no perfil da pessoa. É somente leitura, então um crash só repete a consulta.
   - `true`: libera o link (`PASSED`).
   - `false`: espera e olha de novo, com 20 s e depois 40 s (a Meta demora a refletir um follow novo). Se ainda não segue, reenvia o convite e espera outro toque (`REMINDED`). O botão do convite original continua valendo, então um lembrete que falhe não custa nada e não é repetido.
   - `null`, campo ausente ou recusa da Meta (por exemplo sem consentimento para essa pessoa): **libera o link** (`RELEASED`) e registra um evento. Decisão do dono em 2026-10-02: segurar o link de quem talvez já siga é pior do que liberar cedo demais de vez em quando.
   - Falha da própria consulta (rede, queda da Meta): tenta de novo após 15 s, 1 min e 5 min, e só então libera como "não sei". Token recusado pausa a checagem e devolve a tentativa.
4. **Link.** A liberação cria um job `FOLLOWUP_DM` com `dedupe_key = followup:<entrega>`, então várias checagens que concordem, mesmo simultâneas, enviam uma DM só. Ela segue as regras de toda DM: janela de 24 h (contada do último toque, que a abriu), erro ambíguo vira `UNCONFIRMED` e não é reenviado, e a recusa do botão de link refaz o envio uma vez com o link no texto. O resultado fica em `followup_status`, `followup_sent_at` e `followup_error`, separado do `status` do convite.

O convite **nunca** tem fallback para texto: sem o botão não dá para tocar, e um texto com o link vazaria o que o gate segura. Se a Meta recusar o postback (código 100), a entrega falha na hora e o motivo aparece em Entregas.

Constantes do gate (atrasos, teto de toques) ficam em `app/gate.py`, porque são escolhas nossas, não limites da Meta. Desligar o gate numa campanha com convites ainda não enviados faz esses convites saírem como DM normal, já com o link.

Verificado em produção em 2026-10-02, por story reply: o postback chega, e `is_user_follow_business` devolve `true` e `false` de verdade (também `is_business_follow_user`). **Não verificado:** postback dentro de uma private reply a comentário, e como fica o botão para quem não segue, porque a Meta diz que a DM de quem não segue pode cair em "Solicitações de mensagem".

## Story reply (Fase 3, implementado)

Quem responde a um dos seus stories gera um evento `messages` com `message.reply_to.story`. A Meta documenta dois formatos para esse evento (`entry[].messaging[]` e `entry[].changes[]` com `field: "messages"`), e o parser lê os dois. O campo `messages` já fica assinado por `./scripts/iw subscribe`.

- **O que dispara:** só resposta com texto que case uma palavra-chave de uma campanha com `trigger_story_replies`. Emoji solto e conversa normal não disparam. Entre várias campanhas, vence a mais antiga. O story em si não é escolhido (stories expiram em 24 h, então campanha por story não faz sentido).
- **O que nunca dispara:** eco (`is_echo`), mensagem apagada, `is_self`, mensagem sem texto, mensagem sem `reply_to.story` (um DM comum) e mensagem enviada pela própria conta. Sem o filtro de eco, uma resposta automática com a própria palavra-chave responderia a si mesma. Um DM comum com palavra-chave continua sem efeito: o gatilho por DM não foi feito.
- **Idempotência:** `delivery.trigger = STORY_REPLY` e `source_id` é o `mid` da mensagem, sobre o mesmo `UNIQUE(trigger, source_id)` dos comentários.
- **Envio:** job `DIRECT_MESSAGE`, endereçado pelo ID da pessoa (`recipient.id`) e não por comentário. Só vale por 24 h depois da resposta (`MESSAGING_WINDOW`): passado esse prazo o job falha na hora, sem chamar a Meta, com a mensagem "24 h messaging window expired". Não passa pelo limite de 750 por hora, que é das private replies, e não gera resposta pública (não há comentário).
- **`{username}`:** o webhook não traz o @, então o worker consulta o perfil (`GET /<id>?fields=username`) antes de enviar. Se a consulta falhar, o envio segue com o nome vazio. O @ fica guardado na entrega.
- **Falha:** mesma regra das private replies. Timeout ou 5xx vira `UNCONFIRMED` e não é reenviado sozinho, e a rejeição do template de botão (código 100) refaz o envio uma vez com o link no texto.
- **Sem rede de segurança:** o reconciliador só varre comentários. Uma resposta a story que o webhook não entregar não é recuperada.

## Links rastreados (fase 3)

`GET /r/{slug}` registra o clique e redireciona com 302. O slug é aleatório (7 bytes em base64url). O IP é guardado como hash com sal vindo de `CLICK_HASH_SALT`, sem valor padrão. Até 2 botões de link por DM, cada um com slug próprio. Títulos de botão têm no máximo 20 caracteres.

## Chat

Tudo o que chega por mensagem direta passa pelo dispatcher (`app/chat.py`, dentro da mesma transação do evento). Cada mensagem vira uma linha em `chat_message` (`mid` único, então webhook repetido não grava duas vezes) e a pessoa vira um `contact`. A janela de 24h começa na última mensagem dela (`last_inbound_at`). A primeira regra que casar responde:

1. **Modo humano** (`human_until` no futuro): nada automático responde.
2. **Toque num botão de resposta rápida** de um fluxo (payload `bwf:{fluxo}:{passo}:{opção}`): a ação da opção, que pode ser ir para outro passo, mandar um link ou chamar o dono.
3. **Resposta a story** que casa com uma campanha de story (o caminho de campanha de sempre).
4. **DM comum** que casa com uma campanha com `trigger_messages` (vira `delivery` com `trigger = MESSAGE` e um job `DIRECT_MESSAGE`, então follow gate e botão de link funcionam igual).
5. **Palavra-chave de um fluxo** ativo: o primeiro passo.
6. **Primeira mensagem da vida** da pessoa, com fluxo de boas-vindas configurado: esse fluxo.
7. **I.A.**, se estiver ligada e houver `OPENROUTER_API_KEY`: uma resposta, 8 s depois. Mensagens em sequência empurram a mesma resposta pendente, então uma rajada recebe uma resposta só.

O envio é do `chat` (`app/chat_worker.py`), um loop próprio sobre a tabela `chat_outbox`, acordado pelo dispatcher. As regras são as mesmas da fila de campanhas: `send_started_at` gravado antes de chamar a Meta, rejeição explícita pode ter retry (backoff de `meta/limits.py`), resultado ambíguo vira `UNCONFIRMED` e não é reenviado, e no boot um `RUNNING` volta a `PENDING` se a Meta não foi chamada ou vira `UNCONFIRMED` se pode ter sido. Logo antes de cada envio o worker confere:

- se a pessoa está em modo humano (cancela, exceto a mensagem de passagem e a resposta digitada no painel);
- se a janela de 24h está aberta (cancela se fechou);
- se um acompanhamento com `only_if_no_reply` ainda faz sentido (cancela se a pessoa escreveu depois do passo).

Um fluxo é uma lista de passos (`flow.steps`, validado por `app/flows.py`). Cada passo manda um texto com **ou** um botão de link (template de botão) **ou** até 10 botões de resposta rápida, nunca os dois, porque o template não carrega respostas rápidas. Pode também pôr etiquetas no contato e agendar um acompanhamento de até 23 h.

**Eco e modo humano.** A Meta devolve como eco toda mensagem que sai da conta. Um eco é nosso quando bate com um `message_id` do outbox ou de uma entrega, ou quando há um envio nosso para essa pessoa em andamento ou nos últimos 2 min (o eco pode chegar antes de a chamada de envio voltar). Qualquer outro eco é o dono digitando no app: vira mensagem `OWNER` e, com `pause_on_manual_reply`, pausa a automação para essa pessoa por `handoff_hours` e cancela o que estava agendado. Responder pelo painel também pausa.

**I.A.** (`app/chat_ai.py`). O modelo (`ChatConfig.ai_model` ou `CHAT_AI_MODEL`) recebe a persona, a base de conhecimento, os fluxos com as palavras que os disparam e as últimas 12 mensagens, e devolve JSON `{reply, handoff}` por saída estruturada. Não tem ferramenta nenhuma. As regras do prompt mandam passar para o dono quando a base não cobre, quando a pessoa pede alguém, reclama, negocia ou o assunto é sensível. Resposta ilegível também vira passagem. Na passagem sai a `handoff_message` e a conversa entra em modo humano. Limites: `ai_max_per_contact_day` (passou, a pessoa recebe a passagem) e `ai_max_per_hour` para todas as pessoas (passou, a resposta é cancelada e fica um evento em Diagnóstico). O texto gerado é gravado no outbox antes do envio, então um retry depois de `NotSent` não paga o modelo de novo. Erro do OpenRouter falha a mensagem sem enviar nada.

A configuração do Chat fica na tabela `setting`, chave `chat` (`ChatConfig`). Tudo começa desligado.

**Decisão (2026-10-19):** o `CLAUDE.md` deixava a I.A. fora das respostas. O dono pediu resposta automática. Ela entrou como último recurso, desligada por padrão, sem ferramentas, com passagem para o dono quando não sabe, limites de volume e pausa automática quando o dono responde. Sequências e broadcast continuam fora: tudo o que sai é resposta a quem escreveu, dentro da janela de 24h.

## Ponto de extensão para classificação de intenção

A resposta livre por I.A. já existe no Chat (seção Chat), como último recurso. A classificação de intenção para escolher campanha continua fora. O matcher fica atrás de uma interface pequena:

```python
class TriggerMatcher(Protocol):
    async def match(self, text: str, campaign: Campaign) -> MatchResult: ...
```

A implementação atual é `KeywordMatcher`. Uma classificação de intenção por modelo entraria como outra implementação, sem mexer no dispatcher.

## Deploy

Passo a passo em `docs/deploy.md`. Arquivos: `docker-compose.yml`, `deploy/Caddyfile`, `backend/Dockerfile`, `frontend/Dockerfile`, `scripts/setup.sh`.

- Três serviços: `api`, `web` (perfil `web`) e `gateway` (Caddy), mais túneis da Cloudflare opcionais (perfis `tunnel` e `named-tunnel`). O mesmo compose serve todos os modos; o que muda é o `.env` (`SITE_ADDRESS`, `HTTP_BIND`, `HTTPS_BIND`, `COMPOSE_PROFILES`).
- Só o gateway publica porta. Ele manda `/webhook`, `/health` e as páginas legais para a API, fecha `/api/v1` e `/docs`, e o resto vai para o painel. Com um domínio em `SITE_ADDRESS`, o Caddy emite e renova o certificado sozinho.
- Imagem da API multi-stage com bases fixadas por digest, usuário não-root (uid 10001), venv pronta no build. Em execução não roda `uv`, porque o filesystem é somente leitura. O painel é um build `standalone` do Next.js, também não-root.
- No boot: `alembic upgrade head` e depois `uvicorn --workers 1 --proxy-headers`. Como a API não publica porta, só o gateway e o painel, na rede interna do compose, chegam até ela, e por isso `FORWARDED_ALLOW_IPS` é `*`.
- Containers com `read_only`, `tmpfs /tmp`, `cap_drop: ALL`, `no-new-privileges`, limites de memória e PIDs. O gateway recebe só `NET_BIND_SERVICE`.
- O SQLite fica no volume `insta_watcher_data` (`/data`). `scripts/backup.sh` faz cópia consistente com a API no ar.

Proteções de log:

- `app/logging_setup.py` remove query strings e tokens Bearer das linhas de log, inclusive do access log do uvicorn, porque o desafio do webhook leva `hub.verify_token` na URL.
- O engine do SQLAlchemy usa `hide_parameters=True`, para que um erro de banco não imprima o payload do webhook (texto de comentário de terceiros).
- Falha de assinatura vai só para o log (tamanho e presença do cabeçalho), não para `ops_event`. Gravar no banco a cada POST inválido deixaria qualquer pessoa encher o banco.

O painel recebe `API_BASE_URL`, `API_ADMIN_TOKEN`, `ADMIN_PASSWORD_HASH` e `SESSION_SECRET`, todas só de servidor (nenhuma com `NEXT_PUBLIC`). Em container, `API_BASE_URL` é `http://api:8000`, na rede interna; na Vercel, o endereço público. As páginas de privacidade, termos e exclusão de dados exigidas pela Meta são servidas pela API.

## Painel e API de admin (Fase 2)

O painel (Next.js, em container ou na Vercel) é um BFF. O navegador só fala com ele, e ele chama `/api/v1` com `API_ADMIN_TOKEN`. As rotas estão em `docs/api.md`, e os tipos do frontend são gerados por `python -m app.export_openapi`, que monta o schema sem servidor e sem segredos reais. O `/openapi.json` continua desligado em produção.

Decisões tomadas em 2026-10-02:

- **Contador de login no backend.** A senha é conferida no Next.js, com hash argon2 em `ADMIN_PASSWORD_HASH`. Um contador em memória não funcionaria, porque cada request na Vercel pode cair numa instância diferente. Um Redis gerenciado (Upstash ou Vercel KV) seria mais um serviço e mais um segredo. Por isso as falhas são registradas no SQLite pelas rotas `/api/v1/login-throttle/*`. Os detalhes estão em `docs/security.md`, seção 5.
- **Reconciliação manual** (`POST /api/v1/reconcile`) divide um lock com o loop, então duas varreduras nunca rodam juntas.
- **Retry pelo painel** só aceita `FAILED`, e `UNCONFIRMED` com `confirm_not_delivered: true`. O job volta para `PENDING` com as tentativas zeradas, e o `ops_event` registra quem pediu.

## Limites e quando mudar

SQLite com um processo atende o volume de uma conta com folga, mas isso não foi medido. Sinais de que é hora de rever a decisão: `SQLITE_BUSY` recorrente em `ops_event`, necessidade de mais de uma conta ou de mais de um processo, ou fila acumulando por causa de escrita e não por causa do rate limit da Meta. Nesse ponto, o caminho é Postgres e uma fila externa, com migração planejada.
