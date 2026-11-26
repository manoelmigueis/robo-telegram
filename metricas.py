"""Desempenho dos posts: cliques nos links e reações.

Como chega:
  - cliques: o link do post passa pela ponte na Vercel (/r/<post>.<canal>?u=...), que anota o clique;
  - reações: o Telegram avisa a ponte quando muda a contagem de reações de um post do canal.
A ponte guarda cada aviso como um arquivo vazio na pasta metricas/ do ramo "painel-respostas".
O nome do arquivo já traz tudo:  c~<post>~<canal>~<hora>~<id>  ou  r~<chat>~<mensagem>~<total>~<hora>~<id>
Aqui o robô lê a pasta inteira de uma vez, soma no banco e apaga os arquivos que já contou.
Cada aviso é contado uma vez só (tabela eventos), mesmo se apagar falhar.
"""
import os
from datetime import datetime
from urllib.parse import quote, urlparse

import banco

RAMO = "painel-respostas"
PONTE = "https://ponte-vercel-ten.vercel.app"
SITES = ("amazon.com.br", "amazon.com", "amzn.to", "amzn.com", "a.co", "link.amazon",
         "t.me", "telegram.me", "kiwify.com.br", "kiwify.app", "hotmart.com", "udemy.com")


# ---------- links rastreados ----------
def site_ok(url):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and any(host == s or host.endswith("." + s) for s in SITES)


def rastrear(url, pid, cid):
    """Troca o link pelo da ponte, que conta o clique e manda para o mesmo lugar."""
    if not pid or not cid or not site_ok(url) or url.startswith(PONTE):
        return url
    return f"{PONTE}/r/{int(pid)}.{int(cid)}?u={quote(url, safe='')}"


# link para um canal (t.me/nome), sem ser link de mensagem (t.me/nome/123) nem convite (t.me/+abc)
LINK_CANAL = r'(?:https?://)?(?:www\.)?(?:t|telegram)\.me/([A-Za-z0-9_]{4,32})(?![\w/+])'


def canais_nos_links(legenda, botoes):
    """Os nomes de canal (sem @, minúsculos) que aparecem nos links do post."""
    import re
    textos = [legenda or ""] + [b.get("url", "") for linha in (botoes or []) for b in linha]
    return {m.group(1).lower() for t in textos for m in re.finditer(LINK_CANAL, t)}


def com_convites(legenda, botoes, convites):
    """Troca t.me/canal pelo convite do anúncio, que conta quem entrou. O texto que aparece não muda."""
    import re
    if not convites:
        return legenda, botoes

    def _url(u):
        m = re.fullmatch(LINK_CANAL + r'/?', (u or "").strip())
        return convites.get(m.group(1).lower(), u) if m else u

    partes, dentro_a, saida = re.split(r'(<[^>]+>)', legenda or ""), False, []
    for parte in partes:
        if parte.startswith("<"):
            m_tag = re.match(r'<\s*(/?\w+)', parte)
            tag = m_tag.group(1).lower() if m_tag else ""
            if tag == "a":
                dentro_a = True
                parte = re.sub(r'href="([^"]+)"', lambda m: f'href="{_url(m.group(1))}"', parte)
            elif tag == "/a":
                dentro_a = False
        elif not dentro_a:  # link escrito solto no texto: vira link com o mesmo texto
            parte = re.sub(LINK_CANAL, lambda m: f'<a href="{convites[m.group(1).lower()]}">{m.group(0)}</a>'
                           if m.group(1).lower() in convites else m.group(0), parte)
        saida.append(parte)
    novos = [[{**b, "url": _url(b["url"])} if b.get("url") else b for b in linha] for linha in (botoes or [])]
    return "".join(saida), novos


def links_rastreados(p, canal, legenda, botoes):
    """Legenda e botões de um post com os links passando pela ponte."""
    import re
    pid, cid = p.get("id"), canal.get("id")
    if p.get("origem") == "manual":  # anúncio escrito por você: o link vai direto, sem passar pela ponte
        pid = None
    legenda = re.sub(r'href="([^"]+)"', lambda m: f'href="{rastrear(m.group(1).replace("&amp;", "&"), pid, cid)}"'
                     .replace("&", "&amp;") if site_ok(m.group(1).replace("&amp;", "&")) else m.group(0), legenda or "")
    cor = canal.get("cor_botao") or ""

    def _bt(b):
        b = {**b, "url": rastrear(b["url"], pid, cid)} if b.get("url") and pid else dict(b)
        if b.get("style") not in banco.CORES_BOTAO:  # sem cor própria: usa a cor do canal
            b.pop("style", None)
            if cor in banco.CORES_BOTAO:
                b["style"] = cor
        return b
    novos = [[_bt(b) for b in linha] for linha in (botoes or [])]
    if p.get("origem") == "manual":
        legenda, novos = com_convites(legenda, novos, (p.get("dados") or {}).get("convites"))
    return legenda, novos


