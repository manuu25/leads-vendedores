#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/imovirtual.py
=====================
Imovirtual.com — portal imobiliário (grupo OLX/Adevinta), frontend React novo
em /pt/resultados/.

VERIFICADO contra HTML real (2026-07): a listagem embute um JSON grande
(props.pageProps.data.searchAds.items) com TUDO estruturado por anúncio —
título, totalPrice, área, roomsNumber, location, images, transaction (SELL/RENT)
e `isPrivateOwner`. Lemos daí (uma leitura por página, sem visitar cada anúncio).

DESCOBERTA IMPORTANTE: o Imovirtual é dominado por AGÊNCIAS — nas pesquisas
testadas, `isPrivateOwner` era falso em ~todos os anúncios. Com "só particulares"
esta fonte rende pouco/nada; o grosso dos leads FSBO vem do OLX e do CustoJusto.
POR AFINAR: filtro por região/ilha (o frontend novo usa IDs de localização) —
por agora devolve Portugal inteiro. Telefone não vem no payload da listagem.
"""

import json
import re

from custojusto_leads import TIPOLOGIA_RE
from scrapers import polite_sleep_between
from sources.base import Source
from sources._common import generic_parse_detail

DOMAINS = ("imovirtual.com",)
BASE = "https://www.imovirtual.com"

# vocabulário comum -> segmento de tipo no Imovirtual
CATEGORIAS = {
    "moradias": "moradia",
    "apartamentos": "apartamento",
    "terrenos-quintas": "terreno",
    "predios": "predio",
}

# roomsNumber (enum do payload) -> tipologia
ROOMS = {"STUDIO": "T0", "ONE": "T1", "TWO": "T2", "THREE": "T3", "FOUR": "T4",
         "FIVE": "T5", "SIX": "T6", "SEVEN": "T7", "EIGHT": "T8", "NINE": "T9", "TEN": "T10"}


def build_listing_url(regiao, categoria, venda, so_particulares, pagina):
    tipo = CATEGORIAS.get(categoria, categoria)
    url = f"{BASE}/pt/resultados/comprar/{tipo}"
    # NOTA: região por afinar (o frontend novo usa IDs de localização).
    if pagina and pagina > 1:
        url += f"?page={pagina}"
    return url


def _items_from_html(html):
    """Extrai a lista de anúncios do maior blob <script type=application/json>."""
    blobs = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S)
    if not blobs:
        return []
    try:
        data = json.loads(max(blobs, key=len))
        return data["props"]["pageProps"]["data"]["searchAds"]["items"] or []
    except (ValueError, KeyError, TypeError):
        return []


def _record(it):
    slug = (it.get("href") or "").split("/ad/")[-1]
    loc = it.get("location") if isinstance(it.get("location"), dict) else {}
    addr = loc.get("address") or {}
    city = (addr.get("city") or {}).get("name")
    prov = (addr.get("province") or {}).get("name")
    tp = it.get("totalPrice") if isinstance(it.get("totalPrice"), dict) else {}
    priv = bool(it.get("isPrivateOwner"))
    owner = it.get("agency") if isinstance(it.get("agency"), dict) else {}
    title = it.get("title") or ""
    mt = TIPOLOGIA_RE.search(title)
    tipologia = mt.group(0) if mt else ROOMS.get(it.get("roomsNumber"))
    return {
        "id": str(it.get("id")),
        "url": f"{BASE}/pt/anuncio/{slug}" if slug else None,
        "titulo": title,
        "preco": tp.get("value"),
        "area_m2": it.get("areaInSquareMeters"),
        "tipologia": tipologia,
        "localidade": ", ".join([x for x in (city, prov) if x]) or None,
        "tipo_anunciante": "particular" if priv else "profissional",
        "anunciante": None if priv else owner.get("name"),
        "fotos": [im.get("medium") for im in (it.get("images") or []) if im.get("medium")],
    }


def _collect(params, fetch, log):
    """Percorre páginas, lê o JSON e filtra por venda (SELL) e particular."""
    regiao = params.get("regiao", "portugal")
    categoria = params.get("categoria", "moradias")
    so_part = params.get("so_particulares", True)
    max_paginas = int(params.get("max_paginas", 3))

    todos = {}
    for pagina in range(1, max_paginas + 1):
        url = build_listing_url(regiao, categoria, True, so_part, pagina)
        html, motor = fetch(url)
        if not html:
            log(f"[imovirtual] página {pagina}: sem resposta — parar.")
            break
        items = _items_from_html(html)
        venda = [it for it in items if it.get("transaction") == "SELL"]
        if so_part:
            venda = [it for it in venda if it.get("isPrivateOwner")]
        novos = 0
        for it in venda:
            rec = _record(it)
            if not rec["id"] or rec["id"] in todos:
                continue
            rec["fonte"] = "Imovirtual.com"
            rec["_src_key"] = "imovirtual"
            rec.setdefault("regiao", regiao)
            rec.setdefault("categoria", categoria)
            todos[rec["id"]] = rec
            novos += 1
        extra = " particulares" if so_part else ""
        log(f"[imovirtual] página {pagina} [{motor}]: {len(items)} anúncios, "
            f"{len(venda)} venda{extra}, {novos} novos (total {len(todos)})")
        if novos == 0:
            break
        polite_sleep_between()
    return list(todos.values())


SOURCE = Source(
    key="imovirtual",
    label="Imovirtual.com",
    build_listing_url=build_listing_url,
    collect=_collect,                     # lê o JSON da listagem (não pagina por <a>)
    parse_detail=generic_parse_detail,    # usado só quando o Google encaminha 1 URL
    needs_detail=False,                    # a listagem já traz tudo (menos telefone)
    engine_hint="playwright",
    verified=True,
    notes="JSON da listagem: título/preço/tipologia/área/localidade/fotos/tipo. Portal de AGÊNCIAS (poucos particulares); região por afinar.",
)
