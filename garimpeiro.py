"""Garimpeiro de e-books grátis da Amazon para o canal Free Kindle Books.

Etapa 1: lê a lista oficial "100 mais baixados grátis" do Kindle (Amazon.com.br).
Etapa 2: abre a página de cada candidato para conferir se ainda está grátis,
         se é PROMOÇÃO de verdade (tem "Preço digital sugerido") e pegar
         descrição, páginas e capa grande.
Etapa 3: dá nota e salva o top do dia em garimpo/AAAA-MM-DD.md e .json.

Navega com um navegador de verdade (Playwright), devagar, como uma pessoa.

Uso:  python garimpeiro.py              (livros + ofertas)
      python garimpeiro.py --so-ofertas
"""
import json
import math
import random
import re
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

PASTA = Path(__file__).parent
CONFIG = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
HISTORICO = PASTA / "historico_postados.json"
SAIDA = PASTA / "garimpo"

TOP_GRATIS = "https://www.amazon.com.br/gp/bestsellers/digital-text/?ie=UTF8&tf=1&pg={pg}"
# Top grátis de uma categoria (Romance, Autoajuda...): os ids ficam em config.json -> categorias_gratis
TOP_GRATIS_CATEGORIA = "https://www.amazon.com.br/gp/bestsellers/digital-text/{cat}/?ie=UTF8&tf=1&pg={pg}"
LIXO = re.compile(
    r"cap[ií]tulo extra|conto extra|cena extra|amostra|degusta[çc][ãa]o|"
    r"trecho|pr[ée]via|sample|preview|excerpt|bonus chapter",
    re.I,
)


def numero_br(texto):
    """'4,8' -> 4.8 ; '44.898' -> 44898 ; '12,06' -> 12.06"""
    texto = re.sub(r"[^\d,\.]", "", texto or "")
    if not texto:
        return 0
    if "," in texto:
        return float(texto.replace(".", "").replace(",", "."))
    return float(texto.replace(".", ""))


def pausa():
    time.sleep(random.uniform(*CONFIG["pausa_segundos"]))


def texto_de(pagina, seletor):
    el = pagina.query_selector(seletor)
    return el.inner_text().strip() if el else ""


def bloqueado(pagina):
    corpo = pagina.content().lower()
    return "captcha" in corpo or "digite os caracteres" in corpo


def capa_do_card(card):
    img = card.query_selector("img")
    src = (img.get_attribute("src") or "") if img else ""
    return re.sub(r"\._[^/]*_\.(jpg|png)$", r".\1", src)  # tira o "._AC_UL300_" = imagem grande


def ler_top_gratis(pagina, url=TOP_GRATIS, paginas=(1, 2), nome="Top grátis"):
    livros = []
    for pg in paginas:
        pagina.goto(url.format(pg=pg), timeout=60000)
        for _ in range(8):  # a lista carrega rolando
            pagina.mouse.wheel(0, 3000)
            pagina.wait_for_timeout(500)
        if bloqueado(pagina):
            raise RuntimeError("A Amazon pediu captcha na lista. Parei para não insistir.")
        for card in pagina.query_selector_all("div[data-asin]"):
            asin = card.get_attribute("data-asin")
            if not asin:
                continue
            partes = [p.strip() for p in card.inner_text().split("\n") if p.strip()]
            posicao = next((int(p[1:]) for p in partes if re.fullmatch(r"#\d+", p)), 999)
            nota = next((numero_br(p.split(" de ")[0]) for p in partes if "de 5 estrelas" in p), 0)
            i_nota = next((i for i, p in enumerate(partes) if "de 5 estrelas" in p), None)
            avaliacoes = int(numero_br(partes[i_nota + 1])) if i_nota is not None and i_nota + 1 < len(partes) else 0
            sem_rank = [p for p in partes if not re.fullmatch(r"#\d+", p)]
            livros.append({
                "asin": asin,
                "posicao": posicao,
                "titulo": sem_rank[0] if sem_rank else "",
                "autor": sem_rank[1] if len(sem_rank) > 1 else "",
                "nota": nota,
                "avaliacoes": avaliacoes,
                "capa": capa_do_card(card),
            })
        print(f"{nome} página {pg}: {len(livros)} livros até agora")
        pausa()
    return livros


