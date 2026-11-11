"""Desenha as artes do canal de renda extra a partir do campo "arte" de renda/posts.json.

    python renda/arte.py          -> desenha só as que ainda não existem
    python renda/arte.py tudo     -> redesenha todas

Campo "arte": titulo, sub, icone (emoji grande do tema), itens [..], icones [um emoji por item].
Arte própria, 4:5 (1080x1350), verde-escuro + dourado, flat, com ícones coloridos.
O texto sai do posts.json, então não tem erro de letra como numa imagem de IA.
"""
import html
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PASTA = Path(__file__).parent
CANAL = "@rendaextraparatodos"
EMOJI = '"Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji"'

MODELO = """<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1350px;background:#0E3B2E;color:#F7F3E8;font-family:"Segoe UI",Arial,sans-serif;
  display:flex;flex-direction:column;padding:72px 72px 0;position:relative;overflow:hidden}
.fundo{position:absolute;inset:0;font-family:Georgia,serif;font-weight:700;font-size:64px;color:#F2C14E;opacity:.05;
  line-height:120px;letter-spacing:70px;word-break:break-all;transform:rotate(-12deg) scale(1.3);pointer-events:none}
.topo{display:flex;align-items:flex-start;gap:24px;position:relative}
.esq{flex:1}
.selo{display:inline-block;background:#F2C14E;color:#0E3B2E;font-weight:800;font-size:26px;letter-spacing:3px;
  padding:10px 22px;border-radius:999px}
h1{font-size:__TAM__px;line-height:1.06;font-weight:800;margin:30px 0 14px;letter-spacing:-1px}
h1 em{font-style:normal;color:#F2C14E}
.sub{font-size:__SUBT__px;line-height:1.25;color:#F2C14E;font-weight:600}
.heroi{flex:none;width:250px;height:250px;border-radius:50%;background:#F2C14E;display:grid;place-items:center;
  font-family:__EMOJI__;font-size:150px;box-shadow:0 0 0 14px rgba(242,193,78,.18),0 0 0 30px rgba(242,193,78,.08);
  margin:6px 4px 0 0;transform:rotate(-6deg)}
.lista{flex:1;display:flex;flex-direction:column;justify-content:center;gap:__GAP__px;margin:30px 0 34px;position:relative}
.item{display:flex;align-items:center;gap:24px;background:#F7F3E8;color:#0E3B2E;border-radius:26px;padding:__PAD__px 28px;
  box-shadow:0 8px 0 rgba(0,0,0,.18)}
.item .ic{flex:none;width:__ICW__px;height:__ICW__px;border-radius:20px;background:#FBE3A6;display:grid;place-items:center;
  font-family:__EMOJI__;font-size:__ICF__px}
.item .tx{font-size:__ITEM__px;font-weight:700;line-height:1.15;flex:1}
.item .n{flex:none;font-size:28px;font-weight:800;color:#0E3B2E;opacity:.35}
.letra{flex:none;width:__ICW__px;height:__ICW__px;border-radius:50%;background:#0E3B2E;color:#F2C14E;display:grid;
  place-items:center;font-size:34px;font-weight:800}
.rod{position:relative;background:#F2C14E;color:#0E3B2E;margin:0 -72px;padding:28px 72px;display:flex;
  justify-content:space-between;align-items:center;font-size:30px;font-weight:800}
.rod span{font-family:"Segoe UI",__EMOJI__}
</style></head><body><div class="fundo">__FUNDO__</div>
<div class="topo"><div class="esq"><div class="selo">RENDA EXTRA</div><h1>__TITULO__</h1>__SUB__</div>
<div class="heroi">__ICONE__</div></div>
<div class="lista">__CORPO__</div>
<div class="rod"><span>📲 __CANAL__</span><span>📌 Salve e compartilhe</span></div></body></html>"""


def titulo_html(t):
    """Destaca em dourado o número inicial."""
    t = html.escape(t)
    p = t.split(" ", 1)
    if p[0].isdigit() and len(p) > 1:
        return f"<em>{p[0]}</em> {p[1]}"
    return t


