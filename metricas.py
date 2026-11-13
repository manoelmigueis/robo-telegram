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


def links_rastreados(p, canal, legenda, botoes):
    """Legenda e botões de um post com os links passando pela ponte."""
    import re
    pid, cid = p.get("id"), canal.get("id")
    legenda = re.sub(r'href="([^"]+)"', lambda m: f'href="{rastrear(m.group(1).replace("&amp;", "&"), pid, cid)}"'
                     .replace("&", "&amp;") if site_ok(m.group(1).replace("&amp;", "&")) else m.group(0), legenda or "")
    novos = [[{**b, "url": rastrear(b["url"], pid, cid)} if b.get("url") else b for b in linha] for linha in (botoes or [])]
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
    cliques = reacoes = 0
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
def resumo(dias=7):
    from datetime import timedelta
    desde = (datetime.now(banco.FUSO) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M")
    cliques = banco.sql("SELECT COUNT(*) n FROM eventos WHERE tipo='clique' AND quando>=?", (desde,), um=True)["n"]
    reacoes = banco.sql("SELECT COALESCE(SUM(reacoes),0) n FROM envios WHERE enviado_em>=?", (desde,), um=True)["n"]
    por_canal = banco.sql(
        "SELECT c.nome, c.chat, COUNT(e.nome) cliques FROM eventos e JOIN canais c ON c.id=e.canal_id "
        "WHERE e.tipo='clique' AND e.quando>=? GROUP BY c.id ORDER BY cliques DESC", (desde,))
    melhores = banco.sql(
        "SELECT p.id, p.titulo, p.origem, SUM(COALESCE(v.cliques,0)) cliques, SUM(COALESCE(v.reacoes,0)) reacoes "
        "FROM envios v JOIN posts p ON p.id=v.post_id WHERE v.enviado_em>=? "
        "GROUP BY p.id HAVING cliques>0 OR reacoes>0 ORDER BY cliques DESC, reacoes DESC LIMIT 5", (desde,))
    return {"dias": dias, "cliques": cliques, "reacoes": reacoes, "por_canal": por_canal, "melhores": melhores}
