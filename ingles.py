"""Canal de inglês (@inglescomia): posta o próximo item do banco ingles/posts.json.

Tipos de item:
  palavra  -> imagem + legenda (palavra/expressão do dia)
  aula     -> imagem + legenda (mini-aula)
  quiz     -> enquete-quiz do Telegram (mostra a resposta certa e a explicação)

    python ingles.py proximo            -> posta o próximo no canal
    python ingles.py previa 185039128   -> manda os próximos 6 só para esse chat (não marca como postado)

De vez em quando (a cada N posts, config "ingles.curso_a_cada") a legenda ganha o convite do curso.
"""
import html
import json
import sys
from pathlib import Path

import postador

PASTA = Path(__file__).parent / "ingles"
BANCO = PASTA / "posts.json"
ESTADO = PASTA / "estado.json"
CFG = postador.CONFIG.get("ingles", {})


def banco():
    return json.loads(BANCO.read_text(encoding="utf-8")) if BANCO.exists() else []


def estado():
    return json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {"postados": [], "contador": 0}


def salvar_estado(e):
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def convite_curso():
    link = CFG.get("link_curso")
    if not link:
        return ""
    e = html.escape
    return (f'\n\n🇺🇸 <b>Quer falar inglês de verdade?</b> Curso completo com 9 e-books + professor de IA '
            f'por R$ 29,90 👉 <a href="{e(link, quote=True)}"><b>QUERO APRENDER</b></a>')


def assinatura():
    return f"\n\n📲 {CFG.get('canal', '@inglescomia')}"


def enviar(chat, item, com_curso=False):
    """Manda um item. Devolve o message_id."""
    if item["tipo"] == "quiz":
        q = item["quiz"]
        r = postador.tg("sendPoll", chat_id=chat, question=q["pergunta"], options=q["opcoes"],
                        type="quiz", correct_option_id=q["correta"], explanation=q.get("explicacao", "")[:200],
                        is_anonymous=True)
        if com_curso and convite_curso():  # enquete não tem legenda: o convite vai numa mensagem depois
            postador.tg("sendMessage", chat_id=chat, text=convite_curso().strip(), parse_mode="HTML",
                        link_preview_options={"is_disabled": True})
        return r["message_id"]
    legenda = item["texto"] + (convite_curso() if com_curso else "") + assinatura()
    imagem = PASTA / item["imagem"] if item.get("imagem") else None
    if imagem and imagem.exists():
        with open(imagem, "rb") as f:
            r = postador.tg("sendPhoto", arquivos={"photo": f}, chat_id=chat, caption=legenda, parse_mode="HTML")
    else:
        r = postador.tg("sendMessage", chat_id=chat, text=legenda, parse_mode="HTML",
                        link_preview_options={"is_disabled": True})
    return r["message_id"]


def proximos(n=1):
    feitos = set(estado()["postados"])
    return [i for i in banco() if i["id"] not in feitos][:n]


def postar_proximo():
    itens = proximos(1)
    if not itens:
        postador.avisar("🇺🇸 O banco de posts de inglês acabou. Peça ao Claude um lote novo.")
        print("Banco de inglês vazio.")
        return False
    e = estado()
    e["contador"] = e.get("contador", 0) + 1
    com_curso = e["contador"] % int(CFG.get("curso_a_cada", 5)) == 0
    mid = enviar(CFG.get("canal", "@inglescomia"), itens[0], com_curso)
    e["postados"].append(itens[0]["id"])
    salvar_estado(e)
    print(f"Inglês postado: {itens[0]['id']} (mensagem {mid})")
    restam = len(proximos(10**6))
    if restam in (9, 3):  # avisa antes de acabar (3 posts por dia)
        postador.avisar(f"🇺🇸 Restam {restam} posts de inglês no banco. Peça ao Claude um lote novo.")
    return True


def previa(chat, n=6):
    for i, item in enumerate(proximos(n), 1):
        enviar(chat, item, com_curso=(i == n))


if __name__ == "__main__":
    postador.carregar_env()
    acao = sys.argv[1] if len(sys.argv) > 1 else "proximo"
    if acao == "previa":
        previa(sys.argv[2] if len(sys.argv) > 2 else postador.estado().get("admin"))
    else:
        postar_proximo()
