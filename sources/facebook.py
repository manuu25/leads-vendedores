#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/facebook.py
===================
Facebook Marketplace via APIFY — a MELHOR fonte de FSBO (particulares) na Madeira.

Ao contrário do Idealista/Imovirtual (dominados por agências), o Marketplace é
dominado por PARTICULARES a vender casa. Testado live (2026-07, Funchal): devolve
moradias por toda a ilha (Funchal, Machico, Santa Cruz, Ribeira Brava, Porto
Moniz…) com título, preço, localidade, descrição completa, fotos e link.

Porquê via Apify (e não Playwright com login próprio): usar o ator do Apify
NÃO arrisca a conta de Facebook do consultor (sem risco de bloqueio) e é barato
(~$0.01–0.03 / 100 anúncios). Continua a ser dado pessoal — respeita o RGPD.

O Marketplace localiza por ID NUMÉRICO de zona (não por nome). O default é o
Funchal (110189845667755). Para outra zona, muda APIFY_FB_LOCATION no .env
(abre o Marketplace na zona desejada e copia o número do URL /marketplace/<id>/).

Config no .env:
  APIFY_TOKEN=apify_api_...            (o mesmo token do Idealista)
  APIFY_FB_LOCATION=110189845667755   (ID de zona; default Funchal)
  APIFY_FB_MAX=30                     (teto de anúncios por corrida — custo)
  APIFY_FB_DETAILS=1                  (1 = traz descrição/telefone; custa um pouco mais)

O telefone só vem quando o vendedor o escreve na descrição; caso contrário,
contacta-se pelo link do anúncio (Messenger).
"""

import json
import os
import re

import requests

from custojusto_leads import PHONE_RE
from sources.base import Source

DEFAULT_ACTOR = "apify~facebook-marketplace-scraper"
DEFAULT_LOCATION = "110189845667755"  # Funchal, Madeira (propertyforsale)


def _token():
    return os.environ.get("APIFY_TOKEN", "").strip()


def _actor():
    return os.environ.get("APIFY_FB_ACTOR", DEFAULT_ACTOR).strip().replace("/", "~")


def _gate():
    if not _token():
        return False, "falta APIFY_TOKEN no .env (Facebook Marketplace via Apify)."
    return True, ""


def _clean_int(v):
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        d = re.sub(r"[^\d]", "", v.split(",")[0].split(".")[0])
        return int(d) if d else None
    return None


def _map(it):
    loc = it.get("location") if isinstance(it.get("location"), dict) else {}
    geo = loc.get("reverse_geocode_detailed") or loc.get("reverse_geocode") or {}
    city = geo.get("city") if isinstance(geo, dict) else None

    lp = it.get("listingPrice") or it.get("listing_price") or {}
    preco = _clean_int(lp.get("amount") or lp.get("formatted_amount")) if isinstance(lp, dict) else None
    if preco is not None and preco < 1000:
        preco = None  # €0/€1/€150 são placeholders típicos ("negociável")

    desc = it.get("description")
    desc = desc.get("text") if isinstance(desc, dict) else (desc or "")
    tel = None
    m = PHONE_RE.search(desc or "")
    if m:
        tel = re.sub(r"[\s.\-]", "", m.group(0))

    fotos = []
    for p in (it.get("listingPhotos") or []):
        uri = ((p.get("image") or {}).get("uri")) if isinstance(p, dict) else None
        if uri:
            fotos.append(uri)
    if not fotos:
        pp = it.get("primaryListingPhoto") or it.get("primary_listing_photo") or {}
        uri = ((pp.get("image") or {}).get("uri")) if isinstance(pp, dict) else None
        if uri:
            fotos.append(uri)

    titulo = it.get("listingTitle") or it.get("marketplace_listing_title") or ""
    if not titulo and desc:
        titulo = desc.strip().splitlines()[0][:80]

    url = it.get("itemUrl")
    if not url and it.get("id"):
        url = f"https://www.facebook.com/marketplace/item/{it['id']}/"
    fid = it.get("id")
    if not fid and url:
        mm = re.search(r"/item/(\d+)", url)
        fid = mm.group(1) if mm else url

    return {
        "id": str(fid),
        "url": url,
        "titulo": titulo,
        "preco": preco,
        "localidade": city,
        "telefone": tel,
        "tipo_anunciante": "particular",  # Marketplace ~ particulares (best-effort)
        "fotos": fotos,
    }


def _collect(params, fetch, log):
    loc = os.environ.get("APIFY_FB_LOCATION", DEFAULT_LOCATION).strip()
    max_itens = int(params.get("apify_max") or os.environ.get("APIFY_FB_MAX", 30))
    details = os.environ.get("APIFY_FB_DETAILS", "1").strip().lower() in ("1", "true", "yes")
    url = f"https://www.facebook.com/marketplace/{loc}/propertyforsale/"

    log(f"[facebook] Apify Marketplace zona={loc} maxItems={max_itens} detalhes={details}")
    try:
        r = requests.post(
            f"https://api.apify.com/v2/acts/{_actor()}/run-sync-get-dataset-items",
            params={"token": _token(), "maxItems": max_itens, "timeout": 300},
            json={"startUrls": [{"url": url}], "resultsLimit": max_itens,
                  "includeListingDetails": details},
            timeout=330,
        )
    except requests.RequestException as e:
        log(f"[facebook] Apify erro de rede: {e}")
        return []
    if r.status_code not in (200, 201):
        log(f"[facebook] Apify HTTP {r.status_code}: {r.text[:200]}")
        return []
    try:
        items = r.json()
    except ValueError:
        return []
    if not isinstance(items, list):
        return []

    dump = os.environ.get("APIFY_DUMP")
    if dump and items:
        try:
            json.dump(items, open(dump, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except OSError:
            pass

    recs, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        rec = _map(it)
        if not rec.get("url") or rec["id"] in seen:
            continue
        seen.add(rec["id"])
        rec["fonte"] = "Facebook Marketplace"
        rec["_src_key"] = "facebook"
        rec.setdefault("regiao", params.get("regiao", "madeira"))
        rec.setdefault("categoria", params.get("categoria", "moradias"))
        recs.append(rec)

    com_tel = sum(1 for r in recs if r.get("telefone"))
    log(f"[facebook] {len(recs)} anúncios (Marketplace ~ particulares), "
        f"{com_tel} com telefone no texto; os restantes contactam-se pelo link.")
    return recs


SOURCE = Source(
    key="facebook",
    label="Facebook Marketplace",
    collect=_collect,
    gate=_gate,
    needs_detail=False,
    verified=True,
    notes=("Via Apify (sem risco p/ a conta pessoal). Marketplace = PARTICULARES — "
           "a melhor fonte FSBO da Madeira. Zona por APIFY_FB_LOCATION (default "
           "Funchal). Telefone só quando está no texto; senão, link/Messenger."),
)
