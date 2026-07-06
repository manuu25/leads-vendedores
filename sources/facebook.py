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


# --- Filtros de qualidade (tirar falsos positivos) ------------------------- #
# Anúncios claramente estrangeiros (não-Madeira): cirílico, grego, árabe,
# hebraico, CJK, kana, hangul. As casas da Madeira estão em pt/en (alfabeto
# latino). Basta um punhado de caracteres não-latinos para excluir.
_NONLATIN_RE = re.compile(
    r"[Ѐ-ԯͰ-Ͽ֐-׿؀-ۿݐ-ݿ"
    r"぀-ヿ一-鿿가-힯]"
)

# O vendedor DIZ que é particular / sem intermediários → confiamos e mantemos,
# mesmo que a palavra "imobiliária" apareça (ex.: "vendo SEM imobiliárias").
_FSBO_RE = re.compile(
    r"(particular(es)?|propriet[aá]ri[ao]|dono\s+direto|direto\s+do\s+dono|"
    r"sem\s+(imobili|interm|ag[êe]nci|comiss))",
    re.IGNORECASE,
)

# Sinais fortes de agência / consultor imobiliário (baixo falso-positivo).
# NOTA: quando a marca da agência só existe na marca-de-água da FOTO (ex.: um
# anúncio da ERA cujo texto nada diz), isto NÃO a apanha — não há OCR nem campo
# de vendedor no dataset do Apify. Para esses, o utilizador usa "Descartar".
_AGENCY_RE = re.compile(
    r"(imobili[aá]ri[ao]|media[çc][aã]o\s+imobili|consultor[ae]?\s+imobili|"
    r"licen[çc]a\s*ami|\bami[\s:.#ºª-]*\d{3,}|"
    # marcas / redes
    r"re\s*/?\s*max|century\s*21|\bc21\b|keller\s*williams|\bkw\b\s*(portugal|imob|madeira)|"
    r"predimed|\bzome\b|engel\s*&|v[öo]lkers|vanguard\s+properties|"
    r"\biad\s+(portugal|imob)|\bera\s+(imobili|madeira|funchal|calheta|portugal)|grupo\s+era|\bera\.pt|"
    r"sotheby|fine\s*&\s*country|\bjll\b|remax|sonasa|casaiberia|poliana|novaco|"
    r"grupo\s+imobili|real\s*estate|luxury\s+real|properties\b|"
    # pegadas típicas de anúncio profissional (baixo falso-positivo em pt-PT)
    r"www\.[a-z0-9-]+\.(pt|com)|https?://|"
    r"refer[êe]ncia\s*[:#]|\bref[\.:ºª]\s*\w*\d|"
    r"certificad[oa]\s+energ[ée]tic|classe\s+energ[ée]tic|"
    r"master\s*suite|portf[oó]lio|home\s*staging|"
    r"oportunidade\s+de\s+investimento|rentabilidade|"
    r"financiamento\s+(aprovado|a\s*100|100%)|"
    r"(marque|agende|marcar|agendar)\s+(j[áa]\s+)?(a\s+sua\s+)?(visita|marca[çc][ãa]o))",
    re.IGNORECASE,
)


def _is_foreign(text):
    return bool(text) and len(_NONLATIN_RE.findall(text)) >= 3


def _is_agency(text):
    if not text:
        return False
    if _FSBO_RE.search(text):   # diz explicitamente que é particular → não é agência
        return False
    return bool(_AGENCY_RE.search(text))


# Nome do VENDEDOR com marca de agência (ex.: "ERA Funchal", "RE/MAX ...").
# Um nome de pessoa ("Joao Miguel Sousa") NÃO é apanhado por aqui — esse deteta-se
# pelo nº de anúncios ativos (agentes têm muitos; um particular vende 1 casa).
_AGENCY_NAME_RE = re.compile(
    r"(imobili[aá]ri|re\s*/?\s*max|century\s*21|keller\s*williams|\bera\b|\bkw\b|"
    r"zome|predimed|sotheby|engel|v[öo]lkers|vanguard|properties|real\s*estate|"
    r"consultor|media[çc][ãa]o|realty|\bhomes?\b|luxury)",
    re.IGNORECASE,
)

# limiares para classificar o vendedor como profissional/agente
_SELLER_ACTIVE_THRESHOLD = 3   # nº de anúncios ativos do vendedor (se o actor o der)
_SELLER_BATCH_THRESHOLD = 2    # nº de anúncios do mesmo vendedor na nossa amostra


