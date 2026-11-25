"""Canal de prompts (@chatgpclaudegemini, "Prompts Milionários").

O robô garimpa prompts de imagem nas galerias (promptplum.com, prompts.chat e nanobananaprompt.org), reescreve em
português, puxa a imagem de exemplo da própria galeria (igual o espelho da Amazon puxa a foto do produto; só gera
pelo Cloudflare FLUX quando a fonte não tem imagem) e manda a prévia só para você com
✅ Aprovar / 🗑 Descartar. Só o que você aprova entra em prompts/posts.json e sai no canal nos horários
(09:00, 12:30, 18:00 e 21:00 de Manaus), e só com "ligado": true no config.json -> "prompts".

    python prompts.py garimpar           -> garimpa, gera até N imagens no dia e manda as prévias (1x por dia)
    python prompts.py agendado           -> posta se um horário venceu (só com o canal ligado)
    python prompts.py fontes             -> só mostra o que achou nas galerias, sem gastar nada
    python prompts.py previa ID [chat]   -> manda um post (aprovado ou candidato) só para você

Regras do canal: "Linha editorial - Prompts Milionários.md" (no cofre). Estilo: prompts/ESTILO.md
A imagem de exemplo vem da galeria, com o crédito da fonte no post.
"""
import base64
import hashlib
import html
import json
import os
import re
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import postador

try:  # console do Windows (cp1252) quebra no emoji dos títulos; troca o que não cabe em vez de parar
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

PASTA = Path(__file__).parent / "prompts"
BANCO = PASTA / "posts.json"
ESTADO = PASTA / "estado.json"
CFG = postador.CONFIG.get("prompts", {})
FUSO = timezone(timedelta(hours=-4))  # Manaus
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
FUNCIONA_NO = "Gemini (Nano Banana) · ChatGPT · Grok"

# Prompt que precisa da foto de quem lê (editar selfie, foto de referência) fica fora da v1.
PRECISA_FOTO = re.compile(r"\b(uploaded|upload|your (own )?(photo|picture|image|selfie|face)|reference (photo|image)|"
                          r"this (photo|image|picture)|the (attached|provided|given) (photo|image)|input image|"
                          r"keep the (face|person|subject)|same face|selfie|foto (sua|enviada)|sua foto|"
                          r"make (this|the) (character|person|image|photo)|keeping the original|this character)\b", re.I)
# Texto de site que não é prompt (explicação de ferramenta, legenda de post).
NAO_E_PROMPT = re.compile(r"(use tools like|software like|here'?s a prompt|great prompt|#\w*prompt)", re.I)
# Prompt de vídeo (câmera andando, FPV, cena em sequência) também fica fora.
E_VIDEO = re.compile(r"\b(fpv|single (uncut )?take|tracking shot|dolly|camera (moves|pans|flies|pushes)|"
                     r"seconds?\b.*\bscene|video|timelapse|time-lapse|slow motion|music:)\b", re.I)


# ---------------- estado e banco ----------------
def banco():
    return json.loads(BANCO.read_text(encoding="utf-8")) if BANCO.exists() else []


def estado():
    e = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    for k in ("postados", "noticias", "candidatos", "vistos"):
        e.setdefault(k, [])
    e.setdefault("contador", 0)
    return e


def salvar_estado(e):
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def canal():
    return CFG.get("canal", "@chatgpclaudegemini")


def ligado():
    return bool(CFG.get("ligado"))


# ---------------- o post ----------------
def hashtags(item):
    tags = ["prompts"] + [t.lstrip("#").lower() for t in item.get("tags", []) if t.lstrip("#").lower() != "prompts"]
    return " ".join("#" + t for t in tags[:5])


def corpo(item):
    """Texto do post em HTML do Telegram. O prompt vai num <pre> montado aqui (um toque copia)."""
    partes = [f"<p>⚙️ <b>Funciona no:</b> {html.escape(item.get('funciona_no') or FUNCIONA_NO)}</p>",
              f"<pre>{html.escape(item['prompt'])}</pre>"]
    if item.get("dica"):
        partes.append(f"<p>💡 {html.escape(item['dica'])}</p>")
    if item.get("imagem_da_fonte"):
        partes.append(f"<p>🖼 Imagem de exemplo: {html.escape(item.get('fonte', ''))}</p>")
    partes.append(f"<p>💾 Salve · 📤 Mande para um amigo · {html.escape(canal())}</p>")
    return "".join(partes)


