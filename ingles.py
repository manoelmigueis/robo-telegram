"""Canal de inglês (@inglescomia): posta o próximo item do banco ingles/posts.json.

Formato rico do Telegram (sendRichMessage, Bot API 10.1+):
  palavra / aula / lista -> título + carrossel de imagens (slideshow) + texto com tabela,
                            "toque para ver" (details) + hashtags + botão do curso
  quiz                   -> enquete-quiz com imagem e descrição com hashtags

Campos de cada item em posts.json:
  id, tipo, titulo, imagens [..], corpo (HTML rico), tags [3 a 5, sem #]
  quiz: {pergunta, opcoes, correta, explicacao, descricao}

    python ingles.py proximo            -> posta o próximo no canal (agora, sem olhar a hora)
    python ingles.py agendado           -> só posta se um horário da grade venceu sem post (usado pela nuvem)
    python ingles.py previa 185039128   -> manda os próximos 6 só para esse chat (não marca como postado)

Estilo e regras de conteúdo: ingles/ESTILO.md
"""
import html
import json
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
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


def canal():
    return CFG.get("canal", "@inglescomia")


def hashtags(item):
    return " ".join("#" + t.lstrip("#") for t in item.get("tags", [])[:5])


def link_curso():
    return CFG.get("link_curso") or ""


def html_rico(item, ids, com_curso):
    """Monta o HTML do sendRichMessage. ids = ids das fotos anexadas, na ordem."""
    e = html.escape
    partes = [f"<h2>{e(item['titulo'])}</h2>"]
    if len(ids) > 1:
        fotos = "".join(f'<img src="tg://photo?id={i}"/>' for i in ids)
        partes.append(f"<tg-slideshow>{fotos}<figcaption>Arraste para o lado 👉</figcaption></tg-slideshow>")
    elif ids:
        partes.append(f'<img src="tg://photo?id={ids[0]}"/>')
    partes.append(item.get("corpo", ""))
    if item.get("tags"):
        partes.append(f"<p>{e(hashtags(item))}</p>")
    botoes = [f'<tg-button type="url" url="https://t.me/{canal().lstrip("@")}">📲 {e(canal())}</tg-button>']
    if com_curso and link_curso():
        partes.append("<p>🇺🇸 <b>Quer falar inglês de verdade?</b> Curso completo com 9 e-books + professor de IA por R$ 29,90.</p>")
        botoes.insert(0, f'<tg-button type="url" style="success" url="{e(link_curso(), quote=True)}">QUERO APRENDER</tg-button>')
    partes.append(f'<tg-button-row align="center">{"".join(botoes)}</tg-button-row>')
    return "\n".join(partes)


def legenda_simples(item, com_curso):
    """Plano B (se o formato rico falhar): legenda curta no estilo antigo do canal."""
    e = html.escape
    txt = f"<b>{e(item['titulo'])}</b>"
    if item.get("tags"):
        txt += f"\n\n{e(hashtags(item))}"
    if com_curso and link_curso():
        txt += f'\n\n🇺🇸 Curso completo de inglês 👉 <a href="{e(link_curso(), quote=True)}"><b>QUERO APRENDER</b></a>'
    return txt + f"\n\n📲 {canal()}"


def enviar_quiz(chat, item, com_curso):
    q = item["quiz"]
    img = PASTA / item["imagens"][0] if item.get("imagens") else None
    desc = (q.get("descricao", "") + ("\n" + hashtags(item) if item.get("tags") else "")).strip()
    base = dict(chat_id=chat, question=q["pergunta"], options=[{"text": o} for o in q["opcoes"]],
                type="quiz", explanation=q.get("explicacao", "")[:200], is_anonymous=True)
    try:
        with ExitStack() as pilha:
            arquivos = {}
            extra = {"correct_option_ids": [q["correta"]], "description": desc[:1024]}
            if img and img.exists():
                arquivos["capa"] = pilha.enter_context(open(img, "rb"))
                extra["media"] = {"type": "photo", "media": "attach://capa"}
            r = postador.tg("sendPoll", arquivos=arquivos or None, **base, **extra)
    except RuntimeError as erro:  # plano B: enquete simples
        print(f"Enquete rica falhou ({erro}); mandando a simples.")
        r = postador.tg("sendPoll", **base, correct_option_id=q["correta"])
    if com_curso and link_curso():
        postador.tg("sendMessage", chat_id=chat, parse_mode="HTML", link_preview_options={"is_disabled": True},
                    text=f'🇺🇸 <b>Quer falar inglês de verdade?</b> Curso completo com 9 e-books + professor de IA '
                         f'por R$ 29,90 👉 <a href="{html.escape(link_curso(), quote=True)}"><b>QUERO APRENDER</b></a>')
    return r["message_id"]


