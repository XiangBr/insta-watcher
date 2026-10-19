# Segurança

O sistema guarda um token que manda mensagem em nome da sua conta, recebe eventos de um endpoint público e mostra dados de terceiros (comentários, usernames) em um painel. Os riscos grandes são vazamento do token, endpoint de admin exposto, envio duplicado ou em massa por bug, e tratamento descuidado de dados pessoais.

## Prioridades

P0 antes do primeiro uso real. P1 antes de publicar o repositório.

| # | Foco | Prioridade |
|---|---|---|
| 1 | Token do Instagram e segredos fora de log, de repositório e de query string | P0 |
| 2 | Autenticidade do webhook (assinatura sobre corpo bruto, tempo constante) | P0 |
| 3 | Superfície de admin: token forte, só o BFF chama, nada de OpenAPI público | P0 |
| 4 | Idempotência e envio ambíguo (não duplicar DM) | P0 |
| 5 | Auth do dashboard: hash, cookie seguro, limite de tentativas, CSRF | P0 |
| 6 | Containers e servidor endurecidos, backup protegido | P0 |
| 7 | Dados pessoais: retenção, exclusão, anonimização | P1 |
| 8 | Validação de entrada, links rastreados, CSV, XSS | P0 |
| 9 | Cadeia de suprimentos e CI (segredos, dependências) | P1 |
| 10 | Defaults seguros para quem hospedar o seu | P1 |

## 1. Token e segredos

- Token do Instagram criptografado em repouso com AES-256-GCM. Chave em `ENCRYPTION_KEY`, 32 bytes. A API nunca devolve o token.
- Chamadas à Meta enviam o token em `Authorization: Bearer`. Se alguma chamada precisar de token na query (o refresh costuma usar), a mensagem de erro e o log removem a query string antes de gravar. O OpenReply faz isso e vale copiar.
- Nenhum segredo com valor padrão. A aplicação recusa subir se `ENCRYPTION_KEY`, `API_ADMIN_TOKEN`, `WEBHOOK_VERIFY_TOKEN`, `CLICK_HASH_SALT` ou os segredos do app estiverem ausentes, curtos ou iguais ao exemplo.
- `.env` no `.gitignore`. Varredura de segredo (gitleaks) em pre-commit e no CI.
- Segredo que apareceu em lugar que pode ter log (chat, issue, screenshot, terminal compartilhado) é rotacionado: segredo do app no console da Meta, token regenerado, `API_ADMIN_TOKEN` e `SESSION_SECRET` trocados.
- Perder a `ENCRYPTION_KEY` custa colar o token de novo. Guarde a chave no gerenciador de senhas.

### Manuseio no dia a dia

- Segredo nunca é colado em chat (inclusive com assistentes de I.A.), issue, print ou terminal compartilhado. Se acontecer, rotacione.
- Segredos gerados pela própria instância (`ENCRYPTION_KEY`, `API_ADMIN_TOKEN`, `WEBHOOK_VERIFY_TOKEN`, `CLICK_HASH_SALT`, `SESSION_SECRET`) são criados pelo `scripts/setup.sh` (`openssl rand -hex`) na própria máquina e ficam no `.env`. Guarde uma cópia no gerenciador de senhas.
- O `.env` fica na raiz do repositório, com `chmod 600` (o script já aplica), e está no `.gitignore`. Num servidor compartilhado, só o usuário que roda o Docker deve ler essa pasta.
- O token do Instagram entra pelo painel ou pela CLI com leitura sem eco (`getpass`). Nunca como argumento de linha de comando, porque fica no histórico do shell e na lista de processos.
- O hook `.githooks/pre-commit` roda gitleaks e falha fechado: sem gitleaks instalado, não há commit. Ative com `git config core.hooksPath .githooks`.

## 2. Webhook

- Valide `X-Hub-Signature-256` com HMAC-SHA256 sobre o corpo bruto, antes de parsear JSON. Use `hmac.compare_digest`. Aceite os dois segredos do app.
- Compare o verify token em tempo constante. O OpenReply usa `===` nessa comparação, e aqui não.
- Limite o tamanho do corpo e responda 200 só depois de gravar.
- Assinatura não impede replay. A proteção contra replay é a idempotência (`UNIQUE(campaign_id, source_id)` e `postback_receipt.mid`).
- Em falha de assinatura, registre só tamanho e presença do cabeçalho. Não grave pedaço do corpo, porque pode ter comentário de terceiros.
- Rate limit no `/webhook` deve ser folgado. Bloquear a Meta por engano derruba a automação.

## 3. Superfície de admin

