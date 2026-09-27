"""Roda UM ciclo do bot. O GitHub Actions chama este arquivo a cada ~10 minutos.

Em cada ciclo:
  1. Lê as mensagens que chegaram no Telegram desde o último ciclo e responde
     (cadastro de links, /lista, /remover, ...).
  2. Verifica o preço dos produtos que não são checados há ~1 hora.
  3. Avisa no Telegram se algum preço mudou.
  4. Salva o estado (o workflow faz commit dele no repositório).

Obs.: os logs do Actions são públicos em repositório público, por isso o
script só imprime contagens, nunca links ou nomes de produtos.
"""

from __future__ import annotations

import html
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import estado as est
import lojas
from telegram_api import Telegram

INTERVALO_MIN = int(os.environ.get("INTERVALO_MINUTOS", "60"))
# tolerância: o agendamento do GitHub atrasa às vezes, então checa se passou "quase" 1 hora
FOLGA = timedelta(minutes=8)
AVISAR_APOS_FALHAS = 3
MAX_HISTORICO = 60
FUSO = ZoneInfo("America/Sao_Paulo")
RE_URL = re.compile(r"https?://[^\s<>\"]+", re.I)

AJUDA = (
    "<b>Como usar</b>\n"
    "• Mande o <b>link de um anúncio</b> (Mercado Livre, KaBuM, Pichau, Terabyte e outras) "
    "e eu respondo com o preço atual.\n"
    f"• Depois disso eu confiro o preço a cada {INTERVALO_MIN} min e te aviso quando mudar.\n\n"
    "/lista – produtos monitorados\n"
    "/verificar – checar todos agora\n"
    "/historico N – histórico de preço do produto N\n"
    "/remover N – parar de monitorar o produto N\n\n"
    "<i>O bot roda em ciclos, então as respostas podem levar alguns minutos para chegar.</i>"
)


# ------------------------------------------------------------------ utilidades

def agora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def brl(valor: float | None) -> str:
    if valor is None:
        return "—"
    s = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def quando(texto_iso: str | None) -> str:
    if not texto_iso:
        return "nunca"
    return datetime.fromisoformat(texto_iso).astimezone(FUSO).strftime("%d/%m %H:%M")


def esc(s) -> str:
    return html.escape(str(s or ""))


def titulo(p: dict, limite: int = 70) -> str:
    nome = p.get("nome") or "Produto sem nome"
    if len(nome) > limite:
        nome = nome[: limite - 1] + "…"
    return f'<a href="{esc(p["url"])}">{esc(nome)}</a>'


def achar(estado: dict, pid: int) -> dict | None:
    return next((p for p in estado["produtos"] if p["id"] == pid), None)


# ------------------------------------------------------------------ preços

def registrar_consulta(p: dict, res: lojas.Resultado) -> list[str]:
    """Atualiza o produto com o resultado e devolve as mensagens de aviso (se houver)."""
    avisos: list[str] = []
    p["verificado_em"] = iso(agora())
    if res.nome and not p.get("nome"):
        p["nome"] = res.nome

    if res.erro:
        p["falhas"] = p.get("falhas", 0) + 1
        p["ultimo_erro"] = res.erro
        if p["falhas"] == AVISAR_APOS_FALHAS:
            avisos.append(
                f"⚠️ Não consigo ler o preço de {titulo(p)} há {AVISAR_APOS_FALHAS} verificações "
                f"seguidas.\nMotivo: {esc(res.erro)}\nVou continuar tentando."
            )
        return avisos

    if p.get("falhas", 0) >= AVISAR_APOS_FALHAS:
        avisos.append(f"✅ Voltei a conseguir ler {titulo(p)}.")
    p["falhas"] = 0
    p.pop("ultimo_erro", None)

    antigo_disp = p.get("disponivel")
    if res.disponivel is not None:
        p["disponivel"] = res.disponivel
        if antigo_disp is True and res.disponivel is False:
            avisos.append(f"🚫 {titulo(p)} ficou <b>indisponível</b>.")
        elif antigo_disp is False and res.disponivel is True:
            avisos.append(f"📦 {titulo(p)} está <b>disponível de novo</b> por {brl(res.preco)}!")

    if res.preco is None:
        return avisos

    antigo = p.get("preco")
    p["preco"] = res.preco
    if p.get("menor_preco") is None or res.preco < p["menor_preco"]:
        p["menor_preco"] = res.preco
    if antigo is None or abs(res.preco - antigo) >= 0.01:
        p.setdefault("historico", []).append({"em": p["verificado_em"], "preco": res.preco})
        p["historico"] = p["historico"][-MAX_HISTORICO:]

    if antigo is not None and abs(res.preco - antigo) >= 0.01:
        dif = res.preco - antigo
        pct = dif / antigo * 100
        seta = "📉 Baixou" if dif < 0 else "📈 Subiu"
        linhas = [
            f"{seta} {abs(pct):.1f}%!".replace(".", ","),
            titulo(p),
            f"{brl(antigo)} → <b>{brl(res.preco)}</b> ({'-' if dif < 0 else '+'}{brl(abs(dif))})",
            f"Loja: {esc(p['loja'])}",
        ]
        if dif < 0 and res.preco <= p["menor_preco"]:
            linhas.append("🏆 Menor preço desde que comecei a monitorar!")
        else:
            linhas.append(f"Menor preço visto: {brl(p['menor_preco'])}")
        # só avisa se não for o mesmo aviso de "disponível de novo"
        if not (antigo_disp is False and res.disponivel is True):
            avisos.append("\n".join(linhas))
    return avisos