def _is_agency_seller(name):
    return bool(name) and bool(_AGENCY_NAME_RE.search(name))


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

    url = it.get("itemUrl") or it.get("listingUrl") or it.get("url")
    if not url and it.get("id"):
        url = f"https://www.facebook.com/marketplace/item/{it['id']}/"
    fid = it.get("id") or it.get("listingId")
    if not fid and url:
        mm = re.search(r"/item/(\d+)", url)
        fid = mm.group(1) if mm else url

    # ---- Vendedor (só alguns actores o trazem, ex.: curious_coder) ----
    sel = it.get("marketplace_listing_seller") or it.get("seller") or {}
    if isinstance(sel, dict):
        seller_name = sel.get("name") or sel.get("sellerName") or ""
        prof = sel.get("marketplace_user_profile") or {}
        seller_id = str(sel.get("id") or sel.get("user_id")
                        or (prof.get("id") if isinstance(prof, dict) else "") or "")
        seller_active = (sel.get("active_listings_count")
                         or (prof.get("active_listings_count") if isinstance(prof, dict) else None))
    else:
        seller_name, seller_id, seller_active = (str(sel) if sel else ""), "", None
    seller_active = seller_active or it.get("sellerActiveListings") or it.get("active_listings_count")

    return {
        "id": str(fid),
        "url": url,
        "titulo": titulo,
        "preco": preco,
        "localidade": city,
        "telefone": tel,
        "tipo_anunciante": "particular",  # Marketplace ~ particulares (best-effort)
        "fotos": fotos,
        "_text": f"{titulo}\n{desc or ''}",  # usado só para filtrar (removido depois)
        "_seller_name": seller_name,
        "_seller_id": seller_id,
        "_seller_active": seller_active if isinstance(seller_active, int) else None,
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

    so_part = bool(params.get("so_particulares", True))
    recs, seen = [], set()
    n_foreign = n_agency = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        rec = _map(it)
        texto = rec.pop("_text", "")
        if not rec.get("url") or rec["id"] in seen:
            continue
        # falso positivo 1: anúncio estrangeiro (não é da Madeira)
        if _is_foreign(texto):
            n_foreign += 1
            continue
        # falso positivo 2: agência / consultor imobiliário (não é particular)
        if _is_agency(texto):
            rec["tipo_anunciante"] = "profissional"
            if so_part:
                n_agency += 1
                continue
        seen.add(rec["id"])
        rec["fonte"] = "Facebook Marketplace"
        rec["_src_key"] = "facebook"
        rec.setdefault("regiao", params.get("regiao", "madeira"))
        rec.setdefault("categoria", params.get("categoria", "moradias"))
        recs.append(rec)

    # ---- Filtro por VENDEDOR (quando o actor traz o vendedor, ex.: curious_coder) ----
    recs, n_seller = _apply_seller_filter(recs, so_part)

    com_tel = sum(1 for r in recs if r.get("telefone"))
    extra = f" + {n_seller} agentes (perfil/vendedor)" if n_seller else ""
    log(f"[facebook] {len(recs)} anúncios de particulares, {com_tel} com telefone "
        f"no texto; filtrados {n_foreign} estrangeiros + {n_agency} agências (texto){extra}.")
    return recs


def _apply_seller_filter(recs, so_part):
    """Descarta agentes por VENDEDOR: (a) nome/marca de agência, (b) o actor
    reporta muitos anúncios ativos, ou (c) o mesmo vendedor aparece com vários
    anúncios na amostra (um particular vende 1 casa; um agente publica muitos).
    Com o actor por defeito (sem vendedor) não remove nada — no-op seguro."""
    from collections import Counter
    id_counts = Counter(r.get("_seller_id") for r in recs if r.get("_seller_id"))
    n_seller, out = 0, []
    for r in recs:
        sname = r.get("_seller_name") or ""
        sid = r.get("_seller_id") or ""
        sact = r.get("_seller_active")
        agente = (
            _is_agency_seller(sname)
            or (isinstance(sact, int) and sact >= _SELLER_ACTIVE_THRESHOLD)
            or (bool(sid) and id_counts.get(sid, 0) >= _SELLER_BATCH_THRESHOLD)
        )
        for k in ("_seller_name", "_seller_id", "_seller_active"):
            r.pop(k, None)
        if agente and so_part:
            n_seller += 1
            continue
        out.append(r)
    return out, n_seller


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
