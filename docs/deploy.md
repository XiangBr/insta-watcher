# Instalar e publicar

Tudo roda em Docker, com um `docker-compose.yml` só, em qualquer máquina: um servidor, um computador em casa ou um notebook. O que muda entre os casos é como a Meta chega até ele.

```
                         ┌─ /webhook, /privacy, /terms, /data-deletion, /health ─> api
Meta ──HTTPS──> gateway ─┤
Navegador ─────> (Caddy) └─ todo o resto ─> web (painel) ──rede interna──> api ──> SQLite (volume)
```

- **api**: FastAPI e os loops (fila, Chat, insights...). Não publica porta.
- **web**: o painel (Next.js). Fala com a API pela rede interna.
- **gateway**: Caddy. É o único que publica porta. Faz HTTPS automático quando recebe um domínio, ou HTTP simples para um proxy ou túnel na frente. A API de admin (`/api/v1`) não passa por ele.
- **tunnel** / **named-tunnel** (opcionais): Cloudflare Tunnel, para quem não tem servidor nem domínio.

## Requisitos

- Docker com Compose v2 (`docker compose version`). Linux, macOS ou Windows (rode os scripts no WSL ou no Git Bash).
- Para receber eventos da Meta: um endereço público com HTTPS. Os modos abaixo resolvem isso de jeitos diferentes.
- Cerca de 1 GB de RAM livre para montar as imagens. Rodando, a API e o painel usam menos de 400 MB cada.

## Jeito rápido

```sh
git clone https://github.com/XiangBr/insta-watcher.git
cd insta-watcher
./scripts/setup.sh
```

O script pergunta o modo, o domínio (se houver), os segredos do app da Meta (ocultos), o email de contato e a senha do painel. Gera os outros segredos, escreve o `.env` (permissão 600), sobe tudo e imprime o que colar na Meta. Depois siga `docs/meta-setup.md`.

Rodar de novo é seguro: segredos que já existem ficam. Para mudar de modo, rode com `--mode`. Para rever as URLs e valores da Meta: `./scripts/setup.sh --show`.

Sem perguntas (agentes, CI): `./scripts/setup.sh --mode domain --yes`, com as respostas no ambiente (`IW_DOMAIN`, `IW_LEGAL_EMAIL`, `IW_ADMIN_PASSWORD`, `META_APP_SECRET`, `INSTAGRAM_APP_SECRET`...). A lista completa está no topo do script.

## Modos

### domain: servidor com domínio próprio

Para uma VPS ou qualquer máquina com IP público.

1. Crie um registro DNS `A` (e `AAAA`, se tiver IPv6) do domínio escolhido, por exemplo `insta.seudominio.com`, para o IP da máquina. Confira com `dig +short insta.seudominio.com`.
2. Libere as portas 80 e 443 no firewall do provedor e da máquina (`ufw allow 80,443/tcp`).
3. `./scripts/setup.sh --mode domain`.

O Caddy pede o certificado ao Let's Encrypt na primeira requisição e renova sozinho. Sem domínio, dá para usar `insta.<seu-ip-com-hífens>.sslip.io`, que já aponta para o seu IP.

### proxy: já existe um proxy reverso

Para quem já tem Nginx, Traefik, Caddy ou outro proxy nas portas 80/443 da máquina. O gateway escuta numa porta local e o seu proxy encaminha o domínio para ela, mantendo o HTTPS.

```sh
./scripts/setup.sh --mode proxy     # pergunta o domínio e a porta local (padrão 8080)
```

No seu proxy, mande o domínio inteiro para `http://127.0.0.1:8080`, mantendo o cabeçalho `Host`. Exemplos:

```nginx
# Nginx
server {
    server_name insta.seudominio.com;
    # (seu bloco listen 443 ssl e certificados de sempre)
    client_max_body_size 1m;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```caddyfile
# Caddy
insta.seudominio.com {
	reverse_proxy 127.0.0.1:8080
}
```

**Proxy dentro de outro container Docker** (ele não enxerga `127.0.0.1` do host): ponha o gateway na rede do proxy. No `.env`:

```sh
COMPOSE_FILE=docker-compose.yml:deploy/compose.proxy-network.yml   # no Windows, separe com ;
PROXY_NETWORK=nome_da_rede_do_proxy                                 # docker network ls
```

E aponte o proxy para `http://insta-watcher-gateway-1:80`. Rode `docker compose up -d` para aplicar.

### tunnel: no seu computador, sem domínio

Para testar sem servidor. Um container da Cloudflare abre um túnel e dá um endereço `https://algo.trycloudflare.com`.

```sh
./scripts/setup.sh --mode tunnel
```

O script espera o túnel subir, lê o endereço e já mostra as URLs para a Meta. Limitações: o endereço **muda a cada reinício** do container do túnel (aí é preciso atualizar o webhook e as URLs legais no app da Meta), e o computador precisa ficar ligado. Para uso diário, prefira um dos outros modos.

### cloudflare: túnel fixo

Igual ao anterior, mas com um hostname fixo no seu domínio, sem abrir portas. Precisa de uma conta na Cloudflare com o domínio lá.

