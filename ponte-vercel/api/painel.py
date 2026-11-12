"""Painel do robô na nuvem (Vercel), aberto 24 horas, com o PC desligado.

- Para MOSTRAR: baixa do GitHub o código e os dados do robô (robo.db, estado.json...) e usa as
  mesmas funções do painel do PC (painel.py). Sempre a versão mais nova do GitHub.
- Para FAZER (postar, cancelar, mudar canal, ajustes...): pede ao GitHub Actions (bot.yml), que roda
  o painel.py de verdade e guarda a resposta no ramo "painel-respostas". A página espera a resposta.

Senhas aqui: só o "resumo" da senha do painel (PAINEL_HASH) e uma chave de sessão própria
(SESSAO_SEGREDO). O token do robô do Telegram NÃO fica aqui.
"""
import base64
import hashlib
import hmac
import importlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

REPO = os.environ.get("REPO", "manoelmigueis/robo-telegram")
PASTA = Path("/tmp/robo")
ARQUIVOS = ["banco.py", "postador.py", "escritor.py", "ingles.py", "painel.py", "painel.html",
            "config.json", "robo.db", "estado.json", "ingles/posts.json", "ingles/estado.json"]
# chave pública do Telegram para conferir a assinatura do Mini App sem precisar do token do robô
TELEGRAM_CHAVE = "e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242a"

# o que mexe na fila de livros espera o robô de postar terminar
ESCREVE = ("/api/posts", "/api/envios/", "/api/apagar-link", "/api/canais", "/api/ajustes")
NAVEGADOR = re.compile(r"^/api/(criar/amazon|posts/\d+/agora)$")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_trava = threading.Lock()
_cache = {"sha": None, "olhado": 0.0, "painel": None}


# ---------- GitHub ----------
def github(caminho, metodo="GET", corpo=None, bruto=False):
    req = urllib.request.Request(
        f"https://api.github.com{caminho}", method=metodo,
        data=json.dumps(corpo).encode() if corpo is not None else None,
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "User-Agent": "painel-robo",
                 "Accept": "application/vnd.github.raw" if bruto else "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        dados = r.read()
        return dados if bruto else (json.loads(dados) if dados else {})


def baixar(sha, arquivo):
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{arquivo}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "painel-robo"}), timeout=20) as r:
        destino = PASTA / arquivo
        destino.parent.mkdir(parents=True, exist_ok=True)
        temp = destino.with_name(destino.name + ".novo")
        temp.write_bytes(r.read())
        os.replace(temp, destino)


def painel():
    """O painel.py do PC, com os dados mais novos do GitHub (confere a cada 10 segundos)."""
    with _trava:
        if _cache["painel"] and time.time() - _cache["olhado"] < 10:
            return _cache["painel"]
        sha = github(f"/repos/{REPO}/commits/main")["sha"]
        _cache["olhado"] = time.time()
        if sha != _cache["sha"] or not _cache["painel"]:
            with ThreadPoolExecutor(8) as ex:
                list(ex.map(lambda a: baixar(sha, a), ARQUIVOS))
            os.environ.setdefault("PAINEL_SENHA", "nuvem")  # o painel.py não cria .env
            if str(PASTA) not in sys.path:
                sys.path.insert(0, str(PASTA))
            for nome in ("painel", "ingles", "postador", "escritor", "banco"):
                sys.modules.pop(nome, None)
            _cache["painel"] = importlib.import_module("painel")
            _cache["sha"] = sha
        return _cache["painel"]


# ---------- acesso ----------
def sessao():
    return hmac.new(os.environ["SESSAO_SEGREDO"].encode(), b"painel-nuvem", hashlib.sha256).hexdigest()


def autorizado(request):
    chave = request.headers.get("x-painel") or request.cookies.get("painel", "")
    return bool(chave) and hmac.compare_digest(chave, sessao())


def entrou(request):
    r = JSONResponse({"ok": True, "token": sessao()})
    r.set_cookie("painel", sessao(), max_age=60 * 60 * 24 * 60, httponly=True, samesite="none", secure=True)
    return r


def assinatura_telegram_ok(campos):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    assinatura = campos.get("signature", "")
    if not assinatura:
        return False
    texto = f"{os.environ['BOT_ID']}:WebAppData\n" + "\n".join(
        f"{k}={v}" for k, v in sorted(campos.items()) if k not in ("hash", "signature"))
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(TELEGRAM_CHAVE)).verify(
            base64.urlsafe_b64decode(assinatura + "=" * (-len(assinatura) % 4)), texto.encode())
        return True
    except Exception:
        return False


