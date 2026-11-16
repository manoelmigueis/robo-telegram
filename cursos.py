"""Canal de cursos grátis (@cursosgratuitosudemy): cursos da Udemy que estão de graça hoje, com cupom.

De onde vêm: o site Couponami (antigo DiscUdemy) lista os cupons de 100% que saíram hoje.
O robô pega o link real da Udemy com o cupom, a foto e a descrição do curso, e a IA (Claude ou a grátis)
escreve 2 ou 3 frases em português dizendo o que a pessoa aprende. Nada é inventado: sem link com cupom, não posta.

Horários (Manaus) no config.json -> "cursos" -> "horarios".

    python cursos.py agendado   -> posta só se um horário venceu sem post (usado pela nuvem)
    python cursos.py agora      -> posta um curso agora
    python cursos.py garimpar   -> só mostra os cursos encontrados, sem postar
"""
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import postador

PASTA = Path(__file__).parent / "cursos"
ESTADO = PASTA / "estado.json"
CFG = postador.CONFIG.get("cursos", {})
FUSO = timezone(timedelta(hours=-4))  # Manaus
SITE = "https://www.couponami.com"
# Português primeiro, depois espanhol e inglês (dá para mudar no config, em "listas").
LISTAS = CFG.get("listas") or [["Português", "/language/Portuguese"], ["Espanhol", "/language/Spanish"],
                               ["Inglês", "/language/English"]]
# Igual ao canal de livros: curso de fora sai 100% no idioma dele (descrição, idioma, gatilho, botão e hashtags).
LINGUAS = {
    "Português": {"selo": "🇧🇷 Português", "gratis": "GRÁTIS POR TEMPO LIMITADO", "botao": "🎓 Pegar o curso grátis",
                  "escrever": "português do Brasil", "fixas": ["#cursosgratis", "#cursogratuito", "#udemy", "#cursosonline"],
                  "exemplo": "um curso de TikTok Shop -> #tiktokshop #vendasonline #ecommerce; de Excel -> #excel #informatica #planilhas"},
    "Espanhol": {"selo": "🇪🇸 Español", "gratis": "GRATIS POR TIEMPO LIMITADO", "botao": "🎓 Obtener el curso gratis",
                 "escrever": "espanhol (español neutro, de América Latina)",
                 "fixas": ["#cursosgratis", "#cursogratuito", "#udemy", "#cursosenlinea"],
                 "exemplo": "um curso de TikTok Shop -> #tiktokshop #ventasonline #ecommerce; de Excel -> #excel #informatica #hojasdecalculo"},
    "Inglês": {"selo": "🇺🇸 English", "gratis": "FREE FOR A LIMITED TIME", "botao": "🎓 Get the course for free",
               "escrever": "inglês (English)", "fixas": ["#freecourses", "#freecourse", "#udemy", "#onlinecourses"],
               "exemplo": "um curso de TikTok Shop -> #tiktokshop #onlineselling #ecommerce; de Excel -> #excel #computerskills #spreadsheets"},
}


def lingua(c):
    return LINGUAS.get(c.get("idioma"), LINGUAS["Português"])
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
NOVOS = ("today", "yesterday")  # cupom de curso grátis acaba em poucos dias: só os de hoje e ontem


def canal():
    return CFG.get("canal", "@cursosgratuitosudemy")


def horarios():
    return CFG.get("horarios") or ["09:00", "12:00", "15:00", "18:00", "21:00"]


def estado():
    e = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    e.setdefault("postados", [])
    return e


def salvar_estado(e):
    e["postados"] = e["postados"][-1000:]
    PASTA.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def _baixar(caminho):
    url = caminho if caminho.startswith("http") else SITE + caminho
    r = requests.get(url, headers=NAVEGADOR, timeout=25)
    r.raise_for_status()
    return r.text


def _limpo(t):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or ""))).strip()


