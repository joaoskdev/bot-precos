"""Teste rápido no seu PC: mostra o que o bot consegue ler de um ou mais links.

Uso:
    python testar.py https://www.kabum.com.br/produto/... https://www.pichau.com.br/...
"""

import sys

import lojas
from main import brl

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

for url in sys.argv[1:]:
    r = lojas.consultar(url)
    print(f"\n{lojas.nome_loja(url)}  {url}")
    print(f"  nome:       {r.nome}")
    print(f"  preço:      {brl(r.preco)}  (achado via: {r.fonte})")
    print(f"  disponível: {r.disponivel}")
    if r.erro:
        print(f"  ERRO:       {r.erro}")