def botao_canal():
    return f'<tg-button type="url" url="https://t.me/{canal().lstrip("@")}">📲 {html.escape(canal())}</tg-button>'


def enviar_dica(chat, item):
    """Posta o prompt: imagem + título + "Funciona no" + prompt num <pre> + dica + rodapé + hashtags."""
    img = PASTA / item["imagens"][0] if item.get("imagens") else None
    tem_img = bool(img and img.exists())
    rico_html = (f"<h2>{html.escape(item['titulo'])}</h2>" + ('<img src="tg://photo?id=f0"/>' if tem_img else "")
                 + corpo(item) + f"<p>{html.escape(hashtags(item))}</p>"
                 + f'<tg-button-row align="center">{botao_canal()}</tg-button-row>')
    try:
        with ExitStack() as pilha:
            arquivos = {"f0": pilha.enter_context(open(img, "rb"))} if tem_img else None
            rico = {"html": rico_html}
            if tem_img:
                rico["media"] = [{"id": "f0", "media": {"type": "photo", "media": "attach://f0"}}]
            return postador.tg("sendRichMessage", arquivos=arquivos, chat_id=chat, rich_message=rico)["message_id"]
    except RuntimeError as erro:  # plano B: foto com o título e, logo abaixo, o prompt para copiar
        print(f"Post rico falhou ({erro}); mandando foto + prompt separado.")
    cabeca = (f"<b>{html.escape(item['titulo'])}</b>\n\n⚙️ <b>Funciona no:</b> "
              f"{html.escape(item.get('funciona_no') or FUNCIONA_NO)}")
    if tem_img:
        with open(img, "rb") as f:
            mid = postador.tg("sendPhoto", arquivos={"photo": f}, chat_id=chat, caption=cabeca[:1024],
                              parse_mode="HTML")["message_id"]
    else:
        mid = postador.tg("sendMessage", chat_id=chat, text=cabeca, parse_mode="HTML")["message_id"]
    resto = (f"<pre>{html.escape(item['prompt'])}</pre>\n\n" + (f"💡 {html.escape(item['dica'])}\n\n" if item.get("dica") else "")
             + (f"🖼 Imagem de exemplo: {html.escape(item.get('fonte', ''))}\n" if item.get("imagem_da_fonte") else "")
             + f"💾 Salve · 📤 Mande para um amigo · {html.escape(canal())}\n{html.escape(hashtags(item))}")
    postador.tg("sendMessage", chat_id=chat, text=resto[:4096], parse_mode="HTML")
    return mid


