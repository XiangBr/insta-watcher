# Política de segurança

Este projeto manipula tokens de acesso do Instagram, payloads de webhook com dados de terceiros e um painel de administração. Reporte problemas de forma responsável.

## Versões com correção

A branch `main`. Correções de segurança entram nela.

## Como reportar

Não abra issue pública para vulnerabilidade. Use o recurso de relato privado de vulnerabilidade do GitHub neste repositório (aba Security › Report a vulnerability).

Inclua descrição, passos para reproduzir, impacto, se há risco de exposição de token ou dado pessoal, e uma sugestão de correção se tiver.

## Áreas mais sensíveis

- Validação de assinatura e verify token do webhook
- Criptografia e uso do token do Instagram
- Autenticação do painel e token de admin
- Idempotência e classificação de envio ambíguo
- Redirecionamento dos links rastreados
- Tratamento de variáveis de ambiente e logs
- Exportação CSV

## Segredos

Nunca versione nem cole em lugar com log: `ENCRYPTION_KEY`, `API_ADMIN_TOKEN`, `SESSION_SECRET`, `WEBHOOK_VERIFY_TOKEN`, `CLICK_HASH_SALT`, segredos do app na Meta, token do Instagram, `ADMIN_PASSWORD_HASH` e payloads reais de webhook. Se algum vazar, rotacione.

## Divulgação

Relatos válidos serão confirmados e corrigidos por ordem de gravidade. A meta é responder em até 7 dias.
