#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/google.py
=================
Fonte de DESCOBERTA. Não raspa o Google diretamente: usa uma API de pesquisa
(`discovery.py`) para encontrar URLs de anúncios de particulares espalhados por
vários sites e depois encaminha cada URL para o adaptador da fonte respetiva
(OLX, Imovirtual, Idealista, CustoJusto) para extrair o detalhe.

Assim, o "Google" acrescenta alcance sem parsers próprios: só descobre e delega.
"""

import hashlib

import discovery
from sources.base import Source
from sources._common import generic_parse_detail

DOMAINS = ()  # não é um portal; não recebe encaminhamento


def _gate():
    if not discovery.is_configured():
        return False, "falta SEARCH_API_KEY no .env (Serper.dev) para a descoberta via Google."
    return True, ""


def _collect(params, fetch, log):
    """Descobre URLs por pesquisa e cria registos base; o detalhe é encaminhado."""
    from sources import route_url  # import tardio (evita ciclo)

    regiao = params.get("regiao") or "portugal"
    categoria = params.get("categoria") or "casa"
    so_part = params.get("so_particulares", True)
    n = int(params.get("max_paginas", 3)) * 10

    termo_part = "particular" if so_part else ""
    queries = [
        f"vender {categoria} {regiao} {termo_part}".strip(),
        f"{categoria} {regiao} {termo_part} vende-se contacto".strip(),
    ]

    vistos, recs = set(), []
    for q in queries:
        urls = discovery.search(q, num=n)
        encontrados = 0
        for u in urls:
            if u in vistos:
                continue
            vistos.add(u)
            src = route_url(u)
            if src is None:
                continue  # domínio sem adaptador conhecido
            recs.append({
                "id": hashlib.sha1(u.encode("utf-8")).hexdigest()[:12],
                "url": u,
                "fonte": f"Google → {src.label}",
                "_src_key": src.key,
                "regiao": regiao,
                "categoria": categoria,
            })
            encontrados += 1
        log(f"[google] '{q}': {len(urls)} resultados, {encontrados} úteis")
    return recs


def _parse_detail(html, rec):
    """Encaminha o detalhe para o parser da fonte de origem do URL."""
    from sources import route_url  # import tardio

    src = route_url(rec.get("url", ""))
    fn = src.parse_detail if (src and src.parse_detail) else generic_parse_detail
    return fn(html, rec)


SOURCE = Source(
    key="google",
    label="Google (descoberta)",
    collect=_collect,
    parse_detail=_parse_detail,
    gate=_gate,
    verified=False,
    notes="Descoberta via API de pesquisa (Serper.dev). Delega o detalhe a cada portal.",
)
