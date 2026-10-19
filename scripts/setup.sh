#!/usr/bin/env bash
# insta-watcher setup: writes .env, generates the secrets, starts everything with Docker and
# prints exactly what to paste in the Meta app.
#
#   ./scripts/setup.sh                 asks a few questions
#   ./scripts/setup.sh --mode tunnel   picks the way it is published (see --help)
#   ./scripts/setup.sh --show          prints the URLs and next steps again, changes nothing
#
# For coding agents and CI: --yes never prompts and reads the answers from the environment
# (IW_MODE, IW_DOMAIN, IW_LEGAL_EMAIL, IW_ADMIN_PASSWORD, META_APP_SECRET,
# INSTAGRAM_APP_SECRET, APIFY_TOKEN, OPENROUTER_API_KEY, CLOUDFLARE_TUNNEL_TOKEN,
# IW_PROXY_PORT). Secrets are never printed, except the webhook verify token, which has to
# be pasted in the Meta app.
#
# Running it again is safe: secrets already in .env are kept (a new ENCRYPTION_KEY would make
# the stored Instagram token unreadable), and only what is missing is asked.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env

MODE="${IW_MODE:-}"
ASSUME_YES=0
START=1
SHOW_ONLY=0

usage() {
  cat <<'EOF'
Usage: ./scripts/setup.sh [--mode MODE] [--yes] [--no-start] [--show]

MODE is how the instance is reached from the internet:
  domain    a server with a domain pointing to it: automatic HTTPS on ports 80 and 443
  proxy     a server that already runs a reverse proxy (Nginx, Traefik, Caddy): it forwards
            your domain to a local port, and keeps doing the HTTPS
  tunnel    your own computer, no domain: a temporary Cloudflare address (changes on restart)
  cloudflare  a fixed Cloudflare Tunnel hostname (needs a Cloudflare account and token)
  local     only the dashboard on localhost, to look around (Meta cannot reach it)
  vercel    API here (with a domain), dashboard on Vercel

  --yes       never prompt; answers come from the environment (see the header of this file)
  --no-start  write .env only
  --show      print the URLs and next steps for the current .env
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --mode=*) MODE="${1#*=}"; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --no-start) START=0; shift ;;
    --show) SHOW_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---- .env reading and writing (bash 3 compatible: no associative arrays) ------------------

KNOWN_KEYS=""
env_get() { local name="ENV_$1"; printf '%s' "${!name-}"; }
env_set() {
  case " $KNOWN_KEYS " in *" $1 "*) ;; *) KNOWN_KEYS="$KNOWN_KEYS $1" ;; esac
  printf -v "ENV_$1" '%s' "$2"
}

