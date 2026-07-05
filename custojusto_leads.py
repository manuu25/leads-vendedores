#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
custojusto_leads.py
===================
Captação de leads de VENDEDORES PARTICULARES (FSBO) no CustoJusto.pt.

Pensado para um consultor imobiliário que quer construir uma carteira de
proprietários que estão a vender casa SEM agência (os melhores leads de angariação).

Estratégia em 2 fases:
  1) LISTAGEM  -> percorre as páginas de resultados (filtro ?f=p = particulares)
                  e recolhe o link + id de cada anúncio.
  2) DETALHE   -> visita cada anúncio e extrai preço, tipologia, área, localidade,
                  data, nome do anunciante e contacto (quando disponível),
                  via JSON-LD (schema.org) + __NEXT_DATA__ + meta tags.

Exporta para CSV e XLSX, com deduplicação por id e colunas de rastreabilidade
(fonte_url + data_recolha) — importantes para conformidade RGPD.

------------------------------------------------------------------------------
AVISO LEGAL / RGPD (ler antes de usar):
  - Estás a recolher dados pessoais (nome, telefone) de pessoas singulares na UE.
    O RGPD aplica-se. Tem uma base legal definida para o tratamento e o contacto
    (tipicamente "interesse legítimo", devidamente ponderado e documentado),
    informa o titular dos dados no 1.º contacto e respeita pedidos de oposição.
  - Respeita os Termos de Utilização e o robots.txt do CustoJusto. Este script
    usa ritmo lento e cabeçalhos normais; NÃO contornes proteções anti-bot,
    captchas nem faças scraping massivo. Usa-o de forma proporcional.
  - Guarda sempre a fonte (fonte_url) e a data de recolha de cada lead.
------------------------------------------------------------------------------

Uso rápido:
  python custojusto_leads.py --regiao portugal --categoria moradias --max-paginas 3
  python custojusto_leads.py --regiao lisboa --categoria apartamentos --venda --com-contacto
  python custojusto_leads.py --debug   (despeja a estrutura para afinar campos)

Dependências:
  pip install requests beautifulsoup4 pandas openpyxl lxml
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.custojusto.pt"

# Cabeçalhos de um browser normal. Mantém-te discreto e a ritmo humano.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Padrão de URL de um anúncio: /{regiao}/imobiliario/{categoria}/{slug}-{id}
AD_URL_RE = re.compile(
    r"^/([a-z\-]+)/imobiliario/([a-z\-]+)/([a-z0-9\-]+?)-(\d+)$", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def get(session, url, tries=3):
    """GET com retry simples e pausa aleatória (educada)."""
    for attempt in range(1, tries + 1):
        try:
            r = session.get(url, timeout=25)
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 429):
                print(f"  ! {r.status_code} em {url} — a abrandar…", file=sys.stderr)
                time.sleep(8 * attempt)
            else:
                print(f"  ! HTTP {r.status_code} em {url}", file=sys.stderr)
                return None
        except requests.RequestException as e:
            print(f"  ! erro de rede ({e}) tentativa {attempt}", file=sys.stderr)
            time.sleep(4 * attempt)
    return None


def polite_sleep(lo=1.5, hi=3.5):
    time.sleep(random.uniform(lo, hi))


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def extract_next_data(html):
    """Devolve o JSON do <script id="__NEXT_DATA__"> ou None."""
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def extract_jsonld(soup):
    """Devolve uma lista de objetos JSON-LD (schema.org) presentes na página."""
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            out.extend(data)
        else:
            out.append(data)
    return out


