# insta-watcher

Automação de Instagram self-hosted e de código aberto. Alguém comenta uma palavra-chave no seu post ou reel e recebe o seu link por DM, pela API oficial da Meta. Junto vêm um Chat no direct com fluxos de botões e respostas com I.A., insights do perfil e análise de concorrentes. Tudo num painel seu, sem mensalidade por contato.

Uma instalação atende uma conta profissional do Instagram (Criador ou Comercial). Cada pessoa instala a sua, com o próprio app na Meta.

## O que faz

- **Comentário → DM**: palavras-chave por post ou em qualquer post, sem diferenciar maiúsculas e acentos, com resposta pública opcional. Nunca envia duas vezes para o mesmo comentário e respeita o limite de 750 DMs por hora da Meta.
- **Respostas a stories e mensagens no direct** com palavra-chave.
- **Seguir para liberar**: o link só vai para quem segue a conta.
- **Chat**: caixa de entrada, fluxos com botões de resposta rápida, mensagens de acompanhamento dentro da janela de 24h, etiquetas, e o modo humano (a automação sai de cena quando você responde).
- **Respostas com I.A.** (opcional): responde o que nenhum fluxo pegou, com a sua base de conhecimento, e passa para você quando não sabe.
- **Insights**: alcance, formatos, horários, público e o que fazer em seguida.
- **Concorrência** (opcional): coleta perfis públicos, classifica os posts em massa e escreve um relatório com ideias de conteúdo.

## Instalar

Precisa de Docker. Funciona num servidor com domínio, atrás de um proxy que você já tenha, ou no seu próprio computador com um túnel.

```sh
git clone https://github.com/XiangBr/insta-watcher.git
cd insta-watcher
./scripts/setup.sh
```

O script pergunta o que precisa, gera os segredos, sobe tudo e mostra o que colar no app da Meta. Depois:

1. **App da Meta**: `docs/meta-setup.md`, passo a passo, com tokens, webhook e publicação.
2. **Conferir**: `./scripts/iw doctor` diz o que falta.

Detalhes de cada modo de publicação, atualização e backup: `docs/deploy.md`.

### Com um agente de código

O repositório foi feito para um agente instalar com você. No Claude Code, abra a pasta e rode `/setup`. Em outro agente, peça: *"siga a seção 'Setup assistido por agente' do AGENTS.md"*. O agente confere a máquina, escolhe o modo com você e conduz o app da Meta. Os segredos você digita no seu terminal, nunca no chat.

## Requisitos da Meta

- Conta profissional no Instagram e uma conta no Facebook (para o painel de desenvolvedores).
- Não precisa de CNPJ, App Review nem verificação de empresa para usar na sua própria conta.
- Atender contas de terceiros com o mesmo app exige App Review e verificação de empresa. Este projeto não foi feito para isso.

## O que não faz

- Não é um serviço hospedado nem multi-conta.
- Não pede senha do Instagram nem automatiza navegador. A coleta de concorrentes (opcional) lê só perfis públicos, por um serviço externo (Apify).
- Não envia sequências nem broadcast: a Meta só permite responder a quem escreveu, em até 24 horas.

## Como funciona

```
                         ┌─ /webhook e páginas legais ─> api (FastAPI) ──> SQLite
Meta ──HTTPS──> gateway ─┤                                 │
Navegador ─────> (Caddy) └─ painel ─> web (Next.js) ───────┘──> API do Instagram
```

Um processo Python com fila em SQLite, sem Redis nem Postgres. Arquitetura em `docs/architecture.md`.

## Documentação

| | |
|---|---|
| `docs/deploy.md` | Instalar, modos de publicação, atualizar, backup |
| `docs/meta-setup.md` | App da Meta, passo a passo |
| `docs/dashboard.md` | O painel, página por página |
| `docs/architecture.md` | Como funciona por dentro, decisões e limites |
| `docs/data-model.md` | Tabelas e retenção |
| `docs/api.md` | Endpoints |
| `docs/security.md` | Ameaças, controles e cuidados com dados pessoais |
| `AGENTS.md` | Comandos e regras para quem (ou o agente que) mexe no código |

## Aviso

Projeto independente, sem vínculo com a Meta ou o Instagram. As políticas, limites e termos da Meta valem para quem usa, e uma automação mal configurada pode levar a restrições na conta. Quem instala é responsável pelos dados pessoais que coleta (comentários, nomes de usuário, conversas). `docs/security.md` lista os cuidados, e as páginas de privacidade servidas pela instalação são um modelo para você adaptar.

## Licença

MIT. Veja `LICENSE`.
