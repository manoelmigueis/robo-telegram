"""Motor do robô: publica, agenda, apaga e garimpa. Quem liga tudo é o painel.py.

O que ele faz sozinho, o tempo todo:
  - posts "agendado" saem na data marcada, nos canais escolhidos;
  - envios com prazo de validade são apagados quando o prazo vence;
  - em cada horário de "horarios", posta o próximo livro/oferta da fila
    nos canais marcados como automáticos (antes confere na Amazon);
  - todo dia no "garimpo_diario", garimpa livros e ofertas e enche a fila.

Comandos no privado do robô: /menu /proximos /agora /aprovacao /garimpar /pausar /continuar /painel
(o /menu tem botões para tudo; com aprovação ligada, cada post chega antes como prévia)
"""
import json
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import banco
import escritor

PASTA = Path(__file__).parent
CONFIG = banco.CONFIG
ESTADO_ARQ = PASTA / "estado.json"
_trava = threading.RLock()
_garimpando = threading.Event()


# ---------- .env e estado ----------
def carregar_env():
    arq = PASTA / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.strip().startswith("#"):
                chave, valor = linha.split("=", 1)
                if valor.strip():
                    os.environ.setdefault(chave.strip(), valor.strip())


def estado():
    with _trava:
        return json.loads(ESTADO_ARQ.read_text(encoding="utf-8")) if ESTADO_ARQ.exists() else {}


def mudar_estado(**campos):
    with _trava:
        e = estado()
        e.update(campos)
        ESTADO_ARQ.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
        return e


def opcao(chave):
    return estado().get(chave, CONFIG.get(chave))


# ---------- Telegram ----------
def tg(metodo, arquivos=None, **dados):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{metodo}"
    if arquivos:
        dados = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in dados.items()}
        r = requests.post(url, data=dados, files=arquivos, timeout=70)
    else:
        r = requests.post(url, json=dados, timeout=70)
    resposta = r.json()
    if not resposta.get("ok"):
        raise RuntimeError(f"Telegram recusou {metodo}: {resposta.get('description')}")
    return resposta["result"]


def avisar(texto):
    admin = estado().get("admin")
    if admin:
        try:
            tg("sendMessage", chat_id=admin, text=texto, disable_web_page_preview=True)
        except Exception as e:
            print(f"[aviso] {e}")


def enviar(chat, p, teclado=None):
    """Manda um post para um chat. Devolve (message_id, file_id da foto ou None)."""
    extra = {"parse_mode": "HTML"}
    if teclado:
        extra["reply_markup"] = teclado
    elif p["botoes"]:
        extra["reply_markup"] = {"inline_keyboard": p["botoes"]}
    foto = p.get("foto") or ""
    if foto:
        try:
            if foto.startswith("uploads/"):
                with open(PASTA / foto, "rb") as f:
                    r = tg("sendPhoto", arquivos={"photo": f}, chat_id=chat, caption=p["legenda"], **extra)
            else:
                r = tg("sendPhoto", chat_id=chat, photo=foto, caption=p["legenda"], **extra)
            return r["message_id"], r["photo"][-1]["file_id"]
        except RuntimeError as e:
            if "caption is too long" in str(e):
                raise
            print(f"[foto] {e}. Mando sem foto.")
    if p.get("origem") in ("livro", "oferta"):
        extra["link_preview_options"] = {"is_disabled": True}
    r = tg("sendMessage", chat_id=chat, text=p["legenda"], **extra)
    return r["message_id"], None


def apagar(chat, message_id):
    tg("deleteMessage", chat_id=chat, message_id=int(message_id))


def ler_link(link):
    """t.me/canal/123 ou t.me/c/123456/789  ->  (chat, message_id)"""
    m = re.search(r"t\.me/c/(\d+)/(\d+)", link)
    if m:
        return f"-100{m.group(1)}", int(m.group(2))
    m = re.search(r"t\.me/([A-Za-z0-9_]{4,})/(\d+)", link)
    if m:
        return f"@{m.group(1)}", int(m.group(2))
    raise ValueError("Não entendi o link. Use o link da mensagem, tipo https://t.me/canal/123")


