"""Privacy policy, terms and data-deletion instructions. Meta requires all three to go Live.

Static HTML, no scripts. The contact comes from LEGAL_CONTACT_EMAIL; without it the pages 404,
so a fresh instance never publishes someone else's contact.
"""

import html
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.routers.public import SettingsDep

router = APIRouter()

LAST_UPDATED = date(2026, 10, 2)
_MONTHS = (
    "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro"
).split()

_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "public, max-age=3600",
}

_STYLE = """
:root { color-scheme: light dark; --fg: #1d1d1f; --muted: #5f5f66; --bg: #fbfbf8; --rule: #dcdcd4; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #ececea; --muted: #a3a39c; --bg: #161615; --rule: #34342f; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 17px/1.6 Georgia, "Times New Roman", serif; }
main { max-width: 42rem; margin: 0 auto; padding: 3rem 1rem 4rem; }
header { border-bottom: 1px solid var(--rule); margin-bottom: 2rem; padding-bottom: 1rem; }
h1 { font-size: 1.9rem; line-height: 1.2; margin: 0 0 .4rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 .5rem; }
p, li { margin: .5rem 0; }
.meta, nav { color: var(--muted); font: 14px/1.5 system-ui, sans-serif; }
nav { border-top: 1px solid var(--rule); margin-top: 3rem; padding-top: 1rem; }
nav a { color: inherit; margin-right: 1rem; }
a { color: inherit; }
"""


def _page(title: str, body: str, email: str) -> HTMLResponse:
    updated = f"{LAST_UPDATED.day} de {_MONTHS[LAST_UPDATED.month - 1]} de {LAST_UPDATED.year}"
    contact = html.escape(email)
    document = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)} · insta-watcher</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<header>
<h1>{html.escape(title)}</h1>
<p class="meta">insta-watcher · última atualização em {updated}</p>
</header>
{body.replace("{contact}", contact)}
<nav>
<a href="/privacy">Privacidade</a>
<a href="/terms">Termos</a>
<a href="/data-deletion">Exclusão de dados</a>
</nav>
</main>
</body>
</html>"""
    return HTMLResponse(document, headers=_HEADERS)


def _contact(settings_email: str | None) -> str:
    if not settings_email:
        raise HTTPException(status_code=404)
    return settings_email


_PRIVACY = """
<p>Esta política explica como o insta-watcher trata dados pessoais. O insta-watcher é uma automação
que responde, por mensagem direta, a quem comenta palavras-chave em publicações de uma conta do
Instagram. Ele usa somente a API oficial da Meta.</p>

<h2>Quem é o responsável</h2>
<p>O controlador dos dados é o titular da conta do Instagram que opera esta instância.
Contato: <a href="mailto:{contact}">{contact}</a>.</p>

<h2>Quais dados tratamos</h2>
<ul>
<li>Identificador e nome de usuário do Instagram de quem comenta ou envia mensagem.</li>
<li>Texto do comentário ou da mensagem que acionou a automação.</li>
<li>Identificadores técnicos fornecidos pela Meta (do comentário, da publicação e da mensagem)
e o horário de cada evento.</li>
<li>Registro de cada envio: se a mensagem saiu, falhou ou ficou sem confirmação.</li>
<li>Para estatísticas da própria conta: quando cada comentário foi feito e quem comentou. O texto
desses comentários não é guardado, só a contagem das palavras usadas, sem identificar quem.</li>
<li>Conversas por mensagem direta com a conta: o texto das mensagens trocadas, etiquetas que a
conta atribui e se a conversa está com uma pessoa ou com a automação.</li>
</ul>
<p>Não pedimos senha, não coletamos dados fora do Instagram e não acessamos conteúdo que não
tenha sido enviado à conta por comentário ou mensagem.</p>

<h2>Para que usamos</h2>
<p>Exclusivamente para responder ao seu comentário ou mensagem com a informação pedida, para
não enviar a mesma mensagem duas vezes, para diagnosticar falhas e para estatísticas da própria
conta (quais posts e horários funcionam melhor). Os dados não são vendidos e
não são usados para publicidade. A Meta, como plataforma, processa as mensagens conforme a
política dela. Se a conta usar respostas automáticas escritas por inteligência artificial, o
texto recente da sua conversa é enviado ao provedor do modelo (via OpenRouter) só para escrever
a resposta, e não é usado por nós para outro fim. O servidor fica em um provedor de hospedagem
contratado pelo responsável.</p>

<h2>Base legal</h2>
<p>O atendimento a um pedido feito por você, ao comentar a palavra-chave, e o legítimo interesse
em responder a quem interage com a conta (Lei 13.709/2018, art. 7º, incisos V e IX).</p>