def conferir_livro(pagina, livro):
    """Abre a página do livro. Devolve False se não está mais grátis."""
    pagina.goto(f"https://www.amazon.com.br/dp/{livro['asin']}", timeout=60000)
    pagina.wait_for_timeout(1200)
    if bloqueado(pagina):
        raise RuntimeError("A Amazon pediu captcha numa página de livro. Parei para não insistir.")

    caixa = texto_de(pagina, "#buybox") or texto_de(pagina, "#tmm-grid-swatch-KINDLE")
    precos = re.findall(r"R\$\s*([\d\.,]+)", caixa)
    preco = numero_br(precos[0]) if precos else None
    sugerido = re.search(r"Preço digital sugerido:\s*R\$\s*([\d\.,]+)", caixa)
    detalhes = texto_de(pagina, "#detailBullets_feature_div")
    paginas = re.search(r"(\d+)\s*páginas", texto_de(pagina, "#rpi-attribute-book_details-ebook_pages"))
    idioma = re.search(r"Idioma\W+(\w+)", detalhes)
    publicado = re.search(r"Data da publicação\W+([^\n]+)", detalhes)
    capa = pagina.query_selector("#landingImage, #ebooksImgBlkFront, #imgBlkFront")

    livro.update({
        "preco": preco,
        "preco_sugerido": numero_br(sugerido.group(1)) if sugerido else 0,
        "kindle_unlimited": "Leia gratuitamente" in caixa or "Kindle Unlimited" in caixa,
        "paginas": int(paginas.group(1)) if paginas else 0,
        "idioma": idioma.group(1).lower() if idioma else "",
        "publicado": publicado.group(1).strip() if publicado else "",
        "descricao": texto_de(pagina, "#bookDescription_feature_div")[:1500],
        "capa": (capa.get_attribute("data-old-hires") or capa.get_attribute("src")) if capa else "",
    })
    return preco == 0


def pontuar(livro):
    """Nota de 0 a 100. Estar no topo e ser promoção pesam mais."""
    p = 30 * (1 - (livro["posicao"] - 1) / 100)            # #1 = +30
    if livro["preco_sugerido"] > 0:
        p += 25 + min(livro["preco_sugerido"], 50) * 0.2   # promoção de verdade
    p += max(0, livro["nota"] - 3.5) * 8                   # nota 5 = +12
    p += min(math.log10(livro["avaliacoes"] + 1) * 4, 15)  # até +15
    if livro["paginas"] >= 80:
        p += 5                                             # livro "de verdade"
    if livro["idioma"] == "português":
        p += 3
    return round(p, 1)


# A Amazon chama tudo de "Português". Palavras que só se usam em Portugal denunciam o livro de lá.
PT_PORTUGAL = re.compile(
    r"\b(telem[óo]vel|equipa|autocarro|comboio|pequeno-almo[çc]o|casa de banho|rapariga|ecr[ãa]|"
    r"utilizador|facto|frigor[íi]fico|sumo|talho|portagem|apanhar o comboio|miúdos)\b", re.I)


def marcar_portugal(livro):
    if livro.get("idioma") == "português":
        texto = f"{livro.get('titulo', '')} {livro.get('descricao', '')}"
        if len(PT_PORTUGAL.findall(texto)) >= 2:
            livro["idioma"] = "português de portugal"


def misturar_idiomas(livros):
    """Escolhe o top do dia com uma cota por idioma e intercala na ordem da fila,
    para os livros em inglês e espanhol não ficarem todos no fim."""
    cotas = CONFIG.get("cota_idiomas", {})
    total = CONFIG["top_do_dia"]
    por_idioma = {}
    for l in livros:  # já vêm do melhor para o pior
        por_idioma.setdefault(l.get("idioma") or "português", []).append(l)
    estrangeiros = [l for idioma, cota in cotas.items() for l in por_idioma.get(idioma, [])[:cota]]
    escolhidos_ids = {id(l) for l in estrangeiros}
    resto = [l for l in livros if id(l) not in escolhidos_ids][: max(total - len(estrangeiros), 0)]
    estrangeiros.sort(key=lambda l: l["pontos"], reverse=True)
    if not estrangeiros:
        return resto
    # espalha por igual: o k-ésimo estrangeiro entra depois de k*(len(resto)/(n+1)) livros em português
    n = len(estrangeiros)
    pontos_de_entrada = {round((k + 1) * len(resto) / (n + 1)): k for k in range(n)}
    mistura = []
    for i in range(len(resto) + 1):
        if i in pontos_de_entrada:
            mistura.append(estrangeiros[pontos_de_entrada[i]])
        if i < len(resto):
            mistura.append(resto[i])
    usados = {id(l) for l in mistura}
    return (mistura + [l for l in estrangeiros if id(l) not in usados])[:total]