def legenda_do_canal(p, canal):
    """Troca o @ da assinatura pelo do canal e soma as tags próprias dele (cabendo no limite)."""
    legenda = p["legenda"]
    assinatura = (canal.get("assinatura") or "").strip() or (canal["chat"] if canal["chat"].startswith("@") else "")
    padrao = CONFIG.get("canal_assinatura", "")
    if assinatura and padrao and padrao in legenda:
        legenda = legenda.replace(f"📚: {padrao}", f"📚: {assinatura}")
    tags = " ".join(t if t.startswith("#") else "#" + t for t in (canal.get("tags") or "").split() if t.strip("#"))
    if escritor.estrangeiro(p.get("dados") or {}):
        tags = ""  # livro em outro idioma: as tags do canal são em português, ficam de fora
    limite = 1024 if p.get("foto") else 4096
    if tags and len(legenda) + len(tags) + 2 <= limite:
        legenda = legenda.rstrip() + ("\n\n" if "\n#" not in legenda[-300:] else " ") + tags
    return legenda


# ---------- publicar ----------
def ainda_vale(dados):
    """Confere na Amazon antes de postar livro/oferta. Na dúvida, não posta.
    Livro: atualiza 'preco' em dados (o livro pode ter saído do grátis)."""
    try:
        import garimpeiro
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            nav = p.chromium.launch()
            pagina = nav.new_page(locale="pt-BR")
            if dados.get("tipo") == "oferta":
                ok = garimpeiro.conferir_oferta(pagina, dados)  # atualiza o preço
            else:
                ok = garimpeiro.conferir_livro(pagina, dados)
            nav.close()
        return ok
    except Exception as e:
        print(f"[conferência] não consegui conferir {dados.get('asin')}: {e}")
        return False


def alvos_do_post(p, canais_lista=None):
    """Canais que recebem o post. canais_lista limita (ex.: só os canais cujo horário chegou)."""
    if p["canais"]:
        alvos = [c for c in banco.canais() if c["id"] in p["canais"]]
    else:  # automático: só os canais que recebem esse tipo (livro / oferta)
        alvos = [c for c in banco.canais(so_auto=True)
                 if p["origem"] == "manual" or p["origem"] in (c.get("recebe") or "livro,oferta").split(",")]
    if canais_lista is not None:
        ids = {c["id"] for c in canais_lista}
        alvos = [c for c in alvos if c["id"] in ids]
    return alvos


def horarios_do_canal(c):
    """Horários próprios do canal; vazio = usa os horários gerais."""
    proprios = sorted(set(re.findall(r"\b\d{2}:\d{2}\b", c.get("horarios") or "")))
    return proprios or list(opcao("horarios") or [])


def proxima_hora():
    """Próximo horário automático entre todos os canais ligados."""
    hora = banco.agora()[11:]
    todos = sorted({h for c in banco.canais(so_auto=True) for h in horarios_do_canal(c)})
    return next((h for h in todos if h > hora), todos[0] if todos else "")


