#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/custojusto.py
=====================
Fonte original (CustoJusto.pt). Reaproveita as funções já testadas em
`custojusto_leads` e usa o detalhe genérico (que acrescenta fotos ao que a
função original já extraía).
"""

from custojusto_leads import build_listing_url, parse_listing
from scrapers import polite_sleep_between
from sources.base import Source, collect_pages
from sources._common import generic_parse_detail

DOMAINS = ("custojusto.pt",)

# O CustoJusto tem POUCOS particulares por categoria (~4), mas com telefone.
# Para maximizar leads reais, varremos as várias categorias de venda de uma vez.
CATEGORIAS_VENDA = ("moradias", "apartamentos", "terrenos-quintas")


def _collect(params, fetch, log):
    """Varre moradias + apartamentos + terrenos (venda, particulares) e junta
    tudo, deduplicando por id. Triplica os particulares com contacto face a
    varrer só 'moradias'. A fase de detalhe (em app.py) acrescenta telefone/fotos."""
    escolhida = (params.get("categoria") or "").strip()
    cats = list(CATEGORIAS_VENDA)
    if escolhida and escolhida not in cats:
        cats = [escolhida]                      # respeita uma categoria fora da lista
    vistos, recs = set(), []
    for cat in cats:
        base = collect_pages(SOURCE, {**params, "categoria": cat}, fetch, log, polite_sleep_between)
        for a in base:
            if a["id"] in vistos:
                continue
            vistos.add(a["id"])
            recs.append(a)
    log(f"[custojusto] {len(recs)} anúncios em {len(cats)} categoria(s): {', '.join(cats)}.")
    return recs


SOURCE = Source(
    key="custojusto",
    label="CustoJusto.pt",
    build_listing_url=build_listing_url,
    parse_listing=parse_listing,
    parse_detail=generic_parse_detail,
    collect=_collect,
    verified=True,
    notes="Fonte original; varre moradias+apartamentos+terrenos p/ + particulares.",
)
