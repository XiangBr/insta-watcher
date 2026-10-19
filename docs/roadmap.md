# Roadmap

Uma fase por vez. Cada fase termina com critérios de aceite verificáveis.

## Fase 0: Meta e esqueleto

Objetivo: saber se o projeto cabe em Standard Access antes de construir o resto.

- App na Meta configurado conforme `docs/meta-setup.md`.
- FastAPI mínimo com `GET /webhook`, `POST /webhook` (assinatura), tabela `webhook_event`, Dockerfile, deploy com HTTPS.
- Teste inicial na Meta executado (comentário de outra conta chega e a private reply é entregue).

Aceite: comentário de uma segunda conta, sem função no app, chega como `webhook_event`, e uma private reply manual é entregue. Se não for, parar e decidir o caminho (ver `docs/meta-setup.md`).

**Concluída em 2026-10-02.** Os dois critérios foram atendidos com o app em Live (ver `docs/meta-setup.md`).

## Fase 1: Núcleo de envio

**Implementada em 2026-10-02.** Falta validar em produção os critérios de aceite abaixo, com campanha real.

Ordem de trabalho, um item por vez:

0. Manutenção de retenção (payload em 7 dias, texto e username anonimizados em 90), antes de qualquer uso real. A política de privacidade publicada já promete esses prazos.
1. Config com falha em segredo ausente, banco e migrações, PRAGMAs do SQLite (feito na Fase 0).
2. Validação de assinatura e do verify token, com testes.
3. Matcher com normalização e testes (acentos, palavra inteira, emoji).
4. Dispatcher: filtros de comentário próprio, campanha por post, `delivery` com `UNIQUE`, `job`.
5. Cliente da Meta (httpx), classes de erro, classificação confirmada contra ambígua.
6. Worker, fila com lease, backoff 5, 15 e 45 min, recuperação de crash.
7. Rate limit por janela deslizante com `send_event`.
8. Resposta pública independente da DM.
9. Reconciler com as regras estreitas.
10. Token: criptografia, validação em `/me`, refresh diário, avisos.
11. `/health` e `/api/v1/status`.

Aceite: com campanha criada por script ou SQL, comentar a palavra-chave gera uma DM e, se configurado, uma resposta pública. Webhook duplicado e polling sobre o mesmo comentário não geram segunda DM. 760 comentários simulados em uma hora enviam 750 e agendam o restante. Reiniciar o container no meio não duplica nem perde envio. Todos os testes obrigatórios de `AGENTS.md` passam.

## Fase 2: Dashboard

**Em andamento.** A API de admin ficou pronta em 2026-10-02. Faltam o esqueleto do Next.js, as páginas e o deploy. Exportação CSV e cliques ficaram para a Fase 3, junto com os links rastreados.

Ordem de trabalho:

1. API de admin no backend, com testes (feito).
2. Esqueleto do Next.js: login, sessão, proxy, BFF e `pnpm gen:api` (feito).
3. Páginas: visão geral, campanhas e construtor, entregas, configurações, diagnóstico (feito).
4. Deploy (feito: painel em container pelo gateway, ou na Vercel; `docs/deploy.md`).

- Next.js com login por senha, proxy e BFF.
- Visão geral, campanhas (lista e construtor), entregas, configurações, diagnóstico (`docs/dashboard.md`).
- API de admin conforme `docs/api.md`, tipos gerados do OpenAPI.
- Deploy do painel com variáveis só de servidor (container ou Vercel).

Aceite: criar, pausar e duplicar campanha pelo painel, ver a entrega aparecer, colar token novo e ver o estado atualizado, sem abrir terminal.

## Fase 3: Recursos de campanha

Cada um com sua flag de teste e seus testes:

1. Story reply (feito, falta confirmar com um story real). Gatilho por DM comum (feito em 2026-10-19, `trigger_messages`).
2. Links rastreados e CTR.
3. Follow gate (feito: convite com botão, checagem de `is_user_follow_business`, recheck e liberação se vier `null`). Falta confirmar com um comentário real, porque o postback numa private reply nunca foi testado.
4. Seguimento de mensagem depois de um atraso, dentro da janela de 24h (feito no Chat, por passo de fluxo).
5. Modelos de campanha, importação e exportação CSV.

## Insights (feito em 2026-10-12)

Página `/insights` com sincronizador próprio (`docs/architecture.md`, seção Insights). Falta confirmar com a conta real: gerar o token com `instagram_business_manage_insights`, conferir quais métricas a Meta aceita (as recusadas ficam em `unsupported_metrics`) e se o fuso de `online_followers` bate.

## Concorrência (feito em 2026-10-19)

Página `/competitors`: coleta de perfis públicos pelo Apify, classificação com o JEV pelo OpenRouter e relatório por LLM (`docs/architecture.md`, seção Concorrência). Falta confirmar com chaves reais: o formato da resposta do actor de perfis para contas grandes, o slug de `COMPETITORS_REPORT_MODEL` no OpenRouter e o custo real por post do JEV.

## Chat (feito em 2026-10-19)

Abas Conversas, Fluxos e I.A. em `/chat` (`docs/architecture.md`, seção Chat). Falta confirmar com a conta real:

- se as respostas rápidas (`quick_replies`) aparecem e o toque volta em `message.quick_reply.payload` na API com Instagram Login;
- se o eco de uma mensagem enviada pela API chega com o mesmo `mid` que o envio devolveu (é o que separa resposta manual de automática);
- o slug de `CHAT_AI_MODEL` no OpenRouter e o custo por resposta.

## Fase 4: Publicar

- Checklist de `docs/security.md` completo.
- README com instalação em menos de uma hora, `docker-compose.yml` de exemplo, capturas de tela.
- CI (testes, lint, auditoria, gitleaks, CodeQL), Dependabot.
- `SECURITY.md` com contato, `CONTRIBUTING.md`, licença.
- Páginas de privacidade, termos e exclusão como modelo.

## Depois

- Camada de I.A. (classificação de intenção por modelo, resposta com aprovação) atrás das interfaces descritas em `docs/architecture.md`.
- Notificações (webhook genérico) para token perto de vencer, fila parada e `UNCONFIRMED`.
- Várias contas, o que obriga a reavaliar SQLite, fila e o modelo de usuário.
- Internacionalização do painel.
