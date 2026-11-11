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


def desenhar(tudo=False):
    posts = json.loads((PASTA / "posts.json").read_text(encoding="utf-8"))
    (PASTA / "img").mkdir(exist_ok=True)
    feitos = 0
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1080, "height": 1350})
        for post in posts:
            destino = PASTA / post["imagens"][0]
            if "arte" not in post or (destino.exists() and not tudo):
                continue
            pag.set_content(montar(post["arte"], post["tipo"] == "quiz"))
            pag.screenshot(path=str(destino))
            feitos += 1
        nav.close()
    print(f"{feitos} arte(s) desenhada(s).")


if __name__ == "__main__":
    desenhar(len(sys.argv) > 1 and sys.argv[1] == "tudo")