load_env() {
  [ -f "$ENV_FILE" ] || return 0
  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    if [[ "$value" == \'*\' ]]; then value="${value:1:${#value}-2}";
    elif [[ "$value" == \"*\" ]]; then value="${value:1:${#value}-2}"; fi
    env_set "$key" "$value"
  done <"$ENV_FILE"
}

quote() {
  # Single quotes: Docker Compose takes the value literally (the $ of the argon2 hash).
  local v="$1"
  if [[ "$v" =~ [[:space:]\$\#\"\'] ]]; then
    printf "'%s'" "${v//\'/}"
  else
    printf '%s' "$v"
  fi
}

ORDER="COMPOSE_PROFILES SITE_ADDRESS HTTP_BIND HTTPS_BIND PUBLIC_URL ADMIN_API_HANDLER
CLOUDFLARE_TUNNEL_TOKEN META_APP_SECRET INSTAGRAM_APP_SECRET WEBHOOK_VERIFY_TOKEN
META_GRAPH_API_VERSION LEGAL_CONTACT_EMAIL ENCRYPTION_KEY API_ADMIN_TOKEN CLICK_HASH_SALT
SESSION_SECRET ADMIN_PASSWORD_HASH APIFY_TOKEN OPENROUTER_API_KEY INSIGHTS_UTC_OFFSET_MINUTES"

write_env() {
  local tmp key value
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  chmod 600 "$tmp"
  {
    say "# Written by scripts/setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Keep it private."
    say "# Every variable is explained in .env.example."
    for key in $ORDER; do
      value="$(env_get "$key")"
      if [ "$key" = ADMIN_API_HANDLER ] && [ -z "$value" ]; then continue; fi
      say "$key=$(quote "$value")"
    done
    for key in $KNOWN_KEYS; do
      case " $(echo $ORDER) " in *" $key "*) continue ;; esac
      say "$key=$(quote "$(env_get "$key")")"
    done
  } >"$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

# ---- input --------------------------------------------------------------------------------

ask() { # ask VAR "question" "default"
  local var="$1" question="$2" default="${3:-}" answer
  if [ "$ASSUME_YES" = 1 ]; then printf -v "$var" '%s' "$default"; return; fi
  if [ -n "$default" ]; then read -r -p "$question [$default]: " answer || true
  else read -r -p "$question: " answer || true; fi
  printf -v "$var" '%s' "${answer:-$default}"
}

ask_secret() { # ask_secret VAR "question" (empty answer allowed)
  local var="$1" question="$2" answer=""
  if [ "$ASSUME_YES" = 1 ]; then printf -v "$var" '%s' "${!var-}"; return; fi
  read -r -s -p "$question (oculto): " answer || true
  printf '\n'
  printf -v "$var" '%s' "$answer"
}

random_hex() { # random_hex BYTES
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; fi
}

need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found. $2"; }

# ---- summary ------------------------------------------------------------------------------

panel_url() {
  local public bind
  public="$(env_get PUBLIC_URL)"
  bind="$(env_get HTTP_BIND)"
  case "$(env_get COMPOSE_PROFILES)" in
    *web*) ;;
    *) say "(Vercel: o endereço do seu projeto lá)"; return ;;
  esac
  if [ -n "$public" ]; then say "$public"; else say "http://localhost:${bind##*:}"; fi
}

show_summary() {
  local public
  public="$(env_get PUBLIC_URL)"
  step "Pronto"
  say "Painel:  $(panel_url)  (entre com a senha que você definiu)"
  if [ -z "$public" ]; then
    say ""
    say "Sem endereço público: a Meta não consegue mandar webhooks para cá."
    say "Para receber comentários e mensagens, rode de novo com --mode tunnel, domain ou proxy."
    return
  fi
  cat <<EOF

No app da Meta (passo a passo em docs/meta-setup.md):
  URL de callback do webhook:  $public/webhook
  Token de verificação:        $(env_get WEBHOOK_VERIFY_TOKEN)
  Política de privacidade:     $public/privacy
  Termos de serviço:           $public/terms
  Exclusão de dados:           $public/data-deletion
  Campos do webhook:           comments, messages, messaging_postbacks

Depois de gerar o token do Instagram no app da Meta:
  ./scripts/iw set-token     cola o token (sem eco) e guarda criptografado
  ./scripts/iw subscribe     inscreve a conta nos webhooks
  ./scripts/iw doctor        confere tudo e diz o que falta
EOF
  if [[ "$public" == *trycloudflare.com ]]; then
    say ""
    say "Atenção: o endereço do túnel rápido muda quando o container do túnel reinicia."
    say "Aí é preciso rodar ./scripts/setup.sh --mode tunnel de novo e atualizar o app da Meta."
  fi
}

load_env

if [ "$SHOW_ONLY" = 1 ]; then
  [ -f "$ENV_FILE" ] || die "no .env yet: run ./scripts/setup.sh first"
  show_summary
  exit 0
fi

# ---- requirements -------------------------------------------------------------------------

step "Conferindo o ambiente"
need docker "Install Docker: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "docker compose not found (Docker Compose v2 is required)."
if [ "$START" = 1 ]; then
  docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start it and try again."
fi
say "Docker ok."

# ---- mode ---------------------------------------------------------------------------------

if [ -z "$MODE" ]; then
  if [ "$ASSUME_YES" = 1 ]; then die "--yes needs --mode or IW_MODE"; fi
  cat <<'EOF'

Como a Meta vai chegar até esta instalação?
  1) domain      servidor com um domínio apontando para ele (HTTPS automático)
  2) proxy       servidor que já tem um proxy reverso (Nginx, Traefik, Caddy...)
  3) tunnel      meu computador, sem domínio (endereço temporário da Cloudflare)
  4) cloudflare  túnel fixo da Cloudflare (precisa de conta e token)
  5) local       só ver o painel em localhost (sem receber nada da Meta)
  6) vercel      API aqui com domínio, painel na Vercel
EOF
  ask choice "Escolha" "3"
  case "$choice" in
    1|domain) MODE=domain ;; 2|proxy) MODE=proxy ;; 3|tunnel) MODE=tunnel ;;
    4|cloudflare) MODE=cloudflare ;; 5|local) MODE=local ;; 6|vercel) MODE=vercel ;;
    *) die "invalid choice: $choice" ;;
  esac
fi

