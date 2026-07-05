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
from sources.base import Source
from sources._common import generic_parse_detail

DOMAINS = ("custojusto.pt",)

SOURCE = Source(
    key="custojusto",
    label="CustoJusto.pt",
    build_listing_url=build_listing_url,
    parse_listing=parse_listing,
    parse_detail=generic_parse_detail,
    verified=True,
    notes="Fonte original, seletores testados.",
)
