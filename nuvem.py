"""Robô na nuvem (GitHub Actions): roda uma vez e termina.

    python nuvem.py postar    -> chamado toda hora cheia: agendados, validade e o post do horário
    python nuvem.py garimpar  -> chamado de manhã: garimpa livros e ofertas e enche a fila

O token vem dos "secrets" do GitHub (TELEGRAM_BOT_TOKEN). No PC, vem do .env.
Sem botões de aprovação aqui: na nuvem o robô posta direto.
"""
import os
import sys

import banco
import postador


def garimpar():
    import garimpeiro
    postador.mudar_estado(ultimo_garimpo=banco.agora()[:10])
    garimpeiro.garimpar_tudo()
    n = postador.encher_fila()
    print(f"{n} posts novos na fila.")
    postador.avisar(f"☁️ Garimpo da nuvem: {n} posts novos na fila.")
    return n


def canais_desta_hora(hora_cheia):
    """Canais no automático com algum horário nesta hora (08:00, 08:30... valem para as 08)."""
    hh = hora_cheia[:2]
    return [c for c in banco.canais(so_auto=True)
            if any(h[:2] == hh for h in postador.horarios_do_canal(c))]


def relogio(so_manuais=True):
    """Entradas e saídas com hora marcada. Roda a cada 10 minutos (relogio.yml) e também na hora cheia.
    so_manuais: na rodada de 10 minutos não há navegador, então livros e ofertas ficam para a hora cheia."""
    try:
        import metricas
        metricas.coletar()
    except Exception as x:
        print(f"[métricas] {x}")
    agora_txt = banco.agora()
    if not postador.pausa_total():
        for p in banco.posts(status=("agendado",)):
            if p["agendado_para"] and p["agendado_para"] <= agora_txt and (p["origem"] == "manual" or not so_manuais):
                postador.publicar(p["id"], conferir=p["origem"] != "manual")
    # saída do ar: vale mesmo com tudo pausado
    for e in banco.envios_vencidos():
        try:
            postador.apagar(e["chat"], e["message_id"])
            banco.marcar_envio(e["id"], "apagado")
        except Exception as erro:
            print(f"[apagar] {erro}")
            banco.marcar_envio(e["id"], "apagado" if "not found" in str(erro) else "erro_apagar")


def postar():
    agora_txt = banco.agora()
    hoje, hh = agora_txt[:10], agora_txt[11:13]
    marca = f"{hoje} {hh}:00"

    relogio(so_manuais=False)
    if postador.pausa_total():
        print("Tudo pausado (pausa geral).")
        return

    # "Run workflow" apertado na mão = postar agora, mesmo pausado ou com a hora já postada
    na_mao = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    st = postador.estado()
    if st.get("pausado") and not na_mao:
        print("Automático pausado.")
        return
    if st.get("ultimo_horario", "")[:13] == marca[:13] and not na_mao:
        print("Esta hora já foi postada.")
        return
    na_hora = canais_desta_hora(f"{hh}:00")
    if na_mao and not na_hora:
        na_hora = list(banco.canais(so_auto=True))
    if not na_hora:
        print(f"Nenhum canal tem horário às {hh}h.")
        return

    postador.mudar_estado(ultimo_horario=marca)
    p = postador.proximo_automatico(na_hora)
    if not p:  # fila vazia: garimpa agora mesmo e tenta de novo
        print("Fila vazia, garimpando...")
        garimpar()
    for _ in range(20):  # se um item não vale mais (ou saiu do grátis), tenta o próximo
        p = postador.proximo_automatico(na_hora)
        if not p:
            postador.avisar("☁️ A fila acabou e o garimpo não achou nada novo.")
            return
        if postador.publicar(p["id"], so_canais=na_hora, so_gratis=postador.gratis_nos_dados(p)):
            postador.mudar_estado(ultimo_tipo=p["origem"])
            print(f"Postado: {p.get('titulo') or p['id']}")
            return


if __name__ == "__main__":
    postador.carregar_env()
    acao = sys.argv[1] if len(sys.argv) > 1 else "postar"
    {"postar": postar, "garimpar": garimpar, "relogio": relogio}[acao]()
