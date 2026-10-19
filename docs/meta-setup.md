# App da Meta, passo a passo

O que fazer no painel de desenvolvedores da Meta para o insta-watcher receber comentários e mensagens da sua conta e responder. Leva de 30 minutos a uma hora na primeira vez. Não precisa de CNPJ, App Review nem verificação de empresa: o app atende só a sua própria conta profissional, e para isso o acesso padrão (Standard Access) basta.

A Meta muda nomes de menu com frequência. Se um item não estiver onde este guia diz, procure pelo nome em inglês, entre parênteses.

## Antes de começar

- [ ] **Conta profissional no Instagram** (Criador ou Comercial). Conta pessoal não usa a API. No app: Configurações e atividade › Tipo de conta e ferramentas › Mudar para conta profissional. É gratuito e reversível. Não precisa ligar a uma Página do Facebook.
- [ ] **Uma conta no Facebook**, só para entrar no painel de desenvolvedores.
- [ ] **O insta-watcher no ar, com endereço público HTTPS.** A Meta testa a URL do webhook na hora de salvar, e exige as páginas de privacidade para publicar o app. Rode `./scripts/setup.sh` (`docs/deploy.md`) até ele mostrar o resumo. Pode deixar os segredos da Meta em branco na primeira rodada: você preenche no passo 3 e roda o script de novo.
- [ ] **Uma segunda conta do Instagram** (qualquer uma, pessoal serve) para testar no fim. Comentário da própria conta é ignorado de propósito.

Os valores que você vai colar na Meta aparecem com:

```sh
./scripts/setup.sh --show
```

## 1. Conta de desenvolvedor

