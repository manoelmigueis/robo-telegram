"""Canal de renda extra (@rendaextraparatodos): dicas do banco + notícias garimpadas.

Grade do dia (Manaus), no config.json -> "renda" -> "grade":
  dica    -> próximo item de renda/posts.json (arte própria + texto rico, ou enquete-quiz)
  noticia -> garimpa o Google Notícias (cursos grátis, vagas home office, renda extra, alertas de golpe)
             e posta a mais nova que ainda não saiu, com o link

    python renda.py agendado           -> posta só se um horário da grade venceu sem post (usado pela nuvem)
    python renda.py dica               -> posta a próxima dica agora
    python renda.py noticia            -> posta uma notícia agora
    python renda.py previa 185039128   -> manda 3 dicas e 1 notícia só para esse chat (não marca como postado)
    python renda.py garimpar           -> só mostra as notícias encontradas, sem postar

Artes: renda/arte.py. Estilo dos lotes: renda/ESTILO.md
"""
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import requests

import postador

PASTA = Path(__file__).parent / "renda"
BANCO = PASTA / "posts.json"
ESTADO = PASTA / "estado.json"
CFG = postador.CONFIG.get("renda", {})
FUSO = timezone(timedelta(hours=-4))  # Manaus

# Buscas do garimpo de notícias: (tema, busca no Google Notícias)
BUSCAS = CFG.get("buscas") or [
    ["curso", '"cursos gratuitos" certificado online'],
    ["vaga", '"home office" vagas abertas'],
    ["vaga", '"trabalho remoto" vagas inscrições'],
    ["renda", '"renda extra" como ganhar'],
    ["golpe", 'golpe "renda extra" OR "falsa vaga" alerta'],
]
CHAMADA = {
    "curso": "🎓 Aprender de graça e turbinar o currículo. Confira como se inscrever:",
    "vaga": "💼 Vaga para trabalhar de casa. Veja os requisitos e como se candidatar:",
    "renda": "💡 Mais uma ideia de renda extra para você avaliar com calma:",
    "golpe": "🚨 Fique atento e avise quem você ama. Saiba como o golpe funciona:",
}
TAGS = {"curso": "#cursosgratuitos #certificado", "vaga": "#vagas #homeoffice",
        "renda": "#rendaextra #dinheiro", "golpe": "#golpe #seguranca"}
# Nada de aposta, cassino, pirâmide ou promessa de dinheiro fácil no canal.
PROIBIDO = re.compile(r"\b(bet|bets|aposta\w*|cassino|tigrinho|jogo do bicho|loteria|mega-sena|pir[âa]mide|"
                      r"cripto\w*|bitcoin|forex|day ?trade|sem sair de casa ganhe|ganhe r\$)\b", re.I)


# ---------------- estado e banco ----------------
def banco():
    return json.loads(BANCO.read_text(encoding="utf-8")) if BANCO.exists() else []


def estado():
    e = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    e.setdefault("postados", []); e.setdefault("noticias", []); e.setdefault("contador", 0)
    return e


def salvar_estado(e):
    e["noticias"] = e["noticias"][-400:]
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def canal():
    return CFG.get("canal", "@rendaextraparatodos")


def proximas(n=1):
    feitos = set(estado()["postados"])
    return [i for i in banco() if i["id"] not in feitos][:n]


# ---------------- envio das dicas (mesmo formato rico do canal de inglês) ----------------
def hashtags(item):
    return " ".join("#" + t.lstrip("#") for t in item.get("tags", [])[:5])


def botao_canal():
    return f'<tg-button type="url" url="https://t.me/{canal().lstrip("@")}">📲 {html.escape(canal())}</tg-button>'


def enviar_dica(chat, item):
    if item["tipo"] == "quiz":
        return enviar_quiz(chat, item)
    img = PASTA / item["imagens"][0] if item.get("imagens") else None
    tem_img = bool(img and img.exists())
    corpo = (f"<h2>{html.escape(item['titulo'])}</h2>" + ('<img src="tg://photo?id=f0"/>' if tem_img else "")
             + item.get("corpo", "") + f"<p>{html.escape(hashtags(item))}</p>"
             + f'<tg-button-row align="center">{botao_canal()}</tg-button-row>')
    try:
        with ExitStack() as pilha:
            arquivos = {"f0": pilha.enter_context(open(img, "rb"))} if tem_img else None
            rico = {"html": corpo}
            if tem_img:
                rico["media"] = [{"id": "f0", "media": {"type": "photo", "media": "attach://f0"}}]
            return postador.tg("sendRichMessage", arquivos=arquivos, chat_id=chat, rich_message=rico)["message_id"]
    except RuntimeError as erro:  # plano B: foto com legenda
        print(f"Post rico falhou ({erro}); mandando foto com legenda.")
    texto = re.sub(r"<details><summary>(.*?)</summary>", r"\n\1\n", item.get("corpo", ""))
    texto = re.sub(r"<br\s*/?>|</p>", "\n", texto)
    texto = re.sub(r"<(?!/?b>)[^>]+>", "", texto).strip()
    legenda = f"<b>{html.escape(item['titulo'])}</b>\n\n{texto}\n\n{html.escape(hashtags(item))}\n📲 {canal()}"
    if not tem_img:
        return postador.tg("sendMessage", chat_id=chat, text=legenda[:4000], parse_mode="HTML")["message_id"]
    with open(img, "rb") as f:
        return postador.tg("sendPhoto", arquivos={"photo": f}, chat_id=chat, caption=legenda[:1024],
                           parse_mode="HTML")["message_id"]


