"""Baixa a foto de perfil de cada canal do Telegram para fotos/ (o painel mostra nos cards).

Roda no GitHub Actions (workflow fotos.yml), uma vez por dia. Gera fotos/index.json: {chat: arquivo}.
"""
import hashlib
import json
from pathlib import Path

import requests

import banco
import central
import postador

PASTA = Path(__file__).parent / "fotos"


def chats():
    lista = [c["chat"] for c in banco.canais() if c.get("chat")]
    for n in ("ingles", "renda", "prompts"):
        try:
            lista.append(central.modulo(n).canal())
        except Exception as erro:
            print(f"[fotos] {n}: sem canal ({erro})")
    return list(dict.fromkeys(str(c) for c in lista if c))


def baixar(chat):
    info = postador.tg("getChat", chat_id=chat)
    foto = info.get("photo")
    if not foto:
        return None, info
    arq = postador.tg("getFile", file_id=foto["small_file_id"])
    token = postador.os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.get(f"https://api.telegram.org/file/bot{token}/{arq['file_path']}", timeout=60)
    r.raise_for_status()
    nome = hashlib.sha1(str(info["id"]).encode()).hexdigest()[:12] + ".jpg"
    (PASTA / nome).write_bytes(r.content)
    return nome, info


def main():
    postador.carregar_env()
    PASTA.mkdir(exist_ok=True)
    indice, bios = {}, {}
    for chat in chats():
        try:
            nome, info = baixar(chat)
        except Exception as erro:
            print(f"[fotos] {chat}: {erro}")
            continue
        chaves = {chat, str(info["id"]), "@" + info["username"] if info.get("username") else chat}
        for chave in chaves:
            bios[chave] = {"titulo": info.get("title") or "", "bio": info.get("description") or "",
                           "usuario": info.get("username") or ""}
            if nome:
                indice[chave] = nome
        print(f"[fotos] {chat}: {nome or 'canal sem foto'}")
    (PASTA / "index.json").write_text(json.dumps(indice, ensure_ascii=False, indent=2), encoding="utf-8")
    (PASTA / "info.json").write_text(json.dumps(bios, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
