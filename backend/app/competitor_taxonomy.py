"""What the decision model is asked about each competitor and each post.

This file is configuration: edit the questions and options to fit your niche. The defaults
fit a creator who talks about technology, AI and automation. Option keys are what gets
stored; the labels are shown in the dashboard; the criteria are what the model reads.

Change TAXONOMY_VERSION after editing, so everything already classified is classified again.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

TAXONOMY_VERSION = "1"

QuestionType = Literal["choice", "score", "noul"]


@dataclass(frozen=True)
class Option:
    label: str
    criteria: str


@dataclass(frozen=True)
class Question:
    type: QuestionType
    label: str
    instructions: str
    # choice: key -> option. score: levels from lowest to highest (key is the index).
    # noul: "true" and "false".
    options: dict[str, Option] = field(default_factory=dict)

    def as_decision(self) -> dict[str, Any]:
        """The question in the Decisions API format."""
        if self.type == "score":
            return {
                "type": "score",
                "instructions": self.instructions,
                "criteria": [o.criteria for o in self.options.values()],
            }
        return {
            "type": self.type,
            "instructions": self.instructions,
            "criteria": {key: o.criteria for key, o in self.options.items()},
        }


POST_QUESTIONS: dict[str, Question] = {
    "format": Question(
        "choice",
        "Formato",
        "Qual é o formato principal deste post do Instagram, a julgar pela legenda?",
        {
            "tutorial": Option(
                "Tutorial", "Ensina a fazer algo em etapas ou mostra uma ferramenta em uso."
            ),
            "tips": Option("Lista de dicas", "Lista de dicas, ferramentas, erros ou recursos."),
            "opinion": Option(
                "Opinião", "Defende um ponto de vista, critica algo ou provoca debate."
            ),
            "case": Option(
                "Case ou resultado",
                "Mostra um resultado obtido: antes e depois, números, projeto ou depoimento.",
            ),
            "behind": Option(
                "Bastidores", "Mostra rotina, setup, dia a dia ou a história pessoal do autor."
            ),
            "news": Option("Novidade", "Comenta um lançamento, atualização ou notícia do setor."),
            "humor": Option("Humor", "Meme, piada, trend ou situação engraçada do nicho."),
            "offer": Option("Oferta", "Divulga produto, curso, serviço, evento ou desconto."),
        },
    ),
    "hook": Question(
        "choice",
        "Gancho",
        "Como a primeira frase da legenda tenta prender a atenção de quem lê?",
        {
            "question": Option("Pergunta", "Abre com uma pergunta direta ao leitor."),
            "promise": Option(
                "Promessa", "Promete um resultado, ganho de tempo ou dinheiro, ou transformação."
            ),
            "number": Option("Número ou lista", "Abre com um número: '5 ferramentas', '3 erros'."),
            "contrarian": Option(
                "Contraintuitivo", "Contraria o senso comum ou faz uma afirmação polêmica."
            ),
            "curiosity": Option(
                "Curiosidade", "Esconde a informação para gerar curiosidade: segredo, ninguém fala."
            ),
            "proof": Option("Prova", "Abre com autoridade, número de resultado ou prova social."),
            "pain": Option("Dor", "Abre nomeando um problema ou frustração do público."),
            "none": Option("Sem gancho", "Não há tentativa clara de prender a atenção."),
        },
    ),
    "cta": Question(
        "choice",
        "Chamada para ação",
        "O que a legenda pede para o leitor fazer?",
        {
            "comment_keyword": Option(
                "Comentar palavra-chave",
                "Pede para comentar uma palavra específica para receber algo por DM.",
            ),
            "comment_opinion": Option(
                "Comentar opinião", "Pede a opinião ou a experiência do leitor nos comentários."
            ),
            "save_share": Option(
                "Salvar ou compartilhar", "Pede para salvar o post ou enviar para alguém."
            ),
            "follow": Option("Seguir", "Pede para seguir o perfil."),
            "link_bio": Option("Link na bio", "Manda para o link da bio."),
            "dm": Option("Mandar DM", "Pede para o leitor mandar mensagem direta."),
            "none": Option("Nenhuma", "Não pede nada ao leitor."),
        },
    ),
    "tone": Question(
        "choice",
        "Tom",
        "Qual é o tom predominante da legenda?",
        {
            "educational": Option("Educativo", "Explica com calma, didático, técnico."),
            "inspirational": Option("Inspiracional", "Motiva, fala de mentalidade e conquistas."),
            "casual": Option("Descontraído", "Informal, bem-humorado, conversa de amigo."),
            "salesy": Option("Vendedor", "Urgência, escassez, foco em converter."),
            "provocative": Option("Provocativo", "Confronta, polemiza, desafia o leitor."),
        },
    ),
    "topic": Question(
        "choice",
        "Tema",
        "Qual é o tema principal do post?",
        {
            "ai": Option("I.A.", "Inteligência artificial, LLMs, ChatGPT, Claude, agentes."),
            "automation": Option(
                "Automação", "Automação de tarefas, no-code, n8n, Make, Zapier, bots."
            ),
            "programming": Option("Programação", "Código, linguagens, frameworks, carreira dev."),
            "marketing": Option("Marketing", "Redes sociais, conteúdo, tráfego, vendas online."),
            "business": Option("Negócios", "Empreendedorismo, renda, produto, gestão, freelas."),
            "productivity": Option("Produtividade", "Rotina, organização, ferramentas pessoais."),
            "other": Option("Outro", "Nenhum dos temas acima."),
        },
    ),
    "level": Question(
        "score",
        "Nível",
        "Para que nível de conhecimento este post foi escrito?",
        {
            "0": Option("Iniciante", "Iniciante: não supõe nenhum conhecimento prévio."),
            "1": Option("Intermediário", "Intermediário: supõe familiaridade com o tema."),
            "2": Option("Avançado", "Avançado: técnico, com jargão, para especialistas."),
        },
    ),
}

PROFILE_QUESTIONS: dict[str, Question] = {
    "audience": Question(
        "choice",
        "Público",
        "Para quem este perfil do Instagram fala, pela bio e pelas legendas recentes?",
        {
            "devs": Option("Devs e tech", "Programadores e profissionais de tecnologia."),
            "founders": Option(
                "Empreendedores", "Donos de negócio, freelancers e quem quer empreender."
            ),
            "marketers": Option(
                "Marketing", "Social medias, gestores de tráfego e profissionais de marketing."
            ),
            "creators": Option("Criadores", "Criadores de conteúdo e influenciadores."),
            "career": Option("Carreira", "Estudantes e quem quer entrar ou crescer na área."),
            "general": Option("Público geral", "Pessoas leigas, curiosas sobre o tema."),
        },
    ),
    "positioning": Question(
        "choice",
        "Discurso",
        "Como este perfil se posiciona diante do público?",
        {
            "teacher": Option("Professor", "Ensina de forma didática e generosa."),
            "expert": Option(
                "Especialista", "Autoridade técnica que mostra domínio e profundidade."
            ),
            "seller": Option("Vendedor", "Todo o conteúdo leva a um produto ou infoproduto."),
            "agency": Option("Prestador", "Agência ou prestador que mostra serviços e clientes."),
            "entertainer": Option("Entretenimento", "Humor, lifestyle e personalidade primeiro."),
            "curator": Option("Curador", "Notícias, novidades e listas de ferramentas."),
        },
    ),
    "monetization": Question(
        "choice",
        "Monetização",
        "Como este perfil parece ganhar dinheiro?",
        {
            "course": Option("Curso", "Vende curso, infoproduto, ebook ou comunidade paga."),
            "mentoring": Option("Mentoria", "Vende mentoria, consultoria ou aulas individuais."),
            "service": Option("Serviço", "Vende serviço, projeto sob medida ou agência."),
            "product": Option("Produto", "Vende SaaS, app, template ou produto próprio."),
            "sponsor": Option("Publis", "Publicidade, parcerias e afiliados."),
            "none": Option("Não aparente", "Não há monetização visível."),
        },
    ),
    "comment_to_dm": Question(
        "noul",
        "Usa comentário para DM",
        "O perfil pede, nas legendas, para comentar uma palavra-chave para receber algo por DM?",
        {
            "true": Option("Sim", "Há legendas do tipo 'comenta X que eu te mando'."),
            "false": Option("Não", "Nenhuma legenda pede isso."),
        },
    ),
    "depth": Question(
        "score",
        "Profundidade",
        "Quão profundo é o conteúdo deste perfil?",
        {
            "0": Option("Raso", "Raso: frases genéricas e dicas superficiais."),
            "1": Option("Médio", "Médio: dicas práticas com alguma explicação."),
            "2": Option("Profundo", "Profundo: explica o porquê, mostra detalhes e bastidores."),
        },
    ),
}


def taxonomy_out() -> dict[str, Any]:
    """The questions and labels, for the dashboard."""

    def group(questions: dict[str, Question]) -> list[dict[str, Any]]:
        return [
            {
                "key": key,
                "type": q.type,
                "label": q.label,
                "options": [{"key": k, "label": o.label} for k, o in q.options.items()],
            }
            for key, q in questions.items()
        ]

    return {
        "version": TAXONOMY_VERSION,
        "post": group(POST_QUESTIONS),
        "profile": group(PROFILE_QUESTIONS),
    }
