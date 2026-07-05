#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/base.py
===============
Contrato comum a todas as fontes (CustoJusto, OLX, Idealista, ...).

Cada fonte é descrita por um objeto `Source`. As fontes "de portal" (páginas de
listagem + detalhe) só precisam de fornecer:
  - build_listing_url(regiao, categoria, venda, so_particulares, pagina) -> str | None
  - parse_listing(html) -> list[dict]   (cada dict tem pelo menos {id, url})
  - parse_detail(html, rec) -> rec

As fontes que não seguem esse padrão (ex.: descoberta via Google, Facebook
Marketplace) fornecem um `collect(params, fetch, log)` próprio.

`gate()` permite desativar uma fonte em runtime (ex.: falta de chave, ToS).
"""

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Source:
    key: str
    label: str
    build_listing_url: Optional[Callable] = None
    parse_listing: Optional[Callable] = None
    parse_detail: Optional[Callable] = None
    collect: Optional[Callable] = None          # override p/ fontes não-paginadas
    gate: Optional[Callable] = None             # () -> (ok: bool, motivo: str)
    engine_hint: Optional[str] = None           # motor preferido (informativo)
    risky: bool = False                         # cuidado legal/ToS (ex.: facebook)
    verified: bool = True                        # False = seletores por afinar (--debug)
    needs_detail: bool = True                    # False = a listagem já traz tudo
    notes: str = ""

    def available(self):
        """(ok, motivo). Fontes sem gate estão sempre disponíveis."""
        if self.gate is None:
            return True, ""
        return self.gate()


def collect_pages(source, params, fetch, log, sleep):
    """
    Percorre as páginas de listagem de uma fonte de portal e devolve os registos
    base (sem detalhe). `fetch(url) -> (html, motor)`; `log(str)`; `sleep()`.
    Deduplica por id dentro da própria fonte e pára cedo quando não há novos.
    """
    regiao = params.get("regiao", "portugal")
    categoria = params.get("categoria", "moradias")
    venda = params.get("venda", True)
    so_particulares = params.get("so_particulares", True)
    max_paginas = int(params.get("max_paginas", 3))

    todos = {}
    for pagina in range(1, max_paginas + 1):
        url = source.build_listing_url(regiao, categoria, venda, so_particulares, pagina)
        if not url:
            break
        html, motor = fetch(url)
        if not html:
            log(f"[{source.key}] página {pagina}: sem resposta de nenhum motor — parar.")
            break
        anuncios = source.parse_listing(html) or []
        novos = [a for a in anuncios if a["id"] not in todos]
        for a in novos:
            a.setdefault("fonte", source.label)
            a.setdefault("regiao", regiao)
            a.setdefault("categoria", categoria)
            a["_src_key"] = source.key
            todos[a["id"]] = a
        log(f"[{source.key}] página {pagina} [{motor}]: {len(anuncios)} anúncios, "
            f"{len(novos)} novos (total {len(todos)})")
        if not novos:
            break
        sleep()
    return list(todos.values())