def montar(arte, quiz):
    itens = arte.get("itens", [])
    icones = arte.get("icones") or []
    n = len(itens)
    tam = 84 if len(arte["titulo"]) < 30 else 72 if len(arte["titulo"]) < 46 else 64
    if quiz:
        corpo = "".join(f'<div class="item"><div class="letra">{"ABCD"[i]}</div><div class="tx">{html.escape(t)}</div></div>'
                        for i, t in enumerate(itens))
    else:
        corpo = "".join(f'<div class="item"><div class="ic">{icones[i] if i < len(icones) else "✅"}</div>'
                        f'<div class="tx">{html.escape(t)}</div><div class="n">{i + 1:02d}</div></div>'
                        for i, t in enumerate(itens))
    medidas = {  # (fonte do item, padding, ícone, fonte do ícone, espaço)
        4: (46, 30, 96, 60, 26), 5: (44, 24, 88, 54, 22), 6: (40, 18, 80, 50, 18), 7: (37, 13, 72, 44, 14)}
    item, pad, icw, icf, gap = medidas.get(n, medidas[7] if n > 7 else medidas[4])
    sub = f'<p class="sub">{html.escape(arte["sub"])}</p>' if arte.get("sub") else ""
    trocas = {"__TITULO__": titulo_html(arte["titulo"]), "__SUB__": sub, "__CORPO__": corpo, "__CANAL__": CANAL,
              "__ICONE__": arte.get("icone", "💵"), "__FUNDO__": "$ " * 400, "__EMOJI__": EMOJI,
              "__TAM__": str(tam), "__SUBT__": "40" if quiz else "32", "__ITEM__": str(item), "__PAD__": str(pad),
              "__ICW__": str(icw), "__ICF__": str(icf), "__GAP__": str(gap)}
    pagina = MODELO
    for k, v in trocas.items():
        pagina = pagina.replace(k, v)
    return pagina


# ---------------- visual 2: ilustração + pattern (usado quando existe renda/ilustra/NNN.png) ----------------
PATTERN = ("data:image/svg+xml;utf8,"
           "<svg xmlns='http://www.w3.org/2000/svg' width='72' height='72' viewBox='0 0 72 72'>"
           "<g fill='none' stroke='%230E3B2E' stroke-width='1.2' opacity='.10'>"
           "<circle cx='0' cy='0' r='36'/><circle cx='72' cy='0' r='36'/><circle cx='0' cy='72' r='36'/>"
           "<circle cx='72' cy='72' r='36'/><circle cx='36' cy='36' r='36'/></g></svg>")

MODELO2 = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800&family=DM+Serif+Display&display=block" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1350px;background:#F7F3E8 url("__PATTERN__");color:#0E3B2E;font-family:Montserrat,"Segoe UI",sans-serif;
  display:flex;flex-direction:column;overflow:hidden}