def link_afiliado(asin):
    """Mesmo formato do link que o SiteStripe gera. A comissão vem do 'tag='."""
    tag = CONFIG.get("tag_afiliado_br") or ""
    if not tag:
        return f"https://www.amazon.com.br/dp/{asin}"
    return (f"https://www.amazon.com.br/dp/{asin}?tag={tag}"
            "&linkCode=ll1&language=pt_BR&ref_=as_li_ss_tl")


def pontuar_oferta(o):
    """Desconto pesa mais; nota e número de avaliações desempatam."""
    return round(o["desconto"] + (o["nota"] - 4) * 20 + min(math.log10(o["avaliacoes"] + 1) * 3, 15), 1)


def salvar(top, total_lista, total_conferidos):
    SAIDA.mkdir(exist_ok=True)
    hoje = date.today().isoformat()
    (SAIDA / f"{hoje}.json").write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    promos = sum(l["promocao"] for l in top)
    linhas = [f"# Garimpo de {hoje}", "",
              f"Li o Top 100 grátis ({total_lista} livros), conferi {total_conferidos} um por um.",
              f"Top {len(top)} do dia, sendo {promos} promoções de verdade.", ""]
    for i, l in enumerate(top, 1):
        if l["promocao"]:
            selo = f"🔥 PROMOÇÃO: era R$ {l['preco_sugerido']:.2f}".replace(".", ",")
        else:
            selo = "grátis fixo"
        linhas += [
            f"## {i}. {l['titulo']}",
            f"- {l['autor']} · #{l['posicao']} no Top grátis · ⭐ {l['nota']} ({l['avaliacoes']} avaliações)",
            f"- {selo} · {l['paginas']} páginas · {l['idioma']} · nota {l['pontos']}",
            f"- {l['link']}",
            f"- ![capa|200]({l['capa']})",
            "",
        ]
    (SAIDA / f"{hoje}.md").write_text("\n".join(linhas), encoding="utf-8")
    return SAIDA / f"{hoje}.md"


# ---------------- ofertas (fone, mouse etc.) ----------------
def _preco_core(pagina):
    caixa = (texto_de(pagina, "#corePriceDisplay_desktop_feature_div")
             or texto_de(pagina, "#corePrice_feature_div") or texto_de(pagina, "#apex_desktop"))
    precos = re.findall(r"R\$\s*([\d\.,]+)", caixa)
    de = re.search(r"De:\s*R\$\s*([\d\.,]+)", caixa)
    preco = numero_br(precos[0]) if precos else 0
    antigo = numero_br(de.group(1)) if de else 0
    return preco, antigo


def conferir_oferta(pagina, item):
    """Abre o produto e atualiza o preço. Devolve True se ainda tem desconto bom."""
    pagina.goto(f"https://www.amazon.com.br/dp/{item['asin']}", timeout=60000)
    pagina.wait_for_timeout(1500)
    if bloqueado(pagina):
        raise RuntimeError("A Amazon pediu captcha numa página de produto. Parei para não insistir.")
    preco, antigo = _preco_core(pagina)
    capa = pagina.query_selector("#landingImage")
    item.update({
        "titulo_completo": texto_de(pagina, "#productTitle") or item.get("titulo_completo", ""),
        "preco": preco,
        "preco_antigo": antigo,
        "desconto": round((1 - preco / antigo) * 100) if preco and antigo > preco else 0,
        "capa": (capa.get_attribute("data-old-hires") or capa.get_attribute("src")) if capa else item.get("capa", ""),
        "sobre": texto_de(pagina, "#feature-bullets")[:800],
        "conferido_em": datetime.now().strftime("%d/%m às %Hh%M"),
    })
    return item["desconto"] >= CONFIG["ofertas"]["desconto_minimo"]


