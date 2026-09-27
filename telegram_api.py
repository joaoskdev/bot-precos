"""Cliente mínimo da API de bots do Telegram (só o que o bot usa)."""

from __future__ import annotations

import os

from curl_cffi import requests


class Telegram:
    def __init__(self, token: str | None = None):
        self.base = f"https://api.telegram.org/bot{token or os.environ['TELEGRAM_TOKEN']}/"

    def chamar(self, metodo: str, **params):
        r = requests.post(self.base + metodo, json=params, timeout=40)
        dados = r.json()
        if not dados.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {dados.get('description')}")
        return dados["result"]

    def mensagens_novas(self, offset: int) -> list[dict]:
        return self.chamar("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])

    def enviar(self, chat_id: int, texto: str) -> None:
        self.chamar("sendMessage", chat_id=chat_id, text=texto, parse_mode="HTML",
                    disable_web_page_preview=True)

    def digitando(self, chat_id: int) -> None:
        try:
            self.chamar("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass

    def definir_comandos(self) -> None:
        self.chamar("setMyCommands", commands=[
            {"command": "lista", "description": "Produtos monitorados e preços atuais"},
            {"command": "verificar", "description": "Checar todos os preços agora"},
            {"command": "historico", "description": "Histórico de preço de um produto (ex: /historico 3)"},
            {"command": "remover", "description": "Parar de monitorar (ex: /remover 3)"},
            {"command": "ajuda", "description": "Como usar o bot"},
        ])