.topo{display:flex;justify-content:space-between;align-items:center;padding:48px 64px 26px}
.selo{background:#0E3B2E;color:#F2C14E;font-weight:800;font-size:22px;letter-spacing:4px;padding:10px 22px;border-radius:999px}
.num{font-family:"DM Serif Display",serif;font-size:32px;color:#C8643B}
.quadro{margin:0 78px 0 64px;height:__ALT__px;border-radius:30px;overflow:hidden;flex:none;
  box-shadow:14px 14px 0 #F2C14E;border:3px solid #0E3B2E}
.quadro img{width:100%;height:100%;object-fit:cover;object-position:center __FOCO__;display:block}
.txt{flex:1;padding:44px 64px 30px;display:flex;flex-direction:column}
h1{font-family:"DM Serif Display",Georgia,serif;font-weight:400;font-size:__TAM__px;line-height:1.04;letter-spacing:-.5px}
h1 em{font-style:normal;color:#C8643B}
.sub{font-size:28px;line-height:1.3;font-weight:600;margin-top:14px;color:#3E5F52}
.lista{margin-top:auto;display:flex;flex-direction:column}
.it{display:flex;align-items:center;gap:22px;padding:__PAD__px 0;border-top:2px solid rgba(14,59,46,.16);font-size:__ITEM__px;font-weight:700}
.it:last-child{border-bottom:2px solid rgba(14,59,46,.16)}
.it b{flex:none;width:__BOLA__px;height:__BOLA__px;border-radius:50%;border:2.5px solid #0E3B2E;display:grid;place-items:center;
  font-size:__NUMF__px;font-weight:800;background:#F2C14E}
.rod{background:#0E3B2E;color:#F7F3E8;padding:26px 64px;display:flex;justify-content:space-between;font-size:27px;font-weight:700}
.rod span:last-child{color:#F2C14E}
</style></head><body>
<div class="topo"><div class="selo">RENDA EXTRA</div><div class="num">__ROTULO__</div></div>
<div class="quadro"><img src="__IMG__"></div>
<div class="txt"><h1>__TITULO__</h1>__SUB__<div class="lista">__CORPO__</div></div>
<div class="rod"><span>@rendaextraparatodos</span><span>Salve e compartilhe</span></div>
</body></html>"""


def montar2(arte, quiz, ilustra):
    import base64
    itens = arte.get("itens", [])
    n = len(itens)
    t = arte["titulo"]
    tam = 76 if len(t) < 32 else 66 if len(t) < 48 else 58
    letra = "ABCD" if quiz else None
    corpo = "".join(f'<div class="it"><b>{letra[i] if quiz else i + 1}</b>{html.escape(x)}</div>' for i, x in enumerate(itens))
    # (altura da ilustração, fonte do item, espaço, bola, fonte do número)
    med = {4: (540, 38, 18, 56, 26), 5: (490, 36, 15, 52, 24), 6: (450, 34, 12, 48, 22), 7: (390, 31, 10, 44, 20)}
    alt, item, pad, bola, numf = med.get(n, med[7] if n > 7 else med[4])
    if quiz:
        alt -= 60
    sub = f'<p class="sub">{html.escape(arte["sub"])}</p>' if arte.get("sub") else ""
    img = "data:image/png;base64," + base64.b64encode(Path(ilustra).read_bytes()).decode()
    trocas = {"__PATTERN__": PATTERN, "__IMG__": img, "__TITULO__": titulo_html(t), "__SUB__": sub, "__CORPO__": corpo,
              "__ROTULO__": "Quiz" if quiz else f"{n} dicas", "__TAM__": str(tam), "__ALT__": str(alt),
              "__ITEM__": str(item), "__PAD__": str(pad), "__BOLA__": str(bola), "__NUMF__": str(numf),
              "__FOCO__": arte.get("foco", "45%")}
    pagina = MODELO2
    for k, v in trocas.items():
        pagina = pagina.replace(k, v)
    return pagina


def desenhar(tudo=False, so=None):
    """so = lista de números ('006', '017') para redesenhar só esses."""
    posts = json.loads((PASTA / "posts.json").read_text(encoding="utf-8"))
    if so:
        posts = [x for x in posts if x["id"][:3] in so]
        tudo = True
    (PASTA / "img").mkdir(exist_ok=True)
    feitos = 0
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1080, "height": 1350})
        for post in posts:
            destino = PASTA / post["imagens"][0]
            if "arte" not in post or (destino.exists() and not tudo):
                continue
            ilustra = PASTA / "ilustra" / Path(post["imagens"][0]).name
            if ilustra.exists():
                pag.set_content(montar2(post["arte"], post["tipo"] == "quiz", ilustra), wait_until="networkidle")
                pag.evaluate("document.fonts.ready")
            else:
                pag.set_content(montar(post["arte"], post["tipo"] == "quiz"))
            pag.screenshot(path=str(destino))
            feitos += 1
        nav.close()
    print(f"{feitos} arte(s) desenhada(s).")


if __name__ == "__main__":
    args = sys.argv[1:]
    desenhar(bool(args) and args[0] == "tudo", [a for a in args if a.isdigit()] or None)


# ---------------- capa das notícias (roda no PC e na nuvem) ----------------
SELOS = {"curso": ("🎓", "CURSO GRÁTIS"), "vaga": ("💼", "VAGA HOME OFFICE"),
         "renda": ("💡", "RENDA EXTRA"), "golpe": ("🚨", "ALERTA DE GOLPE")}

CAPA = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;800&family=Noto+Color+Emoji&display=block" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:1080px;height:1350px;background:#0E3B2E;color:#F7F3E8;font-family:Montserrat,"Segoe UI",Arial,sans-serif;
  display:flex;flex-direction:column;overflow:hidden;position:relative}
.e{font-family:"Noto Color Emoji","Segoe UI Emoji","Apple Color Emoji",sans-serif;font-weight:400}
.foto{height:700px;position:relative;background:#0B2F25;overflow:hidden}
.foto img{width:100%;height:100%;object-fit:cover;display:block}
.vazio{height:100%;display:grid;place-items:center;position:relative}
.vazio .cif{position:absolute;inset:-100px;font-family:Georgia,serif;font-weight:700;font-size:64px;color:#F2C14E;opacity:.07;
  line-height:120px;letter-spacing:70px;word-break:break-all;transform:rotate(-12deg)}
.vazio .bola{width:380px;height:380px;border-radius:50%;background:#F2C14E;display:grid;place-items:center;font-size:220px;
  box-shadow:0 0 0 22px rgba(242,193,78,.18),0 0 0 46px rgba(242,193,78,.08);position:relative}
.selo{position:absolute;left:56px;top:664px;z-index:5;background:#F2C14E;color:#0E3B2E;font-weight:800;font-size:34px;letter-spacing:2px;
  padding:14px 30px;border-radius:999px;display:flex;gap:14px;align-items:center;box-shadow:0 8px 0 rgba(0,0,0,.25)}
.tipo{position:absolute;right:40px;top:40px;background:#0E3B2E;color:#F7F3E8;font-weight:800;font-size:26px;letter-spacing:3px;
  padding:10px 22px;border-radius:999px;display:flex;gap:10px;align-items:center}
.txt{flex:1;padding:86px 56px 30px;display:flex;flex-direction:column}
h1{font-size:__TAM__px;line-height:1.12;font-weight:800;letter-spacing:-.5px}
.fonte{margin-top:auto;display:flex;justify-content:space-between;font-size:28px;font-weight:600;color:#F2C14E}
.rod{background:#F2C14E;color:#0E3B2E;padding:26px 56px;display:flex;justify-content:space-between;font-size:30px;font-weight:800}
</style></head><body>
<div class="foto">__FOTO__<div class="tipo"><span class="e">📰</span>NOTÍCIA</div>
</div><div class="selo"><span class="e">__ICONE__</span>__SELO__</div>
<div class="txt"><h1>__TITULO__</h1><div class="fonte"><span>__FONTE__</span><span><span class="e">👉</span> Link na legenda</span></div></div>
<div class="rod"><span><span class="e">📲</span> __CANAL__</span><span><span class="e">🔁</span> Mande para um amigo</span></div>
</body></html>"""


def capa_noticia(titulo, tema, fonte, destino, foto=None, foto_tipo="image/jpeg"):
    """Desenha a capa 4:5 de uma notícia. foto = bytes da imagem da matéria (ou None: capa ilustrada)."""
    import base64
    icone, selo = SELOS.get(tema, SELOS["renda"])
    if foto:
        topo = f'<img src="data:{foto_tipo};base64,{base64.b64encode(foto).decode()}">'
    else:
        topo = f'<div class="vazio"><div class="cif">{"$ " * 300}</div><div class="bola e">{icone}</div></div>'
    tam = 70 if len(titulo) < 70 else 62 if len(titulo) < 95 else 54 if len(titulo) < 125 else 48
    pagina = (CAPA.replace("__FOTO__", topo).replace("__ICONE__", icone).replace("__SELO__", selo)
              .replace("__TITULO__", html.escape(titulo)).replace("__FONTE__", html.escape(f"Fonte: {fonte}" if fonte else ""))
              .replace("__CANAL__", CANAL).replace("__TAM__", str(tam)))
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1080, "height": 1350})
        pag.set_content(pagina, wait_until="networkidle", timeout=40000)
        pag.evaluate("document.fonts.ready")
        pag.screenshot(path=str(destino), type="jpeg", quality=90)
        nav.close()
    return destino