# ---------------- garimpo nas galerias ----------------
def _limpo(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()


def de_prompts_chat(paginas=2):
    """prompts.chat tem uma lista pública em JSON. Só os de imagem que não pedem foto de quem lê."""
    achados = []
    for p in range(1, paginas + 1):
        r = requests.get("https://prompts.chat/api/prompts", params={"type": "IMAGE", "perPage": 30, "page": p},
                         headers=NAVEGADOR, timeout=30)
        r.raise_for_status()
        for x in r.json().get("prompts", []):
            imagem = x.get("mediaUrl") or ""
            if x.get("delistReason") or (x.get("requiresMediaUpload") and not imagem):
                continue
            autor = (x.get("author") or {})
            achados.append({"chave": f"pc:{x['id']}", "fonte": "prompts.chat", "titulo": x.get("title", ""),
                            "texto": (x.get("content") or "").strip(), "imagem_url": imagem,
                            "url": f"https://prompts.chat/prompts/{x['id']}_{x.get('slug', '')}",
                            "autor": autor.get("name") or autor.get("username") or ""})
    return achados


def _rsc(pagina):
    """Junta os pedaços de dados que as páginas em Next.js embutem (self.__next_f.push)."""
    partes = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', pagina)
    texto = []
    for p in partes:
        try:
            texto.append(json.loads('"' + p + '"'))
        except ValueError:
            pass
    return "".join(texto)


def _rsc_texto(rsc, valor):
    """No Next.js, texto longo vem como "$42" apontando para um pedaço "42:T<tamanho em hexa>,<texto>"."""
    if not isinstance(valor, str):
        return ""
    m = re.fullmatch(r"\$([0-9a-f]+)", valor)
    if not m:
        return valor
    ref = re.search(r"(?:^|\n|\})" + m.group(1) + r":T([0-9a-f]+),", rsc)
    if not ref:
        return ""
    tamanho = int(ref.group(1), 16)  # em bytes UTF-8
    return rsc[ref.end():].encode("utf-8")[:tamanho].decode("utf-8", "ignore")


def de_promptplum(quantos=12, vistos=(), paginas=30):
    """promptplum.com: o sitemap lista cada prompt com a imagem; a página traz o texto do prompt.
    Item sem o texto inteiro (premium ou página que mudou) volta com texto vazio, para o garimpo marcar como
    visto e não travar a fila. Lê no máximo `paginas` páginas por vez."""
    r = requests.get("https://promptplum.com/ai_prompt-sitemap.xml", headers=NAVEGADOR, timeout=30)
    r.raise_for_status()
    lista = []
    for bloco in re.findall(r"<url>(.*?)</url>", r.text, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", bloco)
        img = re.search(r"<image:loc>\s*(.*?)\s*</image:loc>", bloco)
        data = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", bloco)
        if loc and img and "/prompt/" in loc.group(1):
            slug = loc.group(1).rstrip("/").rsplit("/", 1)[-1]
            lista.append((data.group(1) if data else "", slug, loc.group(1), html.unescape(img.group(1))))
    lista.sort(reverse=True)  # mais novos primeiro
    achados, bons, lidas = [], 0, 0
    for _, slug, url, img in lista:
        if bons >= quantos or lidas >= paginas:
            break
        if f"pp:{slug}" in vistos:
            continue
        lidas += 1
        try:
            pagina = requests.get(url, headers=NAVEGADOR, timeout=30)
            pagina.raise_for_status()
        except requests.RequestException as erro:
            print(f"[garimpo] promptplum {slug}: {erro}")
            continue
        rsc = _rsc(pagina.text)
        dados = None
        for m in re.finditer(r'\{"id":"[0-9a-f]+","slug":"' + re.escape(slug) + '"', rsc):
            try:
                dados, _ = json.JSONDecoder().raw_decode(rsc, m.start())
                break
            except ValueError:
                continue
        dados = dados or {}
        # promptText é só o começo (150 letras): sem o fullPrompt o item não serve
        texto = "" if dados.get("isPremium") else (_rsc_texto(rsc, dados.get("fullPrompt")) or "")
        item = {"chave": f"pp:{slug}", "fonte": "PromptPlum", "titulo": dados.get("title", ""),
                "texto": texto.strip(), "url": url.split("?")[0], "autor": "",
                "imagem_url": img.replace("f=auto", "f=jpeg")}
        achados.append(item)
        bons += serve(item)
    return achados


def de_nanobanana(paginas=2):
    """nanobananaprompt.org: cada card tem o prompt num <p>, a imagem do post e o autor em data-author."""
    achados = []
    for p in range(1, paginas + 1):
        url = "https://nanobananaprompt.org/prompts" + (f"/page/{p}" if p > 1 else "")
        r = requests.get(url, headers=NAVEGADOR, timeout=30)
        r.raise_for_status()
        for bloco in re.split(r'(?=<div[^>]*\bdata-category=")', r.text)[1:]:
            m = re.search(r"<p[^>]*>(.*?)</p>", bloco, re.S)
            texto = _limpo(m.group(1)) if m else ""
            if not texto:
                continue
            if "video_thumb" in bloco:
                continue  # o exemplo é um vídeo
            imgs = re.findall(r'<img[^>]+src="(https://pbs\.twimg\.com/media/[^"]+)"', bloco)
            autor = re.search(r'data-author="([^"]*)"', bloco)
            chave = hashlib.sha1(texto[:300].lower().encode("utf-8")).hexdigest()[:14]
            achados.append({"chave": f"nb:{chave}", "fonte": "nanobananaprompt.org", "titulo": "", "texto": texto,
                            "url": url, "autor": autor.group(1) if autor else "",
                            "imagem_url": html.unescape(imgs[0]) if imgs else ""})
    return achados


def coletar(vistos=()):
    """Junta as galerias, uma de cada vez, para variar a fonte."""
    listas = []
    for funcao in (lambda: de_promptplum(vistos=vistos), de_prompts_chat, de_nanobanana):
        try:
            listas.append(funcao())
        except Exception as erro:
            print(f"[garimpo] uma galeria falhou: {erro}")
    juntos, vistos = [], set()
    for grupo in zip(*[l + [None] * (max(map(len, listas), default=0) - len(l)) for l in listas]) if listas else []:
        for x in grupo:
            if x and x["chave"] not in vistos:
                vistos.add(x["chave"])
                juntos.append(x)
    return juntos


def serve(x):
    """Filtro antes de gastar IA: prompt de imagem, de tamanho bom. Prompt que usa a foto de quem lê só passa
    se a galeria tiver a imagem de exemplo (sem ela não dá para mostrar o resultado)."""
    t = x["texto"]
    if not 60 <= len(t) <= 2500:
        return False
    if PRECISA_FOTO.search(t) and not x.get("imagem_url"):
        return False
    return not E_VIDEO.search(t) and not NAO_E_PROMPT.search(t)


# ---------------- reescrever e desenhar ----------------
def reescrever(x, titulos=()):
    """A IA reescreve o prompt em português no modelo do canal. Devolve dict ou None (recusado)."""
    import central
    import escritor
    estilo = (PASTA / "ESTILO.md").read_text(encoding="utf-8") if (PASTA / "ESTILO.md").exists() else ""
    pedido = f"""Você escreve posts para o canal do Telegram {canal()} ("Prompts Milionários"): prompts prontos para
a pessoa copiar e colar numa IA de imagem.

Guia do canal (siga à risca):
<guia>
{estilo}
</guia>

Prompt original garimpado (em inglês ou outra língua), de {x['fonte']}:
<prompt>
{x['texto'][:2500]}
</prompt>

{"A galeria tem uma imagem de exemplo pronta, que vai junto no post." if x.get("imagem_url") else "Não tem imagem de exemplo: o robô vai gerar uma a partir do prompt_en."}

Primeiro decida se ele serve. NÃO serve se: é de vídeo, é sobre pessoa famosa ou marca real, tem nudez, violência
ou algo sensível, ou é fraco/genérico demais.{"" if x.get("imagem_url") else " Também NÃO serve se precisa de uma foto da pessoa (selfie, foto de referência, editar imagem)."}
Se o prompt usa uma foto de quem lê (selfie, "foto enviada"), ele serve: escreva o prompt pedindo "a foto que eu
enviei" e a dica começa com "Mande sua foto junto com o prompt".
Tire do prompt qualquer assinatura, marca d'água ou nome de site (ex.: "Brand Signature: PromptPlum.com").
Títulos que já saíram (não repita o assunto): {"; ".join(list(titulos)[-40:])}

Responda só com um JSON, sem nada antes ou depois:
{{"serve": true ou false,
 "motivo": "se não serve, por quê (curto)",
 "titulo": "título curto com gancho, começando com 1 emoji, até 60 letras",
 "prompt": "o prompt inteiro reescrito em português do Brasil, pronto para colar. Mantenha todos os detalhes visuais que fazem o resultado ficar bom (luz, lente, estilo, cores, composição). Partes que a pessoa pode trocar ficam entre [colchetes], ex.: [sua cidade]. Sem aspas em volta. Não ponha "Funciona no" nem título aqui, só o prompt.",
 "prompt_en": "o mesmo prompt em inglês, para o robô gerar a imagem de prova. Troque os [colchetes] por um exemplo concreto.",
 "dica": "uma linha só dizendo o que trocar para personalizar",
 "tags": ["2 a 4 hashtags sem #, minúsculas, sem acento: a da IA (nanobanana) e 1 a 2 do tema"]}}

Regras: português simples, frases curtas, sem cara de IA (proibido "no mundo de hoje", "desbloqueie",
"revolucionário", "mergulhe"). Nada de travessão longo. Nunca escreva "imagens ilustrativas"."""
    texto = escritor.responder(pedido)
    if not texto:
        raise RuntimeError("Nenhuma IA respondeu para reescrever o prompt.")
    r = central._extrair_json(texto)
    if not r.get("serve"):
        print(f"[garimpo] recusado ({x['chave']}): {r.get('motivo', '')}")
        return None
    if not (r.get("titulo") and r.get("prompt") and (r.get("prompt_en") or x.get("imagem_url"))):
        print(f"[garimpo] a IA devolveu incompleto ({x['chave']})")
        return None
    return r


def gerar_imagem(prompt_en):
    """Imagem de prova pelo Cloudflare Workers AI (FLUX-1 schnell). Devolve os bytes do JPEG."""
    conta, chave = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
    if not (conta and chave):
        raise RuntimeError("Faltam CLOUDFLARE_ACCOUNT_ID e CLOUDFLARE_API_TOKEN no .env (ou nos segredos do GitHub).")
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{conta}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                      headers={"Authorization": f"Bearer {chave}"}, json={"prompt": prompt_en[:2048], "steps": 8},
                      timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Cloudflare respondeu {r.status_code}: {r.text[:200]}")
    return base64.b64decode(r.json()["result"]["image"])


def baixar_imagem(url):
    """Puxa a imagem de exemplo da galeria (como o espelho faz com a foto da Amazon). Devolve bytes ou None."""
    if not url:
        return None
    try:
        r = requests.get(url, headers=NAVEGADOR, timeout=60)
    except requests.RequestException as erro:
        print(f"[garimpo] imagem da fonte não baixou: {erro}")
        return None
    tipo = r.headers.get("content-type", "")
    if r.status_code != 200 or not tipo.startswith("image/") or len(r.content) < 5000:
        print(f"[garimpo] imagem da fonte inválida ({r.status_code}, {tipo}, {len(r.content)} bytes)")
        return None
    if "webp" in tipo or "avif" in tipo:  # o Telegram aceita melhor JPEG
        try:
            from io import BytesIO
            from PIL import Image
            saida = BytesIO()
            Image.open(BytesIO(r.content)).convert("RGB").save(saida, "JPEG", quality=90)
            return saida.getvalue()
        except Exception as erro:
            print(f"[garimpo] não converti {tipo} para JPEG: {erro}")
            return None
    return r.content


# ---------------- prévia com Aprovar / Descartar ----------------
def _slug(t):
    t = re.sub(r"[^\w\s-]", "", t.lower(), flags=re.A)
    return re.sub(r"[\s_-]+", "-", t).strip("-")[:30] or "prompt"


def mandar_candidato(chat, item):
    """Manda o post como vai sair + a fonte + os botões. Quem escuta os cliques é o robô do PC."""
    enviar_dica(chat, item)
    fonte = f"{item.get('fonte', '')}" + (f" · {item['autor']}" if item.get("autor") else "")
    postador.tg("sendMessage", chat_id=chat, disable_web_page_preview=True, parse_mode="HTML",
                text=(f"👆 Prompt novo garimpado para {html.escape(canal())}.\n"
                      f"Fonte: <a href=\"{html.escape(item.get('url', ''), quote=True)}\">{html.escape(fonte)}</a>\n"
                      "Aprovado, ele entra na fila e sai no próximo horário livre."),
                reply_markup={"inline_keyboard": [[
                    {"text": "✅ Aprovar", "callback_data": f"c:pa:{item['id'][:40]}"},
                    {"text": "🗑 Descartar", "callback_data": f"c:pd:{item['id'][:40]}"}]]})


def garimpar(limite=None, chat=None):
    """Garimpa, reescreve, gera a imagem (até o teto do dia) e manda as prévias. Nada vai para o canal."""
    import central
    if postador.pausa_total():
        print("Tudo pausado (pausa geral do painel).")
        return []
    chat = chat or postador.estado().get("admin")
    if not chat:
        raise RuntimeError("Sem admin no estado.json: mande /start para o robô primeiro.")
    pode_gerar = bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID") and os.environ.get("CLOUDFLARE_API_TOKEN"))
    e = central.estado("prompts", fresco=True)
    hoje = datetime.now(FUSO).strftime("%Y-%m-%d")
    cota = e.get("imagens") if (e.get("imagens") or {}).get("dia") == hoje else {"dia": hoje, "n": 0}
    teto = int(CFG.get("imagens_por_dia", 4))
    vagas = max(0, min(teto - cota["n"], limite if limite is not None else teto))
    if not vagas:
        print(f"Teto de {teto} prévias por dia já usado hoje.")
        return []
    vistos = set(e.get("vistos", [])) | {c.get("chave") for c in e.get("candidatos", [])}
    novos = [x for x in coletar(vistos) if x["chave"] not in vistos]
    achados = [x for x in novos if serve(x) and (x.get("imagem_url") or pode_gerar)]
    fora = [x["chave"] for x in novos if x not in achados]
    if fora:  # não servem: marca como vistos para não ocupar a vez dos bons na próxima rodada
        central.gravar("prompts", f"prompts: {len(fora)} fora do perfil",
                       mudar=lambda e: e.update(vistos=(e.get("vistos", []) + fora)[-3000:]))
    print(f"[garimpo] {len(achados)} prompts novos que servem; vou mandar até {vagas}.")
    titulos = [i.get("titulo", "") for i in central.banco_nuvem("prompts")] + [c.get("titulo", "") for c in e.get("candidatos", [])]
    feitos, tentativas = [], 0
    for x in achados:
        if len(feitos) >= vagas or tentativas >= vagas * 3:
            break
        tentativas += 1
        try:
            r = reescrever(x, titulos)
        except Exception as erro:
            print(f"[garimpo] {erro}")
            break
        if not r:
            central.gravar("prompts", f"prompts: recusado {x['chave']}",
                           mudar=lambda e, k=x["chave"]: e.update(vistos=(e.get("vistos", []) + [k])[-3000:]))
            continue
        cota_agora = central.estado("prompts", fresco=True).get("imagens") or {}
        usadas = cota_agora.get("n", 0) if cota_agora.get("dia") == hoje else 0
        if usadas >= teto:  # teto conferido de novo logo antes de gastar
            print(f"Teto de {teto} prévias por dia atingido.")
            break
        jpg = baixar_imagem(x.get("imagem_url"))
        da_fonte = jpg is not None
        if not da_fonte:
            # prompt que usa a foto de quem lê não tem como ganhar exemplo gerado
            if not (pode_gerar and r.get("prompt_en")) or PRECISA_FOTO.search(x["texto"]):
                print(f"[garimpo] {x['chave']} ficou sem imagem; pulei.")
                central.gravar("prompts", f"prompts: sem imagem {x['chave']}",
                               mudar=lambda e, k=x["chave"]: e.update(vistos=(e.get("vistos", []) + [k])[-3000:]))
                continue
            try:
                jpg = gerar_imagem(r["prompt_en"])
            except Exception as erro:
                print(f"[garimpo] imagem falhou: {erro}")
                break
        pid = f"{datetime.now(FUSO):%y%m%d}-{len(feitos) + 1}-{_slug(r['titulo'])}"
        texto = re.sub(r"^\W*funciona no:[^\n]*\n+", "", r["prompt"].strip(), flags=re.I)  # o post já tem essa linha
        item = {"id": pid, "tipo": "prompt", "titulo": r["titulo"].strip(), "prompt": texto.strip(),
                "prompt_en": (r.get("prompt_en") or "").strip(), "dica": (r.get("dica") or "").strip(),
                "funciona_no": FUNCIONA_NO, "tags": r.get("tags") or [], "imagens": [f"img/{pid}.jpg"],
                "imagem_da_fonte": da_fonte, "imagem_url": x.get("imagem_url", "") if da_fonte else "",
                "fonte": x["fonte"], "url": x["url"], "autor": x["autor"], "chave": x["chave"],
                "garimpado": central.agora_iso()}
        item["corpo"] = corpo(item)

        def mudar(e, item=item, k=x["chave"]):
            c = e.get("imagens") if (e.get("imagens") or {}).get("dia") == hoje else {"dia": hoje, "n": 0}
            e["imagens"] = {"dia": hoje, "n": c["n"] + 1}
            e["candidatos"] = e.get("candidatos", []) + [item]
            e["vistos"] = (e.get("vistos", []) + [k])[-3000:]
        central.gravar("prompts", f"prompts: candidato {pid}", mudar=mudar, arquivos={f"prompts/img/{pid}.jpg": jpg})
        try:
            mandar_candidato(chat, item)
        except Exception as erro:
            print(f"[garimpo] prévia de {pid} falhou: {erro}")
        feitos.append(item)
        titulos.append(item["titulo"])
        print(f"[garimpo] candidato {pid}: {item['titulo']}")
    return feitos


def candidato(alvo, e=None):
    import central
    e = e or central.estado("prompts", fresco=True)
    return next((c for c in e.get("candidatos", []) if c["id"] == alvo or c["id"].startswith(alvo)), None)


def aprovar(alvo):
    """Tira dos candidatos e põe no fim da fila (prompts/posts.json)."""
    import central
    c = candidato(alvo)
    if not c:
        raise central.Erro("Esse prompt não está mais esperando aprovação.")
    item = {k: v for k, v in c.items() if k != "garimpado"}

    def mudar(e):
        e["candidatos"] = [x for x in e.get("candidatos", []) if x["id"] != c["id"]]

    def mudar_banco(b):
        if not any(x["id"] == c["id"] for x in b):
            b.append(item)
    central.gravar("prompts", f"prompts: aprovado {c['id']}", mudar=mudar, mudar_banco=mudar_banco)
    return item


def descartar(alvo):
    import central
    c = candidato(alvo)
    if not c:
        raise central.Erro("Esse prompt não está mais esperando aprovação.")
    central.gravar("prompts", f"prompts: descartado {c['id']}",
                   mudar=lambda e: e.update(candidatos=[x for x in e.get("candidatos", []) if x["id"] != c["id"]]),
                   arquivos={f"prompts/{i}": None for i in c.get("imagens", [])})
    return c


def tratar_candidato(cb, acao, alvo):
    """Botões ✅ Aprovar (a) e 🗑 Descartar (d) da prévia do garimpo."""
    import central
    try:
        if acao == "a":
            aprovar(alvo)
            fila = len(central.fila("prompts", central.estado("prompts", fresco=True), central.banco_nuvem("prompts")))
            aviso = "" if ligado() else " (canal ainda desligado: liga no config.json)"
            postador.trocar_botoes(cb, f"✅ Aprovado. Na fila: {fila}{aviso}")
        else:
            descartar(alvo)
            postador.trocar_botoes(cb, "🗑 Descartado")
    except central.Erro as erro:
        postador.trocar_botoes(cb, str(erro))
    except Exception as erro:
        print(f"[prompts] botão falhou: {erro}")
        postador.trocar_botoes(cb, "⚠️ Não consegui. Tente de novo")


# ---------------- horários ----------------
def horarios():
    return sorted(CFG.get("horarios") or ["09:00", "12:30", "18:00", "21:00"])


def horario_devido(agora=None):
    """Último horário que venceu sem post (até 6 horas atrás), ou None. Igual ao da renda extra."""
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
    import central
    ultimo = central.estado("prompts", fresco=True).get("ultimo")
    if ultimo and datetime.fromisoformat(ultimo) >= alvo:
        return None
    if agora - alvo > timedelta(hours=6):
        return None
    return alvo


def postar_se_devido():
    import central
    if not ligado():
        print('Canal de prompts desligado ("ligado": false no config.json). Nada sai.')
        return False
    if postador.pausa_total():
        print("Tudo pausado (pausa geral do painel).")
        return False
    if central.postar_agendados("prompts"):
        return True
    devido = horario_devido()
    if not devido:
        print("Nenhum horário de prompts vencido agora.")
        return False
    print(f"Horário vencido: {devido:%d/%m %H:%M} (Manaus).")
    item, _ = central.postar("prompts")
    if not item:
        print("Fila vazia: nenhum prompt aprovado esperando. Nada sai (o canal não repete post).")
        return False
    return True


if __name__ == "__main__":
    postador.carregar_env()
    acao = sys.argv[1] if len(sys.argv) > 1 else "agendado"
    if acao == "fontes":
        for x in coletar()[:30]:
            print(f"[{'ok' if serve(x) else '--'}] {x['fonte']:22} {x['texto'][:90]}")
    elif acao == "garimpar":
        garimpar(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif acao == "previa":
        import central
        alvo = sys.argv[2]
        chat = sys.argv[3] if len(sys.argv) > 3 else postador.estado().get("admin")
        c = candidato(alvo)
        mandar_candidato(chat, c) if c else central.previa("prompts", alvo, chat)
    else:
        postar_se_devido()
