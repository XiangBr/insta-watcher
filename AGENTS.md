# AGENTS.md

Regras operacionais para qualquer agente de código neste repositório. Contexto do projeto em `CLAUDE.md`.

## Estado do repositório

Funcional de ponta a ponta. O `backend/` tem webhook, dispatcher, worker com fila e rate limit, reconciliador, token criptografado com refresh, retenção, páginas legais, insights, concorrência, Chat (fluxos e I.A. no direct), API de admin e a CLI de operação (com `doctor`). O `frontend/` tem o painel completo. A raiz tem o `docker-compose.yml` (API, painel e gateway Caddy), o `deploy/` tem o Caddyfile e o `scripts/` tem o setup, a CLI e o backup. Não invente comando que não esteja listado aqui. Se faltar um, proponha e adicione a este arquivo.

## Layout previsto

```
backend/
  app/
    main.py            # entrypoint ASGI (app = create_app())
    factory.py         # create_app + lifespan que inicia os loops
    config.py          # pydantic-settings, recusa segredo ausente ou fraco
    logging_setup.py   # redação de query string e Bearer nos logs
    cli.py             # CLI de operação até o painel existir
    campaigns.py       # validação e gravação de campanhas
    dispatcher.py      # webhook_event -> delivery + jobs (idempotência)
    maintenance.py     # retenção, ledger e refresh de token
    status.py          # snapshot para /api/v1/status e `cli status`
    db.py              # engine, sessão, PRAGMAs do SQLite
    models.py          # SQLAlchemy
    meta/              # cliente httpx, erros, limites, assinatura do webhook
    matching.py        # normalização e match de palavra-chave
    queue.py           # claim, retry, lease dos jobs
    ratelimit.py       # janela deslizante de 750/h
    worker.py          # processa jobs (comentário, DM, postback)
    reconciler.py      # polling de segurança
    insights.py        # sincronizador de insights (só leitura na Meta, cache em insight_*)
    competitors.py     # concorrência: coleta no Apify, classificação com o JEV, relatório
    competitor_taxonomy.py  # perguntas do JEV (configuração por nicho, TAXONOMY_VERSION)
    apify.py           # cliente do Apify (run, status, dataset)
    openrouter.py      # cliente do OpenRouter (Decisions API do JEV e chat do relatório)
    chat.py            # DMs: contatos, roteamento (fluxo, campanha, I.A.), eco e modo humano
    chat_worker.py     # envia o chat_outbox (mesmas regras de UNCONFIRMED da fila)
    chat_ai.py         # prompt e resposta da I.A. no direct
    flows.py           # validação de fluxos e a config do Chat (tabela setting)
    tokens.py          # criptografia e refresh
    login_throttle.py  # contador de falhas de login do painel
    api_models.py      # corpos da API de admin (origem dos tipos do frontend)
    errors.py          # envelope de erro de /api/
    export_openapi.py  # gera o OpenAPI sem servidor nem segredos reais
    routers/           # public.py (webhook, redirect, health), admin.py, insights.py, competitors.py, chat.py
  alembic/
  tests/
  Dockerfile
frontend/
  app/                 # Next.js App Router
  components/          # ui.tsx (botão, cartão, campo, diálogo, selos), icons.tsx (SVG próprios), charts.tsx (gráficos SVG sem dependência), nav, brand, theme-toggle, post-picker, post-thumb, phone-preview
  lib/                 # env, sessão (jose), login, BFF, clientes do OpenAPI, regras puras com teste (attention, campaign-form, chat, competitors, dm-preview, insights, insights-advice)
  tests/               # vitest: sessão, origem, BFF, login, proxy, regras puras
  proxy.ts             # Next 16 chama de proxy o que antes era middleware: CSP com nonce e sessão
  Dockerfile           # painel em container (Next standalone)
deploy/                # Caddyfile do gateway e o compose extra para proxy em Docker
scripts/               # setup.sh (instalação), iw (CLI no container), backup.sh
docker-compose.yml     # api, web, gateway e túneis opcionais (docs/deploy.md)
docs/
```

## Comandos

Instalação e operação (Docker, qualquer máquina; `docs/deploy.md`):

