#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrapers.py
===========
Motores de scraping para o CustoJusto, com cadeia de *fallback*.

Três motores, por ordem de preferência configurável:
  1) requests  -> HTTP simples (grátis, mas costuma apanhar 403 num site Next.js)
  2) playwright-> navegador real local (grátis, contorna anti-bot, mais lento)
  3) context   -> API hospedada do context.dev (paga, 1 crédito/pedido, muito robusta)

A classe `Scraper` recebe a lista ordenada de motores e, em `fetch(url)`, tenta
cada um até obter HTML. O browser do Playwright é reutilizado entre pedidos
(abre uma vez, fecha em `close()`), por isso usa sempre como context manager:

    with Scraper(["playwright", "context"]) as s:
        html, motor = s.fetch(url)

NOTA SEGURANÇA: a chave do context.dev vem SEMPRE de variável de ambiente
(CONTEXT_DEV_API_KEY). Nunca é embutida no código nem enviada ao frontend.
"""

import os
import sys
import time

import requests

# Reutiliza constantes e cabeçalhos do script original.
from custojusto_leads import BASE, HEADERS

CONTEXT_API_BASE = "https://api.context.dev/v1"


class Scraper:
    """Obtém HTML usando uma cadeia de motores com fallback."""

    def __init__(self, engines=None, country=None, wait_ms=2500,
                 context_key=None, timeout=45):
        # ordem de preferência; remove duplicados mantendo a ordem
        engines = engines or ["requests", "playwright", "context"]
        seen = set()
        self.engines = [e for e in engines if not (e in seen or seen.add(e))]

        self.country = (country or os.environ.get("SCRAPE_COUNTRY", "pt")).lower()
        self.wait_ms = wait_ms
        self.timeout = timeout
        self.context_key = context_key or os.environ.get("CONTEXT_DEV_API_KEY")

        # estado preguiçoso (criado só quando é preciso)
        self._session = None
        self._pw = None
        self._browser = None
        self._pw_context = None

    # -- ciclo de vida -------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._pw_context is not None:
            try:
                self._pw_context.close()
            except Exception:
                pass
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw_context = self._browser = self._pw = None

    # -- API pública ---------------------------------------------------------
    def fetch(self, url):
        """Tenta cada motor por ordem. Devolve (html, nome_do_motor) ou (None, None)."""
        for engine in self.engines:
            try:
                html = self._fetch_one(engine, url)
            except Exception as e:  # nunca deixar um motor rebentar a cadeia
                print(f"  ! motor '{engine}' falhou em {url}: {e}", file=sys.stderr)
                html = None
            if html:
                return html, engine
        return None, None

    def available_engines(self):
        """Motores que estão realmente utilizáveis (ex.: context só com chave)."""
        out = []
        for e in self.engines:
            if e == "context" and not self.context_key:
                continue
            out.append(e)
        return out

    # -- motores -------------------------------------------------------------
    def _fetch_one(self, engine, url):
        if engine == "requests":
            return self._fetch_requests(url)
        if engine == "playwright":
            return self._fetch_playwright(url)
        if engine == "context":
            return self._fetch_context(url)
        raise ValueError(f"motor desconhecido: {engine}")

    def _fetch_requests(self, url):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        r = self._session.get(url, timeout=self.timeout)
        if r.status_code == 200:
            return r.text
        return None

    def _ensure_browser(self):
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._pw_context = self._browser.new_context(
            locale="pt-PT",
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
        )

    def _fetch_playwright(self, url):
        self._ensure_browser()
        page = self._pw_context.new_page()
        try:
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(self.wait_ms)
            return page.content()
        finally:
            page.close()

    def _fetch_context(self, url):
        if not self.context_key:
            return None
        resp = requests.get(
            f"{CONTEXT_API_BASE}/web/scrape/html",
            headers={"Authorization": f"Bearer {self.context_key}"},
            params={
                "url": url,
                "country": self.country,
                "waitForMs": min(self.wait_ms, 30000),
            },
            timeout=self.timeout + 30,
        )
        if resp.status_code != 200:
            print(f"  ! context.dev HTTP {resp.status_code}", file=sys.stderr)
            return None
        data = resp.json()
        return data.get("html")


def polite_sleep_between(lo=1.5, hi=3.5):
    """Pausa educada entre pedidos (importante para não sobrecarregar o site)."""
    import random
    time.sleep(random.uniform(lo, hi))
