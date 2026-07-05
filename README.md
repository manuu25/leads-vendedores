# CustoJusto — Leads de Vendedores Particulares (FSBO)

Painel web local + scraper para encontrar proprietários a vender casa **sem agência**
no CustoJusto.pt. Pesquisa por região/categoria, extrai contacto/detalhes e exporta CSV/Excel.

## Arquitetura

- **Backend (Python / FastAPI)** — faz o scraping (Playwright + context.dev) e a extração
  (JSON-LD + `__NEXT_DATA__` + IA). Expõe a API em `http://localhost:8000`.
- **Frontend (Astro)** — interface bonita em `frontend/`, corre em `http://localhost:4321`
  e consome a API do backend. (Existe também uma página simples servida pelo próprio
  backend em `http://localhost:8000`, como alternativa sem Node.)

São **dois processos**: arranca os dois.

> **Atalho (Windows):** depois da instalação inicial, basta fazer **duplo clique em
> `start.bat`** — abre o backend e o frontend e o browser automaticamente.

## Como arrancar

```bash
# --- UMA VEZ: backend ---
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env        # Windows  (cp no Mac/Linux) — preenche as chaves

# --- UMA VEZ: frontend ---
cd frontend
npm install
cd ..

# --- SEMPRE: arrancar os dois (em terminais separados) ---
# Terminal 1 — backend (API)
python -m uvicorn app:app --port 8000

# Terminal 2 — frontend Astro
cd frontend && npm run dev
#   Abre http://localhost:4321 no browser
#   (parar o Astro: cd frontend && npx astro dev stop)
```

> **Sem chaves também funciona:** o motor **Playwright** (navegador local) não precisa
> de nenhuma chave. As chaves só são necessárias para o **context.dev** (motor pago) e
> para a **extração com IA** (OpenRouter).
>
> Se mudares a porta do backend, ajusta `PUBLIC_API_BASE` em `frontend/.env`
> (copia de `frontend/.env.example`).

## Chaves (ficheiro `.env`)

| Variável | Para quê | Onde obter |
|---|---|---|
| `CONTEXT_DEV_API_KEY` | Motor de scraping hospedado (fallback robusto) | https://www.context.dev/ |
| `OPENROUTER_API_KEY`  | Extrair nome/telefone com IA | https://openrouter.ai/keys |
| `OPENROUTER_MODEL`    | Modelo da IA (default `openai/gpt-4o-mini`) | — |

⚠️ **Segurança:** o `.env` está no `.gitignore` e **nunca** deve ser partilhado nem
versionado. As chaves vivem só no backend — nunca são enviadas para o browser.
Se alguma vez expuseres uma chave, **revoga-a e gera uma nova**.

## Como funciona

- **Motores de scraping** (escolhes no painel, usados por ordem com *fallback*):
  `requests` → `Playwright` → `context.dev`. Se um falha (ex.: 403), tenta o seguinte.
- **Extração de campos:** primeiro determinística (JSON-LD + `__NEXT_DATA__` + regex,
  grátis); se ativares a IA, esta **preenche as lacunas** (tipicamente nome e telefone).
- **Exportação:** botões CSV e Excel descarregam o último resultado.

## Linha de comandos (alternativa ao painel)

O scraper original continua disponível:

```bash
python custojusto_leads.py --regiao lisboa --categoria apartamentos --com-contacto
```

## ⚖️ RGPD

Recolhes dados pessoais (nome, telefone) de pessoas singulares na UE. Cada lead inclui
`fonte_url` + `data_recolha`. Define a base legal do tratamento (tipicamente interesse
legítimo, documentado), informa o titular no 1.º contacto e respeita pedidos de oposição.
Usa de forma proporcional e respeita os Termos do CustoJusto — sem scraping massivo.