def publicar(pid, conferir=True, so_canais=None, so_gratis=False):
    """Publica o post nos canais dele (ou só em so_canais). Devolve quantos receberam.
    so_gratis: livro que saiu do grátis volta para o fim da fila em vez de ser postado."""
    p = banco.post(pid)
    if not p or p["status"] in ("postado", "cancelado"):
        return 0
    if p["origem"] in ("livro", "oferta") and conferir:
        ok = ainda_vale(p["dados"])
        if not ok and p["origem"] == "livro" and (p["dados"].get("preco") or 0) > 0 and so_gratis:
            banco.mudar_post(pid, dados=p["dados"])  # guarda o preço novo: fica atrás dos grátis
            print(f"[fila] #{pid} saiu do grátis, fica para depois: {p['titulo'][:50]}")
            return 0
        if not ok and p["origem"] == "livro" and (p["dados"].get("preco") or 0) > 0:
            # saiu do grátis: posta mesmo assim (o link tem a tag), com o preço de agora e sem "GRÁTIS"
            p["legenda"] = escritor.legenda(p["dados"])
            p["botoes"] = [escritor.botao(p["dados"])]
            banco.mudar_post(pid, legenda=p["legenda"], botoes=p["botoes"], dados=p["dados"])
        elif not ok:
            banco.mudar_post(pid, status="expirou")
            return 0
        if p["origem"] == "oferta":
            p["legenda"] = escritor.legenda(p["dados"])
            banco.mudar_post(pid, legenda=p["legenda"], dados=p["dados"])
        elif p["origem"] == "livro" and p["dados"].pop("sem_conferir", False):
            # entrou na fila só com os dados da lista: agora que a página foi lida, escreve o texto completo
            escritor.escrever([p["dados"]])
            p["legenda"] = escritor.legenda(p["dados"])
            p["botoes"] = [escritor.botao(p["dados"])]
            p["foto"] = p["dados"].get("capa") or p["foto"]
            banco.mudar_post(pid, legenda=p["legenda"], botoes=p["botoes"], foto=p["foto"], dados=p["dados"])
    if p["origem"] == "livro" and escritor.estrangeiro(p["dados"]) and p["botoes"] != [escritor.botao(p["dados"])]:
        # livro em outro idioma que entrou na fila com o texto antigo (em português): reescreve tudo no idioma dele
        escritor.escrever([p["dados"]])
        p["legenda"] = escritor.legenda(p["dados"])
        p["botoes"] = [escritor.botao(p["dados"])]
        banco.mudar_post(pid, legenda=p["legenda"], botoes=p["botoes"], dados=p["dados"])
    alvos = alvos_do_post(p, so_canais)
    horas = p["expira_horas"] if p["expira_horas"] is not None else (
        opcao("expira_auto_horas") if p["origem"] != "manual" else None)
    expira_em = None
    if horas:
        expira_em = (datetime.now(banco.FUSO) + timedelta(hours=float(horas))).strftime("%Y-%m-%d %H:%M")
    enviados, erros = 0, []
    for canal in alvos:
        try:
            mid, file_id = enviar(canal["chat"], {**p, "legenda": legenda_do_canal(p, canal)})
            if file_id and p["foto"].startswith("uploads/"):
                p["foto"] = file_id  # depois da 1ª vez, reaproveita a foto já enviada
                banco.mudar_post(pid, foto=file_id)
            banco.novo_envio(pid, canal, mid, expira_em)
            enviados += 1
        except Exception as e:
            erros.append(f"{canal['nome']}: {e}")
        time.sleep(0.5)
    banco.mudar_post(pid, status="postado" if enviados else "erro", erro="; ".join(erros) or None)
    print(f"[postado] #{pid} em {enviados} canal(is) {p['titulo'][:50]}")
    return enviados


def gratis_nos_dados(p):
    """Livro que, pela última conferência, ainda está de graça."""
    return p["origem"] == "livro" and not ((p.get("dados") or {}).get("preco") or 0)


def proximo_automatico(canais_lista=None):
    """Escolhe o próximo da fila: livro, ou às vezes uma oferta (nunca duas seguidas).
    Com canais_lista, só vale post que vai para algum desses canais."""
    fila = banco.posts(status=("fila",))
    vencimento = (datetime.now(banco.FUSO) - timedelta(days=2)).strftime("%Y-%m-%d")
    for p in fila:
        if p["criado"] < vencimento and p["origem"] != "manual":
            banco.mudar_post(p["id"], status="expirou")
    fila = [p for p in fila if p["criado"] >= vencimento or p["origem"] == "manual"]
    if canais_lista is not None:
        fila = [p for p in fila if alvos_do_post(p, canais_lista)]
    livros = [p for p in fila if p["origem"] in ("livro", "manual")]
    livros.sort(key=lambda p: 0 if p["origem"] == "manual" or gratis_nos_dados(p) else 1)  # grátis primeiro
    ofertas = [p for p in fila if p["origem"] == "oferta"] if CONFIG.get("tag_afiliado_br") else []
    if not livros:  # sem livro na fila: oferta mesmo que a última tenha sido oferta
        return ofertas[0] if ofertas else None
    if ofertas and estado().get("ultimo_tipo") != "oferta" and random.random() < CONFIG["oferta_chance"]:
        return ofertas[0]
    return livros[0]