# ---------- juntar o que a ponte anotou ----------
def _gh(metodo, caminho, **kw):
    import requests
    return requests.request(metodo, f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{caminho}",
                            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                     "Accept": "application/vnd.github+json"}, timeout=30, **kw)


def _quando(epoca):
    return datetime.fromtimestamp(int(epoca), banco.FUSO).strftime("%Y-%m-%d %H:%M")


def _novo_evento(nome, tipo, post_id, canal_id, envio_id, quando, valor):
    """Guarda o aviso. Devolve False se ele já tinha sido contado antes."""
    if banco.sql("SELECT 1 FROM eventos WHERE nome=?", (nome,), um=True):
        return False
    banco.sql("INSERT INTO eventos (nome, tipo, post_id, canal_id, envio_id, quando, valor) VALUES (?,?,?,?,?,?,?)",
              (nome, tipo, post_id, canal_id, envio_id, quando, valor))
    return True


def contar(nomes):
    """Soma no banco os avisos (nomes de arquivo sem .json). Devolve (cliques, reações) novos."""
    cliques = reacoes = entradas = 0
    chats = {}
    for c in banco.canais():
        chats[str(c["chat"]).lstrip("@").lower()] = c["id"]
    for nome in sorted(nomes, key=lambda n: n.split("~")[-2] if n.count("~") >= 4 else ""):
        p = nome.split("~")
        try:
            if p[0] == "c" and len(p) == 5:
                pid, cid, quando = int(p[1]), int(p[2]), _quando(p[3])
                e = banco.sql("SELECT id FROM envios WHERE post_id=? AND canal_id=? ORDER BY id DESC LIMIT 1",
                              (pid, cid), um=True)
                if _novo_evento(nome, "clique", pid, cid, e and e["id"], quando, 1):
                    if e:
                        banco.sql("UPDATE envios SET cliques=COALESCE(cliques,0)+1 WHERE id=?", (e["id"],))
                    cliques += 1
            elif p[0] == "j" and len(p) == 5:  # alguém entrou num canal pelo convite de um anúncio
                if _novo_evento(nome, "entrada", int(p[1]), None, None, _quando(p[3]), 1):
                    entradas += 1
            elif p[0] == "r" and len(p) == 6:
                chat, mid, total, quando = p[1].lstrip("@").lower(), int(p[2]), int(p[3]), _quando(p[4])
                cid = chats.get(chat)
                e = banco.sql("SELECT id, post_id FROM envios WHERE canal_id=? AND message_id=? ORDER BY id DESC LIMIT 1",
                              (cid, mid), um=True) if cid else None
                if _novo_evento(nome, "reacao", e and e["post_id"], cid, e and e["id"], quando, total):
                    if e:
                        banco.sql("UPDATE envios SET reacoes=? WHERE id=?", (total, e["id"]))
                    reacoes += 1
        except (ValueError, IndexError) as x:
            print(f"[métricas] aviso estranho {nome}: {x}")
    if entradas:
        print(f"[métricas] {entradas} pessoa(s) entraram em canais pelos convites dos anúncios")
    return cliques, reacoes


def coletar():
    """Lê a pasta metricas/ do ramo da fila, conta e apaga o que já foi contado."""
    if not os.environ.get("GITHUB_TOKEN") or not os.environ.get("GITHUB_REPOSITORY"):
        return 0, 0
    r = _gh("GET", f"/git/ref/heads/{RAMO}")
    if r.status_code != 200:
        print(f"[métricas] não achei o ramo da fila ({r.status_code})")
        return 0, 0
    topo = r.json()["object"]["sha"]
    arvore = _gh("GET", f"/git/commits/{topo}").json()["tree"]["sha"]
    itens = _gh("GET", f"/git/trees/{arvore}?recursive=1").json().get("tree", [])
    arquivos = [i["path"] for i in itens if i["path"].startswith("metricas/") and i["path"].endswith(".json")]
    if not arquivos:
        return 0, 0
    novos = contar([a[len("metricas/"):-len(".json")] for a in arquivos])
    print(f"[métricas] {novos[0]} clique(s) e {novos[1]} aviso(s) de reação novos ({len(arquivos)} arquivos)")
    # apaga de uma vez só (uma mudança no ramo, não uma por arquivo)
    try:
        t = _gh("POST", "/git/trees", json={"base_tree": arvore, "tree": [
            {"path": a, "mode": "100644", "type": "blob", "sha": None} for a in arquivos]}).json()["sha"]
        c = _gh("POST", "/git/commits", json={"message": "métricas contadas", "tree": t, "parents": [topo]}).json()["sha"]
        r = _gh("PATCH", f"/git/refs/heads/{RAMO}", json={"sha": c})
        if r.status_code >= 300:
            print(f"[métricas] não apaguei agora ({r.status_code}); fica para a próxima rodada")
    except Exception as x:
        print(f"[métricas] não apaguei agora: {x}")
    return novos


