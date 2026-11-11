"""Ponte grátis entre o Telegram e o GitHub.

O Telegram manda cada clique/comando para cá. A ponte:
  1. confere a senha secreta do webhook e se quem clicou é o dono;
  2. responde ao clique na hora ("⏳ Recebi...");
  3. pede ao GitHub para rodar o bot.yml com o clique, que faz o trabalho de verdade.
Não guarda nada e não tem o token do robô.
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

# cliques e comandos que mexem na fila de livros (esperam o robô de postar terminar)
ESCREVE_BOTAO = ("p:", "d:", "m:pausa", "m:aprov", "m:agora")
ESCREVE_CMD = ("/pausar", "/continuar", "/agora", "/aprovacao")
# os que precisam do navegador (conferir a Amazon, desenhar a capa da notícia)
NAVEGADOR = ("p:", "m:agora", "r:np")


def chamar_github(update, grupo, navegador):
    corpo = json.dumps({"event_type": "telegram", "client_payload": {
        "update": update, "grupo": grupo, "navegador": navegador}}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['REPO']}/dispatches", data=corpo, method="POST",
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json", "User-Agent": "ponte-robo"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status < 300


class handler(BaseHTTPRequestHandler):
    def _responder(self, codigo, dados):
        corpo = json.dumps(dados).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        self._responder(200, {"ok": True, "ponte": "robo do Telegram"})

    def do_POST(self):
        if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != os.environ.get("WEBHOOK_SECRET"):
            return self._responder(401, {"ok": False})
        update = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        cb = update.get("callback_query")
        msg = update.get("message") or {}
        quem = (cb or msg).get("from", {}).get("id")

        def responder_clique(texto="", alerta=False):
            return self._responder(200, {"method": "answerCallbackQuery", "callback_query_id": cb["id"],
                                         "text": texto, "show_alert": alerta})

        if str(quem) != os.environ.get("ADMIN_ID"):
            return responder_clique("Só o dono do robô pode usar.", True) if cb else self._responder(200, {})
        dado = (cb or {}).get("data", "")
        texto = msg.get("text", "")
        if cb and dado == "x":
            return responder_clique()
        if not cb and not texto:
            return self._responder(200, {})
        cmd = texto.split()[0].split("@")[0].lower() if texto.strip() else ""
        grupo = "robo" if dado.startswith(ESCREVE_BOTAO) or cmd in ESCREVE_CMD else "solto"
        navegador = dado.startswith(NAVEGADOR) or cmd == "/agora"
        try:
            ok = chamar_github(update, grupo, navegador)
        except Exception:
            ok = False
        if cb:
            return responder_clique("⏳ Recebi! Fica pronto em menos de 1 minuto." if ok
                                    else "⚠️ A nuvem não respondeu. Tente de novo.", not ok)
        chat = msg["chat"]["id"]
        if ok:
            return self._responder(200, {"method": "sendChatAction", "chat_id": chat, "action": "typing"})
        return self._responder(200, {"method": "sendMessage", "chat_id": chat,
                                     "text": "⚠️ A nuvem não respondeu. Tente de novo."})
