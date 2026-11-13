"""Painel de administração + motor do robô, num programa só.

Ligar:  python painel.py
Abrir:  http://localhost:8800   (senha: PAINEL_SENHA no arquivo .env)
"""
import asyncio
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import banco
import metricas
import postador

PASTA = Path(__file__).parent
UPLOADS = PASTA / "uploads"
UPLOADS.mkdir(exist_ok=True)
postador.carregar_env()

if not os.environ.get("PAINEL_SENHA"):
    senha = secrets.token_urlsafe(9)
    with open(PASTA / ".env", "a", encoding="utf-8") as f:
        f.write(f"\n# Senha do painel (pode trocar)\nPAINEL_SENHA={senha}\n")
    os.environ["PAINEL_SENHA"] = senha

TOKEN = hmac.new(os.environ["PAINEL_SENHA"].encode(), b"painel", hashlib.sha256).hexdigest()
app = FastAPI(docs_url=None, redoc_url=None)


@app.middleware("http")
async def exigir_senha(request: Request, chamar):
    livre = request.url.path in ("/", "/api/login", "/api/login-telegram") or not request.url.path.startswith("/api")
    chave = request.headers.get("x-painel") or request.cookies.get("painel", "")
    if not livre and not hmac.compare_digest(chave, TOKEN):
        return JSONResponse({"erro": "Entre com a senha."}, status_code=401)
    return await chamar(request)


def _cookie(r, request):
    https = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
    r.set_cookie("painel", TOKEN, max_age=60 * 60 * 24 * 60, httponly=True,
                 samesite="none" if https else "strict", secure=https)
    return r


def erro(texto, codigo=400):
    raise HTTPException(codigo, texto)


@app.exception_handler(HTTPException)
async def mostrar_erro(_, e):
    return JSONResponse({"erro": e.detail}, status_code=e.status_code)