# ---------- números para o painel ----------
TIPOS = {"livro": "Livros", "oferta": "Ofertas", "manual": "Escritos por você", "ingles": "Inglês", "renda": "Renda"}


def _desde(dias):
    from datetime import timedelta
    return (datetime.now(banco.FUSO) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M")


def _semana(de, ate):
    """Cliques, reações e posts entre 'de' dias atrás e 'ate' dias atrás."""
    a, b = _desde(de), (_desde(ate) if ate else "9999")
    cli = banco.sql("SELECT COUNT(*) n FROM eventos WHERE tipo='clique' AND quando>=? AND quando<?", (a, b), um=True)["n"]
    env = banco.sql("SELECT COUNT(*) n, COALESCE(SUM(reacoes),0) r FROM envios WHERE enviado_em>=? AND enviado_em<?",
                    (a, b), um=True)
    return {"cliques": cli, "reacoes": env["r"], "posts": env["n"]}


def _conclusao(por_hora, por_tipo):
    """O que funcionou melhor, em palavras. Só fala quando já tem post suficiente para comparar."""
    frases = []
    horas = [h for h in por_hora if h["posts"] >= 3 and (h["cliques"] + h["reacoes"]) > 0]
    if len(horas) >= 2:
        h = max(horas, key=lambda x: x["media"])
        frases.append(f"Os posts das {int(h['hora'])}h são os que mais recebem cliques e reações.")
    tipos = [t for t in por_tipo if t["posts"] >= 3 and (t["cliques"] + t["reacoes"]) > 0]
    if len(tipos) >= 2:
        t = max(tipos, key=lambda x: x["media"])
        frases.append(f"O tipo de post que mais funciona é: {t['nome']}.")
    return " ".join(frases) or None


def resumo(dias=30):
    from datetime import timedelta
    desde = _desde(dias)
    hoje = datetime.now(banco.FUSO).date()
    datas = [(hoje - timedelta(days=n)).isoformat() for n in range(dias - 1, -1, -1)]
    cli_dia = {r["d"]: r["n"] for r in banco.sql(
        "SELECT substr(quando,1,10) d, COUNT(*) n FROM eventos WHERE tipo='clique' AND quando>=? GROUP BY d", (desde,))}
    rea_dia = {r["d"]: r["n"] for r in banco.sql(
        "SELECT substr(enviado_em,1,10) d, COALESCE(SUM(reacoes),0) n FROM envios WHERE enviado_em>=? GROUP BY d", (desde,))}
    por_dia = [{"dia": d, "cliques": cli_dia.get(d, 0), "reacoes": rea_dia.get(d, 0)} for d in datas]

    def com_media(linhas):
        for l in linhas:
            l["media"] = round((l["cliques"] + l["reacoes"]) / l["posts"], 2) if l["posts"] else 0
        return linhas

    por_hora = com_media(banco.sql(
        "SELECT substr(enviado_em,12,2) hora, COUNT(*) posts, COALESCE(SUM(cliques),0) cliques, "
        "COALESCE(SUM(reacoes),0) reacoes FROM envios WHERE enviado_em>=? GROUP BY hora ORDER BY hora", (desde,)))
    por_tipo = com_media(banco.sql(
        "SELECT p.origem tipo, COUNT(*) posts, COALESCE(SUM(v.cliques),0) cliques, COALESCE(SUM(v.reacoes),0) reacoes "
        "FROM envios v JOIN posts p ON p.id=v.post_id WHERE v.enviado_em>=? GROUP BY p.origem", (desde,)))
    for t in por_tipo:
        t["nome"] = TIPOS.get(t["tipo"], t["tipo"])
    por_canal = banco.sql(
        "SELECT c.nome, c.chat, COUNT(e.nome) cliques FROM eventos e JOIN canais c ON c.id=e.canal_id "
        "WHERE e.tipo='clique' AND e.quando>=? GROUP BY c.id ORDER BY cliques DESC", (_desde(7),))
    melhores = banco.sql(
        "SELECT p.id, p.titulo, p.origem, SUM(COALESCE(v.cliques,0)) cliques, SUM(COALESCE(v.reacoes,0)) reacoes "
        "FROM envios v JOIN posts p ON p.id=v.post_id WHERE v.enviado_em>=? "
        "GROUP BY p.id HAVING cliques>0 OR reacoes>0 ORDER BY cliques DESC, reacoes DESC LIMIT 5", (_desde(7),))
    esta, antes = _semana(7, 0), _semana(14, 7)
    return {"dias": dias, "cliques": esta["cliques"], "reacoes": esta["reacoes"], "semana": esta, "semana_antes": antes,
            "por_dia": por_dia, "por_hora": por_hora, "por_tipo": por_tipo, "por_canal": por_canal,
            "melhores": melhores, "conclusao": _conclusao(por_hora, por_tipo)}


# ---------- crescimento dos canais (membros) ----------
def canais_medidos():
    """Canais públicos do robô (os da tabela + o de renda extra), sem chats privados."""
    vistos, lista = set(), []
    for c in banco.canais():
        chat = str(c["chat"])
        if chat.startswith("@") or chat.startswith("-100"):
            vistos.add(chat.lower())
            lista.append((chat, c["nome"] or chat))
    try:
        import renda
        if renda.canal().lower() not in vistos:
            lista.append((renda.canal(), "💰 Renda Extra"))
            vistos.add(renda.canal().lower())
    except Exception:
        pass
    try:
        import achadinhos
        if achadinhos.canal().lower() not in vistos:
            lista.append((achadinhos.canal(), "🏠 Achadinhos Casa e Cozinha"))
    except Exception:
        pass
    return lista


def medir_membros():
    """Anota quantos membros cada canal tem agora. Uma medida por hora (as outras rodadas pulam)."""
    import postador
    hora = banco.agora()[:13] + ":00"
    feitas = {r["chat"] for r in banco.sql("SELECT chat FROM membros WHERE hora=?", (hora,))}
    for chat, nome in canais_medidos():
        if chat in feitas:
            continue
        try:
            total = postador.tg("getChatMemberCount", chat_id=chat)
            banco.anotar_membros(chat, nome, hora, int(total))
        except Exception as x:
            print(f"[membros] {chat}: {x}")


def crescimento(dias=30):
    """Por canal: membros por dia, entradas e saídas (estimadas pela diferença de hora em hora)."""
    from datetime import timedelta
    desde = (datetime.now(banco.FUSO) - timedelta(days=dias)).strftime("%Y-%m-%d 00:00")
    por_chat = {}
    for r in banco.membros_desde(desde):
        por_chat.setdefault(r["chat"], []).append(r)
    canais = []
    for chat, pontos in por_chat.items():
        dia_a_dia = {}
        anterior = None
        for p in pontos:
            d = dia_a_dia.setdefault(p["hora"][:10], {"dia": p["hora"][:10], "total": 0, "entradas": 0, "saidas": 0})
            d["total"] = p["total"]
            if anterior is not None:
                dif = p["total"] - anterior
                if dif > 0:
                    d["entradas"] += dif
                else:
                    d["saidas"] += -dif
            anterior = p["total"]
        serie = list(dia_a_dia.values())
        atual = pontos[-1]["total"]

        def ha(n):  # total de n dias atrás (o mais perto que tiver)
            alvo = (datetime.now(banco.FUSO) - timedelta(days=n)).strftime("%Y-%m-%d %H:%M")
            antes = [p["total"] for p in pontos if p["hora"] <= alvo]
            return antes[-1] if antes else pontos[0]["total"]
        canais.append({"chat": chat, "nome": pontos[-1]["nome"] or chat, "atual": atual,
                       "dia": atual - ha(1), "semana": atual - ha(7), "desde": pontos[0]["hora"],
                       "entradas": sum(d["entradas"] for d in serie), "saidas": sum(d["saidas"] for d in serie),
                       "serie": serie})
    canais.sort(key=lambda c: -c["atual"])
    return {"dias": dias, "canais": canais, "medido": max((p["hora"] for v in por_chat.values() for p in v), default=None)}
