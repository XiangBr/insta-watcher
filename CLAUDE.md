# insta-watcher

@AGENTS.md

Automação self-hosted de comment-to-DM para Instagram. Alguém comenta uma palavra-chave num post ou reel, e a conta recebe uma DM com o link (private reply oficial da Meta), com resposta pública opcional. É uma versão enxuta, de conta única, do que ManyChat e OpenReply fazem.

## Contexto

- Código aberto e self-hosted: cada pessoa instala o seu, para a própria conta profissional do Instagram, com o próprio app na Meta.
- O projeto não é nem será um SaaS multi-tenant (ver Não objetivos).
- Instalação: `./scripts/setup.sh` (Docker, qualquer máquina) e `docs/meta-setup.md`. Um agente de código faz a instalação seguindo `AGENTS.md`, seção "Setup assistido por agente".
- Referência de produto e de armadilhas da Meta: o repositório diwenne/openreply (MIT). Não copiamos código. Reimplementamos em Python com escopo menor. O que aprendemos com ele está em `docs/lessons-from-openreply.md`.

## O que esta versão faz

Fase 0 e 1 (MVP, caminho principal):

- Webhook da Meta com verify token e validação de assinatura sobre o corpo bruto.
- Match de palavra-chave (sem diferenciar maiúsculas e acentos, palavra inteira ou parcial, várias palavras).
- Private reply com resposta pública opcional.
- Idempotência por ID do comentário, filtro de comentário próprio e de eco.
- Fila em SQLite com limite de 750 private replies por hora e retry com backoff.
- Reconciliação por polling para comentários que o webhook não entregou.
- Log por envio e refresh automático do token.
- Dashboard para criar e controlar campanhas.

Fase 3 (depois do painel): story reply, gatilho por DM, links rastreados, follow gate.

Análise de concorrência (opcional, desde 2026-10-19): coleta de perfis públicos de concorrentes pelo Apify, classificação em massa com o JEV (modelo de decisão da TypeSafe, via OpenRouter) e relatório comparativo feito por um LLM no OpenRouter. Só liga com `APIFY_TOKEN` e `OPENROUTER_API_KEY`. Detalhes em `docs/architecture.md`, seção Concorrência.

Chat (desde 2026-10-19): fluxos com botões de resposta rápida no direct, acompanhamento dentro da janela de 24h, contatos com etiquetas, modo humano (a automação pausa quando o dono responde) e respostas automáticas com I.A. para o que nenhuma campanha ou fluxo pegou. A I.A. vem desligada, só liga com `OPENROUTER_API_KEY`, tem limites por pessoa e por hora e passa para o dono quando não sabe. Detalhes em `docs/architecture.md`, seção Chat.

## Stack

| Parte | Escolha |
|---|---|
| API e worker | Python 3.12, FastAPI, um único processo Uvicorn |
| Banco | SQLite (modo WAL) via SQLAlchemy 2.0 async + aiosqlite, migrações com Alembic |
| Fila e rate limit | Tabelas no próprio SQLite, sem Redis. O limite de 750 por hora é calculado sobre os envios dos últimos 60 minutos, então sobrevive a reinício |
| HTTP para a Meta | httpx assíncrono |
| Config | pydantic-settings, tudo por variável de ambiente |
| Tarefas periódicas | tasks asyncio no lifespan do FastAPI (worker, reconciliador, refresh de token, limpeza) |
| Concorrência (opcional) | API do Apify (actor de perfis do Instagram), JEV e LLM pelo OpenRouter, tudo por httpx |
| Frontend | Next.js (App Router) + TypeScript + Tailwind, em container (`frontend/Dockerfile`) ou na Vercel |
| Auth do dashboard | Uma senha de admin, sessão em cookie httpOnly no Next.js (BFF) |
| Auth Next para API | Token estático de admin, só no servidor do Next, nunca no navegador |
| Auth do Instagram | Token de longa duração colado pelo dono, guardado criptografado, renovado por job |
| Deploy | Um `docker-compose.yml`: API, painel e um gateway Caddy (HTTPS automático, ou HTTP atrás de proxy ou túnel). Volume para o SQLite. Qualquer máquina com Docker (`docs/deploy.md`) |

