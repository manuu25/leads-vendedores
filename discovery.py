#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discovery.py
============
Descoberta de anúncios via API de pesquisa (em vez de raspar o SERP do Google,
o que viola os ToS e apanha captcha). Por defeito usa o Serper.dev, que devolve
os resultados orgânicos do Google em JSON.

Configuração no .env:
  SEARCH_API_KEY=...            (chave do Serper.dev — https://serper.dev)
  SEARCH_COUNTRY=pt            (opcional)

O resultado é uma lista de URLs; quem sabe interpretá-las é o adaptador da fonte
correspondente (ver `sources.route_url`).
"""

import os
import sys

import requests

SERPER_URL = "https://google.serper.dev/search"


def is_configured():
    return bool(os.environ.get("SEARCH_API_KEY"))


def search(query, num=20, api_key=None, country=None, lang="pt"):
    """Devolve uma lista de URLs orgânicos para a query, ou [] se não configurado."""
    api_key = api_key or os.environ.get("SEARCH_API_KEY")
    if not api_key:
        return []
    country = (country or os.environ.get("SEARCH_COUNTRY", "pt")).lower()
    try:
        r = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": min(num, 100), "gl": country, "hl": lang},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"  ! pesquisa erro de rede: {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        print(f"  ! pesquisa HTTP {r.status_code}: {r.text[:150]}", file=sys.stderr)
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    urls = []
    for item in data.get("organic", []):
        u = item.get("link")
        if u:
            urls.append(u)
    return urls
