"""Canal 🏠 Achadinhos Casa e Cozinha (@achadinhoscasacozinhabr): ofertas reais da Amazon com o link de afiliado.

De onde vêm: o Promobit lista as promoções da Amazon que a comunidade achou, com o preço do momento.
O robô pega só as de casa e cozinha, abre o "ir à loja" do Promobit para achar o código do produto na
Amazon (ASIN) e monta o link limpo com a nossa tag (manoeljuniorm-20). Nada é inventado: sem ASIN, não posta.
O preço é o do momento em que a oferta foi vista; a legenda avisa que ele pode mudar.

Horários (Manaus) no config.json -> "achadinhos" -> "horarios".

    python achadinhos.py agendado   -> posta só se um horário venceu sem post (usado pela nuvem)
    python achadinhos.py agora      -> posta uma oferta agora
    python achadinhos.py garimpar   -> só mostra as ofertas encontradas, sem postar
    python achadinhos.py previa     -> monta a legenda da primeira oferta e imprime, sem postar
"""
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import postador

PASTA = Path(__file__).parent / "achadinhos"
ESTADO = PASTA / "estado.json"
CFG = postador.CONFIG.get("achadinhos", {})
FUSO = timezone(timedelta(hours=-4))  # Manaus
SITE = "https://www.promobit.com.br"
TAG = postador.CONFIG.get("tag_afiliado_br") or "manoeljuniorm-20"
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/130.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9"}
# Páginas do Promobit que mais trazem casa e cozinha. A da loja Amazon traz um pouco de tudo (o filtro separa).
PAGINAS = CFG.get("paginas") or ["/promocoes/utilidades-domesticas/", "/promocoes/eletroportateis/",
                                 "/promocoes/eletrodomesticos/", "/promocoes/moveis-e-decoracao/",
                                 "/promocoes/loja/amazon/"]
CATEGORIAS = {"Utilidades Domésticas", "Eletroportáteis", "Eletrodomésticos", "Móveis e Decoração"}
VALIDAS = {"APPROVED", "TOP_OFFER"}
PRECO_MAX = float(CFG.get("preco_max", 600))
FORA = set(CFG.get("fora") or ["Ar condicionado", "Geladeira", "Máquina de lavar", "Fogão", "Lava e seca",
                               "Freezer", "Televisão"])
IDADE_MAX = timedelta(hours=CFG.get("idade_max_horas", 36))  # oferta velha costuma já ter acabado


def canal():
    return CFG.get("canal", "@achadinhoscasacozinhabr")


def horarios():
    return CFG.get("horarios") or ["09:00", "12:00", "15:00", "18:00", "21:00"]


def estado():
    e = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    e.setdefault("postados", [])
    return e


def salvar_estado(e):
    e["postados"] = e["postados"][-2000:]
    PASTA.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def _baixar(caminho):
    url = caminho if caminho.startswith("http") else SITE + caminho
    r = requests.get(url, headers=NAVEGADOR, timeout=25)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _ofertas_da_pagina(caminho):
    pagina = _baixar(caminho)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pagina, re.S)
    if not m:
        return []
    props = json.loads(m.group(1))["props"]["pageProps"]
    return (props.get("serverOffers") or {}).get("offers") or []


# ---------------- garimpo ----------------
def garimpar():
    """Ofertas de casa e cozinha da Amazon, recentes e ainda não postadas. As mais curtidas primeiro."""
    feitos = {str(x) for x in estado()["postados"]}
    agora = datetime.now(timezone.utc)
    vistos, achados = set(), []
    for caminho in PAGINAS:
        try:
            ofertas = _ofertas_da_pagina(caminho)
        except Exception as erro:
            print(f"[achadinhos] não consegui abrir {caminho}: {erro}")
            continue
        for o in ofertas:
            oid = str(o.get("offerId") or "")
            if not oid or oid in vistos or oid in feitos:
                continue
            vistos.add(oid)
            if (o.get("storeDomain") or "") != "amazon.com.br" or o.get("categoryName") not in CATEGORIAS:
                continue
            if o.get("offerStatusName") not in VALIDAS or o.get("offerPriceType") != "NORMAL":
                continue
            preco = float(o.get("offerPrice") or 0)
            if preco < 5 or preco > PRECO_MAX:  # 0,01 é cupom genérico; achadinho é coisa barata
                continue
            if (o.get("subcategoryName") or "") in FORA:
                continue
            try:
                publicada = datetime.strptime(o["offerPublished"], "%Y-%m-%dT%H:%M:%S%z")
            except Exception:
                continue
            if agora - publicada > IDADE_MAX:
                continue
            antigo = float(o.get("offerOldPrice") or 0)
            achados.append({
                "id": oid, "titulo": html.unescape(o.get("offerTitle") or "").strip(), "preco": preco,
                "antigo": antigo if antigo > preco else 0, "cupom": (o.get("offerCoupon") or "").strip(),
                "frete": any("frete" in (t.get("name") or "").lower() for t in o.get("offerTags") or []),
                "foto": o.get("offerPhoto") or "", "categoria": o.get("subcategoryName") or o.get("categoryName"),
                "publicada": publicada.isoformat(), "nota": int(o.get("offerLikes") or 0) + int(o.get("offerClicks") or 0) // 20,
            })
    achados.sort(key=lambda c: (c["nota"], c["publicada"]), reverse=True)
    return achados


def link_amazon(asin):
    return f"https://www.amazon.com.br/dp/{asin}?tag={TAG}"


