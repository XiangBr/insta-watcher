# Contribuindo

Contribuições são bem-vindas: correções, relatos de armadilhas da Meta que não estejam em `docs/meta-setup.md`, novos modos de publicação e traduções do painel.

1. Leia `AGENTS.md`. As regras valem para pessoas e para agentes de código.
2. Ative o hook de segredos uma vez por clone: `git config core.hooksPath .githooks` (precisa do gitleaks ou do Docker).
3. Abra uma issue antes de mudanças grandes, principalmente as que mexem nas "Decisões firmes" do `CLAUDE.md`.
4. Uma mudança por PR, com testes. Os testes obrigatórios estão em `AGENTS.md`. Antes de abrir: testes, lint e tipos do backend e do frontend limpos.
5. Mudou comportamento, endpoint ou variável de ambiente: atualize `docs/` e o `.env.example`.
6. Commits em Conventional Commits, em inglês. Documentação em português do Brasil.
7. Nada de segredo, token, payload real de webhook ou dado de pessoa real em PR, issue ou screenshot.
8. Vulnerabilidade vai pelo canal de `SECURITY.md`, nunca em issue pública.
