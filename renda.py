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
    ["curso", '"cursos profissionalizantes" gratuitos inscrições'],
    ["ia", '"inteligência artificial" curso gratuito'],
    ["vaga", '"home office" vagas abertas'],
    ["vaga", 'vagas emprego "inscrições abertas" empresa'],
]
CHAMADA = {
    "curso": "🎓 Aprender de graça e turbinar o currículo. Confira como se inscrever:",
    "vaga": "💼 Vaga para trabalhar de casa. Veja os requisitos e como se candidatar:",
    "renda": "💡 Mais uma ideia de renda extra para você avaliar com calma:",
    "golpe": "🚨 Fique atento e avise quem você ama. Saiba como o golpe funciona:",
    "ia": "🤖 Aprender IA agora é sair na frente no trabalho. Veja como participar:",
}
TAGS = {"curso": "#cursosgratuitos #certificado", "vaga": "#vagas #homeoffice",
        "renda": "#rendaextra #dinheiro", "golpe": "#golpe #seguranca",
        "ia": "#inteligenciaartificial #cursosgratuitos"}
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
    e = estado()
    feitos = set(e["postados"]) | set(e.get("pulados", []))
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
    p += {"curso": 2, "vaga": 2, "ia": 2, "renda": 1, "golpe": 0}.get(n["tema"], 0)
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
            if tema in ("curso", "vaga", "ia") and not ONLINE.search(t) and not NACIONAL.search(t):
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


NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/130 Safari/537.36"}


def link_real(link):
    """O Google Notícias esconde o endereço da matéria; isto pede o endereço verdadeiro ao próprio Google."""
    if "news.google.com" not in link or "/articles/" not in link:
        return link
    art = link.split("/articles/")[1].split("?")[0]
    pag = requests.get(f"https://news.google.com/articles/{art}", headers=NAVEGADOR, timeout=20).text
    sg = re.search(r'data-n-a-sg="([^"]+)"', pag).group(1)
    ts = re.search(r'data-n-a-ts="([^"]+)"', pag).group(1)
    req = [[["Fbv4je", json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
                                                    None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0,
                                                   None, 0], art, int(ts), sg])]]]
    r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", data={"f.req": json.dumps(req)},
                      headers={**NAVEGADOR, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=20)
    dados = json.loads(r.text.split("\n\n")[1])[:-2]
    return json.loads(dados[0][2])[1]