def detalhes(c):
    """Acha o ASIN pelo "ir à loja" do Promobit e monta o nosso link. None se não achar."""
    try:
        ida = _baixar(f"/Redirect/to/{c['id']}/")
    except Exception as erro:
        print(f"[achadinhos] não abri a ida para a loja de {c['titulo']}: {erro}")
        return None
    asin = re.search(r'amazon\.com\.br/(?:[^"\'<> ]*/)?(?:dp|gp/product)/([A-Z0-9]{10})', ida)
    if not asin:
        print(f"[achadinhos] sem código da Amazon: {c['titulo']}")
        return None
    c["asin"] = asin.group(1)
    c["link"] = link_amazon(c["asin"])
    if c.get("foto") and not c["foto"].startswith("http"):
        c["foto"] = "https://i.promobit.com.br/" + c["foto"].lstrip("/")
    return c


# ---------------- texto ----------------
def reais(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto_ia(c):
    """1 ou 2 frases sobre para que serve o produto, só com o título. Sem IA: nada (o título já basta)."""
    try:
        import escritor
        pedido = f"""Você escreve posts de um canal do Telegram de achadinhos de casa e cozinha.
Escreva 1 ou 2 frases curtas e simples, em português do Brasil, dizendo para que serve este produto no dia a dia.
Use só o que está no nome do produto. Não invente medida, material, potência, garantia, avaliação nem preço.
Sem emoji, sem hashtag, sem travessão longo. Responda só com as frases.

Produto: {c['titulo']}
Categoria: {c.get('categoria', '')}"""
        resposta = (escritor.responder(pedido) or "").strip().replace("—", ",").replace("–", ",")
        resposta = re.sub(r"^\s*(RESUMO|Resposta)\s*:\s*", "", resposta, flags=re.I)
        if 10 < len(resposta) < 400 and "#" not in resposta:
            return resposta
    except Exception as erro:
        print(f"[achadinhos] a IA não escreveu o texto: {erro}")
    return ""


def legenda(c, agora=None):
    agora = (agora or datetime.now(timezone.utc)).astimezone(FUSO)
    frase = texto_ia(c)
    linhas = [f"🏠 <b>{html.escape(c['titulo'])}</b>", ""]
    if frase:
        linhas += [html.escape(frase), ""]
    if c.get("antigo"):
        desconto = round(100 * (1 - c["preco"] / c["antigo"]))
        linhas.append(f"❌ De {reais(c['antigo'])}")
        linhas.append(f"✅ Por <b>{reais(c['preco'])}</b> ({desconto}% de desconto)")
    else:
        linhas.append(f"✅ Por <b>{reais(c['preco'])}</b>")
    if c.get("cupom"):
        linhas.append(f"🎟️ Cupom: <code>{html.escape(c['cupom'])}</code>")
    if c.get("frete"):
        linhas.append("🚚 Frete grátis")
    linhas += ["", f'<a href="{html.escape(c["link"], quote=True)}">🛒 VER OFERTA NA AMAZON</a>', "",
               f"⏰ Preço visto às {agora:%H:%M} ({agora:%d/%m}). Pode mudar a qualquer momento.",
               "🔗 Link de afiliado: você paga o mesmo e ajuda o canal.", "",
               f"🏠: {canal()}", "", "#achadinhos #casaecozinha #ofertas #amazon"]
    return "\n".join(linhas)[:1024]


def enviar(chat, c):
    teclado = {"inline_keyboard": [[{"text": "🛒 Ver oferta na Amazon", "url": c["link"]}]]}
    texto = legenda(c)
    if c.get("foto"):
        try:
            return postador.tg("sendPhoto", chat_id=chat, photo=c["foto"], caption=texto, parse_mode="HTML",
                               reply_markup=teclado)["message_id"]
        except Exception as erro:
            print(f"[achadinhos] a foto falhou, vai só o texto: {erro}")
    return postador.tg("sendMessage", chat_id=chat, text=texto, parse_mode="HTML", reply_markup=teclado,
                       link_preview_options={"is_disabled": True})["message_id"]


# ---------------- postar ----------------
def proxima():
    ja = set(estado().get("asins", []))  # o mesmo produto às vezes volta com outro número no Promobit
    for c in garimpar()[:8]:
        c = detalhes(c)
        if c and c["asin"] not in ja:
            return c
    return None


def postar_um():
    c = proxima()
    if not c:
        print("Nenhuma oferta nova de casa e cozinha agora.")
        return False
    mid = enviar(canal(), c)
    e = estado()
    e["postados"].append(c["id"])
    e["asins"] = (e.get("asins", []) + [c["asin"]])[-2000:]
    e["ultimo"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    salvar_estado(e)
    print(f"Oferta postada: {c['titulo']} por {reais(c['preco'])} (mensagem {mid})")
    return True


def horario_devido(agora=None):
    """Último horário que venceu sem post nas últimas 3 horas, ou None (igual ao dos cursos)."""
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
        print("Nenhum horário de achadinhos vencido agora.")
        return False
    print(f"Horário vencido: {devido:%d/%m %H:%M} (Manaus).")
    return postar_um()


if __name__ == "__main__":
    postador.carregar_env()
    acao = sys.argv[1] if len(sys.argv) > 1 else "agendado"
    if acao == "garimpar":
        for c in garimpar():
            print(f"[{c['categoria']}] {c['titulo']} | {reais(c['preco'])} | antes {reais(c['antigo'])} | {c['id']}")
    elif acao == "previa":
        c = proxima()
        print(legenda(c) if c else "Nenhuma oferta agora.")
        if c:
            print("\nFOTO:", c.get("foto"), "\nLINK:", c["link"])
    elif acao == "agora":
        postar_um()
    else:
        postar_se_devido()
