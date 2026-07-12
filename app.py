#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py
======
Dashboard web local (FastAPI) para captação de leads de vendedores
particulares (FSBO) no CustoJusto.pt.

Fluxo:
  1) O frontend envia os filtros (região, categoria, páginas, motores, IA).
  2) O backend faz o scraping (fase listagem + fase detalhe opcional) usando a
     cadeia de motores escolhida (requests -> playwright -> context.dev).
  3) Os detalhes são extraídos por JSON-LD/__NEXT_DATA__/regex e, se ativado,
     completados com IA (OpenRouter).
  4) Devolve a tabela de leads ao frontend e permite exportar CSV/XLSX.

As chaves (context.dev, OpenRouter) vêm do .env e NUNCA são enviadas ao browser.

Arrancar:
  uvicorn app:app --reload --port 8000
  (depois abre http://localhost:8000)
"""

import asyncio
import io
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from starlette.requests import Request

load_dotenv()  # carrega o .env ANTES de ler as chaves

from scrapers import Scraper, polite_sleep_between  # noqa: E402
from sources import get_sources, collect_pages, all_sources  # noqa: E402
import extractor  # noqa: E402
import store  # noqa: E402  (persistência dos leads)

app = FastAPI(title="CustoJusto — Leads FSBO")

# CORS — permite que o frontend Astro (porta 4321) chame esta API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4321", "http://127.0.0.1:4321",
        "http://localhost:4322", "http://127.0.0.1:4322",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Auth opcional (para deploy público) ----------------------------------- #
# Se APP_PASSWORD estiver definido (ex.: no painel do Railway/Render), TUDO
# menos /api/status exige HTTP Basic Auth. Protege os leads (dados pessoais —
# RGPD) e evita que estranhos disparem scrapes e gastem os teus créditos Apify.
# Em local, sem APP_PASSWORD, fica aberto.
_APP_USER = os.environ.get("APP_USER", "admin")
_APP_PASSWORD = os.environ.get("APP_PASSWORD")
# Rotas públicas (nunca pedem login): health-check e a página de captação de
# vendedores (que é para partilhar com o público) + o seu endpoint.
_PUBLICO = {"/api/status", "/vender", "/api/lead-form"}


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    if _APP_PASSWORD and request.url.path not in _PUBLICO:
        import base64
        import secrets
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok = (secrets.compare_digest(user, _APP_USER)
                      and secrets.compare_digest(pw, _APP_PASSWORD))
            except Exception:
                ok = False
        if not ok:
            return Response("Autenticação necessária.", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Leads FSBO"'})
    return await call_next(request)


INDEX_HTML = Path(__file__).parent / "templates" / "index.html"

COLUNAS = [
    "id", "fonte", "titulo", "tipologia", "area_m2", "preco", "localidade", "regiao",
    "categoria", "anunciante", "telefone", "tipo_anunciante", "fotos",
    "fonte_url", "data_recolha", "url",
    # ciclo de vida do anúncio (para deteção de retirados / off-market)
    "estado", "primeira_vez", "ultima_vez", "retirado_em",
]

# Um anúncio conta como RETIRADO (off-market) se deixou de aparecer na fonte
# durante pelo menos estas horas, apesar de a fonte ter continuado a ser recolhida.
# Alto o suficiente para absorver a amostragem do Facebook (que roda os anúncios).
_RETIRADO_HORAS = float(os.environ.get("RETIRADO_HORAS", "36"))

# Guarda o último resultado em memória para os botões de exportação (app local,
# um só utilizador). Não é uma base de dados — é só o último scrape.
_LAST = {"rows": [], "lock": threading.Lock()}

# Acumulador do modo "scraping contínuo": junta leads ao longo do tempo,
# deduplicados por (fonte, id). Ao contrário de _LAST, CRESCE a cada ciclo.
_AUTO = {
    "running": False, "thread": None, "leads": {}, "log": [],
    "cycles": 0, "started": None, "lock": threading.Lock(),
    "blocked": set(),   # chaves descartadas manualmente (não voltam nos ciclos)
}

# --- Persistência: carrega o que já havia (sobrevive a reinícios/redeploys) ---
try:
    store.init_db()
    _AUTO["leads"] = store.load_leads()
    _AUTO["blocked"] = store.load_blocked()
    print(f"[store] {store.backend()}: {len(_AUTO['leads'])} leads + "
          f"{len(_AUTO['blocked'])} bloqueados carregados.")
except Exception as _e:  # noqa: BLE001
    print("[store] persistência indisponível:", _e)


def _persist():
    """Grava o estado atual (defensivo — nunca rebenta o scraping)."""
    try:
        store.save_leads(_AUTO["leads"])
        store.save_blocked(_AUTO["blocked"])
    except Exception as e:  # noqa: BLE001
        print("[store] persist falhou:", e)


def _is_madeira_regiao(regiao):
    r = (regiao or "").strip().lower()
    return "madeira" in r or "funchal" in r


def _mainland_landline(tel):
    """True se o telefone é um fixo GEOGRÁFICO do continente (indicativo 2xx que
    não 291). Fixos da Madeira/Porto Santo = 291. Móveis (9x) → False (não dá
    para saber a região). Serve para tirar cross-posts do continente mal-postos
    sob a Madeira no CustoJusto."""
    if not tel:
        return False
    d = re.sub(r"\D", "", str(tel))
    if d.startswith("351"):
        d = d[3:]
    return bool(re.match(r"^2(?!91)\d{7,8}$", d))


def _horas_desde(ts, agora):
    """Horas decorridas desde o timestamp (string) ts até `agora` (datetime)."""
    if not ts:
        return None
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return (agora - t).total_seconds() / 3600.0


def _merge_leads(rows):
    """Junta os leads de um ciclo ao acumulador e faz a gestão do CICLO DE VIDA:
    - marca primeira_vez / ultima_vez / visto_contagem de cada anúncio;
    - deteta RETIRADOS (off-market): anúncios que estavam no acumulador, cuja
      fonte foi recolhida neste ciclo, mas que já não aparecem há >= _RETIRADO_HORAS
      → o dono publicou e retirou (não vendeu conosco) = lead off-market (ouro).
    Ignora chaves na blocklist. Devolve (novos, retirados_novos)."""
    agora = datetime.now()
    agora_s = agora.strftime("%Y-%m-%d %H:%M:%S")
    vistos = {f"{r.get('fonte')}|{r.get('id')}" for r in rows}
    fontes_vistas = {r.get("fonte") for r in rows}

    novos = retirados_novos = 0
    with _AUTO["lock"]:
        # 1) vistos neste ciclo → ativos, atualiza timestamps
        for r in rows:
            key = f"{r.get('fonte')}|{r.get('id')}"
            if key in _AUTO["blocked"]:
                continue
            antigo = _AUTO["leads"].get(key)
            r["primeira_vez"] = (antigo or {}).get("primeira_vez") or agora_s
            r["visto_contagem"] = (antigo or {}).get("visto_contagem", 0) + 1
            r["ultima_vez"] = agora_s
            r["estado"] = "ativo"
            r["retirado_em"] = None
            if antigo is None:
                novos += 1
            _AUTO["leads"][key] = r
        # 2) ausentes cuja fonte correu → candidatos a retirado
        for key, rec in _AUTO["leads"].items():
            if key in vistos or rec.get("fonte") not in fontes_vistas:
                continue
            if rec.get("estado") == "retirado":
                continue
            horas = _horas_desde(rec.get("ultima_vez"), agora)
            if horas is not None and horas >= _RETIRADO_HORAS:
                rec["estado"] = "retirado"
                rec["retirado_em"] = agora_s
                retirados_novos += 1
    _persist()   # grava na BD para sobreviver a reinícios
    return novos, retirados_novos


def _auto_worker(params, intervalo_min):
    """Corre o scrape em ciclo, acumulando leads, até ser parado."""
    while _AUTO["running"]:
        try:
            res = run_scrape(params)
            novos, retirados_novos = _merge_leads(res.get("rows", []))
            with _AUTO["lock"]:
                _AUTO["cycles"] += 1
                total = len(_AUTO["leads"])
                n_ret = sum(1 for r in _AUTO["leads"].values() if r.get("estado") == "retirado")
                extra = f", +{retirados_novos} retirados (off-market)" if retirados_novos else ""
                _AUTO["log"] = ([f"Ciclo {_AUTO['cycles']}: +{novos} novos{extra} "
                                 f"(total {total}, {n_ret} off-market)"] + (res.get("log") or []))[:40]
        except Exception as e:
            with _AUTO["lock"]:
                _AUTO["log"] = [f"Ciclo falhou: {e}"] + _AUTO["log"][:39]
        # dorme em passos de 1s para responder depressa ao stop
        for _ in range(max(1, int(intervalo_min * 60))):
            if not _AUTO["running"]:
                break
            time.sleep(1)


# --------------------------------------------------------------------------- #
# Lógica de scraping (síncrona; corre num thread para não bloquear o servidor)
# --------------------------------------------------------------------------- #
def run_scrape(params):
    venda = params.get("venda", True)
    com_contacto = params.get("com_contacto", False)
    usar_ia = params.get("usar_ia", False)
    engines = params.get("engines") or ["requests", "playwright", "context"]
    fontes_keys = params.get("fontes") or ["custojusto"]

    captura = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log = []
    fontes = get_sources(fontes_keys)
    # parâmetros comuns passados a cada fonte
    p = {
        "regiao": params.get("regiao", "portugal"),
        "categoria": params.get("categoria", "moradias"),
        "venda": venda,
        "so_particulares": params.get("so_particulares", True),
        "max_paginas": int(params.get("max_paginas", 3)),
    }

    with Scraper(engines=engines) as scraper:
        usaveis = scraper.available_engines()
        if not usaveis:
            return {"erro": "Nenhum motor disponível (o context.dev precisa de chave).",
                    "rows": [], "log": log}
        log.append(f"Motores ativos: {', '.join(usaveis)}")
        log.append(f"Fontes pedidas: {', '.join(s.label for s in fontes)}")

        def fetch(url):
            return scraper.fetch(url)

        registos = []
        for src in fontes:
            ok, motivo = src.available()
            if not ok:
                log.append(f"═ {src.label}: indisponível — {motivo}")
                continue
            marca = "" if src.verified else f"  ⚠ seletores por afinar ({src.notes})"
            log.append(f"═ {src.label}{marca}")

            # ---- Fase 1: recolher registos base (listagem ou descoberta) ----
            try:
                if src.collect:
                    base = src.collect(p, fetch, log.append)
                else:
                    base = collect_pages(src, p, fetch, log.append, polite_sleep_between)
            except Exception as e:
                log.append(f"[{src.key}] falhou na recolha: {e}")
                continue
            log.append(f"[{src.key}] {len(base)} anúncios recolhidos.")

            # ---- Fase 2: detalhe (opcional; algumas fontes já trazem tudo) ----
            if com_contacto and base and src.needs_detail:
                for rec in base:
                    html, motor = fetch(rec["url"])
                    if html:
                        rec.update(src.parse_detail(html, rec))
                        # IA preenche lacunas (nome/telefone) se ativada
                        if usar_ia and extractor.is_configured() and (
                                not rec.get("telefone") or not rec.get("anunciante")):
                            texto = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
                            extractor.merge_llm(rec, extractor.extract_with_llm(texto))
                    rec["fonte_url"] = rec["url"]
                    rec["data_recolha"] = captura
                    polite_sleep_between()
                log.append(f"[{src.key}] detalhes concluídos.")
            else:
                for rec in base:
                    rec["fonte_url"] = rec["url"]
                    rec["data_recolha"] = captura

            registos.extend(base)

    # Correspondência à Madeira: tira cross-posts do continente que aparecem
    # mal-postos sob a região Madeira (delatados pelo fixo geográfico 2xx≠291).
    if _is_madeira_regiao(p["regiao"]):
        antes = len(registos)
        registos = [r for r in registos if not _mainland_landline(r.get("telefone"))]
        removidos = antes - len(registos)
        if removidos:
            log.append(f"Filtro Madeira: {removidos} anúncio(s) do continente "
                       f"removidos (fixo 2xx≠291).")

    # fotos: lista -> string separada por " | " (para tabela/CSV)
    for rec in registos:
        if isinstance(rec.get("fotos"), list):
            rec["fotos"] = " | ".join(rec["fotos"])

    df = pd.DataFrame(registos)
    for c in COLUNAS:
        if c not in df.columns:
            df[c] = None
    df = df[COLUNAS]
    # dedup: por (fonte, id) e depois por telefone repetido entre fontes
    df = df.drop_duplicates(subset=["fonte", "id"])
    tem_tel = df["telefone"].notna() & (df["telefone"].astype(str).str.len() > 3)
    df = df[~(tem_tel & df.duplicated(subset="telefone", keep="first"))]

    # to_json converte NaN->null e tipos numpy->nativos (o to_dict deixava NaN,
    # que rebenta a serialização JSON do FastAPI). json.loads devolve dicts limpos.
    rows = json.loads(df.to_json(orient="records"))

    with _LAST["lock"]:
        _LAST["rows"] = rows

    return {"rows": rows, "log": log, "total": len(rows)}


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


_VENDER_HTML = Path(__file__).parent / "templates" / "vender.html"


@app.get("/vender", response_class=HTMLResponse)
async def vender_page():
    """Página pública de captação de vendedores (para partilhar em redes/anúncios)."""
    return HTMLResponse(_VENDER_HTML.read_text(encoding="utf-8"))


@app.post("/api/lead-form")
async def lead_form(request: Request):
    """Recebe um pedido de avaliação (dono que QUER vender e ainda não anunciou)
    e guarda-o como lead pré-mercado na BD."""
    data = await request.json()
    nome = (data.get("nome") or "").strip()
    tel = (data.get("telefone") or "").strip()
    if not nome or not tel:
        return JSONResponse(status_code=400, content={"erro": "Nome e telefone são obrigatórios."})
    zona = (data.get("zona") or "").strip()
    tipo = (data.get("tipo") or "").strip()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    titulo = (f"{tipo} em {zona}" if (tipo and zona) else (tipo or zona)) or "Pedido de avaliação"
    rec = {
        "id": "form-" + uuid.uuid4().hex[:12],
        "fonte": "Formulário (quero vender)",
        "titulo": titulo,
        "tipologia": tipo or None,
        "area_m2": None,
        "preco": None,
        "localidade": zona or None,
        "regiao": "madeira",
        "categoria": "captação",
        "anunciante": nome,
        "telefone": tel,
        "tipo_anunciante": "particular",
        "fotos": "",
        "fonte_url": None,
        "data_recolha": agora,
        "url": None,
        "estado": "ativo",
        "primeira_vez": agora,
        "ultima_vez": agora,
        "retirado_em": None,
        "prazo_venda": (data.get("prazo") or "").strip() or None,
        "mensagem": (data.get("mensagem") or "").strip() or None,
    }
    key = f"{rec['fonte']}|{rec['id']}"
    with _AUTO["lock"]:
        _AUTO["leads"][key] = rec
    _persist()
    return {"ok": True}


@app.get("/api/status")
async def status():
    """Diz ao frontend que chaves estão configuradas (sem revelar os valores)."""
    import os
    return {
        "context_dev": bool(os.environ.get("CONTEXT_DEV_API_KEY")),
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "modelo_ia": os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        "search": bool(os.environ.get("SEARCH_API_KEY")),
        "apify": bool(os.environ.get("APIFY_TOKEN")),
        "fontes": [
            {"key": s.key, "label": s.label, "risky": s.risky, "verified": s.verified}
            for s in all_sources()
        ],
    }


@app.post("/api/scrape")
async def api_scrape(request: Request):
    params = await request.json()
    try:
        resultado = await asyncio.to_thread(run_scrape, params)
    except Exception as e:
        return JSONResponse(status_code=500, content={"erro": str(e), "rows": []})
    return resultado


@app.post("/api/auto/start")
async def auto_start(request: Request):
    """Arranca o scraping contínuo (acumula leads em ciclo)."""
    params = await request.json()
    intervalo = float(params.get("intervalo_min", 20) or 20)
    with _AUTO["lock"]:
        if _AUTO["running"]:
            return {"running": True, "msg": "já está a correr"}
        _AUTO["running"] = True
        _AUTO["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _AUTO["cycles"] = 0
        t = threading.Thread(target=_auto_worker, args=(params, intervalo), daemon=True)
        _AUTO["thread"] = t
        t.start()
    return {"running": True, "intervalo_min": intervalo}


@app.post("/api/auto/stop")
async def auto_stop():
    """Pára o scraping contínuo (mantém os leads acumulados)."""
    _AUTO["running"] = False
    return {"running": False}


@app.get("/api/auto/leads")
async def auto_leads():
    """Devolve os leads acumulados + estado do ciclo (para o frontend fazer poll)."""
    with _AUTO["lock"]:
        rows = list(_AUTO["leads"].values())
        status = {"running": _AUTO["running"], "cycles": _AUTO["cycles"],
                  "started": _AUTO["started"], "total": len(rows)}
        log = list(_AUTO["log"])
    with _LAST["lock"]:  # espelha para a exportação funcionar
        _LAST["rows"] = rows
    return {"rows": rows, "status": status, "log": log}


@app.post("/api/auto/remove")
async def auto_remove(request: Request):
    """Descarta leads escolhidos (ex.: agências que passaram o filtro por só
    terem a marca na foto) e impede que voltem nos próximos ciclos."""
    data = await request.json()
    keys = data.get("keys") or []
    with _AUTO["lock"]:
        for k in keys:
            _AUTO["blocked"].add(k)
            _AUTO["leads"].pop(k, None)
        total = len(_AUTO["leads"])
    _persist()   # grava a remoção + blocklist na BD
    return {"removed": len(keys), "total": total}


@app.get("/api/export")
async def export(fmt: str = "csv"):
    with _LAST["lock"]:
        rows = list(_LAST["rows"])
    if not rows:
        return JSONResponse(status_code=400, content={"erro": "Sem dados para exportar. Faz primeiro uma pesquisa."})

    df = pd.DataFrame(rows)
    for c in COLUNAS:          # garante todas as colunas (leads antigos podem não ter estado/…)
        if c not in df.columns:
            df[c] = None
    df = df[COLUNAS]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    nome = f"leads_{stamp}"

    if fmt == "xlsx":
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nome}.xlsx"'},
        )

    # CSV (utf-8-sig para abrir bem no Excel)
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{nome}.csv"'},
    )