# ---------------- garimpo ----------------
def garimpar():
    """Cursos grátis de hoje e ontem que ainda não saíram no canal, português primeiro."""
    feitos = set(estado()["postados"])
    achados = []
    for idioma, caminho in LISTAS:
        try:
            pagina = _baixar(caminho)
        except Exception as erro:
            print(f"[cursos] não consegui abrir {caminho}: {erro}")
            continue
        for bloco in pagina.split('<section class="card">')[1:]:
            link = re.search(r'class="card-header" href="([^"]+)"[^>]*>(.*?)</a>', bloco, re.S)
            if not link:
                continue
            url, titulo = link.group(1), _limpo(link.group(2))
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            quando = _limpo((re.search(r'class="category">(.*?)</span>', bloco, re.S) or [None, ""])[1]).lower()
            gratis = "free" in bloco.lower()
            if slug in feitos or not gratis or (quando and quando not in NOVOS):
                continue
            achados.append({"slug": slug, "titulo": titulo, "pagina": url, "idioma": idioma})
    return achados


def detalhes(c):
    """Completa o curso com o link da Udemy (com o cupom), a foto e a descrição. None se não achar o cupom."""
    try:
        ida = _baixar(f"/go/{c['slug']}")
        link = re.search(r'https://www\.udemy\.com/course/[^"\'<> ]+couponCode=[A-Za-z0-9_-]+', ida)
        if not link:
            print(f"[cursos] sem cupom: {c['titulo']}")
            return None
        c["link"] = html.unescape(link.group(0))
        pagina = _baixar(c["pagina"])
        meta = lambda nome: (re.search(rf'<meta[^>]+(?:property|name)="{nome}"[^>]+content="([^"]*)"', pagina) or [None, ""])[1]
        c["descricao"] = _limpo(meta("og:description") or meta("description"))
        foto = meta("og:image")
        c["foto"] = re.sub(r"/course/\d+x\d+/", "/course/750x422/", foto) if foto else ""
        cat = re.search(r'/category/([a-z0-9-]+)"', pagina)
        c["categoria"] = cat.group(1) if cat else ""
        c["titulo"] = re.sub(r"^\[(PT|EN|ES)\]\s*", "", _limpo(meta("og:title")) or c["titulo"])
        return c
    except Exception as erro:
        print(f"[cursos] falhou ao abrir {c['titulo']}: {erro}")
        return None


# ---------------- texto ----------------
def _tags(texto):
    """Só hashtags limpas: minúsculas, sem acento, sem espaço."""
    import unicodedata
    sem = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return list(dict.fromkeys("#" + t for t in re.findall(r"#([a-z0-9_]{3,30})", sem)))


def texto_ia(c):
    """Resumo (2 ou 3 frases) e hashtags do tema do curso. Sem IA: descrição do curso e a categoria."""
    resumo, tags, ln = "", [], lingua(c)
    try:
        import escritor
        pedido = f"""Você escreve posts de um canal do Telegram que divulga cursos gratuitos. Responda em 2 linhas, neste formato:
RESUMO: 2 ou 3 frases curtas e simples, escritas em {ln['escrever']}, dizendo o que a pessoa aprende e para quem o curso serve.
TAGS: 5 a 7 hashtags em {ln['escrever']} sobre o TEMA do curso, do jeito que as pessoas procuram no Telegram
(ex.: {ln['exemplo']}).
Hashtags em minúsculas, sem acento e sem espaço. As palavras "RESUMO" e "TAGS" ficam assim, mas o conteúdo vai todo em {ln['escrever']}.
Use só o que está nos dados abaixo. Não invente carga horária, certificado, nota ou número de alunos.
No resumo: sem emoji, sem hashtag, sem travessão longo.

Curso: {c['titulo']}
Idioma das aulas: {c['idioma']}
Categoria no site: {c.get('categoria', '')}
Descrição do curso: {c.get('descricao', '')}"""
        resposta = escritor.responder(pedido) or ""
        r = re.search(r"RESUMO:\s*(.+?)(?:\n\s*TAGS:|$)", resposta, re.S)
        resumo = (r.group(1) if r else "").strip().replace("—", ",").replace("–", ",")
        tg = re.search(r"TAGS:\s*(.+)", resposta)
        tags = _tags(tg.group(1) if tg else "")[:7]
    except Exception as erro:
        print(f"[cursos] a IA não escreveu o texto: {erro}")
    if not resumo or len(resumo) > 600:
        resumo = c.get("descricao", "")[:300]
    if not tags and c.get("categoria"):
        tags = ["#" + c["categoria"].replace("-", "")]
    return resumo, list(dict.fromkeys(tags + ln["fixas"]))