<h2>Por quanto tempo guardamos</h2>
<ul>
<li>Evento bruto recebido da Meta: até 7 dias.</li>
<li>Texto e nome de usuário no registro de envios: até 90 dias. Depois disso são apagados, e
só a contagem anônima é mantida.</li>
<li>Registros operacionais sem conteúdo de usuário: até 90 dias.</li>
<li>Quem comentou, nas estatísticas da conta: até 90 dias. Depois só fica o horário.</li>
<li>Texto das conversas por mensagem direta: até 90 dias. Quem não escreve por 90 dias deixa de
ser identificado.</li>
</ul>

<h2>Seus direitos</h2>
<p>Você pode pedir confirmação, acesso, correção, anonimização ou exclusão dos seus dados, além
de informações sobre o tratamento (LGPD, art. 18). Veja como em
<a href="/data-deletion">Exclusão de dados</a> ou escreva para
<a href="mailto:{contact}">{contact}</a>. Também é possível reclamar à Autoridade Nacional de
Proteção de Dados.</p>

<h2>Segurança</h2>
<p>A comunicação usa HTTPS, as credenciais de acesso à Meta ficam criptografadas, e o acesso ao
servidor é restrito ao responsável.</p>

<h2>Mudanças</h2>
<p>Alterações nesta política serão publicadas nesta página, com nova data de atualização.</p>
"""

_TERMS = """
<p>Ao comentar uma palavra-chave ou enviar mensagem a uma conta que usa o insta-watcher, você pode
receber uma resposta automática por mensagem direta no Instagram.</p>

<h2>O serviço</h2>
<ul>
<li>A resposta é automática e enviada uma única vez por comentário.</li>
<li>O conteúdo das mensagens (textos e links) é de responsabilidade do titular da conta.</li>
<li>O serviço é oferecido como está, sem garantia de disponibilidade. Mensagens podem atrasar
ou não ser entregues por limites da Meta.</li>
</ul>

<h2>Como parar de receber</h2>
<p>Basta não comentar as palavras-chave. Você também pode bloquear a conta ou restringir
mensagens nas configurações do Instagram. Para apagar os seus dados, veja
<a href="/data-deletion">Exclusão de dados</a>.</p>

<h2>Uso aceitável</h2>
<p>Não use comentários para tentar abusar da automação, por exemplo com envio em massa. As
regras e os termos do Instagram e da Meta continuam valendo.</p>

<h2>Dados pessoais</h2>
<p>O tratamento está descrito na <a href="/privacy">Política de privacidade</a>.</p>

<h2>Contato</h2>
<p><a href="mailto:{contact}">{contact}</a></p>
"""

_DELETION = """
<p>Você pode pedir a exclusão dos dados que o insta-watcher guardou sobre você a qualquer
momento.</p>

<h2>Como pedir</h2>
<ol>
<li>Escreva para <a href="mailto:{contact}">{contact}</a> com o assunto
"Exclusão de dados".</li>
<li>Informe o seu nome de usuário do Instagram.</li>
<li>Para confirmar que a conta é sua, podemos pedir que você envie uma mensagem direta pela
própria conta.</li>
</ol>

<h2>O que acontece</h2>
<p>Em até 15 dias apagamos ou anonimizamos o seu identificador, o nome de usuário e o texto dos
seus comentários e mensagens. Você recebe uma confirmação por email. Totais anônimos, sem
nenhum dado que identifique você, podem ser mantidos.</p>

<h2>Exclusão automática</h2>
<p>Mesmo sem pedido, o texto e o nome de usuário são apagados depois de 90 dias, e o evento
bruto depois de 7 dias.</p>

<h2>Dados na Meta</h2>
<p>Mensagens e comentários também ficam no Instagram, sob a política da Meta. Para apagá-los lá,
use as ferramentas do próprio Instagram.</p>
"""


@router.get("/privacy", include_in_schema=False)
async def privacy(settings: SettingsDep) -> HTMLResponse:
    return _page("Política de privacidade", _PRIVACY, _contact(settings.legal_contact_email))


@router.get("/terms", include_in_schema=False)
async def terms(settings: SettingsDep) -> HTMLResponse:
    return _page("Termos de uso", _TERMS, _contact(settings.legal_contact_email))


@router.get("/data-deletion", include_in_schema=False)
async def data_deletion(settings: SettingsDep) -> HTMLResponse:
    return _page("Exclusão de dados", _DELETION, _contact(settings.legal_contact_email))
