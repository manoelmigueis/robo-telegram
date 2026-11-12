"""Central dos canais de conteúdo (inglês e renda extra).

Para cada canal: fila, próximos horários, agendados, já postados e tirados da fila.
Ações: postar agora, pôr como próximo, agendar, tirar da fila, voltar para a fila, apagar do canal,
prévia no privado (com botões) e criar um post novo com IA no estilo do canal.

O estado de cada canal mora no GitHub (ingles/estado.json, renda/estado.json), porque quem posta
nos horários é a nuvem. Toda mudança é aplicada em cima da versão mais nova do GitHub e enviada na
hora (commit direto). Assim o PC, o painel e a nuvem nunca desfazem o trabalho um do outro.
"""
import html
import importlib
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import postador

RAIZ = Path(__file__).parent
FUSO = timezone(timedelta(hours=-4))  # Manaus
ESPELHO = RAIZ / "nuvem-espelho.git"  # PC: cópia leve do GitHub
if not ESPELHO.exists():
    ESPELHO = RAIZ / ".git"  # nuvem (GitHub Actions): o próprio repositório

NICHOS = {
    "ingles": {"nome": "Inglês", "emoji": "🇺🇸", "tipos": ["lista", "aula", "palavra", "quiz"]},
    "renda": {"nome": "Renda extra", "emoji": "💵", "tipos": ["lista", "quiz"]},
}
NOMES_TIPO = {"lista": "Lista", "aula": "Aula", "palavra": "Expressão", "quiz": "Quiz", "post": "Post",
              "dica": "Dica", "noticia": "Notícia"}


class Erro(Exception):
    """Erro para mostrar ao Manoel (texto simples)."""


def modulo(n):
    if n not in NICHOS:
        raise Erro("Esse canal não existe.")
    return importlib.import_module(n)


def normal(e):
    e = dict(e or {})
    for k in ("postados", "pulados", "frente", "apagados", "noticias"):
        e.setdefault(k, [])
    for k in ("agendados", "quando", "mensagens"):
        e.setdefault(k, {})
    e.setdefault("contador", 0)
    return e


def agora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------- GitHub ----------------
def _git(*args, entrada=None, env=None):
    if isinstance(entrada, str):
        entrada = entrada.encode("utf-8")
    r = subprocess.run(["git", f"--git-dir={ESPELHO}", *args], input=entrada, capture_output=True, timeout=90,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip()[:300])
    return r.stdout.decode("utf-8", "replace").strip()


def tem_nuvem():
    return ESPELHO.exists() and not os.environ.get("VERCEL")


_cache = {}


def estado(n, fresco=False):
    """Estado do canal como está no GitHub. Na Vercel os arquivos já vêm baixados do GitHub."""
    m = modulo(n)
    if not tem_nuvem():
        return normal(m.estado())
    c = _cache.get(n)
    if c and not fresco and time.time() - c[0] < 60:
        return c[1]
    try:
        _git("fetch", "-q", "origin", "main")
        e = normal(json.loads(_git("show", f"FETCH_HEAD:{n}/estado.json")))
    except Exception as erro:
        print(f"[central] não li o estado da nuvem ({n}): {erro}")
        e = normal(m.estado())
    _cache[n] = (time.time(), e)
    return e


def banco_nuvem(n):
    m = modulo(n)
    if tem_nuvem():
        try:
            _git("fetch", "-q", "origin", "main")
            return json.loads(_git("show", f"FETCH_HEAD:{n}/posts.json"))
        except Exception as erro:
            print(f"[central] não li o banco da nuvem ({n}): {erro}")
    return m.banco()


def _json_banco(b, n):
    return json.dumps(b, ensure_ascii=False, indent=2 if n == "ingles" else 1)


