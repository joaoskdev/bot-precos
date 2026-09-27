"""Baixa a página de um anúncio e descobre preço, nome e disponibilidade.

Estratégia (na ordem):
  1. JSON-LD (schema.org Product/Offer) — a maioria das lojas publica isso.
  2. Meta tags (itemprop=price, product:price:amount, og:price:amount).
  3. Regras específicas por loja (Mercado Livre, KaBuM, Pichau, Terabyte).

Se nada funcionar, devolve um Resultado com `erro` preenchido.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

NOMES_LOJAS = {
    "mercadolivre": "Mercado Livre",
    "mercadolibre": "Mercado Livre",
    "kabum": "KaBuM!",
    "pichau": "Pichau",
    "terabyteshop": "Terabyte",
    "amazon": "Amazon",
    "magazineluiza": "Magalu",
    "magalu": "Magalu",
    "aliexpress": "AliExpress",
    "shopee": "Shopee",
    "casasbahia": "Casas Bahia",
    "americanas": "Americanas",
}

CABECALHOS = {
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Trechos que indicam que a loja devolveu captcha/bloqueio em vez do produto
SINAIS_BLOQUEIO = ("account-verification", "/captcha", "challenge-platform", "cf-chl-")
TEXTOS_INDISPONIVEL = ("produto indisponível", "produto esgotado", "indisponível no momento",
                       "este produto está indisponível", "anúncio pausado", "publicação pausada")


class ErroLoja(Exception):
    pass


@dataclass
class Resultado:
    preco: float | None = None
    nome: str | None = None
    disponivel: bool | None = None
    fonte: str | None = None  # qual método encontrou o preço (útil para depurar)
    erro: str | None = None


def nome_loja(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for chave, nome in NOMES_LOJAS.items():
        if chave in host:
            return nome
    return host.removeprefix("www.") or "loja"


# ---------------------------------------------------------------- números

def numero_json(valor) -> float | None:
    """Preço vindo de JSON/atributo (formato de máquina: 1234.56)."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 0 else None
    if isinstance(valor, str):
        s = valor.strip()
        if "," in s:  # alguns sites colocam formato brasileiro no atributo
            return numero_texto(s)
        try:
            v = float(re.sub(r"[^\d.]", "", s))
            return v if v > 0 else None
        except ValueError:
            return None
    return None


def numero_texto(texto: str) -> float | None:
    """Preço escrito para humanos: 'R$ 1.234,56', '1.234', '99,90'."""
    if not texto:
        return None
    m = re.search(r"\d[\d.\s]*(?:,\d{1,2})?", texto)
    if not m:
        return None
    s = re.sub(r"\s", "", m.group(0))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


# ---------------------------------------------------------------- JSON-LD

