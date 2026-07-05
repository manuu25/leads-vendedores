#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources — registo das fontes de leads.

Cada módulo de fonte expõe um objeto `SOURCE` (ver `sources.base.Source`) e,
opcionalmente, uma tupla `DOMAINS` para encaminhamento por domínio (usado pela
descoberta via Google).

API:
  all_sources()      -> lista ordenada de Source (para a UI)
  get_sources(keys)  -> Source selecionadas (por ordem do registo)
  route_url(url)     -> Source cujo domínio corresponde ao URL (ou None)
  collect_pages(...) -> ajudante de paginação (reexportado de base)
"""

from urllib.parse import urlparse

from sources.base import Source, collect_pages  # noqa: F401  (reexport)
from sources import custojusto, olx, imovirtual, idealista, google, facebook

# Ordem = ordem de apresentação na UI e de execução.
_MODULES = [custojusto, olx, imovirtual, idealista, google, facebook]
REGISTRY = {m.SOURCE.key: m.SOURCE for m in _MODULES}

# domínio -> Source (para descoberta via Google encaminhar cada URL)
_DOMAIN_MAP = {}
for _m in _MODULES:
    for _d in getattr(_m, "DOMAINS", ()):
        _DOMAIN_MAP[_d] = _m.SOURCE


def all_sources():
    return list(REGISTRY.values())


def get_sources(keys):
    """Fontes selecionadas, pela ordem do registo. Default: só CustoJusto."""
    keys = set(keys or [])
    chosen = [s for k, s in REGISTRY.items() if k in keys]
    return chosen or [REGISTRY["custojusto"]]


def route_url(url):
    """Devolve a Source cujo domínio corresponde ao URL, ou None."""
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for dom, src in _DOMAIN_MAP.items():
        if host == dom or host.endswith("." + dom):
            return src
    return None