1. Entre em [developers.facebook.com](https://developers.facebook.com) com a conta do Facebook.
2. **Começar** (Get Started): aceite os termos, confirme o email ou telefone e escolha "Desenvolvedor" como função, se perguntar.

## 2. Criar o app

1. **Meus apps › Criar app** (My Apps › Create App).
2. Nome do app: qualquer um (ex.: "insta-watcher da minha conta"). Email de contato: o seu.
3. **Caso de uso** (Use case): escolha **Gerenciar mensagens e conteúdo no Instagram** (Manage messaging & content on Instagram). É a "API do Instagram com login do Instagram".
   - Não escolha a Marketing API nem "Login do Facebook": eles pedem review pesado ou quebram o fluxo depois com "cliente incompatível".
4. **Portfólio empresarial** (Business portfolio): "Ainda não quero conectar" serve.
5. Confirme e crie. O app nasce em **Desenvolvimento** (Development). Fica assim até o passo 8.

## 3. Os dois segredos do app

O insta-watcher confere a assinatura de cada webhook com o segredo do app. A Meta mostra dois segredos diferentes e a documentação não deixa claro qual assina, então o sistema aceita qualquer um dos dois e precisa de ambos.

1. **Configurações do app › Básico** (App settings › Basic) › **Chave secreta do app** (App secret) › Mostrar. Esse é o `META_APP_SECRET`.
2. **Casos de uso** (Use cases) › Instagram › **Personalizar** (Customize) › **Configuração da API com login do Instagram** (API setup with Instagram login). Na seção do app do Instagram, **Chave secreta do app do Instagram** (Instagram app secret). Esse é o `INSTAGRAM_APP_SECRET`.
3. No seu terminal (não cole os segredos em chat, email ou issue):

   ```sh
   ./scripts/setup.sh
   ```

   Ele pede os dois sem mostrar na tela, guarda no `.env` e sobe a API.

## 4. Permissões

Na mesma página de configuração da API com login do Instagram, confira que o caso de uso tem estas permissões (Permissions). As três primeiras são obrigatórias:

| Permissão | Para quê |
|---|---|
| `instagram_business_basic` | Ler o perfil e os posts |
| `instagram_business_manage_comments` | Receber comentários e responder em público |
| `instagram_business_manage_messages` | Mandar DMs, receber mensagens, botões e o Chat |
| `instagram_business_manage_insights` | Opcional. Página de insights: alcance, visualizações, público, seguidores online |

Se faltar alguma, adicione em **Permissões e recursos** (Permissions and features) do caso de uso. Em Standard Access elas valem para contas com função no app, que é o caso da sua.

## 5. Ligar a sua conta e gerar o token

1. Ainda na configuração da API com login do Instagram, seção **Gerar tokens de acesso** (Generate access tokens) › **Adicionar conta** (Add account).
2. Entre com a conta profissional do Instagram e autorize as permissões do passo 4.
3. Se a Meta pedir a conta como testadora: **Funções do app › Funções › Testadores do Instagram** (App roles › Instagram Testers) › adicione o @. Depois aceite no app do Instagram: Configurações e atividade › Apps e sites (Website permissions) › **Convites de testador** (Tester invites). Sem o aceite, o erro é "Insufficient Developer Role".
4. Ao lado da conta, **Gerar token** (Generate token). Copie o token inteiro. Ele começa com `IG` e tem centenas de caracteres. Um token que começa com `EAA` é do Facebook e não serve.
5. Guarde no insta-watcher, sem colar em lugar nenhum além do prompt:

   ```sh
   ./scripts/iw set-token
   ```

   Ou cole no painel, em **Configurações**. O sistema valida o token na Meta, mostra o @ da conta e guarda criptografado. Ele renova sozinho antes dos 60 dias de validade. Se um dia vencer (o painel avisa), repita este passo.

## 6. Webhook

Na configuração da API com login do Instagram, seção **Configurar webhooks** (Configure webhooks):

1. **URL de callback** (Callback URL): `https://SEU_ENDERECO/webhook` (o `--show` imprime pronto).
2. **Token de verificação** (Verify token): o valor de `WEBHOOK_VERIFY_TOKEN` (também no `--show`).
3. **Verificar e salvar** (Verify and save). A Meta chama a URL na hora: o insta-watcher precisa estar no ar. Se o botão ficar cinza, cole o token de novo: editar a URL costuma limpar o campo.
4. Na lista de campos (Webhook fields), **assine** (Subscribe):
   - `comments`: comentários nos posts e reels;
   - `messages`: mensagens diretas, respostas a stories, toques em respostas rápidas e o eco das mensagens enviadas;
   - `messaging_postbacks`: toques em botões (o "Já segui" do follow gate).

## 7. Inscrever a conta no app

O webhook verificado ainda não basta: a conta precisa ser inscrita no app. Sem isso, a Meta não manda nada.

```sh
./scripts/iw subscribe
```

Ou, no painel, **Configurações › Inscrever webhooks**. Refaça sempre que mudar os campos do passo 6.

## 8. Páginas legais e publicação

A Meta só entrega eventos de quem não tem função no app (ou seja, de qualquer seguidor) com o app **publicado** (Live). Em Desenvolvimento, nada chega dos outros.

1. **Configurações do app › Básico**:
   - **URL da Política de Privacidade**: `https://SEU_ENDERECO/privacy`
   - **URL dos Termos de Serviço**: `https://SEU_ENDERECO/terms`
   - **Exclusão de dados do usuário** › "URL de instruções de exclusão de dados": `https://SEU_ENDERECO/data-deletion`
   - **Categoria** e **ícone do app**, se pedir.
   - Salve. As três páginas são servidas pelo insta-watcher e mostram o `LEGAL_CONTACT_EMAIL`. Abra cada uma no navegador antes de salvar: sem o email configurado, elas dão 404.
2. **Publicar** (Publish), no topo do painel do app, ou **Modo do app: Ativo** (App mode: Live). Para a sua própria conta, em Standard Access, isso não pede App Review nem verificação de empresa.

## 9. Testar

```sh
./scripts/iw doctor
```

Corrija toda linha marcada `XX`. Depois, de ponta a ponta:

1. No painel, crie uma campanha (Campanhas › Nova) com a palavra `teste` num post seu.
2. Da **segunda conta**, comente `teste` nesse post.
3. Em segundos: o painel mostra a entrega em **Entregas**, o webhook em **Diagnóstico**, e a DM chega na segunda conta. Ela pode cair em **Solicitações de mensagem** se a segunda conta não segue a sua.

Para o Chat: da segunda conta, mande uma mensagem no direct. Ela aparece em **Chat › Conversas**.

## Nada chega? Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "Insufficient Developer Role" | A conta não está como testadora, ou o convite não foi aceito no app do Instagram (passo 5.3) |
| Verificação do webhook falha | Token de verificação diferente do `.env`, instalação fora do ar, ou endereço sem HTTPS válido. Abra `https://SEU_ENDERECO/health` no navegador |
| 401 em todo webhook (Diagnóstico ou `docker compose logs api`) | Segredo do app errado no `.env`. Confira os dois do passo 3 e rode `./scripts/setup.sh` de novo |
| Nada chega ao comentar | App em Desenvolvimento (passo 8), campo `comments` não assinado (passo 6), conta não inscrita (passo 7), ou o comentário é da própria conta |
| Mensagens no direct não chegam | Campo `messages` não assinado, ou a conta não foi reinscrita depois de incluí-lo (passo 7) |
| `Failed to decrypt` (código 190) no `set-token` | O texto colado não é um token válido: colagem incompleta, caractere invisível do terminal, ou token do Facebook (`EAA...`) no lugar do token do Instagram (`IG...`) |
| Erro de permissão ao enviar DM | Token sem `instagram_business_manage_messages` (passo 4). Gere um novo depois de adicionar a permissão |
| Insights vazios | Token sem `instagram_business_manage_insights`. Adicione a permissão, gere um token novo e guarde com `set-token` |
| Parou de chegar depois de mudar o endereço | A URL de callback antiga ficou no app. Atualize no passo 6. Com o túnel rápido isso acontece a cada reinício do túnel |
| Webhook chega mas a DM não sai | `./scripts/iw doctor` e a página Diagnóstico. Envio pausado, token recusado e fila parada aparecem lá |

## Regras da Meta que valem sempre

- **Private reply** (a DM em resposta a um comentário): uma por comentário, em até 7 dias depois do comentário.
- **Mensagem direta** fora disso: só depois que a pessoa escreveu, dentro de 24 horas da última mensagem dela. Por isso não existem sequências nem broadcast.
- Até **750 private replies por hora** por conta. O excedente espera na fila.
- Botões: até 3 por mensagem, título com até 20 caracteres. Respostas rápidas: até 13, título com até 20 caracteres.
- Apps de mensagem precisam de uma saída para um humano. No insta-watcher, o modo humano e a opção "Chamar você" dos fluxos cumprem isso.
- Atender a conta de outra pessoa ou empresa com o seu app exige Advanced Access (App Review e verificação de empresa). Cada pessoa deve instalar o seu.

## O que já foi verificado

Com o app em Live, em Standard Access e com a conta inscrita: comentário de uma conta sem função no app chega por webhook, a private reply é entregue, a resposta a story chega em `messages`, o toque em botão chega em `messaging_postbacks`, e `is_user_follow_business` devolve `true` e `false`. Em Desenvolvimento, nada chega. Publicar exigiu só as URLs de privacidade, termos e exclusão de dados.

A resposta do `/me` traz dois IDs: `user_id` é o da conta profissional (igual ao `entry.id` dos webhooks) e é o que o sistema guarda; `id` tem escopo do app e não é usado.