current_domain="$(env_get PUBLIC_URL)"; current_domain="${current_domain#https://}"
case "$MODE" in
  domain|vercel)
    ask domain "Domínio (ex.: insta.seudominio.com), já apontando para este servidor" \
      "${IW_DOMAIN:-$current_domain}"
    [ -n "$domain" ] || die "a domain is required for --mode $MODE"
    env_set SITE_ADDRESS "$domain"
    env_set HTTP_BIND "0.0.0.0:80"
    env_set HTTPS_BIND "0.0.0.0:443"
    env_set PUBLIC_URL "https://$domain"
    if [ "$MODE" = vercel ]; then
      env_set COMPOSE_PROFILES ""
      env_set ADMIN_API_HANDLER "reverse_proxy api:8000"
    else
      env_set COMPOSE_PROFILES "web"
      env_set ADMIN_API_HANDLER ""
    fi
    ;;
  proxy)
    ask domain "Domínio que o seu proxy atende (ex.: insta.seudominio.com)" \
      "${IW_DOMAIN:-$current_domain}"
    [ -n "$domain" ] || die "a domain is required for --mode proxy"
    ask port "Porta local para o seu proxy encaminhar" "${IW_PROXY_PORT:-8080}"
    env_set SITE_ADDRESS ":80"
    env_set HTTP_BIND "127.0.0.1:$port"
    env_set HTTPS_BIND "127.0.0.1:$((port + 1))"
    env_set PUBLIC_URL "https://$domain"
    env_set COMPOSE_PROFILES "web"
    env_set ADMIN_API_HANDLER ""
    ;;
  tunnel)
    env_set SITE_ADDRESS ":80"
    env_set HTTP_BIND "127.0.0.1:${IW_PROXY_PORT:-8080}"
    env_set HTTPS_BIND "127.0.0.1:$(( ${IW_PROXY_PORT:-8080} + 1 ))"
    env_set PUBLIC_URL ""   # read from the tunnel logs after it starts
    env_set COMPOSE_PROFILES "web,tunnel"
    env_set ADMIN_API_HANDLER ""
    ;;
  cloudflare)
    ask domain "Hostname público do túnel (ex.: insta.seudominio.com)" "${IW_DOMAIN:-$current_domain}"
    [ -n "$domain" ] || die "the tunnel hostname is required"
    CLOUDFLARE_TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-$(env_get CLOUDFLARE_TUNNEL_TOKEN)}"
    if [ -z "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
      ask_secret CLOUDFLARE_TUNNEL_TOKEN "Token do túnel (Zero Trust > Networks > Tunnels)"
    fi
    [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ] || die "CLOUDFLARE_TUNNEL_TOKEN is required"
    say "No painel da Cloudflare, aponte o hostname $domain para o serviço http://gateway:80."
    env_set CLOUDFLARE_TUNNEL_TOKEN "$CLOUDFLARE_TUNNEL_TOKEN"
    env_set SITE_ADDRESS ":80"
    env_set HTTP_BIND "127.0.0.1:8080"
    env_set HTTPS_BIND "127.0.0.1:8081"
    env_set PUBLIC_URL "https://$domain"
    env_set COMPOSE_PROFILES "web,named-tunnel"
    env_set ADMIN_API_HANDLER ""
    ;;
  local)
    env_set SITE_ADDRESS ":80"
    env_set HTTP_BIND "127.0.0.1:${IW_PROXY_PORT:-8080}"
    env_set HTTPS_BIND "127.0.0.1:$(( ${IW_PROXY_PORT:-8080} + 1 ))"
    env_set PUBLIC_URL ""
    env_set COMPOSE_PROFILES "web"
    env_set ADMIN_API_HANDLER ""
    ;;
  *) die "unknown mode: $MODE (see --help)" ;;
esac
say "Modo: $MODE"

# ---- Meta app and contact -----------------------------------------------------------------

step "App da Meta"
say "Os dois segredos ficam no painel do app (docs/meta-setup.md, passo 3). Dá para"
say "deixar em branco agora e preencher depois: a API só sobe quando os dois existirem."
for key in META_APP_SECRET INSTAGRAM_APP_SECRET; do
  current="$(env_get "$key")"
  value="${!key:-$current}"
  if [ -z "$value" ] && [ "$ASSUME_YES" = 0 ]; then
    label="Chave secreta do app (Configurações > Básico)"
    [ "$key" = INSTAGRAM_APP_SECRET ] && label="Chave secreta do app do Instagram (Instagram > Configuração da API)"
    ask_secret value "$label"
  fi
  if [ -n "$value" ] && ! [[ "$value" =~ ^[0-9a-fA-F]{32}$ ]]; then
    say "aviso: $key não parece um segredo da Meta (32 caracteres hex). Confira depois."
  fi
  env_set "$key" "$value"
