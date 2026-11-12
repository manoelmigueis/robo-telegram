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


RESPOSTAS = "respostas-painel"
RAMO = "painel-respostas"


def gh(metodo, caminho, **kw):
    return requests.request(metodo, f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{caminho}",
                            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                     "Accept": "application/vnd.github+json"}, timeout=30, **kw)


def ler_pedido(rid):
    import base64
    r = gh("GET", f"/contents/pedidos/{rid}.json?ref={RAMO}")
    if r.status_code != 200:
        return None, None
    return json.loads(base64.b64decode(r.json()["content"])), r.json()["sha"]


def tratar_painel(carga):
    """Botões do painel da nuvem (Vercel): atende o pedido desta chamada e, na fila do robô,
    também os que ficaram para trás (o GitHub descarta chamadas repetidas). Roda o painel.py de verdade."""
    import subprocess
    import painel
    from fastapi.testclient import TestClient
    ids = [carga["painel"]["id"]]
    if carga.get("grupo") == "robo":
        r = gh("GET", f"/contents/pedidos?ref={RAMO}")
        if r.status_code == 200:
            ids += [a["name"][:-5] for a in r.json() if a["name"].endswith(".json")]
    cliente = TestClient(painel.app, raise_server_exceptions=False)
    cab = {"x-painel": painel.TOKEN}
    os.makedirs(RESPOSTAS, exist_ok=True)
    navegador = bool(carga.get("navegador"))
    feitos = set()
    for rid in ids:
        if rid in feitos:
            continue
        feitos.add(rid)
        dados, _ = ler_pedido(rid)
        if not dados or (rid != ids[0] and dados.get("grupo") != "robo"):
            continue
        pedido = dados["pedido"]
        if dados.get("navegador") and not navegador:
            subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=False)
            navegador = True
        print("painel:", pedido["caminho"])
        if pedido.get("form") is not None:
            r = cliente.post(pedido["caminho"], data=pedido["form"], headers=cab)
        else:
            r = cliente.post(pedido["caminho"], json=pedido.get("corpo") or {}, headers=cab)
        try:
            corpo = r.json()
        except ValueError:
            corpo = {"erro": "A nuvem deu erro. Tente de novo."}
        with open(os.path.join(RESPOSTAS, f"{rid}.json"), "w", encoding="utf-8") as f:
            json.dump({"pronto": True, "status": r.status_code, "json": corpo}, f, ensure_ascii=False)


def responder_painel(rid):
    """Põe as respostas no ramo painel-respostas (a página busca lá) e tira os pedidos da fila."""
    import base64
    import time
    prontos = {n[:-5] for n in os.listdir(RESPOSTAS)} if os.path.isdir(RESPOSTAS) else set()
    if rid not in prontos and ler_pedido(rid)[0]:  # deu erro antes de responder
        os.makedirs(RESPOSTAS, exist_ok=True)
        with open(os.path.join(RESPOSTAS, f"{rid}.json"), "w", encoding="utf-8") as f:
            json.dump({"pronto": True, "status": 500, "json": {"erro": "A nuvem deu erro. Tente de novo."}}, f)
        prontos.add(rid)
    for p in prontos:
        with open(os.path.join(RESPOSTAS, f"{p}.json"), encoding="utf-8") as f:
            conteudo = base64.b64encode(f.read().encode()).decode()
        for tentativa in range(4):
            r = gh("PUT", f"/contents/respostas/{p}.json",
                   json={"message": "painel: resposta", "branch": RAMO, "content": conteudo})
            if r.status_code < 300 or r.status_code == 422:
                break
            time.sleep(1 + tentativa)
        for tentativa in range(4):
            _, sha = ler_pedido(p)
            if not sha:
                break
            r = gh("DELETE", f"/contents/pedidos/{p}.json",
                   json={"message": "painel: pedido atendido", "branch": RAMO, "sha": sha})
            if r.status_code < 300:
                break
            time.sleep(1 + tentativa)
        print("resposta do painel:", p, r.status_code)


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        carga = json.load(f)["client_payload"]
    if len(sys.argv) > 1 and sys.argv[1] == "responder":
        return responder_painel(carga["painel"]["id"])
    if carga.get("painel"):
        return tratar_painel(carga)
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