def gravar(n, msg, mudar=None, mudar_banco=None, arquivos=None):
    """Aplica as mudanças em cima do GitHub mais novo e manda o commit (tenta 4 vezes).
    mudar(estado) e mudar_banco(lista) mexem nos JSON; arquivos = {caminho: bytes} (ex.: imagens)."""
    m = modulo(n)
    if not tem_nuvem():  # sem cópia do GitHub: grava só aqui (o passo "Guardar" do GitHub envia depois)
        e = normal(m.estado())
        if mudar:
            mudar(e)
            m.salvar_estado(e)
        if mudar_banco:
            b = m.banco()
            mudar_banco(b)
            m.BANCO.write_text(_json_banco(b, n), encoding="utf-8")
        for caminho, dados in (arquivos or {}).items():
            (RAIZ / caminho).parent.mkdir(parents=True, exist_ok=True)
            (RAIZ / caminho).write_bytes(dados)
        return e
    ultimo = None
    for tentativa in range(4):
        try:
            _git("fetch", "-q", "origin", "main")
            base = _git("rev-parse", "FETCH_HEAD")
            novos = dict(arquivos or {})
            e = None
            if mudar:
                e = normal(json.loads(_git("show", f"FETCH_HEAD:{n}/estado.json")))
                mudar(e)
                e["noticias"] = e["noticias"][-400:]
                novos[f"{n}/estado.json"] = json.dumps(e, ensure_ascii=False, indent=2).encode("utf-8")
            if mudar_banco:
                b = json.loads(_git("show", f"FETCH_HEAD:{n}/posts.json"))
                mudar_banco(b)
                novos[f"{n}/posts.json"] = _json_banco(b, n).encode("utf-8")
            idx = {"GIT_INDEX_FILE": str(Path(tempfile.gettempdir()) / f"central-indice-{os.getpid()}")}
            _git("read-tree", base, env=idx)
            for caminho, dados in novos.items():
                blob = _git("hash-object", "-w", "--stdin", entrada=dados)
                _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{caminho}", env=idx)
            arvore = _git("write-tree", env=idx)
            nome = "robo-nuvem" if os.environ.get("GITHUB_ACTIONS") else "robo-pc"
            quem = {"GIT_AUTHOR_NAME": nome, "GIT_AUTHOR_EMAIL": f"{nome}@users.noreply.github.com",
                    "GIT_COMMITTER_NAME": nome, "GIT_COMMITTER_EMAIL": f"{nome}@users.noreply.github.com"}
            commit = _git("commit-tree", arvore, "-p", base, "-m", msg, env=quem)
            _git("push", "-q", "origin", f"{commit}:refs/heads/main")
            for caminho, dados in novos.items():  # deixa a cópia local igual à da nuvem
                (RAIZ / caminho).parent.mkdir(parents=True, exist_ok=True)
                (RAIZ / caminho).write_bytes(dados)
            if e is not None:
                _cache[n] = (time.time(), e)
            return e
        except Exception as erro:
            ultimo = erro
            print(f"[central] não consegui gravar na nuvem ({n}), tentativa {tentativa + 1}: {erro}")
            time.sleep(1 + tentativa)
    raise Erro(f"Não consegui salvar na nuvem. Tente de novo. ({ultimo})")


# ---------------- fila e agenda ----------------
def fila(n, e=None, banco=None):
    """Próximos posts na ordem: primeiro os que você pôs na frente, depois a ordem do banco.
    Fora da fila: já postados, tirados e agendados (estes saem na hora marcada)."""
    m = modulo(n)
    e = normal(e if e is not None else estado(n))
    b = banco if banco is not None else m.banco()
    fora = set(e["postados"]) | set(e["pulados"]) | set(e["agendados"])
    por_id = {i["id"]: i for i in b}
    frente = [por_id[i] for i in e["frente"] if i in por_id and i not in fora]
    na_frente = {i["id"] for i in frente}
    return frente + [i for i in b if i["id"] not in fora and i["id"] not in na_frente]


def grade(n):
    m = modulo(n)
    if n == "renda":
        return dict(sorted(m.grade().items()))
    return {h: "post" for h in sorted(m.CFG.get("horarios", ["09:00", "13:00", "19:00"]))}


def rotulo_quando(dt):
    dt = dt.astimezone(FUSO)
    hoje = datetime.now(FUSO).date()
    if dt.date() == hoje:
        dia = "hoje"
    elif dt.date() == hoje + timedelta(days=1):
        dia = "amanhã"
    else:
        dia = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"][dt.weekday()] + dt.strftime(" %d/%m")
    return f"{dia} {dt:%H:%M}"


