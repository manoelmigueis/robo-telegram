"""Contador de cliques. Os links dos posts do robô passam por aqui:
  /r/<post>.<canal>?u=<link de verdade>
A ponte anota o clique na fila de métricas (ramo "painel-respostas") e manda a pessoa
na hora para o link de verdade (a Amazon, com a sua tag). Só aceita links de sites conhecidos,
para ninguém usar o seu endereço para mandar gente a outro lugar.
Visitas de robôs (a prévia do Telegram, buscadores) não contam como clique.
"""
import base64
import json
import os
import re
import time
import uuid
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SITES = ("amazon.com.br", "amazon.com", "amzn.to", "amzn.com", "a.co", "link.amazon",
         "t.me", "telegram.me", "kiwify.com.br", "kiwify.app", "hotmart.com", "udemy.com")
ROBOS = re.compile(r"bot|crawl|spider|preview|facebookexternalhit|whatsapp|slurp", re.I)


def site_ok(url):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and any(host == s or host.endswith("." + s) for s in SITES)


def anotar(nome):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['REPO']}/contents/metricas/{nome}.json",
        data=json.dumps({"message": "clique", "branch": "painel-respostas",
                         "content": base64.b64encode(b"{}").decode()}).encode(), method="PUT",
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json", "User-Agent": "ponte-robo"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status < 300


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        partes = urlparse(self.path)
        destino = (parse_qs(partes.query).get("u") or [""])[0]
        if not site_ok(destino):
            corpo = "Link inválido.".encode()
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return
        m = re.search(r"/r/(\d+)\.(\d+)", partes.path)
        if m and not ROBOS.search(self.headers.get("User-Agent") or ""):
            try:
                anotar(f"c~{m.group(1)}~{m.group(2)}~{int(time.time())}~{uuid.uuid4().hex[:8]}")
            except Exception as erro:
                print(f"[clique] não anotei: {erro}")
        self.send_response(302)
        self.send_header("Location", destino)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
