"""Trata, na nuvem (GitHub Actions), um clique ou comando do Telegram.

Caminho: Telegram -> ponte na Vercel (responde na hora e chama o GitHub) -> este arquivo.
Assim os botões funcionam 24 horas, com o PC desligado.
"""
import json
import os
import sys

import requests

import postador


def disparar_garimpo():
    """Na nuvem o garimpo roda no seu próprio robô (garimpar.yml), não aqui dentro."""
    r = requests.post(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/garimpar.yml/dispatches",
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"},
        json={"ref": "main"}, timeout=30)
    if r.status_code >= 300:
        postador.avisar(f"⚠️ Não consegui começar o garimpo ({r.status_code}).")
    else:
        postador.avisar("⛏ Garimpo começou na nuvem (uns 20 minutos).")
    return True


RESPOSTA = "resposta-painel.json"


def tratar_painel(pedido):
    """Um botão do painel da nuvem (Vercel): roda o painel.py de verdade e guarda a resposta."""
    import painel
    from fastapi.testclient import TestClient
    cliente = TestClient(painel.app, raise_server_exceptions=False)
    cab = {"x-painel": painel.TOKEN}
    if pedido.get("form") is not None:
        r = cliente.post(pedido["caminho"], data=pedido["form"], headers=cab)
    else:
        r = cliente.post(pedido["caminho"], json=pedido.get("corpo") or {}, headers=cab)
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"erro": "A nuvem deu erro. Tente de novo."}
    with open(RESPOSTA, "w", encoding="utf-8") as f:
        json.dump({"pronto": True, "status": r.status_code, "json": corpo}, f, ensure_ascii=False)


def responder_painel(rid):
    """Põe a resposta no ramo painel-respostas, onde a página do painel vai buscar."""
    import base64
    try:
        with open(RESPOSTA, encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        texto = json.dumps({"pronto": True, "status": 500, "json": {"erro": "A nuvem deu erro. Tente de novo."}})
    r = requests.put(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/contents/respostas/{rid}.json",
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"},
        json={"message": "painel: resposta", "branch": "painel-respostas",
              "content": base64.b64encode(texto.encode()).decode()}, timeout=30)
    print("resposta do painel:", r.status_code)


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        carga = json.load(f)["client_payload"]
    if len(sys.argv) > 1 and sys.argv[1] == "responder":
        return responder_painel(carga["painel"]["id"])
    if carga.get("painel"):
        return tratar_painel(carga["painel"])
    upd = carga["update"]
    # aqui o programa termina depois do clique, então tudo roda até o fim, sem segundo plano
    postador.em_paralelo = lambda funcao, *args: funcao(*args)
    postador.garimpar_em_segundo_plano = disparar_garimpo
    if upd.get("callback_query"):
        postador.tratar_botao(upd["callback_query"])
    elif (upd.get("message") or {}).get("text"):
        m = upd["message"]
        postador.tratar_comando(m["text"], m["chat"]["id"])


if __name__ == "__main__":
    main()