def postar_proximo(canais_lista=None):
    while True:
        p = proximo_automatico(canais_lista)
        if not p:
            if garimpar_em_segundo_plano():  # fila vazia: já busca livros e ofertas novos
                avisar("A fila acabou. Já estou garimpando livros e ofertas novos (uns 20 minutos).")
            else:
                avisar("A fila acabou. Mande /garimpar ou use o painel.")
            return False
        if publicar_seguro(p["id"], so_canais=canais_lista, so_gratis=gratis_nos_dados(p)):
            mudar_estado(ultimo_tipo=p["origem"])
            return True


# ---------- garimpo ----------
def encher_fila():
    """Pega o último garimpo e transforma em posts na fila."""
    def ultimo(padrao):
        arquivos = sorted((PASTA / "garimpo").glob(padrao))
        return json.loads(arquivos[-1].read_text(encoding="utf-8")) if arquivos else []
    livros = ultimo("????-??-??.json")
    for l in livros:
        l.setdefault("tipo", "livro")
    conhecidos = banco.asins_conhecidos()
    novos = [i for i in livros + ultimo("????-??-?? ofertas.json") if i["asin"] not in conhecidos]
    escritor.escrever(novos)
    for i in novos:
        banco.novo_post(i["tipo"], escritor.legenda(i), foto=i.get("capa", ""),
                        botoes=[escritor.botao(i)], asin=i["asin"],
                        titulo=i.get("titulo") or i.get("titulo_curto") or i.get("titulo_completo", ""),
                        dados=i)
    return len(novos)


def garimpar_em_segundo_plano():
    if _garimpando.is_set():
        return False

    def trabalho():
        _garimpando.set()
        try:
            import garimpeiro
            avisar("⛏ Garimpando livros e ofertas (uns 20 minutos)...")
            garimpeiro.garimpar_tudo()
            n = encher_fila()
            avisar(f"🗂 {n} posts novos na fila.")
        except Exception as e:
            avisar(f"⚠️ O garimpo falhou: {e}")
        finally:
            _garimpando.clear()
    threading.Thread(target=trabalho, daemon=True).start()
    return True


# ---------- conversa com o dono no Telegram ----------
_publicando = threading.Lock()  # nunca dois "publicar" ao mesmo tempo (evita post duplicado)


def publicar_seguro(pid, conferir=True, so_canais=None, so_gratis=False):
    with _publicando:
        return publicar(pid, conferir, so_canais, so_gratis)


def em_paralelo(funcao, *args):
    threading.Thread(target=funcao, args=args, daemon=True).start()


def resumo():
    conta = {}
    for p in banco.posts(limite=1000):
        conta[p["status"]] = conta.get(p["status"], 0) + 1
    e = estado()
    fila = banco.posts(status=("fila",))
    livros = sum(1 for p in fila if p["origem"] == "livro")
    ofertas = sum(1 for p in fila if p["origem"] == "oferta")
    agendados = banco.posts(status=("agendado",))
    hora = banco.agora()[11:]
    proxima = proxima_hora() or "—"
    linhas = [
        "🤖 <b>Painel do robô</b>",
        "",
        ("⏸ Automático <b>pausado</b>" if e.get("pausado") else f"▶️ Automático <b>ligado</b> · próximo às {proxima}"),
        ("✋ Aprovação <b>ligada</b>: eu te mostro antes de postar" if e.get("aprovacao")
         else "⚡ Aprovação desligada: posto sozinho"),
        "",
        f"🗂 Na fila: <b>{len(fila)}</b> ({livros} livros, {ofertas} ofertas)",
        f"🗓 Agendados: <b>{len(agendados)}</b>",
        f"📣 Já postados: <b>{conta.get('postado', 0)}</b>",
        f"📡 Canais: {', '.join(c['nome'] for c in banco.canais()) or 'nenhum'}",
    ]
    if _garimpando.is_set():
        linhas += ["", "⛏ Garimpando agora..."]
    return "\n".join(linhas)


def teclado_menu():
    e = estado()
    linhas = [
        [{"text": "👀 Ver a fila", "callback_data": "v:0"}, {"text": "🚀 Postar o próximo", "callback_data": "m:agora"}],
        [{"text": "▶️ Ligar automático" if e.get("pausado") else "⏸ Pausar automático", "callback_data": "m:pausa"},
         {"text": "⛏ Garimpar agora", "callback_data": "m:garimpar"}],
        [{"text": "✋ Aprovação: " + ("ligada" if e.get("aprovacao") else "desligada"), "callback_data": "m:aprov"},
         {"text": "🔄 Atualizar", "callback_data": "m:menu"}],
    ]
    if e.get("painel_url"):
        linhas.append([{"text": "📲 Abrir painel completo", "web_app": {"url": e["painel_url"]}}])
    return {"inline_keyboard": linhas}