def enviar_quiz(chat, item):
    q = item["quiz"]
    img = PASTA / item["imagens"][0] if item.get("imagens") else None
    desc = (q.get("descricao", "") + "\n" + hashtags(item)).strip()
    base = dict(chat_id=chat, question=q["pergunta"], options=[{"text": o} for o in q["opcoes"]],
                type="quiz", explanation=q.get("explicacao", "")[:200], is_anonymous=True)
    try:
        with ExitStack() as pilha:
            arquivos, extra = {}, {"correct_option_ids": [q["correta"]], "description": desc[:1024]}
            if img and img.exists():
                arquivos["capa"] = pilha.enter_context(open(img, "rb"))
                extra["media"] = {"type": "photo", "media": "attach://capa"}
            return postador.tg("sendPoll", arquivos=arquivos or None, **base, **extra)["message_id"]
    except RuntimeError as erro:
        print(f"Enquete rica falhou ({erro}); mandando a simples.")
        return postador.tg("sendPoll", **base, correct_option_id=q["correta"])["message_id"]


# ---------------- garimpo de notícias ----------------
def buscar(tema, busca, dias=3):
    url = (f"https://news.google.com/rss/search?q={quote_plus(busca + f' when:{dias}d')}"
           "&hl=pt-BR&gl=BR&ceid=BR:pt-419")
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    achadas = []
    for it in ET.fromstring(r.content).iter("item"):
        titulo = (it.findtext("title") or "").strip()
        fonte = (it.findtext("source") or "").strip()
        if fonte and titulo.endswith(" - " + fonte):
            titulo = titulo[: -len(fonte) - 3].strip()
        try:
            quando = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            continue
        achadas.append({"tema": tema, "titulo": titulo, "fonte": fonte, "link": it.findtext("link"), "quando": quando})
    return achadas


def chave(titulo):
    return re.sub(r"\W+", " ", titulo.lower()).strip()[:80]


ONLINE = re.compile(r"\b(online|on-line|ead|a dist[âa]ncia|remot[oa]s?|home office|digital|internet|todo o brasil|todo o pa[íi]s)\b", re.I)
NACIONAL = re.compile(r"\b(mec|cisco|fiocruz|sebrae|senai|senac|senar|google|microsoft|amazon|ibm|fgv|usp|unicamp|"
                      r"fundação bradesco|instituto federal|ifs?|etecs?|enap|governo federal|ministério|"
                      r"escola virtual|coursera|nubank|itaú|magalu|natura|ifood|mercado livre)\b", re.I)
LOCAL = re.compile(r"\b(prefeitura|munic[íi]pio|secretaria municipal|bairro|vereador|c[âa]mara municipal|morador)\b", re.I)


def palavras(texto):
    return {w for w in re.findall(r"\w{4,}", texto.lower())}


def parecida(k, outras):
    a = palavras(k)
    return any(len(a & palavras(o)) / max(len(a | palavras(o)), 1) >= 0.4 for o in outras)


def pontos(n):
    t = n["titulo"]
    p = 3 * bool(ONLINE.search(t)) + 3 * bool(NACIONAL.search(t)) - 4 * bool(LOCAL.search(t))
    p += {"curso": 2, "vaga": 2, "renda": 1, "golpe": 0}.get(n["tema"], 0)
    horas = (datetime.now(timezone.utc) - n["quando"]).total_seconds() / 3600
    return p - horas / 24  # perde 1 ponto por dia de idade


def garimpar_noticias():
    """Notícias novas, boas e ainda não postadas, da melhor para a pior."""
    ja = estado()["noticias"]
    escolhidas = []
    for tema, busca in BUSCAS:
        try:
            achadas = buscar(tema, busca)
        except Exception as erro:
            print(f"[notícias] '{busca}' falhou: {erro}")
            continue
        for n in achadas:
            t = n["titulo"]
            if PROIBIDO.search(t) or len(t) < 25:
                continue
            if tema in ("curso", "vaga") and not ONLINE.search(t) and not NACIONAL.search(t):
                continue  # curso ou vaga presencial de uma cidade só não serve para o Brasil todo
            n["pontos"] = pontos(n)
            escolhidas.append(n)
    escolhidas.sort(key=lambda n: n["pontos"], reverse=True)
    boas, vistas = [], list(ja[-200:])
    for n in escolhidas:
        k = chave(n["titulo"])
        if k in ja or parecida(k, vistas):
            continue
        vistas.append(k)
        boas.append(n)
    return boas


