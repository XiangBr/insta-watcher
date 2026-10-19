---
description: Install and configure insta-watcher end to end (Docker, .env, Meta app, token, webhooks)
---

You are setting up insta-watcher for the person in this session. Follow `AGENTS.md`, section
"Setup assistido por agente", step by step. Summary:

1. Read `docs/deploy.md` and `docs/meta-setup.md` before acting.
2. Ask the person only what you cannot find out: where it will run (a server with a domain,
   a server with an existing reverse proxy, their own computer through a tunnel, or local
   only), the domain if any, and the public contact email. Check the machine yourself:
   `docker info`, `docker compose version`, open ports, existing proxies (`docker ps`).
3. Never ask for a secret in the chat (Meta app secrets, the Instagram token, API keys, the
   dashboard password). Ask the person to run, in their own terminal:
   `./scripts/setup.sh --mode <mode>` for the setup prompts, and
   `./scripts/iw set-token` for the Instagram token. If they paste a secret here anyway,
   tell them to rotate it.
4. Walk them through `docs/meta-setup.md` one step at a time, giving the exact values to
   paste, which `./scripts/setup.sh --show` prints (callback URL, verify token, legal URLs).
5. Finish with `./scripts/iw doctor` and fix every line marked `XX` before saying it is
   done. Then suggest the end-to-end test: comment a keyword from another account.

$ARGUMENTS