- `./scripts/setup.sh` instala: pergunta o modo (domain, proxy, tunnel, cloudflare, local, vercel), escreve o `.env`, gera os segredos, sobe os containers e imprime o que colar na Meta. Rodar de novo é seguro (não troca segredos já gerados).
- `./scripts/setup.sh --mode MODE --yes` sem perguntas, lendo as respostas do ambiente (lista no topo do script).
- `./scripts/setup.sh --show` imprime de novo as URLs e os valores para a Meta.
- `./scripts/iw doctor` confere a instalação e diz o próximo passo de cada pendência. Sai com código 1 enquanto falta algo obrigatório.
- `./scripts/iw set-token`, `./scripts/iw subscribe`, `./scripts/iw --help` a CLI de operação dentro do container.
- `docker compose up -d --build` sobe ou atualiza. `docker compose logs -f api` acompanha. As migrações rodam no boot.
- `./scripts/backup.sh` copia o banco para `backups/` com a API no ar.

Repositório (uma vez por clone):

- `git config core.hooksPath .githooks` ativa o pre-commit com gitleaks.

Backend (`cd backend`):

- `uv sync` instala dependências.
- `uv run uvicorn app.main:app --reload --workers 1` roda local.
- `uv run pytest` roda os testes.
- `uv run ruff check . && uv run ruff format --check .` lint e formatação.
- `uv run mypy app` tipos.
- `uv run alembic upgrade head` aplica migrações.
- `uv run alembic revision --autogenerate -m "mensagem"` cria migração.
- `uv run python -m app.export_openapi ../frontend/openapi.json` grava o schema da API para o frontend. Rode depois de mudar rota ou modelo da API.
- `uv run python -m app.cli --help` comandos administrativos (`set-token`, `subscribe`, `status`, `pause`, `resume`, `posts`, `campaign create|list|pause|resume|delete`, `deliveries`, `events`). Token sempre por prompt, nunca por argumento. Com Docker: `./scripts/iw ...`.

Windows com Smart App Control: a extensão compilada do SQLAlchemy 2.1 é bloqueada ("An Application Control policy has blocked this file"). Contorno local, só no `.venv`: `DISABLE_SQLALCHEMY_CEXT=1 uv pip install --reinstall --no-binary sqlalchemy sqlalchemy==<versão do uv.lock>` e depois `uv run --no-sync ...`. Um `uv sync` desfaz o contorno. Na imagem Linux isso não acontece.

Frontend (`cd frontend`):

- `pnpm install`, `pnpm dev`, `pnpm lint`, `pnpm typecheck`, `pnpm test`.
- `pnpm hash-password` gera o `ADMIN_PASSWORD_HASH` (argon2id), pedindo a senha sem eco.
- Fixados de propósito: `typescript` em 5.9 (o `openapi-typescript` não roda no TypeScript 7 nativo) e `eslint` em 9 (o `eslint-plugin-react` do `eslint-config-next` quebra no ESLint 10). Antes de subir qualquer um dos dois, rode `pnpm gen:api` e `pnpm lint`.

Windows com Smart App Control: o binário nativo do SWC é bloqueado, e o Turbopack exige o nativo. Localmente use `pnpm exec next dev --webpack` e `pnpm exec next build --webpack`. Na Vercel e na imagem Docker (Linux) o padrão funciona, então o script `build` não muda.
- `pnpm gen:api` regenera os tipos a partir de `frontend/openapi.json` (gerado pelo `app.export_openapi`).

## Convenções

- Python 3.12, type hints em tudo, pydantic v2, async de ponta a ponta. Nada de chamada bloqueante dentro do event loop.
- Datas em UTC, guardadas como timestamps ISO ou inteiros. Nunca datetime ingênuo.
- SQL só pelo SQLAlchemy ou com parâmetros ligados. Nunca montar SQL com f-string.
- Toda mudança de schema vem com migração Alembic.
- Os limites da Meta (750 por hora, 20 caracteres de título de botão, 3 botões, janela de 24h, delays de backoff) ficam em `backend/app/meta/limits.py`, em um lugar só.
- Código, identificadores e mensagens de commit em inglês. Documentação em português do Brasil. Textos da interface passam por um único arquivo de mensagens, para traduzir depois.
- Commits no formato Conventional Commits.

## Regras que não se negociam

