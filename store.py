#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py
========
Persistência simples dos leads (e da blocklist) para que NÃO se percam quando o
servidor reinicia / adormece (o plano free do Render tem disco efémero e apaga a
memória a cada redeploy/spin-down).

Backends (escolhido automaticamente):
  - **Postgres** se existir `DATABASE_URL` (postgres://…) — persiste de verdade
    entre reinícios/redeploys/sleep. É o que se usa em produção (Render/Neon).
  - **SQLite** (ficheiro `DB_PATH`, por omissão `leads.db`) caso contrário — bom
    para correr localmente. No Render free o ficheiro é efémero (não sobrevive a
    redeploys), por isso EM PRODUÇÃO usa-se sempre `DATABASE_URL`.

Modelo: uma tabela chave→valor (`kv`), guardamos o dicionário de leads e a lista
de bloqueados como JSON. Simples e suficiente (centenas de leads). Todas as
operações são defensivas: se a BD falhar, regista e segue — nunca rebenta o
scraping.
"""

import json
import os
import threading

_LOCK = threading.Lock()
_URL = os.environ.get("DATABASE_URL", "").strip()
_IS_PG = _URL.startswith("postgres")

# Tabela com nome próprio: a BD pode ser partilhada com outro projeto (o plano
# free do Render só permite 1 BD), por isso NUNCA usamos um nome genérico.
_TABLE = "fsbo_leads_kv"

if _IS_PG:
    import psycopg2  # noqa: E402  (só quando há Postgres)

    # Render/Heroku usam por vezes o prefixo antigo "postgres://"
    if _URL.startswith("postgres://"):
        _URL = "postgresql://" + _URL[len("postgres://"):]
    # ligação externa (cross-region) exige SSL
    if "sslmode=" not in _URL:
        _URL += ("&" if "?" in _URL else "?") + "sslmode=require"

    def _connect():
        return psycopg2.connect(_URL, connect_timeout=15)

    _PH = "%s"
else:
    import sqlite3  # noqa: E402

    _DB_PATH = os.environ.get("DB_PATH", "leads.db")

    def _connect():
        return sqlite3.connect(_DB_PATH, timeout=10)

    _PH = "?"


def backend():
    return "postgres" if _IS_PG else f"sqlite:{os.environ.get('DB_PATH', 'leads.db')}"


def init_db():
    """Cria a tabela se não existir. Chamar uma vez no arranque."""
    try:
        with _LOCK:
            c = _connect()
            try:
                cur = c.cursor()
                cur.execute(f"CREATE TABLE IF NOT EXISTS {_TABLE} (k TEXT PRIMARY KEY, v TEXT)")
                c.commit()
            finally:
                c.close()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[store] init_db falhou ({backend()}): {e}")
        return False


def _set(k, v):
    try:
        with _LOCK:
            c = _connect()
            try:
                cur = c.cursor()
                cur.execute(
                    f"INSERT INTO {_TABLE} (k, v) VALUES ({_PH}, {_PH}) "
                    f"ON CONFLICT (k) DO UPDATE SET v = excluded.v",
                    (k, v),
                )
                c.commit()
            finally:
                c.close()
    except Exception as e:  # noqa: BLE001
        print(f"[store] set '{k}' falhou: {e}")


def _get(k):
    try:
        with _LOCK:
            c = _connect()
            try:
                cur = c.cursor()
                cur.execute(f"SELECT v FROM {_TABLE} WHERE k = {_PH}", (k,))
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                c.close()
    except Exception as e:  # noqa: BLE001
        print(f"[store] get '{k}' falhou: {e}")
        return None


# --------------------------------------------------------------------------- #
# API usada pela app
# --------------------------------------------------------------------------- #
def save_leads(leads_dict):
    """Guarda todo o dicionário de leads {chave: registo}."""
    _set("leads", json.dumps(leads_dict, ensure_ascii=False))


def load_leads():
    v = _get("leads")
    try:
        return json.loads(v) if v else {}
    except (ValueError, TypeError):
        return {}


def save_blocked(blocked_set):
    _set("blocked", json.dumps(sorted(blocked_set)))


def load_blocked():
    v = _get("blocked")
    try:
        return set(json.loads(v)) if v else set()
    except (ValueError, TypeError):
        return set()