def cartao(item):
    imgs = item.get("imagens") or []
    texto = re.sub(r"<[^>]+>", " ", item.get("corpo", ""))
    texto = re.sub(r"\s+", " ", html.unescape(texto)).strip()
    if item["tipo"] == "quiz":
        q = item.get("quiz", {})
        texto = q.get("pergunta", "") + " " + " · ".join(q.get("opcoes", []))
    return {"id": item["id"], "tipo": item["tipo"], "nome_tipo": NOMES_TIPO.get(item["tipo"], item["tipo"]),
            "titulo": item.get("titulo") or item.get("quiz", {}).get("pergunta", ""),
            "imagem": Path(imgs[0]).name if imgs else "", "slides": len(imgs), "texto": texto[:260]}


def resumo(n):
    m = modulo(n)
    e = estado(n)
    b = m.banco()
    por_id = {i["id"]: i for i in b}
    f = fila(n, e, b)
    # próximos horários, com o post que vai sair em cada um
    g = grade(n)
    agora = datetime.now(FUSO)
    agenda, k = [], 0
    for dia in range(0, 4):
        d = agora.date() + timedelta(days=dia)
        for h, tipo in g.items():
            hh, mm = map(int, h.split(":"))
            quando = datetime(d.year, d.month, d.day, hh, mm, tzinfo=FUSO)
            if quando <= agora or len(agenda) >= 8:
                continue
            if tipo == "noticia":
                agenda.append({"quando": rotulo_quando(quando), "tipo": "noticia", "titulo": "Notícia escolhida na hora "
                               "(curso grátis, IA ou vaga)", "id": ""})
            elif k < len(f):
                agenda.append({"quando": rotulo_quando(quando), **cartao(f[k])})
                k += 1
            else:
                agenda.append({"quando": rotulo_quando(quando), "tipo": "vazio", "titulo": "Fila vazia: nada para postar",
                               "id": ""})
    agendados = sorted(({**cartao(por_id[i]), "quando": rotulo_quando(datetime.fromisoformat(q)), "iso": q}
                        for i, q in e["agendados"].items() if i in por_id), key=lambda x: x["iso"])
    postados = []
    for i in reversed(e["postados"][-25:]):
        if i in por_id:
            q = e["quando"].get(i)
            postados.append({**cartao(por_id[i]), "quando": rotulo_quando(datetime.fromisoformat(q)) if q else "",
                             "apagavel": bool(e["mensagens"].get(i)), "apagado": i in e["apagados"]})
    por_dia = sum(1 for t in g.values() if t != "noticia") or 1
    canal = m.canal()
    return {"nicho": n, **NICHOS[n], "canal": canal, "link": f"https://t.me/{canal.lstrip('@')}",
            "grade": [{"hora": h, "tipo": NOMES_TIPO.get(t, t)} for h, t in g.items()],
            "fila": [cartao(i) for i in f], "agenda": agenda, "agendados": agendados, "postados": postados,
            "pulados": [cartao(por_id[i]) for i in e["pulados"] if i in por_id],
            "total_postados": len(e["postados"]), "ultimo": e.get("ultimo"),
            "dias": round(len(f) / por_dia, 1), "noticias": n == "renda",
            "vercel": bool(os.environ.get("VERCEL"))}


def achar(n, alvo, banco=None):
    b = banco if banco is not None else modulo(n).banco()
    item = next((i for i in b if i["id"] == alvo), None) or next((i for i in b if i["id"].startswith(alvo)), None)
    if not item:
        raise Erro("Não achei esse post.")
    return item


# ---------------- postar ----------------
def enviar(n, item, chat, contador=0):
    m = modulo(n)
    if n == "ingles":
        com_curso = chat == m.canal() and (contador + 1) % int(m.CFG.get("curso_a_cada", 5)) == 0
        return m.enviar(chat, item, com_curso)
    return m.enviar_dica(chat, item)