@app.middleware("http")
async def exigir_senha(request: Request, chamar):
    livre = request.url.path in ("/", "/api/login", "/api/login-telegram") or not request.url.path.startswith("/api")
    if not livre and not autorizado(request):
        return JSONResponse({"erro": "Entre com a senha."}, status_code=401)
    return await chamar(request)


@app.get("/")
def pagina():
    html = (PASTA / "painel.html").read_text(encoding="utf-8") if painel() else ""
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/login")
async def login(request: Request):
    senha = str((await request.json()).get("senha", "")).strip()
    resumo = hashlib.sha256(hmac.new(senha.encode(), b"painel", hashlib.sha256).hexdigest().encode()).hexdigest()
    if not hmac.compare_digest(resumo, os.environ["PAINEL_HASH"]):
        time.sleep(1.5)  # freia quem tenta adivinhar a senha
        return JSONResponse({"erro": "Senha errada."}, status_code=401)
    return entrou(request)


@app.post("/api/login-telegram")
async def login_telegram(request: Request):
    campos = dict(parse_qsl((await request.json()).get("initData", ""), keep_blank_values=True))
    if not assinatura_telegram_ok(campos):
        return JSONResponse({"erro": "Não consegui confirmar que é você."}, status_code=401)
    if time.time() - int(campos.get("auth_date", 0)) > 24 * 3600:
        return JSONResponse({"erro": "Abra o painel de novo pelo robô."}, status_code=401)
    if str(json.loads(campos.get("user", "{}")).get("id")) != os.environ.get("ADMIN_ID"):
        return JSONResponse({"erro": "Este painel é só do dono do robô."}, status_code=403)
    return entrou(request)


# ---------- mostrar ----------
@app.get("/api/resumo")
def resumo():
    dados = painel().resumo()
    dados["ajustes"]["nuvem"] = True
    return dados


@app.get("/api/ingles")
def ingles():
    return painel().ingles_resumo()


@app.get("/api/ingles/img/{nome}")
def ingles_img(nome: str):
    if not re.fullmatch(r"[\w.-]+", nome):
        return JSONResponse({"erro": "Imagem não encontrada."}, status_code=404)
    return RedirectResponse(f"https://raw.githubusercontent.com/{REPO}/main/ingles/img/{nome}")


@app.get("/api/resposta/{rid}")
def resposta(rid: str):
    if not re.fullmatch(r"[a-f0-9]{32}", rid):
        return JSONResponse({"erro": "Pedido não existe."}, status_code=404)
    try:
        return json.loads(github(f"/repos/{REPO}/contents/respostas/{rid}.json?ref=painel-respostas", bruto=True))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"pronto": False}
        raise


# ---------- fazer (no GitHub Actions) ----------
def guardar_foto(arquivo):
    ext = Path(arquivo.filename or "").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValueError("A foto precisa ser JPG, PNG ou WEBP.")
    caminho = f"uploads/{uuid.uuid4().hex}{ext}"
    return caminho, ext


@app.post("/api/{resto:path}")
async def fazer(resto: str, request: Request):
    caminho = "/api/" + resto
    rid = uuid.uuid4().hex
    pedido = {"id": rid, "caminho": caminho}
    if request.headers.get("content-type", "").startswith("multipart/"):
        form = await request.form()
        campos = {k: v for k, v in form.items() if isinstance(v, str)}
        foto = form.get("foto")
        if foto is not None and not isinstance(foto, str) and foto.filename:
            try:
                arquivo, _ = guardar_foto(foto)
            except ValueError as x:
                return JSONResponse({"erro": str(x)}, status_code=400)
            conteudo = await foto.read()
            if len(conteudo) > 10 * 1024 * 1024:
                return JSONResponse({"erro": "Foto grande demais (máximo 10 MB)."}, status_code=400)
            github(f"/repos/{REPO}/contents/{arquivo}", "PUT", {
                "message": "painel: foto de post", "content": base64.b64encode(conteudo).decode(), "branch": "main"})
            campos["foto_url"] = arquivo
        pedido["form"] = campos
    else:
        try:
            pedido["corpo"] = await request.json()
        except Exception:
            pedido["corpo"] = {}
    grupo = "robo" if caminho.startswith(ESCREVE) else "solto"
    try:
        github(f"/repos/{REPO}/dispatches", "POST", {"event_type": "telegram", "client_payload": {
            "painel": pedido, "grupo": grupo, "navegador": bool(NAVEGADOR.match(caminho))}})
    except Exception as erro:
        detalhe = erro.read().decode()[:300] if hasattr(erro, "read") else ""
        print(f"[painel] GitHub recusou: {erro} {detalhe}")
        return JSONResponse({"erro": "A nuvem não respondeu. Tente de novo."}, status_code=502)
    return {"ok": True, "espera": rid}
