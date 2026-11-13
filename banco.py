"""Banco de dados (SQLite) do robô: canais, posts e envios.

- canais: onde o robô posta. "auto" = recebe os posts automáticos (livros e ofertas).
- posts: tudo que vai ou foi para o ar.
    origem: manual | livro | oferta
    status: fila (automático, sai num horário) | agendado (data marcada) |
            postado | cancelado | expirou (Amazon tirou a promo) | erro
- envios: cada vez que um post foi para um canal (guarda o message_id
  para poder apagar depois, à mão ou quando expirar).
"""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PASTA = Path(__file__).parent
CONFIG = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
FUSO = ZoneInfo(CONFIG.get("fuso", "America/Manaus"))
ARQUIVO = PASTA / "robo.db"
_trava = threading.RLock()
_con = sqlite3.connect(ARQUIVO, check_same_thread=False)
_con.row_factory = sqlite3.Row

_con.executescript("""
CREATE TABLE IF NOT EXISTS canais (
    id INTEGER PRIMARY KEY, chat TEXT UNIQUE, nome TEXT,
    auto INTEGER DEFAULT 1, ativo INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY, origem TEXT, asin TEXT, titulo TEXT,
    legenda TEXT, foto TEXT, botoes TEXT, canais TEXT,
    agendado_para TEXT, expira_horas REAL, status TEXT,
    dados TEXT, criado TEXT, erro TEXT);
CREATE TABLE IF NOT EXISTS envios (
    id INTEGER PRIMARY KEY, post_id INTEGER, canal_id INTEGER, chat TEXT,
    message_id INTEGER, enviado_em TEXT, expira_em TEXT, apagado_em TEXT,
    status TEXT DEFAULT 'no_ar');
""")
# Colunas novas dos canais (nicho, o que recebe no automático, @ da assinatura e tags próprias)
_tem = {l[1] for l in _con.execute("PRAGMA table_info(canais)")}
for _col, _tipo in (("nicho", "TEXT DEFAULT ''"), ("recebe", "TEXT DEFAULT 'livro,oferta'"),
                    ("assinatura", "TEXT DEFAULT ''"), ("tags", "TEXT DEFAULT ''"),
                    ("horarios", "TEXT DEFAULT ''")):
    if _col not in _tem:
        _con.execute(f"ALTER TABLE canais ADD COLUMN {_col} {_tipo}")
if "fixado" not in {l[1] for l in _con.execute("PRAGMA table_info(envios)")}:
    _con.execute("ALTER TABLE envios ADD COLUMN fixado INTEGER DEFAULT 0")
_con.commit()


def agora():
    """Hora local como texto 'AAAA-MM-DD HH:MM' (dá para comparar como texto)."""
    return datetime.now(FUSO).strftime("%Y-%m-%d %H:%M")


def sql(comando, args=(), um=False):
    with _trava:
        cur = _con.execute(comando, args)
        _con.commit()
        if comando.lstrip().upper().startswith(("INSERT",)):
            return cur.lastrowid
        linhas = [dict(l) for l in cur.fetchall()]
        return (linhas[0] if linhas else None) if um else linhas


# ---------- canais ----------
def canais(so_auto=False):
    filtro = "WHERE ativo=1" + (" AND auto=1" if so_auto else "")
    return sql(f"SELECT * FROM canais {filtro} ORDER BY id")


def novo_canal(chat, nome, auto=True):
    # canal novo não recebe livros nem ofertas da Amazon até você marcar isso na ficha dele
    sql("INSERT OR IGNORE INTO canais (chat, nome, auto, recebe) VALUES (?,?,?,?)",
        (chat, nome, int(auto), "livro,oferta" if auto else ""))
    sql("UPDATE canais SET ativo=1 WHERE chat=?", (chat,))  # se tinha sido tirado, volta


CAMPOS_CANAL = ("nome", "auto", "nicho", "recebe", "assinatura", "tags", "horarios")


def mudar_canal(cid, **campos):
    campos = {k: v for k, v in campos.items() if k in CAMPOS_CANAL}
    if campos:
        sets = ", ".join(f"{k}=?" for k in campos)
        sql(f"UPDATE canais SET {sets} WHERE id=?", (*campos.values(), cid))


# ---------- posts ----------
def novo_post(origem, legenda, foto="", botoes=None, canais_ids=None, agendado_para=None,
              expira_horas=None, status="fila", asin="", titulo="", dados=None):
    return sql(
        "INSERT INTO posts (origem, asin, titulo, legenda, foto, botoes, canais, agendado_para,"
        " expira_horas, status, dados, criado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (origem, asin, titulo, legenda, foto, json.dumps(botoes or []),
         json.dumps(canais_ids) if canais_ids else None, agendado_para, expira_horas,
         status, json.dumps(dados or {}, ensure_ascii=False), agora()))


def post(pid):
    p = sql("SELECT * FROM posts WHERE id=?", (pid,), um=True)
    return _abrir(p) if p else None


def _abrir(p):
    p["botoes"] = json.loads(p["botoes"] or "[]")
    p["canais"] = json.loads(p["canais"]) if p["canais"] else None
    p["dados"] = json.loads(p["dados"] or "{}")
    return p


def posts(status=None, limite=200):
    if status:
        marcas = ",".join("?" * len(status))
        linhas = sql(f"SELECT * FROM posts WHERE status IN ({marcas}) ORDER BY "
                     "COALESCE(agendado_para, criado), id LIMIT ?", (*status, limite))
    else:
        linhas = sql("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limite,))
    return [_abrir(p) for p in linhas]


def mudar_post(pid, **campos):
    for chave in ("botoes", "canais", "dados"):
        if chave in campos and not isinstance(campos[chave], str) and campos[chave] is not None:
            campos[chave] = json.dumps(campos[chave], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in campos)
    sql(f"UPDATE posts SET {sets} WHERE id=?", (*campos.values(), pid))


def asins_conhecidos():
    return {l["asin"] for l in sql("SELECT asin FROM posts WHERE asin != ''")}


# ---------- envios ----------
def novo_envio(post_id, canal, message_id, expira_em):
    return sql("INSERT INTO envios (post_id, canal_id, chat, message_id, enviado_em, expira_em)"
               " VALUES (?,?,?,?,?,?)", (post_id, canal["id"], canal["chat"], message_id, agora(), expira_em))


def envios_no_ar(limite=300):
    return sql("SELECT e.*, p.titulo, p.origem, c.nome AS canal_nome FROM envios e "
               "LEFT JOIN posts p ON p.id=e.post_id LEFT JOIN canais c ON c.id=e.canal_id "
               "WHERE e.status='no_ar' ORDER BY e.id DESC LIMIT ?", (limite,))


def envios_do_post(pid):
    """Envios de um post que ainda estão no ar (um por canal)."""
    return sql("SELECT e.*, c.nome AS canal_nome FROM envios e LEFT JOIN canais c ON c.id=e.canal_id "
               "WHERE e.post_id=? AND e.status='no_ar' ORDER BY e.id", (pid,))


def envios_vencidos():
    return sql("SELECT * FROM envios WHERE status='no_ar' AND expira_em IS NOT NULL AND expira_em <= ?",
               (agora(),))


def marcar_envio(eid, status):
    sql("UPDATE envios SET status=?, apagado_em=? WHERE id=?", (status, agora(), eid))