def registrar_postado(n, item_id, mid):
    agora = agora_iso()

    def marcar(e):
        if item_id not in e["postados"]:
            e["postados"].append(item_id)
            e["contador"] += 1
        e["ultimo"] = agora
        e["quando"][item_id] = agora
        if mid:
            e["mensagens"][item_id] = [mid]
        e["agendados"].pop(item_id, None)
        for k in ("frente", "pulados", "apagados"):
            e[k] = [x for x in e[k] if x != item_id]
    try:
        return gravar(n, f"central: {n} {item_id[:3]} postado", mudar=marcar)
    except Exception as erro:  # o post já saiu: guarda pelo menos aqui (o GitHub Actions envia no fim)
        print(f"[central] {erro}")
        m = modulo(n)
        e = normal(m.estado())
        marcar(e)
        m.salvar_estado(e)
        return e


def postar(n, item_id=None):
    """Posta no canal um post da fila (ou o próximo) e marca como postado na nuvem."""
    m = modulo(n)
    e = estado(n, fresco=True)
    if item_id:
        item = achar(n, item_id)
        if item["id"] in e["postados"] and item["id"] not in e["apagados"]:
            return item, None
    else:
        f = fila(n, e)
        if not f:
            return None, None
        item = f[0]
    mid = enviar(n, item, m.canal(), e["contador"])
    registrar_postado(n, item["id"], mid)
    print(f"[central] {n}: postado {item['id']} (mensagem {mid})")
    return item, mid


def postar_agendados(n):
    """Posta os agendados cuja hora chegou. Devolve quantos saíram."""
    e = estado(n, fresco=True)
    agora = datetime.now(timezone.utc)
    vencidos = sorted((q, i) for i, q in e["agendados"].items() if datetime.fromisoformat(q) <= agora)
    feitos = 0
    for _, i in vencidos:
        try:
            postar(n, i)
            feitos += 1
        except Exception as erro:
            print(f"[central] agendado {i} falhou: {erro}")
    return feitos


# ---------------- outras ações ----------------
def tirar(n, item_id):
    item_id = achar(n, item_id)["id"]

    def mudar(e):
        if item_id not in e["pulados"]:
            e["pulados"].append(item_id)
        e["agendados"].pop(item_id, None)
        e["frente"] = [x for x in e["frente"] if x != item_id]
    gravar(n, f"central: {n} {item_id[:3]} tirado da fila", mudar=mudar)


def voltar(n, item_id):
    """Volta para a fila um post tirado (ou apagado do canal)."""
    item_id = achar(n, item_id)["id"]

    def mudar(e):
        e["pulados"] = [x for x in e["pulados"] if x != item_id]
        if item_id in e["apagados"]:
            e["apagados"].remove(item_id)
            e["postados"] = [x for x in e["postados"] if x != item_id]
    gravar(n, f"central: {n} {item_id[:3]} de volta na fila", mudar=mudar)


def proximo(n, item_id):
    item_id = achar(n, item_id)["id"]

    def mudar(e):
        e["frente"] = [item_id] + [x for x in e["frente"] if x != item_id]
        e["pulados"] = [x for x in e["pulados"] if x != item_id]
        e["agendados"].pop(item_id, None)
    gravar(n, f"central: {n} {item_id[:3]} é o próximo", mudar=mudar)


def agendar(n, item_id, quando):
    """quando = 'AAAA-MM-DDTHH:MM' no horário de Manaus. Vazio = desfaz o agendamento."""
    item_id = achar(n, item_id)["id"]
    iso = None
    if quando:
        try:
            dt = datetime.fromisoformat(str(quando)[:16]).replace(tzinfo=FUSO)
        except ValueError:
            raise Erro("Data ou hora inválida.")
        if dt <= datetime.now(FUSO):
            raise Erro("Escolha uma data e hora no futuro.")
        iso = dt.astimezone(timezone.utc).isoformat(timespec="seconds")

    def mudar(e):
        if iso:
            e["agendados"][item_id] = iso
            e["pulados"] = [x for x in e["pulados"] if x != item_id]
        else:
            e["agendados"].pop(item_id, None)
    gravar(n, f"central: {n} {item_id[:3]} agendado", mudar=mudar)
    return iso


