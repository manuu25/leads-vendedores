#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractor.py
============
Extração de campos do anúncio com IA, via OpenRouter (gateway de LLMs).

Estratégia: a extração determinística (JSON-LD + __NEXT_DATA__ + regex) do
`custojusto_leads.parse_detail` continua a ser a primeira fonte — é grátis e
fiável para preço/localidade. A IA serve para PREENCHER LACUNAS (tipicamente
nome do anunciante e telefone, que variam muito de página para página).

A chave da OpenRouter vem SEMPRE da variável de ambiente OPENROUTER_API_KEY.
"""

import json
import os
import sys

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Campos que queremos que a IA devolva.
CAMPOS = [
    "titulo", "tipologia", "area_m2", "preco",
    "localidade", "anunciante", "telefone", "tipo_anunciante",
]

SYSTEM_PROMPT = (
    "És um extrator de dados de anúncios imobiliários portugueses do CustoJusto. "
    "Recebes o texto de um anúncio e devolves SÓ um objeto JSON com estes campos: "
    + ", ".join(CAMPOS) + ". "
    "Regras: 'preco' é um número inteiro em euros (sem símbolos nem pontos). "
    "'area_m2' é um número (m²). 'tipologia' é tipo T0/T1/T2/T3... "
    "'tipo_anunciante' é 'particular' ou 'profissional'. "
    "'telefone' são só dígitos (com indicativo se existir). "
    "Se um campo não existir no texto, devolve null. Não inventes nada."
)


def is_configured():
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def extract_with_llm(text, model=None, api_key=None, timeout=60):
    """
    Extrai campos a partir do texto do anúncio. Devolve um dict (só os campos
    encontrados) ou None se a IA não estiver configurada / falhar.
    """
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    model = model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    # Limita o tamanho para poupar tokens (o essencial costuma estar no topo).
    trecho = (text or "")[:12000]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Texto do anúncio:\n\n{trecho}"},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # Cabeçalhos opcionais recomendados pela OpenRouter.
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "CustoJusto Leads",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        print(f"  ! OpenRouter erro de rede: {e}", file=sys.stderr)
        return None

    if r.status_code != 200:
        print(f"  ! OpenRouter HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None

    try:
        content = r.json()["choices"][0]["message"]["content"]
        dados = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"  ! OpenRouter resposta inesperada: {e}", file=sys.stderr)
        return None

    # Mantém só campos conhecidos e não-vazios.
    return {k: dados[k] for k in CAMPOS if dados.get(k) not in (None, "", [])}


def merge_llm(rec, llm_dados):
    """Funde os dados da IA no registo, SÓ onde o registo está vazio."""
    if not llm_dados:
        return rec
    for k, v in llm_dados.items():
        if not rec.get(k):
            rec[k] = v
    return rec