def legenda(c):
    """Padrão do canal de livros: título, descrição, idioma, gatilho com o link, @ do canal e hashtags do tema,
    tudo no idioma do curso."""
    resumo, tags = texto_ia(c)
    ln = lingua(c)
    linhas = [f"🎓 <b>{html.escape(c['titulo'])}</b>", "", html.escape(resumo), "", ln["selo"], "",
              f'<a href="{html.escape(c["link"], quote=True)}">🕐 {ln["gratis"]} 🔥</a>', "",
              f"📚: {canal()}", "", " ".join(tags)]
    return "\n".join(linhas)[:1024]


def enviar(chat, c):
    teclado = {"inline_keyboard": [[{"text": lingua(c)["botao"], "url": c["link"]}]]}
    texto = legenda(c)
    if c.get("foto"):
        try:
            return postador.tg("sendPhoto", chat_id=chat, photo=c["foto"], caption=texto, parse_mode="HTML",
                               reply_markup=teclado)["message_id"]
        except Exception as erro:
            print(f"[cursos] a foto falhou, vai só o texto: {erro}")
    return postador.tg("sendMessage", chat_id=chat, text=texto, parse_mode="HTML", reply_markup=teclado,
                       link_preview_options={"is_disabled": True})["message_id"]


def marcar_postado(slug):
    """Guarda o curso como postado. Pelo painel (na nuvem) grava direto no GitHub, como a central faz."""
    import central
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not central.tem_nuvem():
        e = estado()
        e["postados"].append(slug)
        e["ultimo"] = agora
        salvar_estado(e)
        return
    try:
        central._git("fetch", "-q", "origin", "main")
        e = json.loads(central._git("show", "FETCH_HEAD:cursos/estado.json"))
    except Exception:
        e = estado()
    e.setdefault("postados", []).append(slug)
    e["postados"] = e["postados"][-1000:]
    e["ultimo"] = agora
    central.gravar("ingles", f"cursos grátis: post pelo painel {agora[:16]}",
                   arquivos={"cursos/estado.json": json.dumps(e, ensure_ascii=False, indent=2).encode("utf-8")})


def postar_escolhido(c):
    """Posta o curso que o Manoel escolheu no painel. Devolve (título, message_id) ou None sem cupom."""
    c = detalhes(dict(c))
    if not c:
        return None
    mid = enviar(canal(), c)
    marcar_postado(c["slug"])
    print(f"Curso postado pelo painel: {c['titulo']} (mensagem {mid})")
    return c["titulo"], mid


# ---------------- postar ----------------
def postar_um():
    for c in garimpar()[:8]:
        c = detalhes(c)
        if not c:
            continue
        mid = enviar(canal(), c)
        e = estado()
        e["postados"].append(c["slug"])
        e["ultimo"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        salvar_estado(e)
        print(f"Curso postado: {c['titulo']} (mensagem {mid})")
        return True
    print("Nenhum curso grátis novo agora.")
    return False


def horario_devido(agora=None):
    """Último horário que venceu sem post nas últimas 3 horas, ou None (igual ao do inglês e da renda)."""
    agora = (agora or datetime.now(timezone.utc)).astimezone(FUSO)
    vencidos = []
    for dia in (agora.date() - timedelta(days=1), agora.date()):
        for h in horarios():
            hh, mm = map(int, h.split(":"))
            g = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=FUSO)
            if g <= agora:
                vencidos.append(g)
    if not vencidos:
        return None
    alvo = max(vencidos)
    ultimo = estado().get("ultimo")
    if ultimo and datetime.fromisoformat(ultimo) >= alvo:
        return None
    if agora - alvo > timedelta(hours=3):
        return None
    return alvo


def postar_se_devido():
    if postador.pausa_total():
        print("Tudo pausado (pausa geral do painel).")
        return False
    devido = horario_devido()
    if not devido:
        print("Nenhum horário de cursos vencido agora.")
        return False
    print(f"Horário vencido: {devido:%d/%m %H:%M} (Manaus).")
    return postar_um()


if __name__ == "__main__":
    postador.carregar_env()
    acao = sys.argv[1] if len(sys.argv) > 1 else "agendado"
    if acao == "garimpar":
        for c in garimpar():
            print(f"[{c['idioma']}] {c['titulo']}  ({c['slug']})")
    elif acao == "agora":
        postar_um()
    else:
        postar_se_devido()