1. Cloudflare › Zero Trust › Networks › Tunnels › Create a tunnel (Cloudflared). Copie o token.
2. Em **Public Hostname**, aponte `insta.seudominio.com` para o serviço `http://gateway:80`.
3. `./scripts/setup.sh --mode cloudflare` (pede o hostname e o token, oculto).

### local: só o painel

`./scripts/setup.sh --mode local` sobe tudo em `http://localhost:8080`, sem endereço público. Serve para conhecer o painel. A Meta não consegue mandar nada para cá.

### vercel: painel na Vercel

A API e o gateway rodam no seu servidor (como no modo `domain`), e o painel vai para a Vercel.

1. `./scripts/setup.sh --mode vercel`. O gateway passa a encaminhar `/api/v1` para a API, que continua exigindo o `API_ADMIN_TOKEN`.
2. Na Vercel, importe o repositório com **Root Directory** = `frontend`.
3. Em Settings › Environment Variables, todas como variáveis de servidor (nenhuma com `NEXT_PUBLIC_`):
   - `API_BASE_URL` = `https://insta.seudominio.com`
   - `API_ADMIN_TOKEN` e `SESSION_SECRET`: os mesmos do `.env`
   - `ADMIN_PASSWORD_HASH`: o do `.env`, **sem** as aspas simples
4. Deploy. O painel fica no endereço da Vercel.

## Depois de instalado

```sh
./scripts/iw doctor       # o que falta, com o próximo passo de cada item
docker compose ps         # estado dos containers
docker compose logs -f api
```

`./scripts/iw` roda a CLI dentro do container da API. Os comandos mais usados:

```sh
./scripts/iw set-token     # guarda o token do Instagram (pedido sem eco, nunca por argumento)
./scripts/iw subscribe     # inscreve a conta nos webhooks
./scripts/iw status        # fila, uso do limite por hora, token, pausa
./scripts/iw pause         # interruptor geral: para todo envio (resume para voltar)
./scripts/iw deliveries --limit 20
./scripts/iw --help
```

Tudo isso também existe no painel.

## Atualizar

```sh
git pull
docker compose up -d --build
```

As migrações do banco rodam no boot da API. O `.env` e o volume do banco não são tocados.

## Backup

O banco é um arquivo SQLite no volume `insta_watcher_data`. Ele guarda o token (criptografado) e dados pessoais (quem comentou, conversas).

```sh
./scripts/backup.sh       # cópia consistente em backups/, com a API no ar
```

Guarde o backup **junto com o `.env`**: sem a mesma `ENCRYPTION_KEY`, o token dentro dele não abre (aí basta colar o token de novo). Para restaurar: `docker compose down`, copie o arquivo para o volume (`docker run --rm -v insta_watcher_data:/data -v "$PWD/backups:/b" alpine cp /b/ARQUIVO.db /data/app.db`), `docker compose up -d`.

## Segurança do servidor

Num servidor exposto à internet, o básico:

```sh
# usuário sem root com sua chave SSH, depois:
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/; s/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh
apt install -y ufw fail2ban unattended-upgrades
ufw default deny incoming && ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
```

- Estar no grupo `docker` equivale a ter root. Proteja quem tem acesso.
- Porta publicada por container passa por cima do `ufw`. No insta-watcher só o gateway publica, e nos modos `proxy`, `tunnel` e `local` só em `127.0.0.1`.
- Os containers rodam com sistema de arquivos só leitura, sem capabilities e com limite de memória.

Conferir de fora da máquina:

```sh
curl -s https://insta.seudominio.com/health                                  # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' https://insta.seudominio.com/api/v1/status  # 404
curl -s -o /dev/null -w '%{http_code}\n' -d '{}' https://insta.seudominio.com/webhook # 401
```

## Desenvolvimento sem Docker

Para mexer no código (`AGENTS.md` tem todos os comandos):

```sh
cp .env.example backend/.env     # preencha, com DATABASE_URL=sqlite+aiosqlite:///./dev.db
cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --reload --workers 1
# outro terminal
cd frontend && pnpm install && API_BASE_URL=http://localhost:8000 pnpm dev
```

Para a Meta alcançar a máquina de desenvolvimento, use um túnel (`cloudflared tunnel --url http://localhost:8000`) e aponte o webhook para ele.

## Problemas na instalação

| Sintoma | O que fazer |
|---|---|
| `invalid configuration, refusing to start: META_APP_SECRET...` | Falta um segredo ou ele é fraco/placeholder. `./scripts/setup.sh` de novo, ou edite o `.env`. `docker compose logs api` mostra qual |
| Certificado não sai no modo `domain` | DNS ainda não aponta para a máquina, ou as portas 80/443 estão fechadas ou ocupadas. `docker compose logs gateway` |
| Porta 80 já em uso | Já existe um proxy: use o modo `proxy` |
| Painel abre mas o login falha sempre | `ADMIN_PASSWORD_HASH` sem as aspas simples no `.env` (o `$` some). Rode `./scripts/setup.sh` de novo depois de apagar a linha, para gerar outro |
| 502 no painel | O container `web` ainda está subindo ou caiu: `docker compose logs web`. No modo `vercel` é esperado no endereço do servidor |
| Endereço do túnel mudou | `./scripts/setup.sh --mode tunnel` e atualize as URLs no app da Meta |