def enviar(chat, item, com_curso=False):
    """Manda um item. Devolve o message_id."""
    if item["tipo"] == "quiz":
        return enviar_quiz(chat, item, com_curso)
    imagens = [PASTA / i for i in item.get("imagens", []) if (PASTA / i).exists()]
    ids = [f"f{n}" for n in range(len(imagens))]
    try:
        with ExitStack() as pilha:
            arquivos = {i: pilha.enter_context(open(p, "rb")) for i, p in zip(ids, imagens)}
            media = [{"id": i, "media": {"type": "photo", "media": f"attach://{i}"}} for i in ids]
            rico = {"html": html_rico(item, ids, com_curso)}
            if media:
                rico["media"] = media
            r = postador.tg("sendRichMessage", arquivos=arquivos or None, chat_id=chat, rich_message=rico)
        return r["message_id"]
    except RuntimeError as erro:  # plano B: foto (ou álbum) com legenda curta
        print(f"Post rico falhou ({erro}); mandando no formato simples.")
    legenda = legenda_simples(item, com_curso)
    if not imagens:
        return postador.tg("sendMessage", chat_id=chat, text=legenda, parse_mode="HTML")["message_id"]
    with ExitStack() as pilha:
        arquivos = {f"a{n}": pilha.enter_context(open(p, "rb")) for n, p in enumerate(imagens)}
        if len(imagens) == 1:
            return postador.tg("sendPhoto", arquivos={"photo": arquivos["a0"]}, chat_id=chat,
                               caption=legenda, parse_mode="HTML")["message_id"]
        grupo = [{"type": "photo", "media": f"attach://a{n}"} for n in range(len(imagens))]
        grupo[0].update(caption=legenda, parse_mode="HTML")
        return postador.tg("sendMediaGroup", arquivos=arquivos, chat_id=chat, media=grupo)[0]["message_id"]


def proximos(n=1):
    import central  # fila da central: respeita "pôr como próximo", tirados e agendados
    return central.fila("ingles", estado(), banco())[:n]


def horario_devido(agora=None):
    """Último horário da grade (Manaus) que já passou e ainda não foi postado, ou None.

    O agendamento do GitHub atrasa e às vezes pula execuções. Por isso a nuvem roda de hora
    em hora e só posta quando um horário venceu sem post: um horário perdido sai na rodada seguinte.
    """
    fuso = timezone(timedelta(hours=-4))  # Manaus, sem horário de verão
    agora = (agora or datetime.now(timezone.utc)).astimezone(fuso)
    grade = []
    for dia in (agora.date() - timedelta(days=1), agora.date()):
        for h in CFG.get("horarios", ["09:00", "13:00", "19:00"]):
            hh, mm = map(int, h.split(":"))
            grade.append(datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=fuso))
    vencidos = [g for g in grade if g <= agora]
    if not vencidos:
        return None
    alvo = max(vencidos)
    ultimo = estado().get("ultimo")
    if ultimo and datetime.fromisoformat(ultimo) >= alvo:
        return None
    if agora - alvo > timedelta(hours=6):  # tão atrasado que já é madrugada: espera o próximo
        return None
    return alvo


def postar_se_devido():
    import central
    if central.postar_agendados("ingles"):  # agendados pela central saem primeiro
        return True
    alvo = horario_devido()
    if not alvo:
        print("Nenhum horário de inglês vencido agora.")
        return False
    print(f"Horário vencido: {alvo:%d/%m %H:%M} (Manaus).")
    return postar_proximo()


def postar_proximo():
    import central  # posta e já grava no GitHub (com o número da mensagem, para poder apagar depois)
    item, mid = central.postar("ingles")
    if not item:
        postador.avisar("🇺🇸 O banco de posts de inglês acabou. Peça ao Claude um lote novo.")
        print("Banco de inglês vazio.")
        return False
    print(f"Inglês postado: {item['id']} (mensagem {mid})")
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
    elif acao == "agendado":
        postar_se_devido()
    else:
        postar_proximo()