def deep_find_lists(obj, key_hints=("ads", "listings", "items", "results", "docs")):
    """Procura recursivamente listas de anúncios dentro de um JSON (best-effort)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() in key_hints and isinstance(v, list) and v:
                    found.append(v)
                walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(obj)
    return found


# --------------------------------------------------------------------------- #
# Fase 1 — listagem
# --------------------------------------------------------------------------- #
def build_listing_url(regiao, categoria, venda, particular, pagina):
    """
    Constrói a URL de listagem.
      regiao:    portugal | lisboa | porto | faro | madeira | ...
      categoria: moradias | apartamentos | terrenos-quintas | predios | ...
      venda:     True -> só venda (sufixo -venda)
      particular:True -> filtro ?f=p
      pagina:    1..N  -> parâmetro ?o=N
    """
    cat = f"{categoria}-venda" if venda else categoria
    url = f"{BASE}/{regiao}/imobiliario/{cat}"
    params = []
    if particular:
        params.append("f=p")
    if pagina and pagina > 1:
        params.append(f"o={pagina}")
    if params:
        url += "?" + "&".join(params)
    return url


def parse_listing(html):
    """
    Extrai os anúncios de uma página de listagem.
    Estratégia robusta: recolhe todos os <a> cujo href corresponde ao padrão
    de anúncio, ignorando destaques/spotlight (utm_medium=spotlight) e PUB.
    """
    soup = BeautifulSoup(html, "lxml")
    seen = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # ignora anúncios patrocinados/inseridos
        if "utm_medium=spotlight" in href or "utm_source=li" in href:
            continue
        path = href.split("?")[0]
        if not path.startswith("/"):
            # alguns hrefs podem vir absolutos
            if path.startswith(BASE):
                path = path[len(BASE):]
            else:
                continue
        m = AD_URL_RE.match(path)
        if not m:
            continue
        regiao, categoria, slug, ad_id = m.groups()
        if ad_id in seen:
            continue
        titulo = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        seen[ad_id] = {
            "id": ad_id,
            "url": urljoin(BASE, path),
            "regiao": regiao,
            "categoria": categoria,
            "titulo": titulo,
        }
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Fase 2 — detalhe
# --------------------------------------------------------------------------- #
PRICE_RE = re.compile(r"([\d\s.\u00a0]{2,})\s*€")
TIPOLOGIA_RE = re.compile(r"\bT\d{1,2}(?:\+\d)?\b")
AREA_RE = re.compile(r"(\d{1,4}(?:[.,]\d+)?)\s*m²")
PHONE_RE = re.compile(r"(?:\+351\s?)?(?:9\d{2}|2\d{2})[\s.\-]?\d{3}[\s.\-]?\d{3}")


def clean_price(text):
    m = PRICE_RE.search(text)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return int(digits) if digits else None


def parse_detail(html, base_record):
    """Extrai campos do anúncio individual. Combina JSON-LD + __NEXT_DATA__ + texto."""
    rec = dict(base_record)
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    # --- JSON-LD (mais estável p/ preço, nome, localidade) ---
    for obj in extract_jsonld(soup):
        if not isinstance(obj, dict):
            continue
        if obj.get("name") and not rec.get("titulo"):
            rec["titulo"] = obj["name"]
        offers = obj.get("offers") or {}
        if isinstance(offers, dict) and offers.get("price") and not rec.get("preco"):
            try:
                rec["preco"] = int(float(offers["price"]))
            except (ValueError, TypeError):
                pass
        addr = obj.get("address")
        if isinstance(addr, dict):
            loc = addr.get("addressLocality") or addr.get("addressRegion")
            if loc and not rec.get("localidade"):
                rec["localidade"] = loc

    # --- __NEXT_DATA__ (nome do anunciante, telefone, tipo, data) ---
    nxt = extract_next_data(html)
    if nxt:
        flat = json.dumps(nxt, ensure_ascii=False)
        # nome do anunciante (heurística sobre chaves comuns)
        for key in ("advertiserName", "userName", "displayName", "name", "sellerName"):
            m = re.search(rf'"{key}"\s*:\s*"([^"]{{2,60}})"', flat)
            if m and not rec.get("anunciante"):
                rec["anunciante"] = m.group(1)
                break
        # telefone embebido (algumas páginas trazem-no no payload)
        mp = PHONE_RE.search(flat)
        if mp and not rec.get("telefone"):
            rec["telefone"] = re.sub(r"[\s.\-]", "", mp.group(0))
        # tipo de anunciante (particular vs profissional)
        mt = re.search(r'"(?:adType|sellerType|type)"\s*:\s*"([^"]+)"', flat)
        if mt:
            rec["tipo_anunciante"] = mt.group(1)

    # --- Fallbacks a partir do texto visível ---
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

    return rec


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def scrape(args):
    session = make_session()
    captura = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- Fase 1: recolher links de anúncios ----
    todos = {}
    for pagina in range(1, args.max_paginas + 1):
        url = build_listing_url(
            args.regiao, args.categoria, args.venda, not args.incluir_profissionais, pagina
        )
        print(f"[listagem] página {pagina}: {url}")
        html = get(session, url)
        if not html:
            break
        if args.debug and pagina == 1:
            Path("debug_listagem.html").write_text(html, encoding="utf-8")
            print("  (debug) HTML da listagem guardado em debug_listagem.html")
        anuncios = parse_listing(html)
        novos = [a for a in anuncios if a["id"] not in todos]
        for a in novos:
            todos[a["id"]] = a
        print(f"  -> {len(anuncios)} anúncios na página, {len(novos)} novos "
              f"(total {len(todos)})")
        if not novos:
            print("  -> sem novos anúncios; a terminar a paginação.")
            break
        polite_sleep()

    registos = list(todos.values())
    print(f"\n[listagem] {len(registos)} anúncios únicos recolhidos.")

    # ---- Fase 2: visitar cada anúncio (opcional) ----
    if args.com_contacto and registos:
        print("\n[detalhe] a visitar cada anúncio para extrair contacto/detalhes…")
        enriquecidos = []
        for i, rec in enumerate(registos, 1):
            html = get(session, rec["url"])
            if html:
                if args.debug and i == 1:
                    Path("debug_detalhe.html").write_text(html, encoding="utf-8")
                    print("  (debug) HTML do 1.º detalhe guardado em debug_detalhe.html")
                rec = parse_detail(html, rec)
            rec["fonte_url"] = rec["url"]
            rec["data_recolha"] = captura
            enriquecidos.append(rec)
            tel = rec.get("telefone", "—")
            print(f"  [{i}/{len(registos)}] {rec.get('titulo','')[:45]:45} tel:{tel}")
            polite_sleep()
        registos = enriquecidos
    else:
        for rec in registos:
            rec["fonte_url"] = rec["url"]
            rec["data_recolha"] = captura

    return registos


def exportar(registos, prefixo):
    if not registos:
        print("\nSem registos para exportar.")
        return
    colunas = [
        "id", "titulo", "tipologia", "area_m2", "preco", "localidade", "regiao",
        "categoria", "anunciante", "telefone", "tipo_anunciante",
        "fonte_url", "data_recolha", "url",
    ]
    df = pd.DataFrame(registos)
    for c in colunas:
        if c not in df.columns:
            df[c] = None
    df = df[colunas].drop_duplicates(subset="id")

    csv_path = f"{prefixo}.csv"
    xlsx_path = f"{prefixo}.xlsx"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    try:
        df.to_excel(xlsx_path, index=False)
        print(f"\n✓ Exportado: {csv_path} e {xlsx_path} ({len(df)} leads)")
    except Exception as e:
        print(f"\n✓ Exportado: {csv_path} ({len(df)} leads). XLSX falhou: {e}")


def main():
    p = argparse.ArgumentParser(
        description="Captação de leads de vendedores particulares (FSBO) no CustoJusto.pt"
    )
    p.add_argument("--regiao", default="portugal",
                   help="portugal | lisboa | porto | faro | madeira | braga | ...")
    p.add_argument("--categoria", default="moradias",
                   help="moradias | apartamentos | terrenos-quintas | predios | ...")
    p.add_argument("--venda", action="store_true", default=True,
                   help="apenas anúncios de venda (ativo por defeito)")
    p.add_argument("--max-paginas", type=int, default=3,
                   help="número máximo de páginas de listagem a percorrer")
    p.add_argument("--com-contacto", action="store_true",
                   help="visita cada anúncio para extrair contacto/detalhes (mais lento)")
    p.add_argument("--incluir-profissionais", action="store_true",
                   help="NÃO filtrar por particulares (inclui agências)")
    p.add_argument("--out", default=None, help="prefixo dos ficheiros de saída")
    p.add_argument("--debug", action="store_true",
                   help="guarda HTML em disco para afinares os seletores")
    args = p.parse_args()

    if not args.out:
        args.out = f"leads_{args.regiao}_{args.categoria}_{datetime.now():%Y%m%d}"

    print("=" * 70)
    print("CustoJusto — Captação de leads de vendedores particulares (FSBO)")
    print(f"Região: {args.regiao} | Categoria: {args.categoria} | "
          f"Particulares: {not args.incluir_profissionais}")
    print("=" * 70)

    registos = scrape(args)
    exportar(registos, args.out)

    print("\nLembrete RGPD: cada lead inclui fonte_url + data_recolha. Define a base "
          "legal do tratamento e informa o titular no 1.º contacto.")


if __name__ == "__main__":
    main()