done
[ -n "$(env_get WEBHOOK_VERIFY_TOKEN)" ] || env_set WEBHOOK_VERIFY_TOKEN "$(random_hex 24)"
[ -n "$(env_get META_GRAPH_API_VERSION)" ] || env_set META_GRAPH_API_VERSION "v25.0"

ask email "Email de contato para as páginas de privacidade (público)" \
  "${IW_LEGAL_EMAIL:-$(env_get LEGAL_CONTACT_EMAIL)}"
env_set LEGAL_CONTACT_EMAIL "$email"
[ -n "$(env_get INSIGHTS_UTC_OFFSET_MINUTES)" ] || env_set INSIGHTS_UTC_OFFSET_MINUTES "-180"

# ---- generated secrets (never rotated here) ----------------------------------------------

for pair in ENCRYPTION_KEY:32 API_ADMIN_TOKEN:32 CLICK_HASH_SALT:32 SESSION_SECRET:32; do
  key="${pair%%:*}"
  [ -n "$(env_get "$key")" ] || env_set "$key" "$(random_hex "${pair##*:}")"
done

# ---- optional modules ---------------------------------------------------------------------

step "Módulos opcionais (Enter para pular)"
for key in OPENROUTER_API_KEY APIFY_TOKEN; do
  current="$(env_get "$key")"
  value="${!key:-$current}"
  if [ -z "$value" ] && [ "$ASSUME_YES" = 0 ]; then
    label="OpenRouter (I.A.: respostas no direct, classificação e relatório)"
    [ "$key" = APIFY_TOKEN ] && label="Apify (análise de concorrência)"
    ask_secret value "$label"
  fi
  env_set "$key" "$value"
done

write_env
say ".env escrito (permissão 600)."

# ---- dashboard password -------------------------------------------------------------------

if [ -z "$(env_get ADMIN_PASSWORD_HASH)" ]; then
  step "Senha do painel"
  password="${IW_ADMIN_PASSWORD:-}"
  if [ -z "$password" ]; then
    [ "$ASSUME_YES" = 1 ] && die "IW_ADMIN_PASSWORD is required with --yes on the first run"
    while :; do
      read -r -s -p "Senha do painel (mín. 12 caracteres, oculta): " password; printf '\n'
      read -r -s -p "Repita: " again; printf '\n'
      [ "$password" = "$again" ] && [ "${#password}" -ge 12 ] && break
      say "As senhas não batem ou são curtas. De novo."
    done
  fi
  [ "${#password}" -ge 12 ] || die "the dashboard password needs at least 12 characters"
  say "Montando a imagem do painel para gerar o hash (a primeira vez demora alguns minutos)..."
  docker compose build web >/dev/null
  hash_line="$(printf '%s\n' "$password" \
    | docker compose run --rm --no-deps -T web node scripts/hash-password.mjs 2>/dev/null \
    | grep -m1 '^ADMIN_PASSWORD_HASH=\$argon2' || true)"
  unset password again
  [ -n "$hash_line" ] || die "could not hash the password (see: docker compose run --rm web node scripts/hash-password.mjs)"
  env_set ADMIN_PASSWORD_HASH "${hash_line#ADMIN_PASSWORD_HASH=}"
  write_env
  say "Hash guardado no .env (a senha em si não fica em lugar nenhum)."
fi

if [ "$START" = 0 ]; then
  say "Pronto. Suba com: docker compose up -d --build"
  exit 0
fi

if [ -z "$(env_get META_APP_SECRET)" ] || [ -z "$(env_get INSTAGRAM_APP_SECRET)" ]; then
  say ""
  say "Faltam os segredos do app da Meta. Crie o app (docs/meta-setup.md, passos 1 a 3),"
  say "rode ./scripts/setup.sh de novo e ele sobe tudo."
  exit 0
fi

# ---- start --------------------------------------------------------------------------------

step "Subindo os containers"
docker compose up -d --build

say "Esperando a API..."
for _ in $(seq 1 60); do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" \
    >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 2
done
[ "${ok:-0}" = 1 ] || die "the API did not become healthy. See: docker compose logs api"
say "API no ar."

if [ "$MODE" = tunnel ]; then
  say "Esperando o endereço do túnel..."
  url=""
  for _ in $(seq 1 45); do
    url="$(docker compose logs tunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
    [ -n "$url" ] && break
    sleep 2
  done
  [ -n "$url" ] || die "no tunnel address yet. See: docker compose logs tunnel"
  env_set PUBLIC_URL "$url"
  write_env
  docker compose up -d api >/dev/null 2>&1 || true
fi

show_summary