def menu(chat, editar=None):
    dados = dict(chat_id=chat, text=resumo(), parse_mode="HTML", reply_markup=teclado_menu())
    if editar:
        try:
            return tg("editMessageText", message_id=editar, **dados)
        except RuntimeError as e:
            if "not modified" in str(e):
                return None
    return tg("sendMessage", **dados)


def controles(p, posicao=""):
    """Botões do post + botões de controle embaixo da prévia."""
    return {"inline_keyboard": list(p["botoes"]) + [
        [{"text": "✅ Publicar agora", "callback_data": f"p:{p['id']}"},
         {"text": "⏭ Próximo" + posicao, "callback_data": f"v:{p['id']}"}],
        [{"text": "❌ Tirar da fila", "callback_data": f"d:{p['id']}"},
         {"text": "🏠 Menu", "callback_data": "m:menu"}],
    ]}


def mostrar_previa(chat, depois_de=0, p=None, aviso=""):
    """Manda a prévia do post exatamente como vai sair, com os botões de controle."""
    fila = banco.posts(status=("fila",))
    if p is None:
        ids = [x["id"] for x in fila]
        resto = fila
        if depois_de in ids:
            i = ids.index(depois_de)
            resto = fila[i + 1:] + fila[:i]  # dá a volta na fila
        if not resto:
            tg("sendMessage", chat_id=chat, text="🗂 A fila está vazia. Toque em ⛏ Garimpar agora.",
               reply_markup=teclado_menu())
            return
        p = resto[0]
    n = next((i + 1 for i, x in enumerate(fila) if x["id"] == p["id"]), 0)
    posicao = f" ({n}/{len(fila)})" if n else ""
    if aviso:
        tg("sendMessage", chat_id=chat, text=aviso, parse_mode="HTML")
    enviar(chat, p, teclado=controles(p, posicao))


def trocar_botoes(cb, texto, pid=None):
    linhas = [[{"text": texto, "callback_data": "x"}]]
    if pid:
        linhas.append([{"text": "⏭ Ver o próximo", "callback_data": f"v:{pid}"},
                       {"text": "🏠 Menu", "callback_data": "m:menu"}])
    try:
        tg("editMessageReplyMarkup", chat_id=cb["message"]["chat"]["id"],
           message_id=cb["message"]["message_id"], reply_markup={"inline_keyboard": linhas})
    except Exception as e:
        print(f"[botões] {e}")


def responder(cb, texto="", alerta=False):
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=texto[:190], show_alert=alerta)
    except Exception:
        pass