def verificar_produtos(estado: dict, tg: Telegram, forcar: bool = False) -> int:
    limite = agora() - timedelta(minutes=INTERVALO_MIN) + FOLGA
    pendentes = [
        p for p in estado["produtos"]
        if forcar or not p.get("verificado_em") or datetime.fromisoformat(p["verificado_em"]) <= limite
    ]
    for i, p in enumerate(pendentes):
        if i:
            time.sleep(random.uniform(2, 5))  # não martelar as lojas
        res = lojas.consultar(p["url"])
        for aviso in registrar_consulta(p, res):
            tg.enviar(estado["dono"], aviso)
    return len(pendentes)


# ------------------------------------------------------------------ comandos

def cmd_cadastrar(estado: dict, tg: Telegram, chat: int, urls: list[str]) -> None:
    for url in urls:
        url = url.rstrip(").,;")
        existente = next((p for p in estado["produtos"] if p["url"] == url), None)
        if existente:
            tg.enviar(chat, f"Esse já está na lista (nº {existente['id']}): {titulo(existente)} — "
                            f"{brl(existente.get('preco'))}")
            continue
        tg.digitando(chat)
        p = {
            "id": estado["proximo_id"],
            "url": url,
            "loja": lojas.nome_loja(url),
            "nome": None,
            "preco": None,
            "menor_preco": None,
            "disponivel": None,
            "adicionado_em": iso(agora()),
            "falhas": 0,
            "historico": [],
        }
        estado["proximo_id"] += 1
        estado["produtos"].append(p)
        res = lojas.consultar(url)
        registrar_consulta(p, res)

        if res.preco is not None:
            extra = "" if res.disponivel is not False else "\n🚫 Aparece como indisponível no momento."
            tg.enviar(chat,
                      f"✅ Cadastrado (nº {p['id']})\n{titulo(p, 120)}\n"
                      f"Loja: {esc(p['loja'])}\nPreço atual: <b>{brl(res.preco)}</b>{extra}\n\n"
                      f"Vou conferir a cada {INTERVALO_MIN} min e te aviso se mudar.")
        elif res.disponivel is False:
            tg.enviar(chat,
                      f"✅ Cadastrado (nº {p['id']})\n{titulo(p, 120)}\n"
                      f"🚫 O produto está <b>indisponível</b> agora. Te aviso quando voltar.")
        else:
            tg.enviar(chat,
                      f"⚠️ Cadastrei (nº {p['id']}), mas não consegui ler o preço agora.\n"
                      f"Motivo: {esc(res.erro)}\n"
                      "Vou tentar de novo nas próximas verificações. Se continuar falhando, "
                      "essa loja provavelmente bloqueia robôs — use /remover "
                      f"{p['id']} se quiser tirar da lista.")


def cmd_lista(estado: dict, tg: Telegram, chat: int) -> None:
    if not estado["produtos"]:
        tg.enviar(chat, "Nenhum produto cadastrado ainda. Mande o link de um anúncio!")
        return
    blocos = []
    for p in estado["produtos"]:
        status = ""
        if p.get("falhas", 0) >= AVISAR_APOS_FALHAS:
            status = " ⚠️ sem leitura"
        elif p.get("disponivel") is False:
            status = " 🚫 indisponível"
        blocos.append(
            f"<b>{p['id']}.</b> {titulo(p, 60)}\n"
            f"    {brl(p.get('preco'))}{status} · menor: {brl(p.get('menor_preco'))} · "
            f"{esc(p['loja'])} · {quando(p.get('verificado_em'))}"
        )
    # Telegram limita mensagens a 4096 caracteres
    msg = ""
    for b in blocos:
        if len(msg) + len(b) > 3800:
            tg.enviar(chat, msg)
            msg = ""
        msg += b + "\n\n"
    tg.enviar(chat, msg.strip())