def texto_noticia(n):
    e = html.escape
    return (f"📰 <b>{e(n['titulo'])}</b>\n\n{CHAMADA.get(n['tema'], CHAMADA['renda'])}\n"
            f"👉 <a href=\"{e(n['link'], quote=True)}\">Ler a notícia completa</a>"
            + (f"\n<i>Fonte: {e(n['fonte'])}</i>" if n["fonte"] else "")
            + f"\n\n{TAGS.get(n['tema'], '#rendaextra')} #rendaextra\n📲 {canal()}")


def enviar_noticia(chat, n):
    return postador.tg("sendMessage", chat_id=chat, text=texto_noticia(n), parse_mode="HTML",
                       link_preview_options={"url": n["link"], "prefer_large_media": True})["message_id"]


# ---------------- postar ----------------
def postar_dica():
    itens = proximas(1)
    if not itens:
        postador.avisar("💵 O banco de dicas de renda extra acabou. Peça ao Claude um lote novo.")
        print("Banco de dicas vazio; postando uma notícia no lugar.")
        return postar_noticia(sem_dica=True)
    e = estado()
    mid = enviar_dica(canal(), itens[0])
    e["postados"].append(itens[0]["id"]); e["contador"] += 1
    e["ultimo"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    salvar_estado(e)
    print(f"Dica postada: {itens[0]['id']} (mensagem {mid})")
    restam = len(proximas(10**6))
    if restam in (9, 3):
        postador.avisar(f"💵 Restam {restam} dicas de renda extra no banco. Peça ao Claude um lote novo.")
    return True


def postar_noticia(sem_dica=False):
    noticias = garimpar_noticias()
    e = estado()
    if not noticias:
        print("Nenhuma notícia nova." + ("" if sem_dica else " Postando uma dica no lugar."))
        return False if sem_dica else postar_dica()
    n = noticias[0]
    mid = enviar_noticia(canal(), n)
    e["noticias"].append(chave(n["titulo"]))
    e["ultimo"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    salvar_estado(e)
    print(f"Notícia postada ({n['tema']}): {n['titulo']} (mensagem {mid})")
    return True


def grade():
    return CFG.get("grade") or {"08:00": "dica", "12:00": "dica", "15:00": "noticia", "19:00": "dica"}


def horario_devido(agora=None):
    """(horário, tipo) do último horário da grade que venceu sem post, ou None. Igual ao do inglês."""
    agora = (agora or datetime.now(timezone.utc)).astimezone(FUSO)
    vencidos = []
    for dia in (agora.date() - timedelta(days=1), agora.date()):
        for h, tipo in grade().items():
            hh, mm = map(int, h.split(":"))
            g = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=FUSO)
            if g <= agora:
                vencidos.append((g, tipo))
    if not vencidos:
        return None
    alvo = max(vencidos)
    ultimo = estado().get("ultimo")
    if ultimo and datetime.fromisoformat(ultimo) >= alvo[0]:
        return None
    if agora - alvo[0] > timedelta(hours=6):
        return None
    return alvo


def postar_se_devido():
    devido = horario_devido()
    if not devido:
        print("Nenhum horário de renda extra vencido agora.")
        return False
    print(f"Horário vencido: {devido[0]:%d/%m %H:%M} (Manaus), {devido[1]}.")
    return postar_noticia() if devido[1] == "noticia" else postar_dica()


def previa(chat, n=3):
    postador.tg("sendMessage", chat_id=chat, text="👀 Prévia do canal de renda extra. Não precisa fazer nada: "
                "já está na agenda e sai sozinho no canal.")
    for item in proximas(n):
        enviar_dica(chat, item)
    noticias = garimpar_noticias()
    if noticias:
        enviar_noticia(chat, noticias[0])


if __name__ == "__main__":
    postador.carregar_env()
    acao = sys.argv[1] if len(sys.argv) > 1 else "agendado"
    if acao == "previa":
        previa(sys.argv[2] if len(sys.argv) > 2 else postador.estado().get("admin"))
    elif acao == "garimpar":
        for n in garimpar_noticias()[:15]:
            print(f"{n['pontos']:5.1f} [{n['tema']}] {n['quando']:%d/%m %H:%M} {n['titulo']} ({n['fonte']})")
    elif acao == "dica":
        postar_dica()
    elif acao == "noticia":
        postar_noticia()
    else:
        postar_se_devido()