def apagar(n, item_id):
    m = modulo(n)
    item_id = achar(n, item_id)["id"]
    e = estado(n, fresco=True)
    mids = e["mensagens"].get(item_id)
    if not mids:
        raise Erro("Esse post saiu antes da central existir, então não sei o número da mensagem. "
                   "Apague direto no Telegram (segure o dedo no post e toque em Apagar).")
    for mid in mids:
        try:
            postador.tg("deleteMessage", chat_id=m.canal(), message_id=mid)
        except RuntimeError as erro:
            if "not found" not in str(erro).lower():
                raise Erro(f"O Telegram não deixou apagar ({erro}). Confira se o robô pode apagar mensagens no canal.")

    def mudar(e):
        e["mensagens"].pop(item_id, None)
        if item_id not in e["apagados"]:
            e["apagados"].append(item_id)
    gravar(n, f"central: {n} {item_id[:3]} apagado do canal", mudar=mudar)


def previa(n, item_id, chat):
    """Manda o post só para você, com os botões de postar agora ou tirar da fila."""
    item = achar(n, item_id)
    enviar(n, item, chat)
    cod = n[0]
    postador.tg("sendMessage", chat_id=chat,
                text=f"👆 Prévia do canal de {NICHOS[n]['nome'].lower()}: {item.get('titulo', '')[:150]}\n"
                     "Se não mexer, ele sai sozinho no horário dele.",
                reply_markup={"inline_keyboard": [[
                    {"text": "✅ Postar agora no canal", "callback_data": f"c:{cod}p:{item['id'][:40]}"},
                    {"text": "🗑 Tirar da fila", "callback_data": f"c:{cod}x:{item['id'][:40]}"}]]})


def tratar_botao(cb, arg):
    """Botões da prévia. arg: 'ip:ID' posta o post de inglês, 'rx:ID' tira a dica de renda da fila..."""
    n = {"i": "ingles", "r": "renda"}.get(arg[:1])
    acao, alvo = arg[1:2], arg[3:]
    try:
        if not n:
            return postador.trocar_botoes(cb, "Botão antigo")
        item = achar(n, alvo)
        e = estado(n, fresco=True)
        if acao == "p":
            if item["id"] in e["postados"]:
                return postador.trocar_botoes(cb, "Esse post já saiu no canal")
            postador.trocar_botoes(cb, "⏳ Postando...")
            postar(n, item["id"])
            postador.trocar_botoes(cb, "✅ Postado no canal")
        elif acao == "x":
            tirar(n, item["id"])
            postador.trocar_botoes(cb, "🗑 Tirado da fila")
    except Exception as erro:
        print(f"[central] botão falhou: {erro}")
        postador.trocar_botoes(cb, "⚠️ Não consegui. Tente de novo")


def postar_noticia(n="renda"):
    """Renda extra: garimpa e posta uma notícia agora (com capa)."""
    m = modulo(n)
    noticias = m.garimpar_noticias()
    e = estado(n, fresco=True)
    noticias = [x for x in noticias if m.chave(x["titulo"]) not in e["noticias"]]
    if not noticias:
        raise Erro("Não achei notícia nova boa agora. Tente mais tarde.")
    x = noticias[0]
    m.enviar_noticia(m.canal(), x)
    agora = agora_iso()

    def mudar(e):
        e["noticias"].append(m.chave(x["titulo"]))
        e["ultimo"] = agora
    gravar(n, "central: notícia postada", mudar=mudar)
    return x["titulo"]


# ---------------- criar com IA ----------------
REGRAS = {
    "ingles": "Canal de inglês para brasileiros iniciantes. Inglês correto e natural; explicação em português simples.",
    "renda": ("Canal de renda extra. Foco em aumentar as habilidades da pessoa para ganhar dinheiro: currículo, cursos "
              "profissionalizantes, IA e vagas. Nada de promessa de ganho fácil, valores garantidos, apostas, pirâmide "
              "ou golpe. Dicas reais e práticas, que dá para fazer hoje. O campo \"arte\" é o texto da imagem: "
              "\"titulo\" curto (até 45 letras, de preferência começando com o número de itens), \"sub\" vazio ou curto, "
              "\"itens\" com 4 a 6 frases bem curtas (até 28 letras cada; no quiz, as opções), \"icone\" (1 emoji) e "
              "\"icones\" (1 emoji por item)."),
}


