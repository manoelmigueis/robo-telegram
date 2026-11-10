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
    return FileResponse(PASTA / "painel.html")


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
        "ajustes": {
            "pausado": bool(e.get("pausado")),
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
    titulo = re.sub(r"<[^>]+>", "", legenda).split("\n")[0][:80]
    pid = banco.novo_post("manual", legenda, foto=caminho, botoes=botoes, canais_ids=ids,
                          agendado_para=agendado, expira_horas=horas, status=status, titulo=titulo)
    if modo == "agora":
        n = postador.publicar_seguro(pid, conferir=False)
        p = banco.post(pid)
        if not n:
            erro(f"Não consegui postar. {p.get('erro') or ''}")
        return {"ok": True, "msg": f"Postado em {n} canal(is)."}
    return {"ok": True, "msg": "Agendado." if modo == "agendar" else "Entrou na fila automática."}


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
    n = postador.publicar_seguro(pid, conferir=p["origem"] != "manual")
    if not n:
        p = banco.post(pid)
        erro("Não foi: " + ("a promoção acabou na Amazon." if p["status"] == "expirou" else (p.get("erro") or "")))
    return {"ok": True, "msg": f"Postado em {n} canal(is)."}


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
    postador.mudar_estado(**campos)
    return {"ok": True, "msg": "Ajustes salvos."}


@app.post("/api/garimpar")
def garimpar():
    ok = postador.garimpar_em_segundo_plano()
    return {"ok": True, "msg": "Garimpo começou. Leva uns 5 minutos." if ok else "Já está garimpando."}


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
    ligar_tunel(porta)
    print(f"Painel em http://localhost:{porta}  ({datetime.now():%d/%m %H:%M})")
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=porta, log_level="warning")


if __name__ == "__main__":
    ligar()