def garimpar_ofertas(pagina):
    cfg = CONFIG["ofertas"]
    postados = set(json.loads(HISTORICO.read_text(encoding="utf-8"))) if HISTORICO.exists() else set()
    lista = []
    for cat in cfg["categorias"]:
        pagina.goto(f"https://www.amazon.com.br/gp/bestsellers/{cat}/", timeout=60000)
        for _ in range(6):
            pagina.mouse.wheel(0, 3000)
            pagina.wait_for_timeout(500)
        if bloqueado(pagina):
            print("[aviso] captcha na lista de ofertas, parei.")
            break
        for card in pagina.query_selector_all("div[data-asin]"):
            asin = card.get_attribute("data-asin")
            partes = [p.strip() for p in card.inner_text().split("\n") if p.strip()]
            i_nota = next((i for i, p in enumerate(partes) if "de 5 estrelas" in p), None)
            if not asin or i_nota is None or i_nota + 1 >= len(partes):
                continue
            sem_rank = [p for p in partes if not re.fullmatch(r"#\d+", p)]
            lista.append({"tipo": "oferta", "asin": asin, "categoria": cat,
                          "titulo_completo": sem_rank[0] if sem_rank else "",
                          "nota": numero_br(partes[i_nota].split(" de ")[0]),
                          "avaliacoes": int(numero_br(partes[i_nota + 1]))})
        print(f"Mais vendidos em {cat}: {len(lista)} produtos até agora")
        pausa()

    candidatos = [o for o in lista if o["asin"] not in postados
                  and o["nota"] >= cfg["nota_minima"] and o["avaliacoes"] >= cfg["avaliacoes_minimas"]]
    # confere os mais bem avaliados primeiro e fica com os maiores descontos
    candidatos.sort(key=lambda o: (o["nota"], o["avaliacoes"]), reverse=True)
    achados = []
    for o in candidatos[: cfg["conferir_ate"]]:
        try:
            if conferir_oferta(pagina, o):
                o["link"] = link_afiliado(o["asin"])
                o["pontos"] = pontuar_oferta(o)
                achados.append(o)
                print(f"  oferta: -{o['desconto']}% {o['titulo_completo'][:60]}")
        except RuntimeError as e:
            print(f"[aviso] {e}")
            break
        except Exception as e:
            print(f"[pulei] {o['asin']}: {e}")
        pausa()
    achados.sort(key=lambda o: o["pontos"], reverse=True)
    achados = achados[: cfg["por_dia"]]
    SAIDA.mkdir(exist_ok=True)
    arq = SAIDA / f"{date.today().isoformat()} ofertas.json"
    arq.write_text(json.dumps(achados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ofertas salvas: {arq.name} ({len(achados)})")
    return achados


def tirar_tag(url):
    """Tira tag/linkId de afiliado de outra pessoa da URL da busca."""
    return re.sub(r"[&?](tag|linkCode|linkId|ref|qid)=[^&]*", "", url)


def ler_buscas(pagina, buscas=None, paginas=None):
    """Lê as buscas da Amazon do config (ex.: eBooks Kindle de R$ 0)."""
    livros = []
    for base in (CONFIG.get("buscas_livros", []) if buscas is None else buscas):
        base = tirar_tag(base)
        for pg in range(1, (paginas or CONFIG.get("paginas_por_busca", 3)) + 1):
            pagina.goto(f"{base}&page={pg}", timeout=60000)
            pagina.wait_for_timeout(2000)
            if bloqueado(pagina):
                raise RuntimeError("A Amazon pediu captcha na busca. Parei para não insistir.")
            cards = pagina.query_selector_all("div[data-component-type='s-search-result'][data-asin]")
            if not cards:
                break
            for i, card in enumerate(cards, 1):
                asin = card.get_attribute("data-asin")
                if not asin:
                    continue
                partes = [p.strip() for p in card.inner_text().split(chr(10)) if p.strip()]
                autor = next((p[4:].split(" | ")[0] for p in partes if p.startswith("por ")), "")
                nota = next((numero_br(p.split(" de ")[0]) for p in partes if "de 5 estrelas" in p), 0)
                i_nota = next((k for k, p in enumerate(partes) if "de 5 estrelas" in p), None)
                avaliacoes = 0
                if i_nota is not None and i_nota + 1 < len(partes):
                    t = partes[i_nota + 1].strip("() ")
                    mil = "mil" in t
                    avaliacoes = int(numero_br(t.replace("mil", "")) * (1000 if mil else 1))
                livros.append({
                    "asin": asin,
                    "posicao": (pg - 1) * 50 + i,
                    "titulo": partes[0] if partes else "",
                    "autor": autor,
                    "nota": nota,
                    "avaliacoes": avaliacoes,
                    "capa": capa_do_card(card),
                })
            print(f"Busca grátis página {pg}: {len(livros)} livros até agora")
            pausa()
    return livros


# ---------------- livros ----------------
def garimpar_livros(pagina):
    postados = set(json.loads(HISTORICO.read_text(encoding="utf-8"))) if HISTORICO.exists() else set()
    try:  # livros que já estão no robô (na fila ou já postados) nem são conferidos de novo
        import banco
        postados |= banco.asins_conhecidos()
    except Exception as e:
        print(f"[aviso] não li o banco: {e}")
    lista = ler_top_gratis(pagina)
    try:
        lista += ler_buscas(pagina)
    except RuntimeError as e:
        print(f"[aviso] {e}")
    paginas_cat = tuple(range(1, CONFIG.get("paginas_por_categoria", 1) + 1))
    for cat in CONFIG.get("categorias_gratis", {}):
        try:
            lista += ler_top_gratis(pagina, TOP_GRATIS_CATEGORIA.replace("{cat}", str(cat)), paginas_cat,
                                    CONFIG["categorias_gratis"][cat])
        except RuntimeError as e:
            print(f"[aviso] {e}")
            break
        except Exception as e:  # categoria fora do ar: segue para a próxima
            print(f"[pulei categoria] {cat}: {e}")
    # buscas por idioma (inglês, espanhol): cada uma tem a sua vaga garantida de conferência
    extras = []
    for idioma, url in CONFIG.get("buscas_idiomas", {}).items():
        try:
            achados = ler_buscas(pagina, [url], CONFIG.get("paginas_por_idioma", 2))
        except RuntimeError as e:
            print(f"[aviso] {e}")
            break
        except Exception as e:
            print(f"[pulei busca em {idioma}] {e}")
            continue
        vagas = CONFIG.get("cota_idiomas", {}).get(idioma, 5) * 2
        novos_idioma = [l for l in achados if l["asin"] not in postados
                        and not LIXO.search(unicodedata.normalize("NFC", l["titulo"]))
                        and (l["avaliacoes"] < 5 or l["nota"] >= CONFIG["nota_minima"])][:vagas]
        for l in novos_idioma:
            l["idioma_busca"] = idioma
        print(f"Busca em {idioma}: {len(achados)} achados, {len(novos_idioma)} para conferir")
        extras += novos_idioma
    unicos = {}
    for l in lista:  # mesmo livro nas duas fontes: fica a melhor posição
        if l["asin"] not in unicos or l["posicao"] < unicos[l["asin"]]["posicao"]:
            unicos[l["asin"]] = l
    lista = list(unicos.values())
    candidatos = [
        l for l in sorted(lista, key=lambda l: l["posicao"])
        if l["asin"] not in postados
        and not LIXO.search(unicodedata.normalize("NFC", l["titulo"]))
        and (l["avaliacoes"] < 5 or l["nota"] >= CONFIG["nota_minima"])
    ][: CONFIG["conferir_ate"]]
    ja = {l["asin"] for l in candidatos}
    candidatos += [l for l in extras if l["asin"] not in ja and not ja.add(l["asin"])]

    aprovados = []
    for n, livro in enumerate(candidatos, 1):
        try:
            ainda_gratis = conferir_livro(pagina, livro)
        except RuntimeError as e:
            # captcha: os que faltam entram com os dados da lista (lá já aparecem a R$ 0,00).
            # Antes de postar, o robô abre a página do livro, confere e completa o texto.
            print(f"[aviso] {e} Os outros {len(candidatos) - n + 1} entram sem conferir agora.")
            for l in candidatos[n - 1:]:
                l.update({"preco": 0, "preco_sugerido": 0, "kindle_unlimited": False, "paginas": 0,
                          "idioma": l.get("idioma_busca", ""), "publicado": "", "descricao": "",
                          "sem_conferir": True})
                aprovados.append(l)
            break
        except Exception as e:  # página fora do padrão: pula o livro
            print(f"[pulei] {livro['asin']}: {e}")
            continue
        print(f"{n}/{len(candidatos)} {'ok   ' if ainda_gratis else 'PAGO '} {livro['titulo'][:60]}")
        if ainda_gratis:
            aprovados.append(livro)
        pausa()

    for l in aprovados:
        if not l.get("idioma") and l.get("idioma_busca"):
            l["idioma"] = l["idioma_busca"]
        marcar_portugal(l)
        l["tipo"] = "livro"
        l["promocao"] = l["preco_sugerido"] > 0
        l["pontos"] = pontuar(l)
        l["link"] = link_afiliado(l["asin"])
    aprovados.sort(key=lambda l: l["pontos"], reverse=True)
    arquivo = salvar(misturar_idiomas(aprovados), len(lista), len(candidatos))
    print(f"Livros salvos: {arquivo}")


# ---------------- um produto só (pelo link, para o painel) ----------------
def asin_do_link(link):
    link = (link or "").strip()
    if re.search(r"(amzn\.to|a\.co)/", link):  # link curto: segue o redirecionamento
        try:
            import requests
            link = requests.get(link, allow_redirects=True, timeout=15,
                                headers={"User-Agent": "Mozilla/5.0"}).url
        except Exception:
            pass
    m = (re.search(r"/(?:dp|gp/product|gp/aw/d|d)/([A-Z0-9]{10})", link)
         or re.fullmatch(r"([A-Z0-9]{10})", link))
    return m.group(1) if m else None


def ler_produto(link):
    """Abre a página de um produto ou livro e devolve os dados para montar o post."""
    asin = asin_do_link(link)
    if not asin:
        raise ValueError("Não achei o código do produto nesse link. Cole o link da página do produto na Amazon.")
    with sync_playwright() as p:
        nav = p.chromium.launch()
        try:
            pagina = nav.new_page(locale="pt-BR")
            item = {"asin": asin, "tipo": "oferta"}
            conferir_oferta(pagina, item)
            nota = re.search(r"([\d,]+) de 5", pagina.query_selector("#acrPopover").get_attribute("title") or "") \
                if pagina.query_selector("#acrPopover") else None
            avals = texto_de(pagina, "#acrCustomerReviewText")
            item["nota"] = numero_br(nota.group(1)) if nota else 0
            item["avaliacoes"] = int(numero_br(avals)) if avals else 0
            if pagina.query_selector("#ebooksProductTitle, #bookDescription_feature_div"):
                autor = pagina.query_selector(".author a.a-link-normal, #bylineInfo .author a")
                livro = {"asin": asin, "tipo": "livro",
                         "titulo": texto_de(pagina, "#ebooksProductTitle") or texto_de(pagina, "#productTitle"),
                         "autor": autor.inner_text().strip() if autor else ""}
                conferir_livro(pagina, livro)
                if livro.get("preco") == 0:
                    item = livro
                else:  # livro pago: vira post de oferta
                    item.update({"titulo_completo": livro["titulo"], "descricao": livro.get("descricao", ""),
                                 "capa": livro.get("capa") or item.get("capa", ""),
                                 "preco": livro.get("preco") or item.get("preco", 0)})
                    if livro.get("preco_sugerido") and livro.get("preco") and livro["preco_sugerido"] > livro["preco"]:
                        item["preco_antigo"] = livro["preco_sugerido"]
                        item["desconto"] = round((1 - livro["preco"] / livro["preco_sugerido"]) * 100)
        finally:
            nav.close()
    if not (item.get("titulo") or item.get("titulo_completo")):
        raise RuntimeError("A página abriu, mas não achei o nome do produto. Confira o link.")
    item["link"] = link_afiliado(asin)
    return item


def garimpar_tudo(ofertas=True):
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(locale="pt-BR")
        try:
            garimpar_livros(pagina)
            if ofertas:
                garimpar_ofertas(pagina)
        except RuntimeError as e:
            print(f"[aviso] {e}")
        finally:
            navegador.close()


if __name__ == "__main__":
    import sys
    if "--so-ofertas" in sys.argv:
        with sync_playwright() as p:
            nav = p.chromium.launch()
            garimpar_ofertas(nav.new_page(locale="pt-BR"))
            nav.close()
    else:
        garimpar_tudo(ofertas="--sem-ofertas" not in sys.argv)
