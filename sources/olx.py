#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/olx.py
==============
OLX.pt — o maior portal de classificados de Portugal. Estrutura tipo Next.js,
com anti-bot moderado (usa Playwright ou context.dev).

VERIFICADO contra HTML real (2026-07): a listagem lê-se dos cartões
(data-cy="l-card"), que já trazem título, preço e localidade — logo não é
preciso visitar cada anúncio para os campos base. O filtro ?private_business=
private restringe a particulares.
LIMITAÇÕES: (1) o OLX esconde o telefone atrás de login/clique; (2) a categoria
"venda" traz anúncios de ARRENDAMENTO promovidos no topo — filtramo-los por um
piso de preço (`PRICE_FLOOR`), adequado a moradias/apartamentos (para terrenos
baratos pode ser demasiado alto — ajustar se necessário).
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from sources.base import Source
from sources._common import generic_parse_detail

DOMAINS = ("olx.pt",)
BASE = "https://www.olx.pt"

# Piso de preço para descartar arrendamentos que aparecem na categoria de venda.
PRICE_FLOOR = 20000


def _clean_int(s):
    d = re.sub(r"[^\d]", "", s or "")
    return int(d) if d else None

# vocabulário comum -> caminho de categoria no OLX (venda).
# O OLX junta apartamentos e moradias na mesma categoria "a-venda".
CATEGORIAS = {
    "moradias": "apartamento-casa-a-venda",
    "apartamentos": "apartamento-casa-a-venda",
    "terrenos-quintas": "terrenos-quintas",
    "predios": "apartamento-casa-a-venda",
}

# Anúncio OLX: .../d/anuncio/<slug>-ID<code>.html
AD_HREF_RE = re.compile(r"/d/anuncio/[\w\-]+-ID([0-9A-Za-z]+)\.html", re.IGNORECASE)


def build_listing_url(regiao, categoria, venda, so_particulares, pagina):
    cat = CATEGORIAS.get(categoria, categoria)
    url = f"{BASE}/imoveis/{cat}/"
    params = []
    if regiao and regiao.lower() != "portugal":
        params.append(f"q={regiao}")
    if so_particulares:
        # filtro "particulares" do OLX
        params.append("search%5Bprivate_business%5D=private")
    if pagina and pagina > 1:
        params.append(f"page={pagina}")
    if params:
        url += "?" + "&".join(params)
    return url


def parse_listing(html):
    """
    Lê os cartões do OLX (data-cy="l-card"): id, título (img alt), preço e
    localidade vêm já do cartão. Descarta arrendamentos (preço < PRICE_FLOOR).
    """
    soup = BeautifulSoup(html, "lxml")
    seen = {}
    cards = soup.select('[data-cy="l-card"]')
    for c in cards:
        a = c.find("a", href=True)
        if not a:
            continue
        m = AD_HREF_RE.search(a["href"])
        ad_id = c.get("id") or (m.group(1) if m else None)
        if not ad_id or ad_id in seen:
            continue

        price_el = c.select_one('[data-testid="ad-price"]')
        preco = _clean_int(price_el.get_text() if price_el else "")
        if preco is not None and preco < PRICE_FLOOR:
            continue  # provável arrendamento no topo da categoria de venda

        img = c.find("img")
        loc_el = c.select_one('[data-testid="location-date"]')
        localidade = ""
        if loc_el:
            localidade = re.split(r"\s+-\s+", loc_el.get_text(" ", strip=True))[0].strip()

        seen[ad_id] = {
            "id": ad_id,
            "url": urljoin(BASE, a["href"].split("?")[0]),
            "titulo": (img.get("alt") if img else "") or "",
            "preco": preco,
            "localidade": localidade or None,
        }

    # Fallback: se o layout mudar e não houver cartões, tenta pelos links.
    if not seen:
        for a in soup.find_all("a", href=True):
            m = AD_HREF_RE.search(a["href"])
            if not m or m.group(1) in seen:
                continue
            seen[m.group(1)] = {
                "id": m.group(1),
                "url": urljoin(BASE, a["href"].split("?")[0]),
                "titulo": (a.get("title") or a.get_text(" ", strip=True) or "").strip(),
            }
    return list(seen.values())


SOURCE = Source(
    key="olx",
    label="OLX.pt",
    build_listing_url=build_listing_url,
    parse_listing=parse_listing,
    parse_detail=generic_parse_detail,
    engine_hint="playwright",
    verified=True,
    notes="Cartões: título/preço/localidade OK; arrendamentos filtrados. Telefone atrás de login/clique.",
)