- `API_ADMIN_TOKEN` com pelo menos 32 bytes aleatórios, comparado em tempo constante.
- Quem chama é o servidor do Next.js. O token nunca vai ao navegador nem a variável `NEXT_PUBLIC`.
- Com o painel em container, `/api/v1` nem chega ao gateway: só o painel, na rede interna, fala com ela. Com o painel na Vercel (`ADMIN_API_HANDLER`), ela fica pública atrás do token. A Vercel não tem IP de saída fixo no plano comum, então as compensações são token forte e, se quiser mais, `/api/v1` atrás de Cloudflare Access ou de uma VPN mesh.
- `/docs`, `/redoc` e `/openapi.json` desligados em produção. O frontend gera os tipos com `python -m app.export_openapi`, sem servidor.
- Erros de validação da API devolvem campo, mensagem e tipo, nunca o valor recebido, porque ele pode ser um token.
- Ainda falta o rate limit por IP e por token nas rotas `/api/v1`. Hoje a proteção é o token de 32 bytes.
- O painel só aceita `API_BASE_URL` com `https` em produção, para o token de admin nunca trafegar em claro. A exceção é um nome de serviço sem ponto (`http://api:8000`), que só existe na rede interna do Docker. Redirecionamentos da API não são seguidos, e a rota do BFF só repassa `/api/v1` (sem `login-throttle`).
- A CSP do painel (`proxy.ts`) usa nonce por request, sem `unsafe-inline` nem `unsafe-eval` em produção, e libera imagens apenas de `self` e do CDN do Instagram. O formulário de login usa `method="post"` mesmo sem JavaScript, para a senha nunca ir parar na URL se alguém enviar antes da hidratação.
- `/health` público devolve só `ok`. Detalhes ficam em `/api/v1/status`.

## 4. Envio duplicado e ambíguo

Segurança de conta inclui não mandar DM em dobro, porque isso gera denúncia de spam e pode restringir a conta.

- Idempotência por `UNIQUE(campaign_id, source_id)` antes de criar o job.
- Erro ambíguo (timeout, 5xx, resposta sem `message_id`, código 1) vira `UNCONFIRMED` e não é reenviado sozinho.
- Crash entre chamar a Meta e gravar o resultado resolve para `UNCONFIRMED` no boot, via `send_started_at`.
- Respeitar 750 por hora, a janela de 24h e a regra de uma private reply por comentário. Um bug de laço não pode furar isso, então o limite é checado na camada de envio, não só no agendamento.
- Filtro de eco e de comentário próprio para evitar laço de resposta a si mesmo.
- Interruptor geral de pausa da conta no painel, para parar tudo rápido.

## 5. Dashboard

- Senha de admin com hash argon2. Limite de tentativas com atraso crescente, contado no backend (`/api/v1/login-throttle/*`, tabela `login_attempt`), porque as instâncias da Vercel não compartilham memória. O painel envia um HMAC do IP feito com `SESSION_SECRET`, nunca o IP. Cada cliente tem 3 erros livres. Depois disso, a espera dobra a partir de 1 s, até 15 min. Um contador global faz o mesmo a partir de 20 erros somados, para conter quem distribui tentativas entre muitos IPs. O preço é que um ataque assim também atrasa o login do dono. A CLI continua funcionando nesse caso.
- Cookie de sessão `httpOnly`, `Secure`, `SameSite=Lax`, assinado, 7 dias.
- CSRF: checagem de `Origin` em toda mutação, além do `SameSite`.
- Cabeçalhos: CSP restritiva, HSTS, `frame-ancestors 'none'`, `Referrer-Policy`, `X-Content-Type-Options`.
- Variáveis sensíveis na Vercel sem prefixo `NEXT_PUBLIC`. Deploys de preview não recebem as variáveis de produção, ou ficam atrás de proteção de deploy.

### Follow gate

- O convite nunca leva o link e nunca cai para texto: se a Meta recusar o botão, a entrega falha, em vez de mandar o link sem passar pelo gate.
- Só vale um payload exatamente no formato `bw:gate:<número>`. A Meta assina o webhook, e o payload só existe em botões que nós enviamos.
- Cada toque é registrado uma vez (`postback_receipt`), só conta o de quem recebeu o convite, e há um teto de 5 toques por entrega, para ninguém usar o gate como gerador de chamadas à Meta.
- Quando a Meta não consegue dizer se a pessoa segue, o link é liberado. É uma escolha de produto, e o custo é um não seguidor ocasional receber o link sem seguir.
- O ID de quem tocou (`gate_user_id`) é dado pessoal e é anonimizado junto com o texto do comentário.

## 6. Containers e servidor

- Containers como usuário não root, sistema de arquivos somente leitura exceto `/data` e `/tmp`, sem capabilities extras, limites de memória e PIDs.
- A API não publica porta: só o gateway. Nos modos proxy, túnel e local, o gateway escuta só em `127.0.0.1`. Num servidor exposto: firewall liberando 80, 443 e SSH, SSH só por chave, fail2ban, atualizações automáticas de segurança (`docs/deploy.md`).
- Imagem com versão fixa e varredura (Trivy). Dependências travadas em `uv.lock` e `pnpm-lock.yaml`.
- Backup do SQLite inclui token criptografado e dados pessoais. Criptografe o backup e guarde a chave separada. Teste a restauração.
- `--proxy-headers` confia no `X-Forwarded-For` de quem está na rede interna (o gateway e o painel). O Caddy sobrescreve o cabeçalho com o IP real de quem chegou, então ele não é forjável de fora.