def cmd_remover(estado: dict, tg: Telegram, chat: int, args: str) -> None:
    ids = [int(x) for x in re.findall(r"\d+", args)]
    if not ids:
        tg.enviar(chat, "Diga o número do produto, ex: <code>/remover 3</code> (veja em /lista).")
        return
    removidos = []
    for pid in ids:
        p = achar(estado, pid)
        if p:
            estado["produtos"].remove(p)
            removidos.append(f"{pid}. {esc(p.get('nome') or p['url'])}")
    if removidos:
        tg.enviar(chat, "🗑️ Removido:\n" + "\n".join(removidos))
    else:
        tg.enviar(chat, "Não achei esse número na /lista.")


def cmd_historico(estado: dict, tg: Telegram, chat: int, args: str) -> None:
    m = re.search(r"\d+", args)
    p = achar(estado, int(m.group())) if m else None
    if not p:
        tg.enviar(chat, "Diga o número do produto, ex: <code>/historico 3</code> (veja em /lista).")
        return
    linhas = [f"📊 {titulo(p, 90)}", ""]
    for h in p.get("historico", [])[-25:]:
        linhas.append(f"{quando(h['em'])} — {brl(h['preco'])}")
    if len(linhas) == 2:
        linhas.append("Ainda sem preços registrados.")
    linhas += ["", f"Menor: {brl(p.get('menor_preco'))} · Atual: {brl(p.get('preco'))}"]
    tg.enviar(chat, "\n".join(linhas))


def processar_mensagem(estado: dict, tg: Telegram, msg: dict) -> bool:
    """Trata uma mensagem. Devolve True se pediu /verificar."""
    chat = msg.get("chat", {}).get("id")
    texto = (msg.get("text") or msg.get("caption") or "").strip()
    if not chat or not texto:
        return False

    comando, _, args = texto.partition(" ")
    comando = comando.split("@")[0].lower()

    if estado["dono"] is None:
        if comando == "/start":
            estado["dono"] = chat
            tg.definir_comandos()
            tg.enviar(chat, "👋 Pronto! Este bot agora é seu (só você consegue usar).\n\n" + AJUDA)
        else:
            tg.enviar(chat, "Mande /start para ativar o bot.")
        return False

    if chat != estado["dono"]:
        return False  # bot privado: ignora outras pessoas

    urls = RE_URL.findall(texto)
    if comando in ("/start", "/ajuda", "/help"):
        tg.enviar(chat, AJUDA)
    elif comando == "/lista":
        cmd_lista(estado, tg, chat)
    elif comando in ("/remover", "/apagar"):
        cmd_remover(estado, tg, chat, args)
    elif comando == "/historico":
        cmd_historico(estado, tg, chat, args)
    elif comando == "/verificar":
        tg.enviar(chat, f"🔎 Verificando {len(estado['produtos'])} produto(s)... "
                        "só te mando mensagem se algo mudar.")
        return True
    elif urls:
        cmd_cadastrar(estado, tg, chat, urls)
    else:
        tg.enviar(chat, "Não entendi. Mande o link de um anúncio ou /ajuda.")
    return False


# ------------------------------------------------------------------ ciclo

def ciclo(tg: Telegram | None = None) -> None:
    tg = tg or Telegram()
    estado = est.carregar()
    forcar = False
    try:
        atualizacoes = tg.mensagens_novas(estado["offset"])
        for upd in atualizacoes:
            estado["offset"] = upd["update_id"] + 1
            try:
                if processar_mensagem(estado, tg, upd.get("message") or {}):
                    forcar = True
            except Exception as e:
                print(f"erro ao tratar mensagem: {type(e).__name__}: {e}")
        print(f"mensagens processadas: {len(atualizacoes)}")

        if estado["dono"] is not None:
            n = verificar_produtos(estado, tg, forcar=forcar)
            if forcar:
                tg.enviar(estado["dono"], f"✔️ Verificação concluída ({n} produto(s)).")
            print(f"produtos verificados: {n} de {len(estado['produtos'])}")
    finally:
        est.salvar(estado)  # salva mesmo se algo quebrar no meio


if __name__ == "__main__":
    ciclo()
