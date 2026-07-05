#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/idealista.py
====================
Idealista.pt via APIFY (não por scraping direto).

Porquê: o Idealista tem anti-bot DataDome — testado, mesmo o context.dev devolve
a página-desafio. Em vez de o contornar (contra os ToS), usamos um ator do
Apify que já resolve isso e devolve dados estruturados.

⚠️ CUSTO: a conta Apify tem crédito LIMITADO. Este adaptador é conservador:
  - só corre se a fonte "idealista" for escolhida E houver APIFY_TOKEN;
  - limita o nº de resultados (APIFY_IDEALISTA_MAX, default 20);
  - faz UMA corrida síncrona por scrape (run-sync-get-dataset-items).

Config no .env:
  APIFY_TOKEN=apify_api_...                      (o teu token)
  APIFY_IDEALISTA_ACTOR=username~nome-do-ator    (ex.: igolaizola~idealista-scraper)
  APIFY_IDEALISTA_MAX=20                          (teto de itens; controla o custo)

NOTA: o schema de entrada/saída varia entre atores. O mapeamento abaixo tenta
vários nomes de campo comuns; se o teu ator usar outros, ajusta `_map_item`
(usa o log "keys do 1.º item" que este adaptador imprime na 1.ª corrida).
"""

import json
import os
import re
import sys

import requests

from sources.base import Source

DOMAINS = ("idealista.pt",)
BASE = "https://www.idealista.pt"

CATEGORIAS = {
    "moradias": "moradias",
    "apartamentos": "apartamentos",
    "terrenos-quintas": "terrenos",
    "predios": "predios",
}

# vocabulário comum -> propertyType do ator lukass
PROPERTY_TYPE = {
    "moradias": "homes",
    "apartamentos": "homes",
    "terrenos-quintas": "lands",
    "predios": "buildings",
}

ROOMS_MAX = 20


def _actor():
    return os.environ.get("APIFY_IDEALISTA_ACTOR", "").strip()


def _token():
    return os.environ.get("APIFY_TOKEN", "").strip()


def _gate():
    if not _token():
        return False, "falta APIFY_TOKEN no .env."
    if not _actor():
        return False, "falta APIFY_IDEALISTA_ACTOR no .env (ex.: username~idealista-scraper)."
    return True, ""


def build_search_url(regiao, categoria, so_particulares=True):
    """
    URL de pesquisa do Idealista (passada ao ator via startUrl). Usa o slug de
    região (ex.: "madeira-ilha", "funchal"); "portugal" = pesquisa nacional.

    NÃO acrescentamos /com-particular/: esse filtro server-side QUEBRA o ator
    lukass (erro de proxy 595 no OAuth do Idealista). Filtramos os particulares
    pós-recolha (ver _collect). Testado (2026-07): o Idealista é dominado por
    agências — em Madeira, ~0 FSBO.
    """
    local = (regiao or "portugal").strip().lower().replace(" ", "-")
    cat = CATEGORIAS.get(categoria, "moradias")
    seg = "casas" if cat in ("moradias", "apartamentos") else cat
    url = f"{BASE}/comprar-{seg}/"
    if local and local != "portugal":
        url += f"{local}/"
    return url


def _first(item, *paths):
    """Devolve o 1.º valor não-vazio, aceitando caminhos com pontos (a.b.c)."""
    for path in paths:
        cur = item
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def _to_int(v):
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        import re
        d = re.sub(r"[^\d]", "", v)
        return int(d) if d else None
    return None


def _map_item(it):
    """Mapeia um item do dataset Apify para o nosso registo (tolerante a schemas)."""
    url = _first(it, "url", "link", "detailUrl", "propertyUrl")
    pid = _first(it, "propertyCode", "adid", "id", "code") or (
        (url or "").rstrip("/").split("/")[-1])
    preco = _to_int(_first(it, "price", "priceInfo.amount", "priceInfo.price.amount", "priceInfo.price"))
    area = _to_int(_first(it, "size", "sizeInSquareMeters", "floorArea", "areaInSquareMeters"))
    rooms = _first(it, "rooms", "bedrooms", "roomNumber")
    tipologia = f"T{int(rooms)}" if isinstance(rooms, (int, float)) or (
        isinstance(rooms, str) and rooms.isdigit()) else None
    localidade = _first(it, "municipality", "district", "neighborhood", "province",
                        "location.name", "ubication.name")
    if not localidade:
        # o Idealista costuma esconder a morada; o local vem no título ("... in X").
        t = it.get("title") or it.get("address") or ""
        m = re.search(r"\bin\s+(.+)$", t)
        localidade = m.group(1).strip() if m else None
    telefone = _first(it, "contacts.phone1.phoneNumberForMobileDialing", "contacts.phone1.phoneNumber",
                      "contacts.phone1.formattedPhone", "contactInfo.phone", "phone", "contact.phone")
    utype = _first(it, "contacts.userType", "contactInfo.userType", "advertiser.type", "userType")
    tipo = None
    if isinstance(utype, str):
        tipo = "particular" if utype.lower() in ("private", "particular") else "profissional"
    anunciante = _first(it, "contacts.commercialName", "contacts.contactName",
                        "contactInfo.commercialName", "advertiser.name", "agency.name")
    # Sem campo explícito de tipo: um nome comercial/agência => profissional.
    if tipo is None and anunciante:
        tipo = "profissional"
    imgs = _first(it, "photos", "images", "multimedia.images", "thumbnails") or []
    fotos = []
    if isinstance(imgs, list):
        for im in imgs:
            u = im.get("url") if isinstance(im, dict) else im
            if isinstance(u, str) and u.startswith("http"):
                fotos.append(u)
    if not fotos:
        thumb = _first(it, "thumbnail", "mainImage", "image")
        if isinstance(thumb, str) and thumb.startswith("http"):
            fotos.append(thumb)
    return {
        "id": str(pid),
        "url": url,
        "titulo": _first(it, "title", "suggestedTexts.title", "name") or "",
        "preco": preco,
        "area_m2": area,
        "tipologia": tipologia,
        "localidade": localidade if isinstance(localidade, str) else None,
        "telefone": str(telefone) if telefone else None,
        "tipo_anunciante": tipo,
        "anunciante": anunciante if isinstance(anunciante, str) else None,
        "fotos": fotos,
    }


def _collect(params, fetch, log):
    """Corre o ator do Apify (síncrono) e mapeia o dataset devolvido."""
    regiao = params.get("regiao", "portugal")
    categoria = params.get("categoria", "moradias")
    so_part = params.get("so_particulares", True)
    max_itens = int(params.get("apify_max") or os.environ.get("APIFY_IDEALISTA_MAX", ROOMS_MAX))

    venda = params.get("venda", True)
    search_url = build_search_url(regiao, categoria, so_part)
    actor = _actor().replace("/", "~")  # Apify usa "~" no path
    endpoint = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

    # Input. Por defeito, schema do ator lukass~idealista-scraper (estruturado).
    # O lukass não tem filtro de particular; ou filtramos pós-recolha (abaixo) ou
    # passas uma URL /com-particular/ via APIFY_IDEALISTA_INPUT (startUrl: [{url}]).
    inp_env = os.environ.get("APIFY_IDEALISTA_INPUT")
    if inp_env:
        try:
            payload = json.loads(inp_env)
        except ValueError:
            payload = {}
        payload.setdefault("maxItems", max_itens)
    else:
        # Modo startUrl com a URL BASE da região (testado a funcionar). NÃO uses
        # /com-particular/ aqui — quebra o ator. Os campos country/operation/
        # propertyType são exigidos pelo lukass mesmo com startUrl.
        pages = max(1, min(50, (max_itens + 29) // 30))
        payload = {
            "startUrl": [{"url": search_url}],
            "country": "pt",
            "operation": "sale" if venda else "rent",
            "propertyType": PROPERTY_TYPE.get(categoria, "homes"),
            "maxItems": max_itens,
            "endPage": pages,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }

    modo = "input-próprio" if inp_env else f"startUrl={search_url}"
    log(f"[idealista] Apify ator={_actor()} {modo} maxItems={max_itens}")
    try:
        r = requests.post(
            endpoint,
            params={"token": _token(), "maxItems": max_itens, "timeout": 300},
            json=payload,
            timeout=330,
        )
    except requests.RequestException as e:
        log(f"[idealista] Apify erro de rede: {e}")
        return []
    if r.status_code not in (200, 201):
        log(f"[idealista] Apify HTTP {r.status_code}: {r.text[:200]}")
        return []
    try:
        items = r.json()
    except ValueError:
        log("[idealista] Apify devolveu resposta não-JSON.")
        return []
    if not isinstance(items, list):
        log(f"[idealista] Apify: formato inesperado ({type(items).__name__}).")
        return []

    if items:
        # Ajuda a afinar o mapeamento na 1.ª corrida (barato — só imprime).
        print(f"[idealista] keys do 1.º item: {sorted(items[0].keys())}", file=sys.stderr)
        dump = os.environ.get("APIFY_DUMP")
        if dump:
            try:
                json.dump(items, open(dump, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            except OSError:
                pass

    recs = []
    for it in items[:max_itens]:
        if not isinstance(it, dict):
            continue
        rec = _map_item(it)
        if not rec.get("url"):
            continue
        rec["fonte"] = "Idealista.pt (Apify)"
        rec["_src_key"] = "idealista"
        rec.setdefault("regiao", regiao)
        rec.setdefault("categoria", categoria)
        recs.append(rec)

    # Filtro de particulares pós-recolha (o filtro /com-particular/ do ator não
    # funciona; o Idealista é dominado por agências). Descarta os profissionais.
    if so_part and recs:
        antes = len(recs)
        recs = [r for r in recs if r.get("tipo_anunciante") != "profissional"]
        if antes and not recs:
            log(f"[idealista] {antes} anúncios mas TODOS de agências — 0 particulares "
                "(Idealista é dominado por agências; poucos/nenhuns FSBO).")
    log(f"[idealista] {len(recs)} anúncios via Apify"
        + (" (particulares)" if so_part else "") + ".")
    return recs


SOURCE = Source(
    key="idealista",
    label="Idealista.pt (Apify)",
    collect=_collect,
    gate=_gate,
    needs_detail=False,     # o Apify já devolve tudo, INCLUINDO telefone
    verified=True,          # mapeamento confirmado contra o output real do lukass (2026-07)
    notes=("Via Apify (lukass, modo estruturado). Traz telefone+fotos, MAS o "
           "Idealista é dominado por agências e o filtro particular do ator não "
           "funciona — rende ~0 FSBO. Útil sobretudo p/ ver o mercado de agências."),
)
