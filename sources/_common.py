#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources/_common.py
==================
Extração partilhada por várias fontes. Reaproveita os ajudantes já existentes
em `custojusto_leads` (JSON-LD, __NEXT_DATA__, regexes de preço/telefone/…) e
acrescenta um `generic_parse_detail` que funciona na maioria dos portais
imobiliários portugueses (são quase todos Next.js com JSON-LD).

Se um portal falhar, corre o scraper com --debug para inspecionar o HTML real
e ajusta o `parse_listing`/regexes do adaptador dessa fonte.
"""

import json
import re

from bs4 import BeautifulSoup

from custojusto_leads import (
    extract_jsonld,
    extract_next_data,
    clean_price,
    TIPOLOGIA_RE,
    AREA_RE,
    PHONE_RE,
)

IMG_RE = re.compile(r'https?://[^"\'\\ ]+?\.(?:jpe?g|png|webp)', re.IGNORECASE)


def extract_photos(soup, nxt=None, limit=12):
    """Recolhe URLs de fotos: og:image + JSON-LD 'image' + payload __NEXT_DATA__."""
    fotos, seen = [], set()

    def add(u):
        if not isinstance(u, str) or not u.startswith("http"):
            return
        if u in seen:
            return
        seen.add(u)
        fotos.append(u)

    for m in soup.find_all("meta", attrs={"property": "og:image"}):
        add(m.get("content"))

    for obj in extract_jsonld(soup):
        if not isinstance(obj, dict):
            continue
        img = obj.get("image")
        if isinstance(img, str):
            add(img)
        elif isinstance(img, list):
            for x in img:
                if isinstance(x, str):
                    add(x)
                elif isinstance(x, dict):
                    add(x.get("url"))
        elif isinstance(img, dict):
            add(img.get("url"))

    if nxt is not None:
        flat = json.dumps(nxt, ensure_ascii=False)
        for u in IMG_RE.findall(flat):
            add(u)

    return fotos[:limit]


def generic_parse_detail(html, base_record):
    """
    Detalhe genérico para portais imobiliários: JSON-LD (preço/nome/localidade) +
    __NEXT_DATA__ (anunciante/telefone/tipo) + fallbacks de texto + fotos.
    Só escreve onde o registo ainda está vazio (as fontes mais fiáveis ganham).
    """
    rec = dict(base_record)
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    nxt = extract_next_data(html)

    # --- JSON-LD ---
    for obj in extract_jsonld(soup):
        if not isinstance(obj, dict):
            continue
        if obj.get("name") and not rec.get("titulo"):
            rec["titulo"] = obj["name"]
        offers = obj.get("offers") or {}
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict) and offers.get("price") and not rec.get("preco"):
            try:
                rec["preco"] = int(float(str(offers["price"]).replace(",", ".")))
            except (ValueError, TypeError):
                pass
        addr = obj.get("address")
        if isinstance(addr, dict):
            loc = addr.get("addressLocality") or addr.get("addressRegion")
            if loc and not rec.get("localidade"):
                rec["localidade"] = loc

    # --- __NEXT_DATA__ ---
    if nxt is not None:
        flat = json.dumps(nxt, ensure_ascii=False)
        for key in ("advertiserName", "userName", "displayName", "sellerName", "name"):
            m = re.search(rf'"{key}"\s*:\s*"([^"]{{2,60}})"', flat)
            if m and not rec.get("anunciante"):
                rec["anunciante"] = m.group(1)
                break
        mp = PHONE_RE.search(flat)
        if mp and not rec.get("telefone"):
            rec["telefone"] = re.sub(r"[\s.\-]", "", mp.group(0))
        mt = re.search(r'"(?:adType|sellerType|type)"\s*:\s*"([^"]+)"', flat)
        if mt and not rec.get("tipo_anunciante"):
            rec["tipo_anunciante"] = mt.group(1)

    # --- Fallbacks de texto visível ---
    if not rec.get("preco"):
        rec["preco"] = clean_price(text)
    if not rec.get("tipologia"):
        mt = TIPOLOGIA_RE.search(rec.get("titulo", "") + " " + text[:400])
        if mt:
            rec["tipologia"] = mt.group(0)
    if not rec.get("area_m2"):
        ma = AREA_RE.search(text)
        if ma:
            rec["area_m2"] = ma.group(1).replace(",", ".")
    if not rec.get("telefone"):
        mp = PHONE_RE.search(text)
        if mp:
            rec["telefone"] = re.sub(r"[\s.\-]", "", mp.group(0))

    if not rec.get("fotos"):
        rec["fotos"] = extract_photos(soup, nxt)

    return rec
