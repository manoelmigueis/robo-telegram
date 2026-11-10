"""Escritor: monta a legenda de cada post (livro grátis ou oferta da Amazon).

Os números (preço, páginas, nota, link) vêm direto da Amazon e entram por
modelo fixo, para a IA nunca inventar nada. A IA só escreve:
  - a descrição curta e instigante;
  - o título curto (só nas ofertas);
  - as hashtags do tema.

Quem escreve, nesta ordem:
  1. API do Claude, se houver ANTHROPIC_API_KEY no .env;
  2. o Claude Code instalado no PC (usa o seu plano, sem chave);
  3. sem IA: corta o começo da descrição e usa tags fixas.
"""
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))

INSTRUCOES = """Você escreve posts para o canal de Telegram "Free Kindle Books" (@livrosepubpdfx),
que indica e-books grátis da Amazon e, às vezes, ofertas de produtos da Amazon.

Para cada item da lista, devolva:
- "descricao": 2 frases curtas, no máximo 220 caracteres no total, em português do Brasil.
  Livro: crie curiosidade sobre a história, sem spoiler, como um amigo indicando.
  Oferta: diga para quem o produto é bom e o principal benefício.
  Reescreva com suas palavras, não copie a descrição. Não invente nada que não esteja no texto.
  Pode terminar com 1 emoji. Não use travessão.
- "titulo_curto": só para ofertas, nome do produto em até 45 caracteres (marca + o que é). Para livros, "".
- "tags": de 4 a 6 hashtags em português, sem acento, ligadas ao tema
  (gênero do livro, público, tipo de produto). Ex.: #romance #suspense #fone #gamer.
  Não repita estas, que já entram sozinhas: {fixas}.

Responda SÓ com um JSON válido, uma lista na mesma ordem: [{{"asin": "...", "descricao": "...", "titulo_curto": "...", "tags": ["#...", "..."]}}]

Itens:
{itens}"""


# ---------------- IA ----------------
def _montar_pedido(itens):
    resumo = []
    for i in itens:
        texto = i.get("descricao") or i.get("sobre") or ""
        resumo.append({
            "asin": i["asin"],
            "tipo": i.get("tipo", "livro"),
            "titulo": i.get("titulo") or i.get("titulo_completo", ""),
            "texto": texto[:900],
        })
    return INSTRUCOES.format(fixas=" ".join(CONFIG["tags_fixas"]),
                             itens=json.dumps(resumo, ensure_ascii=False, indent=1))


def _ler_json(texto):
    inicio, fim = texto.find("["), texto.rfind("]")
    return json.loads(texto[inicio: fim + 1])


def _via_api(pedido):
    import anthropic

    client = anthropic.Anthropic()
    resposta = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": pedido}],
    )
    if resposta.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resposta.content if b.type == "text")


