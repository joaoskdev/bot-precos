"""Guarda a lista de produtos num arquivo criptografado dentro do próprio repositório.

Como o repositório é público (para ter minutos ilimitados no GitHub Actions),
o arquivo é criptografado. A chave vem do segredo STATE_KEY ou, se ele não
existir, é derivada do TELEGRAM_TOKEN (que já é secreto).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ARQUIVO = Path(__file__).parent / "dados" / "estado.bin"

VAZIO = {
    "offset": 0,         # última mensagem do Telegram já processada
    "dono": None,        # chat_id de quem pode usar o bot (o primeiro que mandar /start)
    "proximo_id": 1,
    "produtos": [],
}


def _fernet() -> Fernet:
    chave = os.environ.get("STATE_KEY") or os.environ["TELEGRAM_TOKEN"]
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(chave.encode()).digest()))


def carregar() -> dict:
    if not ARQUIVO.exists():
        return json.loads(json.dumps(VAZIO))
    try:
        dados = _fernet().decrypt(ARQUIVO.read_bytes())
    except InvalidToken:
        raise SystemExit(
            "Não consegui abrir dados/estado.bin: a chave mudou (você trocou o TELEGRAM_TOKEN "
            "ou o STATE_KEY?). Volte a chave antiga, ou apague dados/estado.bin para começar do zero."
        )
    estado = json.loads(dados)
    for k, v in VAZIO.items():
        estado.setdefault(k, v)
    return estado


def salvar(estado: dict) -> None:
    ARQUIVO.parent.mkdir(exist_ok=True)
    bruto = json.dumps(estado, ensure_ascii=False, separators=(",", ":")).encode()
    ARQUIVO.write_bytes(_fernet().encrypt(bruto))