def _extrair_json(texto):
    texto = re.sub(r"^```(?:json)?|```$", "", (texto or "").strip(), flags=re.M).strip()
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini < 0 or fim < 0:
        raise Erro("A IA não devolveu um post. Tente de novo.")
    return json.loads(texto[ini:fim + 1])


def _slug(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    palavras = [p for p in re.findall(r"[a-z0-9]+", t) if len(p) > 2][:3]
    return "-".join(palavras) or "post"


def desenhar_renda(arte, quiz):
    """Arte 4:5 do canal de renda extra (o desenho com o ícone grande). Devolve os bytes do PNG."""
    import sys
    sys.path.insert(0, str(RAIZ / "renda"))
    import arte as desenho
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1080, "height": 1350})
        pag.set_content(desenho.montar(arte, quiz), wait_until="networkidle")
        pag.evaluate("document.fonts.ready")
        png = pag.screenshot()
        nav.close()
    return png


def criar(n, tema="", tipo="lista"):
    """A IA escreve um post novo no estilo do canal e ele entra no fim da fila."""
    import escritor
    m = modulo(n)
    if tipo not in NICHOS[n]["tipos"]:
        tipo = NICHOS[n]["tipos"][0]
    b = banco_nuvem(n)
    estilo = (m.PASTA / "ESTILO.md").read_text(encoding="utf-8") if (m.PASTA / "ESTILO.md").exists() else ""
    exemplos = [x for x in b if x["tipo"] == tipo][-2:] or b[-2:]
    exemplos = [{k: v for k, v in x.items() if k not in ("id", "imagens")} for x in exemplos]
    titulos = "; ".join(x.get("titulo", "") for x in b[-40:])
    pedido = f"""Você escreve posts para o canal do Telegram {m.canal()}.
{REGRAS[n]}

Guia de estilo do canal (siga à risca):
<guia>
{estilo}
</guia>

Exemplos de posts que já saíram, no formato JSON exato que o robô usa:
{json.dumps(exemplos, ensure_ascii=False, indent=1)}

Escreva 1 post NOVO do tipo "{tipo}" sobre: {tema.strip() or "um tema útil e diferente dos que já saíram"}.
Títulos que já existem (não repita o assunto): {titulos}

Regras do formato:
- Responda só com o JSON do post, sem nada antes ou depois, com os mesmos campos dos exemplos, menos "id" e "imagens".
- "corpo" é HTML simples do Telegram (p, b, i, br, table, tr, th, td, details, summary), igual aos exemplos.
- No quiz, "quiz" tem "pergunta", "opcoes" (lista), "correta" (número da opção certa, começando em 0), "explicacao" (até 190 letras) e "descricao".
- Português do Brasil simples, frases curtas. Nada de travessão longo.
- Nunca escreva "imagens ilustrativas"."""
    texto = escritor._via_api(pedido) if os.environ.get("ANTHROPIC_API_KEY") else None
    texto = texto or escritor._via_claude_code(pedido)
    if not texto:
        raise Erro("A IA não respondeu agora. Tente de novo em 1 minuto.")
    item = _extrair_json(texto)
    item["tipo"] = tipo
    if not item.get("titulo") or (tipo != "quiz" and not item.get("corpo")):
        raise Erro("A IA devolveu um post incompleto. Tente de novo.")
    if tipo == "quiz":
        q = item.get("quiz") or {}
        if not q.get("pergunta") or len(q.get("opcoes", [])) < 2 or not isinstance(q.get("correta"), int):
            raise Erro("A IA devolveu um quiz incompleto. Tente de novo.")
    numeros = [int(x["id"][:3]) for x in b if x["id"][:3].isdigit()]
    num = (max(numeros) if numeros else 0) + 1
    item = {"id": f"{num:03d}-{_slug(item['titulo'])}", **{k: v for k, v in item.items() if k != "id"}}
    arquivos = {}
    item["imagens"] = []
    if n == "renda" and item.get("arte", {}).get("itens"):
        nome = f"img/{num:03d}.png"
        arquivos[f"renda/{nome}"] = desenhar_renda(item["arte"], tipo == "quiz")
        item["imagens"] = [nome]
    gravar(n, f"central: post novo {item['id']}", mudar_banco=lambda lista: lista.append(item), arquivos=arquivos)
    return item