def _via_claude_code(pedido):
    exe = shutil.which("claude")
    if not exe:
        return None
    r = subprocess.run(
        [exe, "-p", "--model", "sonnet", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
        input=pedido, capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    return r.stdout if r.returncode == 0 else None


def escrever(itens):
    """Preenche 'texto_post', 'titulo_curto' e 'tags' em cada item (em lote, 1 chamada)."""
    if not itens:
        return
    pedido = _montar_pedido(itens)
    resposta = None
    for nome, funcao in (("API", _via_api if os.environ.get("ANTHROPIC_API_KEY") else None),
                         ("Claude Code", _via_claude_code)):
        if not funcao:
            continue
        try:
            bruto = funcao(pedido)
            if bruto:
                resposta = {r["asin"]: r for r in _ler_json(bruto)}
                print(f"[escritor] textos escritos pelo {nome}.")
                break
        except Exception as e:
            print(f"[escritor] {nome} falhou ({type(e).__name__}: {e})")
    for i in itens:
        r = (resposta or {}).get(i["asin"])
        if r and r.get("descricao"):
            i["texto_post"] = r["descricao"].replace("—", ",")
            i["titulo_curto"] = r.get("titulo_curto") or ""
            i["tags"] = [t if t.startswith("#") else "#" + t for t in r.get("tags", [])][:6]
        else:
            i["texto_post"] = _resumo_simples(i.get("descricao") or i.get("sobre") or "")
            i["titulo_curto"] = ""
            i["tags"] = []


def _resumo_simples(texto):
    frases = re.split(r"(?<=[.!?])\s+", " ".join(texto.split()))
    saida = ""
    for f in frases:
        if len(saida) + len(f) > 220:
            break
        saida += f + " "
    return saida.strip() or (texto[:217] + "..." if texto else "")


# ---------------- legendas ----------------
def _reais(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _milhar(n):
    return f"{n:,}".replace(",", ".")


def _tags(item, extras):
    todas = []
    fixas = CONFIG["tags_fixas"] if item.get("tipo") != "oferta" else ["#amazon"]
    for t in list(item.get("tags", [])) + extras + fixas:
        t = re.sub(r"[^\w#]", "", t.lower())
        if len(t) > 1 and t not in todas:
            todas.append(t)
    return " ".join(todas[:10])


IDIOMAS = {"português": "🇧🇷 Português", "português de portugal": "🇵🇹 Português de Portugal",
           "inglês": "🇺🇸 Livro em inglês", "espanhol": "🇪🇸 Livro em espanhol"}
TAGS_IDIOMA = {"inglês": ["#livrosemingles", "#englishbooks"], "espanhol": ["#livrosemespanhol", "#librosgratis"],
               "português de portugal": ["#portugal"]}


def legenda_livro(l):
    e = html.escape
    linhas = [f"📖 <b>{e(l['titulo'])}</b>"]
    if l.get("autor"):
        linhas.append(f"✍️ {e(l['autor'])}")
    if l.get("texto_post"):
        linhas += ["", e(l["texto_post"])]
    info = []
    if l.get("idioma") in IDIOMAS:
        info.append(IDIOMAS[l["idioma"]])
    if l.get("paginas"):
        info.append(f"📄 {_milhar(l['paginas'])} páginas")
    if info:
        linhas += ["", " · ".join(info)]
    linhas.append("")
    pago = (l.get("preco") or 0) > 0  # saiu da promoção grátis: mostra o preço de agora
    if pago:
        linhas.append(f"💰 Por <b>{_reais(l['preco'])}</b> no Kindle")
        if l.get("kindle_unlimited"):
            ku = CONFIG.get("link_kindle_unlimited")  # link de afiliado do Manoel para o KU
            if ku:
                linhas.append(f'📚 <b>Grátis para quem assina o <a href="{e(ku, quote=True)}">Kindle Unlimited</a></b>')
            else:
                linhas.append("📚 <b>Grátis para quem assina o Kindle Unlimited</b>")
        if l.get("link"):
            linhas.append(f'👉 <a href="{e(l["link"], quote=True)}"><b>VER NA AMAZON</b></a>')
    elif l.get("preco_sugerido"):
        linhas.append(f"💰 De <s>{_reais(l['preco_sugerido'])}</s> por <b>R$ 0,00</b>")
    if pago:
        pass
    elif l.get("link"):  # o texto vira link (com a tag de afiliado)
        linhas.append(f'🕐 <a href="{e(l["link"], quote=True)}"><b>GRÁTIS POR TEMPO LIMITADO</b></a> 🔥')
    else:
        linhas.append("🕐 <b>GRÁTIS POR TEMPO LIMITADO</b> 🔥")
    tags = _tags(l, TAGS_IDIOMA.get(l.get("idioma"), []) + ["#ebook", "#leitura"])
    if pago:  # livro pago não leva hashtag de grátis
        tags = " ".join(t for t in tags.split() if "gratis" not in t)
    linhas += ["", f"📚: {CONFIG['canal_assinatura']}", "", tags]
    return _cortar("\n".join(linhas), l, legenda_livro)


def legenda_oferta(o):
    e = html.escape
    titulo = o.get("titulo_curto") or re.split(r"[,|–-]", o.get("titulo_completo", ""))[0][:60]
    linhas = ["🛒 <b>ACHADO NA AMAZON</b>", "", f"<b>{e(titulo.strip())}</b>"]
    if o.get("texto_post"):
        linhas += ["", e(o["texto_post"])]
    linhas.append("")
    if o.get("avaliacoes"):
        linhas.append(f"⭐ {str(o['nota']).replace('.', ',')} · {_milhar(o['avaliacoes'])} avaliações")
    if o.get("preco_antigo") and o.get("desconto"):
        linhas.append(f"💰 De <s>{_reais(o['preco_antigo'])}</s> por <b>{_reais(o['preco'])}</b> (-{o['desconto']}%)")
    elif o.get("preco"):
        linhas.append(f"💰 Por <b>{_reais(o['preco'])}</b>")
    linhas.append("⚡ <b>OFERTA POR TEMPO LIMITADO</b>")
    linhas.append(f"<i>Preço conferido em {o.get('conferido_em', '')}. Pode mudar a qualquer momento.</i>")
    linhas += ["", f"📚: {CONFIG['canal_assinatura']}", "", _tags(o, ["#oferta", "#promocao", "#achadinhos"])]
    return "\n".join(linhas)


FORMATOS = {
    "dica": "uma dica útil e prática, com um gancho forte na primeira linha",
    "promocao": "divulgação de uma promoção ou produto, com benefício claro e chamada para ação",
    "novidade": "uma novidade ou notícia do nicho, curta e clara",
    "lista": "uma lista numerada de 3 a 5 indicações, cada uma com o motivo em poucas palavras",
    "pergunta": "uma pergunta para engajar o público a responder nos comentários",
    "aviso": "um aviso ou recado do canal para os seguidores",
}

PEDIDO_LIVRE = """Escreva UM post para um canal de Telegram em português do Brasil.
Nicho do canal: {nicho}
Formato: {formato}
O que o post precisa dizer: {tema}

Regras:
- No máximo 700 caracteres. Frases curtas, linhas curtas, fácil de ler no celular.
- Emojis com moderação, de preferência no começo das linhas.
- Para destacar, use **assim** (negrito). Não use outros símbolos de formatação.
- Não use travessão.
- Não invente preço, número, data, link, cupom ou fato que não esteja no pedido. Se faltar, escreva sem.
- Termine com 3 a 5 hashtags do nicho, sem acento.
Responda só com o texto do post, sem explicação."""


def post_livre(tema, formato="dica", nicho=""):
    """Escreve um post de qualquer nicho. Devolve o texto (com **negrito**) ou None."""
    pedido = PEDIDO_LIVRE.format(nicho=nicho or "geral", formato=FORMATOS.get(formato, FORMATOS["dica"]),
                                 tema=tema.strip())
    for funcao in (_via_api if os.environ.get("ANTHROPIC_API_KEY") else None, _via_claude_code):
        if not funcao:
            continue
        try:
            texto = (funcao(pedido) or "").strip()
            if texto:
                return texto.replace("—", ",").replace("–", ",")
        except Exception as e:
            print(f"[escritor] post livre falhou ({type(e).__name__}: {e})")
    return None


def _cortar(texto, item, refazer):
    """Foto no Telegram aceita até 1024 caracteres. Se passar, encurta a descrição."""
    if len(texto) <= 1024 or not item.get("texto_post"):
        return texto
    item["texto_post"] = item["texto_post"][: max(0, len(item["texto_post"]) - (len(texto) - 1021))] + "..."
    return refazer(item)


def legenda(item):
    return legenda_oferta(item) if item.get("tipo") == "oferta" else legenda_livro(item)


def botao(item):
    if item.get("tipo") == "oferta":
        texto = "🛒 VER OFERTA NA AMAZON"
    elif (item.get("preco") or 0) > 0:
        texto = "📲 VER O LIVRO NA AMAZON"
    else:
        texto = "📲 BAIXAR GRÁTIS NA AMAZON"
    return [{"text": texto, "url": item["link"]}]


if __name__ == "__main__":  # teste rápido: python escritor.py
    exemplo = {"asin": "X", "tipo": "livro", "titulo": "Teste", "autor": "Autor", "paginas": 200,
               "idioma": "português", "preco_sugerido": 3, "link": "https://amazon.com.br",
               "descricao": "Uma história de teste. Muito boa.", "tags": ["#romance"]}
    exemplo["texto_post"] = _resumo_simples(exemplo["descricao"])
    print(legenda(exemplo))