# ---------- páginas ----------
@app.get("/")
def pagina():
    # sem cache: o navegador e o Telegram sempre pegam a versão nova do painel
    return FileResponse(PASTA / "painel.html", headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/login")
async def login(request: Request):
    dados = await request.json()
    if not hmac.compare_digest(str(dados.get("senha", "")).strip(), os.environ["PAINEL_SENHA"].strip()):
        await asyncio.sleep(1.5)  # freia quem tenta adivinhar a senha
        erro("Senha errada.", 401)
    return _cookie(JSONResponse({"ok": True, "token": TOKEN}), request)


@app.post("/api/login-telegram")
async def login_telegram(request: Request):
    """Entrada sem senha quando o painel abre dentro do Telegram (Mini App).
    Confere a assinatura do Telegram e se quem abriu é o dono do robô."""
    init = (await request.json()).get("initData", "")
    campos = dict(parse_qsl(init, keep_blank_values=True))
    recebido = campos.pop("hash", "")
    texto = "\n".join(f"{k}={v}" for k, v in sorted(campos.items()))
    segredo = hmac.new(b"WebAppData", os.environ["TELEGRAM_BOT_TOKEN"].encode(), hashlib.sha256).digest()
    certo = hmac.new(segredo, texto.encode(), hashlib.sha256).hexdigest()
    if not recebido or not hmac.compare_digest(certo, recebido):
        erro("Não consegui confirmar que é você.", 401)
    if time.time() - int(campos.get("auth_date", 0)) > 24 * 3600:
        erro("Abra o painel de novo pelo robô.", 401)
    usuario = json.loads(campos.get("user", "{}"))
    if usuario.get("id") != postador.estado().get("admin"):
        erro("Este painel é só do dono do robô.", 403)
    return _cookie(JSONResponse({"ok": True, "token": TOKEN}), request)


def lotes():
    """Posts escritos por você (um texto em vários canais): os agendados e os que estão no ar."""
    nomes = {c["id"]: c["nome"] for c in banco.canais()}
    saida = []
    for p in banco.posts(limite=200):
        if p["origem"] != "manual" or p["status"] not in ("agendado", "postado", "publicando", "erro"):
            continue
        no_ar = banco.envios_do_post(p["id"])
        if p["status"] != "agendado" and not no_ar:
            continue
        botao = (p["botoes"] or [[{}]])[0][0] if p["botoes"] else {}
        saida.append({
            "id": p["id"], "titulo": p["titulo"], "status": p["status"], "erro": p.get("erro"),
            "agendado_para": p["agendado_para"], "saida_em": (p["dados"] or {}).get("saida_em")
            or (no_ar[0]["expira_em"] if no_ar else None),
            "fixar": bool((p["dados"] or {}).get("fixar")), "fixados": sum(1 for e in no_ar if e.get("fixado")),
            "texto": para_editor(p["legenda"]), "botao_texto": botao.get("text", ""), "botao_url": botao.get("url", ""),
            "foto": p["foto"] if str(p["foto"] or "").startswith("http") else "",
            "canais": [e["canal_nome"] or e["chat"] for e in no_ar] if no_ar
            else [nomes.get(c, f"canal {c}") for c in (p["canais"] or [])],
            "no_ar": len(no_ar),
        })
        if len(saida) >= 40:
            break
    return saida


@app.get("/api/resumo")
def resumo():
    e = postador.estado()
    return {
        "agora": banco.agora(),
        "proxima": postador.proxima_hora(),
        "postados": banco.sql("SELECT COUNT(*) n FROM posts WHERE status='postado'", um=True)["n"],
        "canais": banco.canais(),
        "agendados": banco.posts(status=("agendado",)),
        "fila": banco.posts(status=("fila",)),
        "historico": [p for p in banco.posts(limite=60) if p["status"] not in ("fila", "agendado")],
        "no_ar": banco.envios_no_ar(),
        "lotes": lotes(),
        "desempenho": metricas.resumo(),
        "ajustes": {
            "pausado": bool(e.get("pausado")),
            "pausa_total": bool(e.get("pausa_total")),
            "aprovacao": bool(postador.opcao("aprovacao")),
            "horarios": postador.opcao("horarios"),
            "expira_auto_horas": postador.opcao("expira_auto_horas"),
            "garimpando": postador._garimpando.is_set(),
            "admin": bool(e.get("admin")),
            "painel_url": e.get("painel_url"),
        },
    }


# ---------- posts ----------
def formatar(texto):
    """Texto simples -> HTML do Telegram. **negrito**, __itálico__, ~~riscado~~, [texto](link)."""
    t = html.escape(texto.strip(), quote=False)
    links = []

    def guardar(m):  # tira o endereço do caminho para o __ e o ** não mexerem nele
        links.append(m.group(2).replace('"', "%22"))
        return f"\x00{len(links) - 1}\x01{m.group(1)}\x02"
    t = re.sub(r"\[([^\]\n]+)\]\(((?:https?|tg)://[^\s)]+)\)", guardar, t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"__(.+?)__", r"<i>\1</i>", t, flags=re.S)
    t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t, flags=re.S)
    return re.sub(r"\x00(\d+)\x01(.*?)\x02", lambda m: f'<a href="{links[int(m.group(1))]}">{m.group(2)}</a>', t, flags=re.S)


