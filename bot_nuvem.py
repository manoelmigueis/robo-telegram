"""Trata, na nuvem (GitHub Actions), um clique ou comando do Telegram.

Caminho: Telegram -> ponte na Vercel (responde na hora e chama o GitHub) -> este arquivo.
Assim os botões funcionam 24 horas, com o PC desligado.
"""
import json
import os

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


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        upd = json.load(f)["client_payload"]["update"]
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