def tratar_botao(cb):
    chat = cb["message"]["chat"]["id"]
    if cb["from"]["id"] != estado().get("admin"):
        return responder(cb, "Só o dono do robô pode usar.", True)
    acao, _, arg = cb.get("data", "").partition(":")

    if acao == "x":
        return responder(cb)
    if acao == "c":  # botões da prévia da central (inglês e renda extra)
        import central
        responder(cb, "⏳ Um instante...")
        em_paralelo(central.tratar_botao, cb, arg)
        return
    if acao == "r":  # botões da prévia do canal de renda extra
        import renda
        responder(cb, "⏳ Um instante...")
        em_paralelo(renda.tratar_botao, cb, arg)
        return
    if acao == "m":
        if arg == "pausa":
            pausado = not estado().get("pausado")
            mudar_estado(pausado=pausado)
            responder(cb, "⏸ Automático pausado" if pausado else "▶️ Automático ligado")
        elif arg == "aprov":
            ligada = not estado().get("aprovacao")
            mudar_estado(aprovacao=ligada)
            responder(cb, "✋ Agora eu te mostro cada post antes de sair" if ligada
                      else "⚡ Agora eu posto sozinho nos horários", True)
        elif arg == "garimpar":
            responder(cb, "⛏ Garimpando (uns 20 minutos)" if garimpar_em_segundo_plano() else "Já estou garimpando")
        elif arg == "agora":
            responder(cb, "🚀 Conferindo na Amazon e postando...")
            em_paralelo(lambda: avisar("📣 Postado." if postar_proximo() else "Nada foi postado."))
            return
        else:
            responder(cb)
        if cb["message"].get("text"):
            menu(chat, editar=cb["message"]["message_id"])
        else:
            menu(chat)
        return

    pid = int(arg or 0)
    if acao == "v":
        responder(cb)
        em_paralelo(mostrar_previa, chat, pid)
    elif acao == "p":
        p = banco.post(pid)
        if not p or p["status"] not in ("fila", "agendado"):
            responder(cb, "Esse post já saiu ou foi tirado da fila.", True)
            return trocar_botoes(cb, f"Esse já foi ({p['status'] if p else 'apagado'})", pid)
        responder(cb, "🚀 Conferindo na Amazon e publicando...")
        trocar_botoes(cb, "⏳ Publicando...")

        def trabalho():
            n = publicar_seguro(pid)
            if n:
                mudar_estado(ultimo_tipo=p["origem"])
                trocar_botoes(cb, f"✅ Publicado em {n} canal(is)", pid)
            else:
                st = (banco.post(pid) or {}).get("status")
                trocar_botoes(cb, "⌛ A promoção acabou na Amazon" if st == "expirou" else "⚠️ Não consegui postar", pid)
        em_paralelo(trabalho)
    elif acao == "d":
        banco.mudar_post(pid, status="cancelado")
        responder(cb, "❌ Tirei da fila")
        trocar_botoes(cb, "❌ Tirado da fila", pid)


def tratar_comando(texto, chat):
    if texto.startswith("/start") and not estado().get("admin"):
        mudar_estado(admin=chat)
        tg("sendMessage", chat_id=chat, text="Pronto, você é o dono deste robô.")
    if chat != estado().get("admin"):
        return
    cmd = texto.split()[0].split("@")[0].lower()
    if cmd in ("/start", "/menu", "/fila"):
        menu(chat)
    elif cmd == "/pausar":
        mudar_estado(pausado=True)
        menu(chat)
    elif cmd == "/continuar":
        mudar_estado(pausado=False)
        menu(chat)
    elif cmd == "/agora":
        avisar("🚀 Conferindo na Amazon e postando...")
        em_paralelo(lambda: avisar("📣 Postado." if postar_proximo() else "Nada foi postado."))
    elif cmd == "/proximos":
        em_paralelo(mostrar_previa, chat, 0)
    elif cmd == "/garimpar":
        garimpar_em_segundo_plano() or avisar("Já estou garimpando.")
    elif cmd == "/aprovacao":
        partes = texto.split()
        ligada = (partes[1].lower() in ("sim", "on", "liga", "ligar")) if len(partes) > 1 else not estado().get("aprovacao")
        mudar_estado(aprovacao=ligada)
        menu(chat)
    elif cmd == "/painel":
        url = estado().get("painel_url")
        if url:
            tg("sendMessage", chat_id=chat, text="📲 Toque para abrir o painel:",
               reply_markup={"inline_keyboard": [[{"text": "Abrir painel", "web_app": {"url": url}}]]})
        else:
            avisar("O painel está só no PC por enquanto: http://localhost:8800")
    else:
        menu(chat)


def botao_do_painel(url):
    """Põe o botão 'Painel' no menu do chat do dono, abrindo o painel dentro do Telegram."""
    admin = estado().get("admin")
    mudar_estado(painel_url=url)
    if not admin:
        return
    try:
        tg("setChatMenuButton", chat_id=admin,
           menu_button={"type": "web_app", "text": "Painel", "web_app": {"url": url}})
        tg("setMyCommands", commands=[
            {"command": "menu", "description": "Menu com botões"},
            {"command": "proximos", "description": "Ver e aprovar os próximos posts"},
            {"command": "agora", "description": "Postar o próximo agora"},
            {"command": "painel", "description": "Abrir o painel completo"},
            {"command": "aprovacao", "description": "Ligar/desligar aprovação antes de postar"},
            {"command": "garimpar", "description": "Garimpar agora"},
            {"command": "pausar", "description": "Pausar o automático"},
            {"command": "continuar", "description": "Ligar o automático"}])
    except Exception as e:
        print(f"[menu] {e}")