def visivel(legenda):
    """Quantos caracteres o Telegram conta (sem as marcações)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", legenda)))


def para_editor(h):
    """HTML do Telegram -> o texto do editor do painel."""
    t = re.sub(r'<a href="([^"]+)">(.*?)</a>', lambda m: f"[{m.group(2)}]({html.unescape(m.group(1))})", h, flags=re.S)
    t = re.sub(r"</?b>", "**", t)
    t = re.sub(r"</?i>", "__", t)
    t = re.sub(r"</?s>", "~~", t)
    return html.unescape(re.sub(r"<[^>]+>", "", t))


@app.post("/api/posts")
async def criar_post(
    texto: str = Form(""), foto_url: str = Form(""), foto: UploadFile | None = File(None),
    botao_texto: str = Form(""), botao_url: str = Form(""), canais: str = Form(""),
    modo: str = Form("agora"), quando: str = Form(""), expira_horas: str = Form(""),
    saida: str = Form(""), fixar: str = Form(""),
):
    legenda = formatar(texto)
    if not legenda:
        erro("Escreva o texto do post.")
    caminho = ""
    if foto and foto.filename:
        ext = Path(foto.filename).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            erro("A foto precisa ser JPG, PNG ou WEBP.")
        caminho = f"uploads/{uuid.uuid4().hex}{ext}"
        (PASTA / caminho).write_bytes(await foto.read())
    elif foto_url.strip():
        caminho = foto_url.strip()
    limite = 1024 if caminho else 4096
    if visivel(legenda) > limite:
        erro(f"Texto grande demais: {visivel(legenda)} caracteres. "
             + ("Com foto, o Telegram aceita 1024." if caminho else "O Telegram aceita 4096."))
    botoes = []
    if botao_texto.strip() and botao_url.strip():
        if not botao_url.startswith(("http://", "https://", "tg://")):
            erro("O link do botão precisa começar com https://")
        botoes = [[{"text": botao_texto.strip(), "url": botao_url.strip()}]]
    ids = [int(c) for c in canais.split(",") if c.strip()]
    if not ids:
        erro("Escolha pelo menos um canal.")
    horas = float(expira_horas.replace(",", ".")) if expira_horas.strip() else None

    agendado = None
    status = "agendado"
    if modo == "agendar":
        if not quando:
            erro("Escolha o dia e a hora.")
        agendado = quando.replace("T", " ")[:16]
        if agendado <= banco.agora():
            erro("Essa hora já passou.")
    elif modo == "fila":
        status = "fila"
    else:
        agendado = banco.agora()
    saida_em = saida.replace("T", " ")[:16] if saida.strip() else None
    if saida_em and saida_em <= (agendado or banco.agora()):
        erro("A hora de sair do ar precisa ser depois da hora de entrar.")
    extra = {k: v for k, v in (("saida_em", saida_em), ("fixar", fixar in ("1", "true", "on"))) if v}
    titulo = re.sub(r"<[^>]+>", "", legenda).split("\n")[0][:80]
    pid = banco.novo_post("manual", legenda, foto=caminho, botoes=botoes, canais_ids=ids,
                          agendado_para=agendado, expira_horas=horas, status=status, titulo=titulo, dados=extra)
    if modo == "agora":
        n = postador.publicar_seguro(pid, conferir=False)
        p = banco.post(pid)
        if not n:
            erro(f"Não consegui postar. {p.get('erro') or ''}")
        aviso = f" Atenção: {p['erro']}" if p.get("erro") else ""
        return {"ok": True, "msg": f"Postado em {n} canal(is).{aviso}"}
    pausa = " Tudo está pausado: ele sai quando você religar." if postador.pausa_total() else ""
    return {"ok": True, "msg": ("Agendado." if modo == "agendar" else "Entrou na fila automática.") + pausa}


# ---------- criar conteúdo ----------
@app.post("/api/criar/amazon")
def criar_amazon(dados: dict = Body(...)):
    """Link da Amazon (livro ou qualquer produto) -> post pronto, com a tag de afiliado."""
    import escritor
    import garimpeiro
    try:
        item = garimpeiro.ler_produto(str(dados.get("link", "")))
    except (ValueError, RuntimeError) as x:
        erro(str(x))
    except Exception as x:
        erro(f"Não consegui abrir a página da Amazon ({type(x).__name__}). Tente de novo em 1 minuto.")
    if dados.get("ia"):
        escritor.escrever([item])
    else:
        item["texto_post"] = escritor._resumo_simples(item.get("descricao") or item.get("sobre") or "")
        item["tags"] = []
    botao = escritor.botao(item)[0]
    return {"ok": True, "tipo": item["tipo"], "texto": para_editor(escritor.legenda(item)),
            "foto": item.get("capa", ""), "botao_texto": botao["text"], "botao_url": botao["url"],
            "msg": "Post montado. Confira e ajuste o que quiser."}


@app.post("/api/criar/ia")
def criar_ia(dados: dict = Body(...)):
    import escritor
    tema = str(dados.get("tema", "")).strip()
    if len(tema) < 5:
        erro("Conte em uma frase sobre o que é o post.")
    texto = escritor.post_livre(tema, str(dados.get("formato", "dica")), str(dados.get("nicho", "")))
    if not texto:
        erro("A IA não respondeu agora. Tente de novo em 1 minuto.")
    return {"ok": True, "texto": texto, "msg": "Texto pronto. Revise antes de postar."}


@app.post("/api/posts/{pid}/cancelar")
def cancelar(pid: int):
    banco.mudar_post(pid, status="cancelado")
    return {"ok": True, "msg": "Cancelado."}


@app.post("/api/posts/{pid}/agora")
def postar_agora(pid: int):
    p = banco.post(pid) or erro("Post não existe.", 404)
    if p["status"] in ("postado", "publicando"):
        return {"ok": True, "msg": "Esse post já foi para o canal."}
    n = postador.publicar_seguro(pid, conferir=p["origem"] != "manual")
    if not n:
        p = banco.post(pid)
        erro("Não foi: " + ("a promoção acabou na Amazon." if p["status"] == "expirou" else (p.get("erro") or "")))
    return {"ok": True, "msg": f"Postado em {n} canal(is)."}


# ---------- um post em vários canais: editar, fixar, apagar tudo ----------
def _canal_do_envio(e):
    return banco.sql("SELECT * FROM canais WHERE id=?", (e["canal_id"],), um=True) or {"chat": e["chat"], "nome": e["chat"]}


@app.post("/api/posts/{pid}/editar")
def editar_post(pid: int, dados: dict = Body(...)):
    p = banco.post(pid) or erro("Post não existe.", 404)
    mudar = {}
    if "texto" in dados:
        legenda = formatar(str(dados["texto"]))
        if not legenda:
            erro("Escreva o texto do post.")
        limite = 1024 if p["foto"] else 4096
        if visivel(legenda) > limite:
            erro(f"Texto grande demais: {visivel(legenda)} caracteres. O Telegram aceita {limite}.")
        mudar["legenda"] = p["legenda"] = legenda
        mudar["titulo"] = re.sub(r"<[^>]+>", "", legenda).split("\n")[0][:80]
    if "botao_texto" in dados or "botao_url" in dados:
        bt, bu = str(dados.get("botao_texto", "")).strip(), str(dados.get("botao_url", "")).strip()
        if bt and bu and not bu.startswith(("http://", "https://", "tg://")):
            erro("O link do botão precisa começar com https://")
        mudar["botoes"] = p["botoes"] = [[{"text": bt, "url": bu}]] if bt and bu else []
    if dados.get("quando") and p["status"] == "agendado":
        q = str(dados["quando"]).replace("T", " ")[:16]
        if q <= banco.agora():
            erro("Essa hora de entrar já passou.")
        mudar["agendado_para"] = q
    if "saida" in dados:
        s = str(dados["saida"] or "").replace("T", " ")[:16] or None
        if s and s <= max(banco.agora(), mudar.get("agendado_para") or p["agendado_para"] or ""):
            erro("A hora de sair do ar precisa ser depois da hora de entrar.")
        p["dados"]["saida_em"] = s
        if not s:
            p["dados"].pop("saida_em", None)
        mudar["dados"] = p["dados"]
        banco.sql("UPDATE envios SET expira_em=? WHERE post_id=? AND status='no_ar'", (s, pid))
    if mudar:
        banco.mudar_post(pid, **mudar)
    feitos, erros = 0, []
    if "legenda" in mudar or "botoes" in mudar:
        for e in banco.envios_do_post(pid):
            try:
                postador.editar_envio(e, p, _canal_do_envio(e))
                feitos += 1
            except RuntimeError as x:
                erros.append(f"{e['canal_nome'] or e['chat']}: {x}")
            time.sleep(0.1)
    msg = f"Salvo. Mudei o post em {feitos} canal(is)." if feitos else "Salvo."
    if erros:
        msg += " Não deu em: " + "; ".join(erros[:5])
    return {"ok": True, "msg": msg}


@app.post("/api/posts/{pid}/fixar")
def fixar_post(pid: int, dados: dict = Body(...)):
    p = banco.post(pid) or erro("Post não existe.", 404)
    sim = bool(dados.get("fixar", True))
    p["dados"]["fixar"] = sim
    banco.mudar_post(pid, dados=p["dados"])
    feitos, erros = 0, []
    for e in banco.envios_do_post(pid):
        try:
            postador.fixar(e["chat"], e["message_id"], sim)
            banco.sql("UPDATE envios SET fixado=? WHERE id=?", (int(sim), e["id"]))
            feitos += 1
        except RuntimeError as x:
            erros.append(f"{e['canal_nome'] or e['chat']}: {x}")
        time.sleep(0.1)
    if p["status"] == "agendado" and not feitos:
        return {"ok": True, "msg": "Vai entrar fixado." if sim else "Vai entrar sem fixar."}
    msg = f"{'Fixado' if sim else 'Desafixado'} em {feitos} canal(is)."
    if erros:
        msg += " Não deu em: " + "; ".join(erros[:5]) + " (o robô precisa da permissão de fixar mensagens)."
    return {"ok": True, "msg": msg}


def _apagar_envios(envios):
    feitos, erros = 0, []
    for e in envios:
        try:
            postador.apagar(e["chat"], e["message_id"])
        except RuntimeError as x:
            if "not found" not in str(x):
                erros.append(f"{e.get('canal_nome') or e['chat']}: {x}")
                continue
        banco.marcar_envio(e["id"], "apagado")
        feitos += 1
        time.sleep(0.05)
    return feitos, erros


@app.post("/api/posts/{pid}/apagar")
def apagar_post(pid: int):
    p = banco.post(pid) or erro("Post não existe.", 404)
    if p["status"] in ("agendado", "fila"):
        banco.mudar_post(pid, status="cancelado")
    feitos, erros = _apagar_envios(banco.envios_do_post(pid))
    msg = f"Apagado de {feitos} canal(is)." if feitos else "Cancelado. Ele não vai entrar."
    if erros:
        msg += " Não deu em: " + "; ".join(erros[:5])
    return {"ok": True, "msg": msg}


@app.post("/api/envios/apagar-tudo")
def apagar_tudo(dados: dict = Body(default={})):
    """Tira do ar TODOS os posts do robô, em todos os canais. Com agendados=true, cancela também os agendados."""
    feitos, erros = _apagar_envios(banco.envios_no_ar(limite=2000))
    cancelados = 0
    if dados.get("agendados"):
        for p in banco.posts(status=("agendado",)):
            banco.mudar_post(p["id"], status="cancelado")
            cancelados += 1
    msg = f"Apaguei {feitos} post(s) dos canais."
    if cancelados:
        msg += f" Cancelei {cancelados} agendado(s)."
    if erros:
        msg += f" {len(erros)} não deram: " + "; ".join(erros[:3])
    return {"ok": True, "msg": msg}


# ---------- apagar ----------
@app.post("/api/envios/{eid}/apagar")
def apagar_envio(eid: int):
    e = banco.sql("SELECT * FROM envios WHERE id=?", (eid,), um=True) or erro("Envio não existe.", 404)
    try:
        postador.apagar(e["chat"], e["message_id"])
    except RuntimeError as x:
        if "not found" not in str(x):
            erro(f"O Telegram não deixou apagar: {x}")
    banco.marcar_envio(eid, "apagado")
    return {"ok": True, "msg": "Apagado do canal."}


@app.post("/api/apagar-link")
async def apagar_link(request: Request):
    link = (await request.json()).get("link", "")
    try:
        chat, mid = postador.ler_link(link)
        postador.apagar(chat, mid)
    except (ValueError, RuntimeError) as x:
        erro(str(x))
    banco.sql("UPDATE envios SET status='apagado', apagado_em=? WHERE message_id=? AND status='no_ar'",
              (banco.agora(), mid))
    return {"ok": True, "msg": "Mensagem apagada."}


# ---------- canais ----------
@app.post("/api/canais")
async def adicionar_canal(request: Request):
    bruto = (await request.json()).get("chat", "").strip()
    m = re.search(r"t\.me/([A-Za-z0-9_]{4,})", bruto)
    chat = "@" + m.group(1) if m else bruto
    if not chat.startswith(("@", "-100")):
        chat = "@" + chat.lstrip("@")
    try:
        info = postador.tg("getChat", chat_id=chat)
        eu = postador.tg("getMe")
        membro = postador.tg("getChatMember", chat_id=info["id"], user_id=eu["id"])
    except RuntimeError as x:
        erro(f"Não achei o canal ou o robô não está nele. Ponha @{os.environ.get('BOT_NOME', 'o robô')} "
             f"como administrador primeiro. ({x})")
    if membro["status"] != "administrator" or not membro.get("can_post_messages", True):
        erro("O robô está no canal, mas não é administrador com permissão de postar.")
    chat_final = f"@{info['username']}" if info.get("username") else str(info["id"])
    banco.novo_canal(chat_final, info.get("title", chat_final), auto=False)
    aviso = "" if membro.get("can_delete_messages") else \
        " Atenção: sem a permissão 'Apagar mensagens', o robô não consegue apagar posts nem fazer a validade."
    return {"ok": True, "msg": f"Canal {info.get('title')} adicionado." + aviso}


@app.post("/api/canais/{cid}")
async def mudar_canal(cid: int, request: Request):
    dados = await request.json()
    if "auto" in dados:
        dados["auto"] = int(bool(dados["auto"]))
    if "assinatura" in dados:
        a = str(dados["assinatura"]).strip()
        dados["assinatura"] = a if not a or a.startswith("@") else "@" + a
    if "horarios" in dados:  # vazio = segue os horários gerais
        dados["horarios"] = " ".join(sorted(set(re.findall(r"\b\d{2}:\d{2}\b", str(dados["horarios"])))))
    if "recebe" in dados:
        dados["recebe"] = ",".join(t for t in ("livro", "oferta") if t in str(dados["recebe"]))
    banco.mudar_canal(cid, **{k: v for k, v in dados.items() if k != "remover"})
    if dados.get("remover"):
        banco.sql("UPDATE canais SET ativo=0 WHERE id=?", (cid,))
    return {"ok": True, "msg": "Canal atualizado."}


# ---------- ajustes ----------
@app.post("/api/ajustes")
async def ajustes(request: Request):
    d = await request.json()
    campos = {}
    if "pausado" in d:
        campos["pausado"] = bool(d["pausado"])
    if "aprovacao" in d:
        campos["aprovacao"] = bool(d["aprovacao"])
    if "horarios" in d:
        hs = sorted(set(h for h in re.findall(r"\b\d{2}:\d{2}\b", d["horarios"])))
        campos["horarios"] = hs
    if "expira_auto_horas" in d:
        v = str(d["expira_auto_horas"]).strip().replace(",", ".")
        campos["expira_auto_horas"] = float(v) if v else None
    msg = "Ajustes salvos."
    if "pausa_total" in d:
        antes = postador.pausa_total()
        campos["pausa_total"] = bool(d["pausa_total"])
        if campos["pausa_total"]:
            msg = "Tudo pausado. Nada sai até você religar (as saídas do ar continuam)."
    postador.mudar_estado(**campos)
    if "pausa_total" in d and antes and not campos["pausa_total"]:
        n = postador.soltar_agendados()
        msg = f"Tudo religado. {n} agendado(s) que passaram da hora entraram agora." if n else "Tudo religado."
    return {"ok": True, "msg": msg}


# ---------- canal de inglês (quem posta é a nuvem; o painel mostra a fila e manda prévias) ----------
ESPELHO = PASTA / "nuvem-espelho.git"
_ingles_cache = {"hora": 0, "estado": None, "fonte": ""}


def estado_ingles_nuvem():
    """O estado.json do inglês que está no GitHub (a nuvem é quem posta). Guarda por 2 minutos."""
    import ingles
    if time.time() - _ingles_cache["hora"] < 120 and _ingles_cache["estado"] is not None:
        return _ingles_cache["estado"], _ingles_cache["fonte"]
    estado, fonte = ingles.estado(), "pc"
    if ESPELHO.exists():
        try:
            git = ["git", f"--git-dir={ESPELHO}"]
            subprocess.run(git + ["fetch", "-q", "origin", "main"], timeout=40, check=True,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, capture_output=True)
            texto = subprocess.run(git + ["show", "FETCH_HEAD:ingles/estado.json"], timeout=20, check=True,
                                   capture_output=True, encoding="utf-8").stdout
            estado, fonte = json.loads(texto), "nuvem"
        except Exception as e:
            print(f"[inglês] não li o estado da nuvem: {e}")
    _ingles_cache.update(hora=time.time(), estado=estado, fonte=fonte)
    return estado, fonte


@app.get("/api/ingles")
def ingles_resumo():
    import ingles
    estado, fonte = estado_ingles_nuvem()
    feitos = set(estado.get("postados", []))
    itens = ingles.banco()
    fila = [{"id": i["id"], "tipo": i["tipo"], "titulo": i.get("titulo") or i.get("quiz", {}).get("pergunta", ""),
             "imagem": Path((i.get("imagens") or [""])[0]).name, "slides": len(i.get("imagens", []))}
            for i in itens if i["id"] not in feitos]
    return {"canal": ingles.canal(), "horarios": ingles.CFG.get("horarios", []), "fonte": fonte,
            "postados": len(feitos), "ultimo": estado.get("ultimo"), "fila": fila,
            "dias": round(len(fila) / max(len(ingles.CFG.get("horarios", [])) or 3, 1), 1),
            "acoes": "https://github.com/manoelmigueis/robo-telegram/actions/workflows/ingles.yml"}


@app.get("/api/ingles/img/{nome}")
def ingles_img(nome: str):
    import ingles
    arquivo = (ingles.PASTA / "img" / nome).resolve()
    if arquivo.parent != (ingles.PASTA / "img").resolve() or not arquivo.exists():
        erro("Imagem não encontrada.", 404)
    return FileResponse(arquivo, headers={"Cache-Control": "max-age=86400"})


@app.post("/api/ingles/previa")
def ingles_previa(dados: dict = Body(default={})):
    import ingles
    admin = postador.estado().get("admin")
    if not admin:
        erro("O robô ainda não sabe quem é o dono. Mande /start para ele no Telegram.")
    estado, _ = estado_ingles_nuvem()
    feitos = set(estado.get("postados", []))
    escolha = dados.get("id")
    itens = [i for i in ingles.banco() if i["id"] not in feitos and (not escolha or i["id"] == escolha)][:1 if escolha else 3]
    if itens:
        postador.avisar("👀 Prévia do canal de inglês. Não precisa fazer nada: já está na agenda e sai sozinho no canal.")
    for i in itens:
        ingles.enviar(admin, i)
    return {"ok": True, "msg": f"Mandei {len(itens)} prévia(s) no seu privado do Telegram."}


# ---------- central dos canais de conteúdo (inglês e renda extra) ----------
def _central(funcao, *args):
    import central
    try:
        return funcao(*args)
    except central.Erro as x:
        erro(str(x))


@app.get("/api/central/{nicho}")
def central_resumo(nicho: str):
    import central
    return _central(central.resumo, nicho)


@app.get("/api/central/{nicho}/img/{nome}")
def central_img(nicho: str, nome: str):
    import central
    m = _central(central.modulo, nicho)
    arquivo = (m.PASTA / "img" / nome).resolve()
    if arquivo.parent != (m.PASTA / "img").resolve() or not arquivo.exists():
        erro("Imagem não encontrada.", 404)
    return FileResponse(arquivo, headers={"Cache-Control": "max-age=86400"})


@app.post("/api/central/{nicho}/{acao}")
def central_acao(nicho: str, acao: str, dados: dict = Body(default={})):
    import central
    pid = str(dados.get("id", ""))
    if acao == "postar":
        item, mid = _central(central.postar, nicho, pid or None)
        if not item:
            erro("A fila está vazia.")
        return {"ok": True, "msg": "Postado no canal." if mid else "Esse post já tinha saído no canal."}
    if acao == "tirar":
        _central(central.tirar, nicho, pid)
        return {"ok": True, "msg": "Tirado da fila."}
    if acao == "voltar":
        _central(central.voltar, nicho, pid)
        return {"ok": True, "msg": "Voltou para a fila."}
    if acao == "proximo":
        _central(central.proximo, nicho, pid)
        return {"ok": True, "msg": "Agora ele é o próximo a sair."}
    if acao == "agendar":
        iso = _central(central.agendar, nicho, pid, dados.get("quando", ""))
        return {"ok": True, "msg": "Agendado. Sai na primeira rodada depois da hora marcada (até 1 hora depois)."
                if iso else "Agendamento desfeito. Ele voltou para a fila."}
    if acao == "apagar":
        _central(central.apagar, nicho, pid)
        return {"ok": True, "msg": "Apagado do canal."}
    if acao == "previa":
        admin = postador.estado().get("admin")
        if not admin:
            erro("O robô ainda não sabe quem é o dono. Mande /start para ele no Telegram.")
        _central(central.previa, nicho, pid, admin)
        return {"ok": True, "msg": "Mandei a prévia no seu privado do Telegram, com os botões."}
    if acao == "criar":
        item = _central(central.criar, nicho, str(dados.get("tema", ""))[:300], str(dados.get("tipo", "lista")))
        return {"ok": True, "msg": f"Post novo criado e posto no fim da fila: {item.get('titulo', '')}", "item": item["id"]}
    if acao == "noticia":
        titulo = _central(central.postar_noticia, nicho)
        return {"ok": True, "msg": f"Notícia postada: {titulo[:120]}"}
    erro("Ação desconhecida.", 404)


@app.post("/api/garimpar")
def garimpar():
    ok = postador.garimpar_em_segundo_plano()
    return {"ok": True, "msg": "Garimpo começou. Leva uns 20 minutos." if ok else "Já está garimpando."}


def achar_cloudflared():
    for c in (shutil.which("cloudflared"), PASTA / "cloudflared.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links/cloudflared.exe",
              Path(os.environ.get("ProgramFiles(x86)", "")) / "cloudflared/cloudflared.exe",
              Path(os.environ.get("ProgramFiles", "")) / "cloudflared/cloudflared.exe"):
        if c and Path(c).is_file():
            return str(c)
    return None


def ligar_tunel(porta):
    """Abre um endereço https grátis (Cloudflare) para o painel e põe o botão no Telegram.
    O endereço muda cada vez que o robô liga; o botão é atualizado sozinho."""
    exe = achar_cloudflared()
    if not exe:
        print("[túnel] cloudflared não instalado: painel só no PC.")
        postador.mudar_estado(painel_url=None)
        return

    def trabalho():
        while True:
            proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{porta}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace",
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for linha in proc.stdout:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", linha)
                if m:
                    url = m.group(0)
                    print(f"Painel na internet: {url}")
                    time.sleep(8)  # o endereço leva uns segundos para responder
                    postador.botao_do_painel(url)
                    postador.avisar(f"🌐 Painel no ar. Toque em “Painel”, ao lado de onde você digita, ou mande /painel.")
            proc.wait()
            print("[túnel] caiu, religando em 20 s")
            time.sleep(20)
    threading.Thread(target=trabalho, daemon=True).start()


def porta_livre(porta):
    import socket
    with socket.socket() as s:
        try:
            s.bind((os.environ.get("HOST", "127.0.0.1"), porta))
            return True
        except OSError:
            return False


def fechar_tuneis_velhos(porta):
    """Fecha túneis que sobraram de uma vez anterior (o cloudflared não morre junto com o robô)."""
    if os.name != "nt":
        subprocess.run(["pkill", "-f", f"cloudflared.*:{porta}"], check=False)
        return
    filtro = f"*127.0.0.1:{porta}*"
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '{filtro}' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   check=False, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ligar():
    porta = int(os.environ.get("PORTA", "8800"))
    if not porta_livre(porta):  # já tem um robô ligado: não liga outro (dois robôs brigam)
        print(f"O robô já está ligado (a porta {porta} está em uso). Não vou ligar outro.")
        raise SystemExit(3)
    fechar_tuneis_velhos(porta)
    postador.preparar_primeira_vez()
    threading.Thread(target=postador.rodar, daemon=True).start()
    # o painel da internet agora mora na nuvem (Vercel), aberto 24h; o do PC fica só no PC
    if os.environ.get("PAINEL_TUNEL") == "1":
        ligar_tunel(porta)
    print(f"Painel em http://localhost:{porta}  ({datetime.now():%d/%m %H:%M})")
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=porta, log_level="warning")


if __name__ == "__main__":
    ligar()