def _percorrer(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            if isinstance(v, (dict, list)):
                yield from _percorrer(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _percorrer(item)


def _tem_tipo(d: dict, tipo: str) -> bool:
    t = d.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() == tipo.lower() for x in t)
    return str(t).lower() == tipo.lower()


def _json_ld(soup: BeautifulSoup) -> list[dict]:
    blocos = []
    for tag in soup.find_all("script", type="application/ld+json"):
        bruto = tag.string or tag.get_text() or ""
        try:
            blocos.append(json.loads(bruto.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    return blocos


def do_json_ld(soup: BeautifulSoup) -> Resultado:
    res = Resultado()
    for bloco in _json_ld(soup):
        for d in _percorrer(bloco):
            if not _tem_tipo(d, "Product"):
                continue
            res.nome = res.nome or d.get("name")
            ofertas = d.get("offers")
            if isinstance(ofertas, dict):
                ofertas = [ofertas]
            precos, disponibilidades = [], []
            for o in ofertas or []:
                if not isinstance(o, dict):
                    continue
                for o2 in _percorrer(o):  # AggregateOffer pode ter "offers" dentro
                    p = numero_json(o2.get("price")) or numero_json(o2.get("lowPrice"))
                    spec = o2.get("priceSpecification")
                    if p is None and isinstance(spec, dict):
                        p = numero_json(spec.get("price"))
                    if p is not None:
                        precos.append(p)
                    if o2.get("availability"):
                        disponibilidades.append(str(o2["availability"]))
            if precos:
                res.preco = min(precos)
                res.fonte = "json-ld"
                if disponibilidades:
                    res.disponivel = any(("InStock" in a or "LimitedAvailability" in a
                                          or "PreOrder" in a) for a in disponibilidades)
                return res
    return res


# ---------------------------------------------------------------- meta tags

def das_metas(soup: BeautifulSoup) -> float | None:
    seletores = [
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        '[itemprop="price"]',
    ]
    for sel in seletores:
        tag = soup.select_one(sel)
        if not tag:
            continue
        p = numero_json(tag.get("content")) if tag.get("content") else numero_texto(tag.get_text())
        if p:
            return p
    return None


# ---------------------------------------------------------------- lojas específicas

def _next_data(soup: BeautifulSoup):
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        return None
    try:
        return json.loads(tag.string or tag.get_text())
    except (json.JSONDecodeError, ValueError):
        return None


def _procurar_chave(obj, chave: str):
    """Procura uma chave em qualquer profundidade, abrindo strings que contêm JSON."""
    if isinstance(obj, dict):
        if chave in obj:
            yield obj[chave]
        for v in obj.values():
            yield from _procurar_chave(v, chave)
    elif isinstance(obj, list):
        for v in obj:
            yield from _procurar_chave(v, chave)
    elif isinstance(obj, str) and len(obj) > 50 and obj.lstrip()[:1] in "{[" and chave in obj:
        try:
            yield from _procurar_chave(json.loads(obj), chave)
        except (json.JSONDecodeError, ValueError):
            pass


def especifico_mercadolivre(soup: BeautifulSoup) -> float | None:
    bloco = soup.select_one(".ui-pdp-price__second-line") or soup.select_one(".ui-pdp-price")
    if not bloco:
        return None
    inteiro = bloco.select_one(".andes-money-amount__fraction")
    if not inteiro:
        return None
    centavos = bloco.select_one(".andes-money-amount__cents")
    texto = inteiro.get_text(strip=True) + ("," + centavos.get_text(strip=True) if centavos else "")
    return numero_texto(texto)


def especifico_kabum(soup: BeautifulSoup) -> float | None:
    dados = _next_data(soup)
    for chave in ("priceWithDiscount", "preco_desconto"):
        for v in _procurar_chave(dados, chave):
            p = numero_json(v)
            if p:
                return p
    tag = soup.select_one("h4.finalPrice") or soup.select_one('[class*="finalPrice"]')
    return numero_texto(tag.get_text()) if tag else None


def especifico_pichau(soup: BeautifulSoup) -> float | None:
    dados = _next_data(soup)
    for precos in _procurar_chave(dados, "pichau_prices"):
        if isinstance(precos, dict):
            for chave in ("avista", "final_price", "base_price"):
                p = numero_json(precos.get(chave))
                if p:
                    return p
    return None


def especifico_terabyte(soup: BeautifulSoup) -> float | None:
    tag = soup.select_one("#valVista") or soup.select_one(".val-prod.valVista")
    return numero_texto(tag.get_text()) if tag else None


ESPECIFICOS = {
    "mercadolivre": especifico_mercadolivre,
    "kabum": especifico_kabum,
    "pichau": especifico_pichau,
    "terabyteshop": especifico_terabyte,
}


def _nome(soup: BeautifulSoup) -> str | None:
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        return og["content"].strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None


def extrair(html: str, url: str) -> Resultado:
    """Extrai as informações de uma página já baixada (separado para poder testar)."""
    soup = BeautifulSoup(html, "lxml")
    res = do_json_ld(soup)

    if res.preco is None:
        host = (urlparse(url).hostname or "").lower()
        for chave, func in ESPECIFICOS.items():
            if chave in host:
                p = func(soup)
                if p:
                    res.preco, res.fonte = p, f"regra-{chave}"
                break

    if res.preco is None:
        p = das_metas(soup)
        if p:
            res.preco, res.fonte = p, "meta"

    res.nome = (res.nome or _nome(soup) or "").strip() or None
    if res.nome:
        res.nome = re.sub(r"\s*[|\-–]\s*(KaBuM!?|Pichau|Mercado Livre|Terabyte.*)$", "", res.nome,
                          flags=re.I).strip()

    if res.preco is None:
        texto = soup.get_text(" ", strip=True).lower()
        if any(t in texto for t in TEXTOS_INDISPONIVEL):
            res.disponivel = False
            res.erro = None
        else:
            res.erro = "não encontrei o preço na página"
    return res


# ---------------------------------------------------------------- download

def baixar(url: str) -> tuple[str, str]:
    """Baixa a página imitando um navegador de verdade. Tenta 2 'navegadores'."""
    ultimo_erro = None
    for navegador in ("chrome", "safari"):
        try:
            r = requests.get(url, impersonate=navegador, headers=CABECALHOS,
                             timeout=30, allow_redirects=True)
        except Exception as e:  # erro de rede
            ultimo_erro = ErroLoja(f"erro de conexão ({type(e).__name__})")
            continue
        final = str(r.url)
        if any(s in final for s in SINAIS_BLOQUEIO):
            ultimo_erro = ErroLoja("a loja pediu verificação/captcha (bloqueio anti-robô)")
            continue
        if r.status_code in (403, 429, 503):
            ultimo_erro = ErroLoja(f"a loja bloqueou o acesso (HTTP {r.status_code})")
            continue
        if r.status_code == 404:
            raise ErroLoja("anúncio não encontrado (HTTP 404) — talvez tenha sido removido")
        if r.status_code >= 400:
            ultimo_erro = ErroLoja(f"a loja respondeu HTTP {r.status_code}")
            continue
        return r.text, final
    raise ultimo_erro or ErroLoja("falha desconhecida")


def _kabum_api(url: str) -> float | None:
    """Plano B para a KaBuM: API pública usada pelo próprio site (pode mudar sem aviso)."""
    m = re.search(r"/produto/(\d+)", url)
    if not m:
        return None
    try:
        r = requests.get(
            f"https://servicespub.prod.api.aws.grupokabum.com.br/descricao/v1/descricao/produto/{m.group(1)}",
            impersonate="chrome", timeout=20)
        if r.status_code != 200:
            return None
        dados = r.json()
        for chave in ("preco_desconto", "priceWithDiscount", "preco"):
            for v in _procurar_chave(dados, chave):
                p = numero_json(v)
                if p:
                    return p
    except Exception:
        return None
    return None


def consultar(url: str) -> Resultado:
    """Ponto de entrada: nunca levanta exceção, erros vão em Resultado.erro."""
    try:
        html, final = baixar(url)
        res = extrair(html, final)
    except ErroLoja as e:
        res = Resultado(erro=str(e))
    except Exception as e:
        res = Resultado(erro=f"erro inesperado ({type(e).__name__})")

    if res.preco is None and res.disponivel is not False and "kabum" in url:
        p = _kabum_api(url)
        if p:
            res.preco, res.fonte, res.erro = p, "api-kabum", None
    return res