1. Valide a assinatura do webhook sobre o corpo bruto, antes de qualquer parse. Compare com `hmac.compare_digest`.
2. Compare verify token e tokens de admin em tempo constante.
3. O handler do webhook só grava e responde 200. O processamento acontece no worker.
4. Idempotência: nunca remova o `UNIQUE(campaign_id, source_id)` nem a checagem antes de enviar.
5. Erro ambíguo de envio vira `UNCONFIRMED`. Não reenvie automaticamente.
6. Nunca registre em log: token do Instagram, segredos de app, `API_ADMIN_TOKEN`, senha, URL com query string de chamada à Meta, texto de comentário em nível INFO.
7. Não use fallback de segredo no código. Se a variável não existe, a aplicação não sobe.
8. Todo URL de destino de link rastreado passa por validação (somente `https`, host não vazio, sem credenciais embutidas).
9. Exportação CSV neutraliza células que começam com `=`, `+`, `-` ou `@`.
10. Um processo só. Não adicione Redis, Celery ou Postgres sem discussão.

## Testes obrigatórios

Cada item abaixo precisa de teste automatizado antes de a fase fechar:

- Assinatura do webhook: válida, inválida, ausente, corpo alterado.
- Matcher: acento (`preço` e `preco`), maiúsculas, palavra inteira contra parcial, emoji, várias palavras.
- Idempotência: o mesmo comentário entregue duas vezes (webhook duplicado, webhook mais polling) gera uma DM.
- Filtro de comentário próprio e de eco de DM.
- Rate limit: 750 envios na janela bloqueiam o 751º e agendam para quando abrir vaga, e o contador sobrevive a reinício.
- Classificação de erro: rejeição confirmada contra ambígua, e o que cada uma dispara.
- Reconciliador: ignora comentário já respondido pelo dono e respeita o teto por varredura.
- Recuperação de crash: job que estava `RUNNING` no reinício vira `UNCONFIRMED` se já houve chamada à Meta, e volta a `PENDING` se não houve.

## Definição de pronto

- Testes passam, lint e tipos limpos.
- Migração incluída se o schema mudou.
- `docs/` atualizado se comportamento, endpoint ou variável de ambiente mudou. Variável nova entra no `.env.example`.
- Nenhum segredo, token ou payload real de webhook no diff.

## Pergunte antes de

- Adicionar dependência nova.
- Mudar qualquer constante em `meta/limits.py`.
- Tocar em autenticação, criptografia ou validação de assinatura.
- Apagar ou renomear coluna.
- Qualquer coisa que torne o projeto multi-conta ou multi-usuário.

## Segurança

Checklist completo em `docs/security.md`. Vulnerabilidade encontrada vai para o canal privado descrito em `SECURITY.md`, nunca para issue pública.

## Setup assistido por agente

Para um agente (Claude Code, Codex, Cursor e afins) instalar o insta-watcher para alguém. No Claude Code, `/setup` carrega este roteiro.

1. Leia `docs/deploy.md` e `docs/meta-setup.md` antes de agir.
2. Descubra sozinho o que dá: `docker info`, `docker compose version`, se é um servidor (IP público, portas 80 e 443 livres: `ss -ltnp` ou `docker ps`), se já existe um proxy reverso, se há um domínio apontando para a máquina (`dig +short DOMINIO`). Pergunte só o que não dá para descobrir: onde vai rodar, o domínio e o email de contato público.
3. Escolha o modo com a pessoa: `domain` (servidor com domínio e portas 80/443 livres), `proxy` (já existe um proxy; use `deploy/compose.proxy-network.yml` se ele roda em Docker), `tunnel` (computador da pessoa, para testar), `cloudflare` (túnel fixo), `local` (só olhar o painel), `vercel`.
4. **Segredos nunca passam pelo chat.** Os dois segredos do app da Meta, o token do Instagram, as chaves do OpenRouter e do Apify e a senha do painel são digitados pela própria pessoa, no terminal dela: `./scripts/setup.sh --mode MODE` (prompts ocultos) e `./scripts/iw set-token`. Os segredos gerados (`ENCRYPTION_KEY` e afins) o script cria sozinho. Se a pessoa colar um segredo na conversa, avise para trocar.
5. Conduza o `docs/meta-setup.md` um passo por vez, com os valores exatos de `./scripts/setup.sh --show`. O app da Meta precisa da instalação no ar antes do passo do webhook.
6. Termine com `./scripts/iw doctor` sem nenhuma linha `XX`, e proponha o teste de ponta a ponta: comentar uma palavra-chave de outra conta e ver a DM chegar (Diagnóstico mostra o webhook recebido).
7. Não mude `ENCRYPTION_KEY` de uma instalação existente, não apague o volume `insta_watcher_data` e não publique portas da API: tudo passa pelo gateway.