## Topologia

```
                         ┌─ /webhook, /privacy, /terms, /data-deletion ─> api (FastAPI, 1 processo) ──> SQLite (volume)
Meta ──HTTPS──> gateway ─┤                                                   │
Navegador ─────> (Caddy) └─ todo o resto ─> web (Next.js BFF) ──Bearer admin──┘──> graph.instagram.com
```

Um endereço público só. A Meta fala com o webhook e as páginas legais. O navegador fala com o painel, que fala com a API pela rede interna, com um token que o navegador nunca vê. A API de admin (`/api/v1`) não é exposta no gateway, exceto com o painel na Vercel (`docs/deploy.md`).

## Decisões firmes

Mudar qualquer uma exige conversar antes e registrar a razão em `docs/architecture.md`.

1. Um processo só, `--workers 1`. SQLite tem um escritor, e a fila depende disso.
2. Sem Redis, Celery, Postgres ou broker. Se o volume pedir, o gatilho para migrar está descrito em `docs/architecture.md`.
3. O webhook grava o evento bruto e responde 200 antes de processar. Nada é processado dentro do request.
4. Envio ambíguo (timeout, erro 5xx, resposta sem ID) vira `UNCONFIRMED` e não é reenviado sozinho. Só rejeição explícita da Meta permite retry.
5. Um gatilho (comentário ou mensagem) gera no máximo uma DM por campanha: `UNIQUE(campaign_id, source_id)`.
6. Segredos só por ambiente. Token do Instagram criptografado em repouso. Nada de segredo em log.
7. Conta única na v1. A tabela `account` tem uma linha.

## Não objetivos

- Multi-tenant, billing, workspaces, convites.
- Atender contas de terceiros. Isso exige Advanced Access na Meta (App Review e Business Verification) e muda o projeto inteiro.
- Automação de navegador, login com a conta do dono ou pedir senha do Instagram. A exceção é a coleta de perfis **públicos** de concorrentes pelo Apify, sem login e opcional, liberada em 2026-10-19 (razão em `docs/architecture.md`).
- Sequências e broadcast. A janela de 24h da Meta limita o que dá para fazer no Instagram.

## O que já foi verificado na Meta

Em Standard Access, com o app em **Live** e a conta inscrita via `subscribed_apps`, o comentário de uma conta sem função no app chega por webhook e a private reply é entregue. Publicar o app exigiu só as URLs de privacidade, termos e exclusão de dados, sem review. Em Development, nada chega. Detalhes em `docs/meta-setup.md`.

Ainda em aberto:

- Se o botão de postback funciona dentro de uma private reply a comentário, e como fica o botão para quem não segue (a DM dessa pessoa pode cair em Solicitações de mensagem). Verificado em 2026-10-02 por story reply: `messaging_postbacks` chega e `is_user_follow_business` devolve `true` e `false`.

## Mapa de documentação

| Arquivo | Conteúdo |
|---|---|
| `AGENTS.md` | Regras operacionais para qualquer agente: comandos, convenções, limites |
| `docs/architecture.md` | Processos, fila, rate limit, fluxos, tratamento de falha, deploy |
| `docs/data-model.md` | Tabelas, índices, retenção |
| `docs/api.md` | Endpoints públicos e de admin |
| `docs/dashboard.md` | Páginas e comportamento do painel |
| `docs/meta-setup.md` | Passo a passo do app na Meta: tokens, webhook, publicação |
| `docs/deploy.md` | Modos de publicação (domínio, proxy, túnel, local, Vercel), operação, backup, atualização |
| `docs/security.md` | Ameaças, controles e checklist antes de publicar |
| `docs/roadmap.md` | Fases, critérios de aceite, ordem de trabalho |
| `docs/lessons-from-openreply.md` | O que copiar, o que evitar |
