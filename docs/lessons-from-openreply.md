# O que aprendemos com o OpenReply

Referência: github.com/diwenne/openreply (MIT), um fork de instagram-comment-to-dm. Li o README, a documentação, o schema Prisma e trechos do código (verificação de webhook, cliente da Meta, rate limiter, matcher, worker, reconciler, redirect de links). Não li tudo nem fiz auditoria. Este projeto reimplementa em Python com escopo menor e não copia código. Se algum trecho for copiado no futuro, mantenha a nota de copyright e a licença MIT.

Tamanho de referência: cerca de 19 mil linhas de TypeScript entre app, lib, worker, componentes e testes, e um arquivo de worker com mais de 1.600 linhas. É o tamanho que um produto multi-tenant tem, e o motivo de a nossa versão ser menor.

## O que adotamos

| Ideia | Origem | Onde entra |
|---|---|---|
| Aceitar assinatura de qualquer um dos dois segredos do app | `lib/meta/webhook.ts` | `docs/architecture.md` |
| Ignorar comentário do próprio dono, porque a Meta rejeita DM para si mesmo | parse de comentários | dispatcher |
| Ignorar eco, mensagem apagada e sem texto, para a resposta automática não disparar a si mesma | parse de mensagens | story reply e DM |
| Comentário em anúncio chega com ID de mídia do anúncio e `original_media_id`, campanha casa com os dois | parse de comentários | dispatcher |
| Só rejeição explícita prova que a DM não saiu. Código 1, rede e resposta inválida são ambíguos e não reenviam | `delivery-errors.ts` | classificação de erros |
| Backoff de 5, 15 e 45 min | `dm-worker.ts` | worker |
| Rejeição do template de botão cai para link no texto | `dm-worker.ts` | worker |
| Follow gate com botão postback, checagem de `is_user_follow_business`, rechecagem a 20 s e 40 s, liberar se o status vier `null` | worker e `meta/client.ts` | fase 3 |
| Recibo durável de postback para distinguir replay de toque novo | schema e docs | `postback_receipt` |
| Reconciliação estreita: só campanha ativa, só comentários recentes, só se casa e o dono ainda não respondeu, com teto por varredura | `comment-reconciler.ts` | reconciler |
| Remover a query string das mensagens de erro, porque ela carrega o token | `meta/client.ts` | segurança |
| Casamento de palavra-chave com propriedades Unicode e remoção de diacríticos dos dois lados | `keyword-matcher.ts` | matcher |
| Limite de 750 por hora tratado como teto sem folga, com aviso para baixar se a Meta limitar antes | `rate-limiter.ts` | variável `PRIVATE_REPLY_HOURLY_LIMIT` |
| Token de longa duração com 60 dias como padrão e refresh por job diário | `meta/client.ts` e cron | manutenção |
| Endpoint de saúde que inclui o batimento do worker | `/api/health` | `/api/v1/status` |
| Página de diagnóstico e tabelas de evento bruto e de eventos operacionais | schema e app | dashboard |

## O que fazemos diferente

| OpenReply | Aqui | Motivo |
|---|---|---|
| Next.js, Postgres, Redis, BullMQ, Auth.js, Resend | FastAPI e SQLite em um processo | Conta única, custo e manutenção menores |
| Contador fixo por hora no Redis, e depois de 3 reagendamentos de 30 min o envio é descartado | Janela deslizante no SQLite, reagenda até abrir vaga | Não perde envio e sobrevive a reinício |
| Processa o webhook dentro do request e responde 500 se falhar | Grava o evento e responde 200 | Evita perda e reentrega por erro nosso |
| Comparação do verify token com `===` | Tempo constante | Boa prática |
| Salt do hash de IP com fallback fixo no código | Sem fallback, variável obrigatória | Segredo padrão público é segredo nenhum |
| Segredo do cron cai para outro segredo se estiver vazio | Sem fallback entre segredos | Separação de responsabilidades |
| Em falha de assinatura grava 200 caracteres do corpo | Grava só tamanho e presença do cabeçalho | Corpo tem texto de terceiros |
| Login por magic link com e-mail | Senha de admin única | Um usuário só |
| Múltiplas contas, workspaces, convites, relatórios públicos | Fora | Não são objetivos |

As linhas sobre verify token, salt do hash de IP, segredo do cron e corpo gravado em falha de assinatura vêm de trechos que li diretamente. Não é auditoria do resto do código, então vale conferir de novo antes de citar publicamente.

## O que não portamos

Workspaces e papéis, convites, cobrança e limite por plano (`SKIPPED_PLAN_LIMIT`, `dmsSentThisPeriod`), relatórios compartilháveis, páginas de marketing e SEO, internacionalização, o provedor Zernio, o gatilho "próximo reel" por cron, e a caixa de entrada de DMs no painel.

## Funcionalidades do OpenReply que podem entrar depois

Seletor de posts com miniaturas, modelos de campanha, duplicar campanha, importar e exportar campanhas em CSV, resposta pública em rodízio, dois botões de link rastreados, histórico diário de seguidores, gatilho por DM e story reply, atraso configurável no seguimento depois do link.
