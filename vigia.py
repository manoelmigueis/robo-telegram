"""Vigia: o relógio de verdade do robô.

O GitHub atrasa (e às vezes pula) as rodadas marcadas com "schedule": o relógio de 10 minutos
chegou a rodar só de 4 em 4 horas. O vigia fica ligado quase 6 horas seguidas e dispara cada
tarefa na hora certa (workflow_dispatch não sofre esse atraso). No fim, chama o próximo vigia.
Não mexe no banco: só aperta o "Run workflow" de cada tarefa. Só usa a biblioteca padrão.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
DURACAO = int(os.environ.get("VIGIA_SEGUNDOS", 5 * 3600 + 45 * 60))
ROBO = ("postar.yml", "relogio.yml", "garimpar.yml", "bot.yml")  # quem divide a vez no grupo "robo"

# (arquivo, minuto UTC, horas UTC). Todos no minuto 0: o post sai em menos de 1 minuto depois da hora. Postar: 08h–22h de Manaus; inglês, renda e cursos: como no cron deles.
TAREFAS = [
    ("postar.yml", 0, set(range(12, 24)) | {0, 1, 2}),
    ("ingles.yml", 0, set(range(13, 24)) | {0, 1}),
    ("renda.yml", 0, set(range(12, 24)) | {0, 1}),
    ("cursos.yml", 0, set(range(13, 24)) | {0, 1}),
    ("garimpar.yml", 30, {11}),
]


def api(metodo, caminho, corpo=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{caminho}", method=metodo,
        data=json.dumps(corpo).encode() if corpo is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read()
        return json.loads(txt) if txt else {}


def disparar(arquivo, entradas=None):
    api("POST", f"/actions/workflows/{arquivo}/dispatches", {"ref": "main", "inputs": entradas or {}})
    print(f"{datetime.now(timezone.utc):%H:%M} disparei {arquivo}", flush=True)


def rodadas(arquivo, n=10):
    return api("GET", f"/actions/workflows/{arquivo}/runs?per_page={n}").get("workflow_runs", [])


def esperando_na_fila_robo():
    """Tem alguém do grupo "robo" esperando a vez? Disparar outro agora cancelaria esse."""
    for st in ("queued", "pending", "waiting"):
        for r in api("GET", f"/actions/runs?status={st}&per_page=30").get("workflow_runs", []):
            if os.path.basename(r.get("path", "")) in ROBO:
                return True
    return False


def ja_rodou_desde(arquivo, desde):
    """Alguma rodada (de qualquer origem) criada depois de `desde` que não foi cancelada."""
    for r in rodadas(arquivo):
        criada = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        if criada >= desde and r.get("conclusion") != "cancelled":
            return True
    return False


def main():
    inicio = time.time()
    feitos = {}          # "postar.yml 2026-11-15 13" -> hora do disparo
    ultimo_relogio = -600.0
    while time.time() - inicio < DURACAO:
        agora = datetime.now(timezone.utc)
        mexeu_robo = False
        try:
            for arquivo, minuto, horas in TAREFAS:
                if agora.hour not in horas or agora.minute < minuto or agora.minute > 55:
                    continue
                chave = f"{arquivo} {agora:%Y-%m-%d %H}"
                hora_cheia = agora.replace(minute=minuto, second=0, microsecond=0)
                disparo = feitos.get(chave)
                if disparo is None:
                    if ja_rodou_desde(arquivo, hora_cheia):  # o cron já fez nesta hora
                        feitos[chave] = agora
                        continue
                    disparar(arquivo, {"vigia": "1"})
                    feitos[chave] = agora
                    mexeu_robo |= arquivo in ROBO
                elif agora - disparo > timedelta(minutes=2) and not ja_rodou_desde(arquivo, disparo - timedelta(seconds=30)):
                    # a rodada foi cancelada (um clique tomou a vez dela na fila): tenta de novo
                    disparar(arquivo, {"vigia": "1"})
                    feitos[chave] = agora
                    mexeu_robo |= arquivo in ROBO
            if not mexeu_robo and time.time() - ultimo_relogio >= 600 and not esperando_na_fila_robo():
                disparar("relogio.yml")
                ultimo_relogio = time.time()
        except Exception as erro:  # sem internet ou limite da API: tenta no próximo minuto
            print(f"[vigia] {erro}", flush=True)
        time.sleep(60 - datetime.now(timezone.utc).second + 2)
    try:
        disparar("vigia.yml")  # passa o turno para o próximo vigia
    except Exception as erro:
        print(f"[vigia] não consegui chamar o próximo: {erro}")


if __name__ == "__main__":
    main()