def foto_da_materia(url):
    """Endereço da imagem de capa que o próprio site da matéria indica (og:image)."""
    pag = requests.get(url, headers=NAVEGADOR, timeout=20).text
    for padrao in (r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
                   r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
                   r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(padrao, pag, re.I)
        if m and m.group(1).startswith("http"):
            return html.unescape(m.group(1))


def baixar_foto(url):
    r = requests.get(url, headers=NAVEGADOR, timeout=25)
    tipo = r.headers.get("Content-Type", "").split(";")[0]
    if r.ok and tipo.startswith("image/") and 8_000 < len(r.content) < 15_000_000:
        return r.content, tipo
    return None, None


def enviar_noticia(chat, n):
    """Sempre com imagem: capa do canal com a foto da matéria; sem foto, capa ilustrada; nunca só texto."""
    foto_url = foto = None
    tipo = "image/jpeg"
    try:
        n["link"] = link_real(n["link"])
    except Exception as erro:
        print(f"[notícia] não achei o link real: {erro}")
    try:
        foto_url = foto_da_materia(n["link"])
        if foto_url:
            foto, tipo = baixar_foto(foto_url)
    except Exception as erro:
        print(f"[notícia] não achei a foto da matéria: {erro}")
    legenda = texto_noticia(n)
    try:
        sys.path.insert(0, str(PASTA))
        import arte
        capa = PASTA / "capa-noticia.jpg"
        arte.capa_noticia(n["titulo"], n["tema"], n["fonte"], capa, foto, tipo or "image/jpeg")
        with open(capa, "rb") as f:
            return postador.tg("sendPhoto", arquivos={"photo": f}, chat_id=chat, caption=legenda,
                               parse_mode="HTML")["message_id"]
    except Exception as erro:
        print(f"[notícia] a capa falhou: {erro}")
    if foto_url:
        try:
            return postador.tg("sendPhoto", chat_id=chat, photo=foto_url, caption=legenda, parse_mode="HTML")["message_id"]
        except Exception as erro:
            print(f"[notícia] a foto do site falhou: {erro}")
    # último recurso: texto com a prévia grande do site em cima
    return postador.tg("sendMessage", chat_id=chat, text=legenda, parse_mode="HTML",
                       link_preview_options={"url": n["link"], "prefer_large_media": True,
                                             "show_above_text": True})["message_id"]


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


# ---------------- prévia com botões ----------------
# Quem escuta os cliques é o robô do PC (painel.py). Quem posta no horário é a nuvem.
# Por isso, depois de um clique, o estado.json vai para o GitHub: a nuvem não repete nem posta o que foi tirado.
ESPELHO = Path(__file__).parent / "nuvem-espelho.git"


def _git(*args, entrada=None, env=None):
    import os
    import subprocess
    r = subprocess.run(["git", f"--git-dir={ESPELHO}", *args], input=entrada, capture_output=True,
                       encoding="utf-8", timeout=90, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout.strip()


def mudar_estado_nuvem(mudar, msg):
    """Aplica 'mudar' no estado.json do GitHub (e no do PC) e manda o commit. Tenta 3 vezes."""
    import os
    import tempfile
    ultimo_erro = None
    for _ in range(3):
        try:
            _git("fetch", "-q", "origin", "main")
            base = _git("rev-parse", "FETCH_HEAD")
            e = json.loads(_git("show", "FETCH_HEAD:renda/estado.json"))
            for k in ("postados", "noticias", "pulados"):
                e.setdefault(k, [])
            e.setdefault("contador", 0)
            mudar(e)
            e["noticias"] = e["noticias"][-400:]
            texto = json.dumps(e, ensure_ascii=False, indent=2)
            blob = _git("hash-object", "-w", "--stdin", entrada=texto)
            indice = Path(tempfile.gettempdir()) / "renda-indice"
            idx = {"GIT_INDEX_FILE": str(indice)}
            _git("read-tree", base, env=idx)
            _git("update-index", "--cacheinfo", f"100644,{blob},renda/estado.json", env=idx)
            arvore = _git("write-tree", env=idx)
            quem = {"GIT_AUTHOR_NAME": "robo-pc", "GIT_AUTHOR_EMAIL": "robo-pc@users.noreply.github.com",
                    "GIT_COMMITTER_NAME": "robo-pc", "GIT_COMMITTER_EMAIL": "robo-pc@users.noreply.github.com"}
            commit = _git("commit-tree", arvore, "-p", base, "-m", msg, env=quem)
            _git("push", "-q", "origin", f"{commit}:refs/heads/main")
            ESTADO.write_text(texto, encoding="utf-8")
            return e
        except Exception as erro:
            ultimo_erro = erro
            print(f"[renda] não consegui mandar o estado para a nuvem: {erro}")
    raise RuntimeError(f"não consegui avisar a nuvem ({ultimo_erro})")


def _botoes(tipo, chave_):
    return {"inline_keyboard": [[{"text": "✅ Postar agora no canal", "callback_data": f"r:{tipo}p:{chave_}"},
                                 {"text": "🗑 Tirar da fila", "callback_data": f"r:{tipo}x:{chave_}"}]]}


def previa(chat, n=3):
    """Manda as próximas dicas e a próxima notícia para o privado, cada uma com os botões de postar ou tirar."""
    postador.tg("sendMessage", chat_id=chat, text="👀 Próximos posts do renda extra. Se não mexer, eles saem "
                "sozinhos nos horários. Use os botões embaixo de cada um para postar agora ou tirar da fila.")
    for item in proximas(n):
        enviar_dica(chat, item)
        postador.tg("sendMessage", chat_id=chat, text=f"👆 Dica {item['id'][:3]}: {item['titulo']}",
                    reply_markup=_botoes("d", item["id"][:40]))
    noticias = garimpar_noticias()
    if noticias:
        n = noticias[0]
        enviar_noticia(chat, n)
        e = estado()
        e["previa_noticia"] = n  # guarda a notícia para o botão postar exatamente esta
        e["previa_noticia"]["quando"] = str(n.get("quando", ""))
        salvar_estado(e)
        postador.tg("sendMessage", chat_id=chat, text=f"👆 Notícia: {n['titulo'][:200]}",
                    reply_markup=_botoes("n", chave(n["titulo"])[:40]))


def tratar_botao(cb, arg):
    """Botões da prévia. arg: 'dp:ID' posta a dica, 'dx:ID' tira, 'np:CHAVE' posta a notícia, 'nx:CHAVE' tira."""
    acao, _, alvo = arg.partition(":")
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        if acao == "dp":
            item = next((i for i in banco() if i["id"].startswith(alvo)), None)
            if not item:
                return postador.trocar_botoes(cb, "Não achei essa dica")
            _git("fetch", "-q", "origin", "main")
            nuvem = json.loads(_git("show", "FETCH_HEAD:renda/estado.json"))
            if item["id"] in nuvem.get("postados", []) + nuvem.get("pulados", []):
                return postador.trocar_botoes(cb, "Essa dica já saiu ou foi tirada")
            postador.trocar_botoes(cb, "⏳ Postando...")
            enviar_dica(canal(), item)

            def marcar(e):
                e["postados"].append(item["id"]); e["contador"] += 1; e["ultimo"] = agora
            mudar_estado_nuvem(marcar, f"robô PC: dica {item['id'][:3]} postada pelo botão")
            postador.trocar_botoes(cb, "✅ Postada no canal")
        elif acao == "dx":
            mudar_estado_nuvem(lambda e: e["pulados"].append(alvo) if alvo not in e["pulados"] else None,
                               f"robô PC: dica {alvo[:3]} tirada da fila")
            postador.trocar_botoes(cb, "🗑 Tirada da fila")
        elif acao in ("np", "nx"):
            n = estado().get("previa_noticia")
            if not n or chave(n["titulo"])[:40] != alvo:
                return postador.trocar_botoes(cb, "Essa prévia de notícia é antiga")
            if acao == "np":
                postador.trocar_botoes(cb, "⏳ Postando...")
                enviar_noticia(canal(), n)

            def marcar_noticia(e):
                e["noticias"].append(chave(n["titulo"]))
                if acao == "np":
                    e["ultimo"] = agora
            mudar_estado_nuvem(marcar_noticia, "robô PC: notícia " + ("postada" if acao == "np" else "tirada"))
            postador.trocar_botoes(cb, "✅ Postada no canal" if acao == "np" else "🗑 Tirada da fila")
    except Exception as erro:
        print(f"[renda] botão falhou: {erro}")
        postador.trocar_botoes(cb, "⚠️ Não consegui. Tente de novo")


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