## 7. Dados pessoais (LGPD)

Comentário, username e ID de pessoa são dados pessoais. Quem hospeda é o controlador. Isto é orientação técnica, não parecer jurídico.

- Retenção configurável com padrões em `docs/data-model.md`, e anonimização depois do prazo.
- Página de exclusão de dados com instrução clara e rota administrativa que apaga ou anonimiza por `actor_id`.
- Política de privacidade e termos prontos como modelo para o projeto, que cada instância adapta.
- Minimizar: não guardar o payload bruto além de 7 dias, não guardar IP em claro. IP com hash e sal continua sendo dado pseudonimizado, não anônimo.
- Backups e exportações seguem a mesma retenção.

## 8. Entrada e saída

- SQL só pelo SQLAlchemy com parâmetros.
- Texto de comentário e username são não confiáveis em qualquer tela. Sem `dangerouslySetInnerHTML`.
- `{username}` é substituição simples de string, nunca avaliação de template.
- URLs de destino de link rastreado: só `https`, host válido, sem credenciais na URL, sem `javascript:` ou `data:`. O backend não faz requisição a essas URLs, então não há SSRF por aí.
- O cliente HTTP da Meta usa uma lista fixa de hosts (`graph.instagram.com`, e `graph.facebook.com` se necessário).
- Apify (`api.apify.com`) e OpenRouter (`openrouter.ai`) também têm host fixo. As chaves vão só no header `Authorization`, nunca na URL, e as mensagens de erro guardadas trazem status e o texto do serviço, nunca a chave. `APIFY_TOKEN` e `OPENROUTER_API_KEY` ausentes ou vazias desligam o módulo; chave curta demais impede a API de subir.
- Respostas da I.A. no direct: a mensagem da pessoa é texto não confiável e pode tentar instruir o modelo. O modelo não tem ferramenta nem acesso a nada além da persona, da base de conhecimento, da lista de fluxos e das últimas 12 mensagens, então o pior caso é uma resposta errada ou o vazamento do que já está na base (não ponha ali nada que não diria em público). A resposta sai como texto simples, cortada em 600 caracteres. A I.A. vem desligada, tem limite por pessoa e por hora, passa para o dono quando não sabe ou quando a resposta não pôde ser lida, e para de responder a pessoa quando o dono responde. O texto recente da conversa vai para o OpenRouter e para o provedor do modelo; a política de privacidade diz isso.
- Payloads de botões de resposta rápida chegam do cliente: só `bwf:{fluxo}:{passo}:{opção}` com fluxo ativo, passo existente e opção no intervalo é aceito. Qualquer outro vira texto comum.
- O eco de uma mensagem que não foi nossa pausa a automação para a pessoa (padrão ligado). Um eco nosso confundido com manual só pausa o bot, nunca faz enviar nada.
- Legendas e bios de concorrentes são texto de terceiros: entram no prompt do relatório como dados JSON, e a resposta do modelo é validada contra um schema e mostrada como texto, sem HTML. Uma legenda pode tentar instruir o modelo (prompt injection); o pior efeito é um relatório ruim, porque o modelo não tem ferramenta nem acesso a nada além do que recebeu.
- Redirecionamento do `/r/{slug}` vai só para o destino salvo, sem parâmetro de URL controlado por quem clica.
- CSV neutraliza células iniciadas em `=`, `+`, `-`, `@`.
- Limites de tamanho em todo campo de campanha (mensagem, palavras, rótulos).

## 9. Cadeia de suprimentos e CI

- CI com lint, tipos, testes, `pip-audit`, `pnpm audit`, gitleaks.
- Dependabot ou Renovate. GitHub Actions fixadas por SHA.
- CodeQL no repositório público.
- Revisão manual de qualquer PR que toque auth, criptografia, assinatura ou cliente da Meta.

## 10. Defaults seguros para quem hospedar

- Nenhuma credencial padrão, nenhum segredo de exemplo funcional.
- Aplicação não inicia com configuração insegura e explica o motivo no log.
- `SECURITY.md` com canal privado de reporte.
- README deixa claro que cada instância usa o próprio app na Meta, e que as regras da Meta e do Instagram valem para quem usa.

## Checklist antes de publicar o repositório

- [ ] Histórico do git sem segredo, sem payload real de webhook, sem screenshot com token
- [ ] `.env.example` só com placeholders
- [ ] Todos os testes obrigatórios de `AGENTS.md` passando
- [ ] Gitleaks e auditoria de dependências limpos
- [ ] Container rodando como não root, com volume único
- [ ] `/docs` e `/openapi.json` desligados no deploy de exemplo
- [ ] Páginas de privacidade, termos e exclusão revisadas
- [ ] `SECURITY.md` com contato real
- [ ] Licença escolhida
- [ ] Revisão de segurança independente do fluxo de assinatura, auth e criptografia
