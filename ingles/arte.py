"""Desenha a arte 4:5 dos posts do canal de inglês a partir do próprio post (título + tabela do corpo).

    python ingles/arte.py          -> desenha só os posts que ainda não têm imagem
    python ingles/arte.py 031      -> redesenha o post 031

O texto sai do posts.json, então não tem erro de letra como numa imagem de IA.
Paleta da bandeira dos EUA, flat, sem degradê; "@inglescomia" no rodapé (ESTILO.md).
"""
import html
import json
import re
import sys
from pathlib import Path

PASTA = Path(__file__).parent
CANAL = "@inglescomia"

MODELO = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700;800&family=Noto+Color+Emoji&display=block" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1350px;background:#0A3161;color:#fff;font-family:Montserrat,"Segoe UI",Arial,sans-serif;
  display:flex;flex-direction:column;padding:64px 64px 0;position:relative;overflow:hidden}
.e{font-family:"Noto Color Emoji","Segoe UI Emoji","Apple Color Emoji",sans-serif;font-weight:400}
.fundo{position:absolute;inset:-120px;font-size:46px;color:#fff;opacity:.05;line-height:110px;letter-spacing:60px;
  word-break:break-all;transform:rotate(-12deg)}
.faixa{position:absolute;left:0;right:0;top:0;height:18px;background:repeating-linear-gradient(90deg,#B31942 0 90px,#fff 90px 180px)}
.topo{display:flex;align-items:flex-start;gap:28px;position:relative}
.esq{flex:1}
.selo{display:inline-block;background:#B31942;color:#fff;font-weight:800;font-size:26px;letter-spacing:3px;
  padding:10px 22px;border-radius:999px}
h1{font-size:__TAM__px;line-height:1.08;font-weight:800;margin:26px 0 0;letter-spacing:-1px}
.heroi{flex:none;width:210px;height:210px;border-radius:50%;background:#fff;display:grid;place-items:center;
  font-size:120px;box-shadow:0 0 0 14px rgba(255,255,255,.15),0 0 0 30px rgba(255,255,255,.06);margin-top:10px}
.cols{display:flex;gap:18px;margin:34px 0 0;position:relative;font-size:24px;font-weight:800;letter-spacing:2px;color:#9DB4D6}
.cols span{flex:1}
.lista{flex:1;display:flex;flex-direction:column;justify-content:center;gap:__GAP__px;margin:14px 0 30px;position:relative}
.item{display:flex;align-items:center;gap:22px;background:#fff;color:#0A3161;border-radius:24px;padding:__PAD__px 26px;
  box-shadow:0 7px 0 #B31942}
.n{flex:none;width:__NW__px;height:__NW__px;border-radius:50%;background:#B31942;color:#fff;display:grid;place-items:center;
  font-size:__NF__px;font-weight:800}
.tx{flex:1;display:flex;gap:18px;align-items:center}
.en{flex:1;font-size:__EN__px;font-weight:800;line-height:1.15}
.pt{flex:1;font-size:__PT__px;font-weight:600;line-height:1.2;color:#4A5B78}
.rod{position:relative;background:#B31942;color:#fff;margin:0 -64px;padding:26px 64px;display:flex;
  justify-content:space-between;align-items:center;font-size:30px;font-weight:800}
</style></head><body><div class="fundo">__FUNDO__</div><div class="faixa"></div>
<div class="topo"><div class="esq"><div class="selo">__SELO__</div><h1>__TITULO__</h1></div>
<div class="heroi e">__ICONE__</div></div>
__COLS__<div class="lista">__CORPO__</div>
<div class="rod"><span><span class="e">📲</span> __CANAL__</span><span><span class="e">📌</span> Salve e compartilhe</span></div>
</body></html>"""

SELOS = {"lista": "VOCABULÁRIO", "palavra": "EXPRESSÃO DO DIA", "aula": "MINI-AULA", "quiz": "QUIZ"}
EMOJI_INICIAL = re.compile(r"^([^\w\s\"'(]+)\s*")


def _limpo(t):
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("✅", "").replace("❌", "✗ ").strip()
    return re.sub(r"\s+", " ", t)


def tabela(corpo):
    """(cabeçalhos, linhas) da primeira tabela do corpo. Cada linha é uma lista de células em texto puro."""
    m = re.search(r"<table>(.*?)</table>", corpo or "", re.S)
    if not m:
        return [], []
    cab = [_limpo(c) for c in re.findall(r"<th>(.*?)</th>", m.group(1), re.S)]
    linhas = [[_limpo(c) for c in re.findall(r"<td>(.*?)</td>", tr, re.S)]
              for tr in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S)]
    return cab, [l for l in linhas if l]


def _negritos(corpo):
    return [_limpo(b) for b in re.findall(r"<b>(.*?)</b>", corpo or "", re.S) if len(_limpo(b)) > 1][:6]


def montar(item):
    titulo = item.get("titulo", "")
    m = EMOJI_INICIAL.match(titulo)
    icone = m.group(1) if m else "🇺🇸"
    titulo = titulo[m.end():] if m else titulo
    tipo = item.get("tipo", "lista")
    cols = ""
    if tipo == "quiz":
        q = item.get("quiz", {})
        titulo = q.get("pergunta", titulo)
        linhas = [[f"{o}", ""] for o in q.get("opcoes", [])]
        marcas = "ABCDEF"
    else:
        cab, linhas = tabela(item.get("corpo", ""))
        if not linhas:
            linhas = [[b, ""] for b in _negritos(item.get("corpo", ""))]
        marcas = None
        if len(cab) >= 2 and not all(re.search(r"ingl|portug", c, re.I) for c in cab[:2]):
            cols = f'<div class="cols"><span>{html.escape(cab[0])}</span><span>{html.escape(cab[1])}</span></div>'
    linhas = linhas[:8]
    n = len(linhas) or 1
    medidas = {  # (inglês, português, padding, bolinha, fonte da bolinha, espaço)
        2: (50, 38, 40, 76, 34, 34), 3: (46, 36, 34, 72, 32, 30), 4: (44, 34, 28, 68, 30, 24),
        5: (40, 32, 22, 62, 28, 20), 6: (37, 30, 18, 58, 26, 16), 7: (34, 28, 14, 54, 24, 13), 8: (31, 26, 11, 50, 22, 11)}
    en, pt, pad, nw, nf, gap = medidas.get(n, medidas[2] if n < 2 else medidas[8])
    corpo = ""
    for i, l in enumerate(linhas):
        marca = marcas[i] if marcas else f"{i + 1}"
        dir_ = f'<div class="pt">{html.escape(l[1])}</div>' if len(l) > 1 and l[1] else ""
        corpo += (f'<div class="item"><div class="n">{marca}</div><div class="tx">'
                  f'<div class="en">{html.escape(l[0])}</div>{dir_}</div></div>')
    tam = 76 if len(titulo) < 34 else 66 if len(titulo) < 52 else 56 if len(titulo) < 75 else 48
    trocas = {"__TITULO__": html.escape(titulo), "__ICONE__": icone, "__SELO__": SELOS.get(tipo, "INGLÊS"),
              "__CORPO__": corpo, "__COLS__": cols, "__CANAL__": CANAL, "__FUNDO__": "★ " * 300,
              "__TAM__": str(tam), "__EN__": str(en), "__PT__": str(pt), "__PAD__": str(pad),
              "__NW__": str(nw), "__NF__": str(nf), "__GAP__": str(gap)}
    pagina = MODELO
    for k, v in trocas.items():
        pagina = pagina.replace(k, v)
    return pagina


def png(item):
    """Bytes do PNG 1080x1350 da arte do post."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1080, "height": 1350})
        pag.set_content(montar(item), wait_until="networkidle", timeout=40000)
        pag.evaluate("document.fonts.ready")
        # Frases longas: encolhe a lista até o rodapé caber inteiro na arte.
        pag.evaluate("""() => {
            const l = document.querySelector('.lista'), r = document.querySelector('.rod');
            let z = 1;
            while (r.getBoundingClientRect().bottom > 1350 && z > 0.5) { z -= 0.03; l.style.zoom = z; }
        }""")
        dados = pag.screenshot()
        nav.close()
    return dados


def desenhar(so=None):
    """Desenha a arte dos posts sem imagem (ou dos números em `so`) e grava o posts.json."""
    banco = PASTA / "posts.json"
    posts = json.loads(banco.read_text(encoding="utf-8"))
    feitos = 0
    for post in posts:
        num = post["id"][:3]
        if so and num not in so:
            continue
        if not so and post.get("imagens"):
            continue
        nome = f"img/{num}.png"
        (PASTA / nome).write_bytes(png(post))
        post["imagens"] = [nome]
        feitos += 1
        print(f"{post['id']} -> {nome}")
    banco.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{feitos} arte(s) desenhada(s).")


if __name__ == "__main__":
    desenhar([a for a in sys.argv[1:] if a.isdigit()] or None)