# ---------- laço principal ----------
def ciclo():
    agora_txt = banco.agora()
    hoje, hora = agora_txt[:10], agora_txt[11:]
    for p in banco.posts(status=("agendado",)):
        if p["agendado_para"] and p["agendado_para"] <= agora_txt:
            publicar_seguro(p["id"])
    for e in banco.envios_vencidos():
        try:
            apagar(e["chat"], e["message_id"])
            banco.marcar_envio(e["id"], "apagado")
        except Exception as erro:
            print(f"[apagar] {erro}")
            banco.marcar_envio(e["id"], "erro_apagar")
    st = estado()
    if not opcao("posta_no_pc"):  # o robô da nuvem (GitHub) cuida de garimpar e postar
        return
    # garimpo do dia: se o PC estava desligado na hora marcada, garimpa assim que ligar
    if hora >= CONFIG["garimpo_diario"] and st.get("ultimo_garimpo") != hoje:
        mudar_estado(ultimo_garimpo=hoje)
        garimpar_em_segundo_plano()
    na_hora = [c for c in banco.canais(so_auto=True) if hora in horarios_do_canal(c)]
    if na_hora and st.get("ultimo_horario") != agora_txt and not st.get("pausado"):
        mudar_estado(ultimo_horario=agora_txt)
        if st.get("aprovacao") and st.get("admin"):
            p = proximo_automatico(na_hora)
            if p:
                mostrar_previa(st["admin"], p=p, aviso=f"⏰ <b>Hora do post das {hora}.</b> Publico este?")
            else:
                avisar("A fila acabou. Toque em ⛏ Garimpar agora no /menu.")
        else:
            postar_proximo(na_hora)


def _laco_do_relogio():
    """Horários, agendados e validade: roda à parte, para os botões responderem na hora."""
    while True:
        try:
            ciclo()
        except Exception as e:
            print(f"[relógio] {type(e).__name__}: {e}")
        time.sleep(20)


def rodar():
    print(f"Motor ligado. Horários: {' '.join(opcao('horarios'))}")
    threading.Thread(target=_laco_do_relogio, daemon=True).start()
    while True:
        try:
            for upd in tg("getUpdates", offset=estado().get("offset", 0), timeout=25,
                          allowed_updates=["message", "callback_query"]):
                mudar_estado(offset=upd["update_id"] + 1)
                try:
                    if upd.get("callback_query"):
                        tratar_botao(upd["callback_query"])
                    elif upd.get("message", {}).get("text"):
                        m = upd["message"]
                        tratar_comando(m["text"], m["chat"]["id"])
                except Exception as e:
                    print(f"[botão/comando] {type(e).__name__}: {e}")
        except requests.RequestException as e:
            print(f"[rede] {e}. Tento de novo em 15 s.")
            time.sleep(15)
        except Exception as e:  # o robô nunca cai por causa de um erro
            if "webhook" in str(e).lower():
                # Os cliques agora chegam pela Vercel e são tratados na nuvem, 24 horas.
                # O PC só olha de novo daqui a 10 minutos, caso o webhook seja desligado.
                print("[telegram] Botões e comandos estão na nuvem (webhook ligado). O PC não escuta o Telegram.")
                time.sleep(600)
                continue
            print(f"[erro] {type(e).__name__}: {e}")
            time.sleep(5)


def preparar_primeira_vez():
    """Cadastra o canal do .env e traz a fila antiga (fila.json) para o banco."""
    if not banco.canais() and os.environ.get("CANAL"):
        banco.novo_canal(os.environ["CANAL"], os.environ["CANAL"], auto=True)
    antiga = PASTA / "fila.json"
    if antiga.exists() and not banco.posts():
        for i in json.loads(antiga.read_text(encoding="utf-8")):
            status = {"aprovado": "fila", "pendente": "fila", "postado": "postado"}.get(i["status"], "cancelado")
            banco.novo_post(i["tipo"], i["legenda"], foto=i.get("capa", ""), botoes=[escritor.botao(i)],
                            asin=i["asin"], status=status,
                            titulo=i.get("titulo") or i.get("titulo_curto") or i.get("titulo_completo", ""),
                            dados=i)
        antiga.rename(PASTA / "fila-importada.json")
