# Deploy — leads-vendedores (Railway ou Render)

Este app **não pode correr no Vercel** (é serverless; não suporta Playwright,
scraping contínuo em segundo plano, nem estado em memória). Usa um **servidor
real** — Railway ou Render. O Docker aqui serve para os dois.

O backend FastAPI serve o próprio painel em `/`, portanto **não** precisas do
frontend Astro em produção — é uma só coisa a correr.

---

## 0) Pré-requisito: repositório Git no GitHub

O Railway e o Render fazem deploy a partir do GitHub.

```bash
git init
git add .
git commit -m "Leads FSBO multicanal + scraping contínuo"
# cria um repo no github.com e depois:
git remote add origin https://github.com/<o-teu-user>/leads-vendedores.git
git branch -M main
git push -u origin main
```

O `.gitignore` já exclui o `.env` (segredos) e o `node_modules`. **Confirma que o
`.env` NÃO foi para o commit** (`git status` não o deve listar).

---

## 1a) Opção Railway (mais simples)

1. https://railway.app → **New Project → Deploy from GitHub repo** → escolhe o repo.
2. O Railway deteta o `Dockerfile` e o `railway.json` e faz build.
3. **Variables** → adiciona as env vars (ver lista abaixo).
4. **Settings → Networking → Generate Domain** → obténs o URL público.
5. (Nome) Settings → o serviço pode chamar-se `leads-vendedores`; o subdomínio
   sai tipo `leads-vendedores-production.up.railway.app`.

## 1b) Opção Render

1. https://render.com → **New + → Blueprint** → aponta ao repo (usa o `render.yaml`).
   (ou **New + → Web Service → Docker** e configura à mão)
2. Preenche as env vars marcadas `sync: false` no painel.
3. Deploy. O URL sai tipo `leads-vendedores.onrender.com`.
4. ⚠️ Na tier **free** o serviço adormece após inatividade e pode fazer **OOM**
   se ativares o Playwright — vê a nota de RAM abaixo.

---

## 2) Env vars a definir no painel (copia os valores do teu `.env`)

| Variável | Obrigatória? | Notas |
|---|---|---|
| `APP_PASSWORD` | **sim (deploy público)** | Protege o site com HTTP Basic Auth (user = `APP_USER`, default `admin`). Sem isto, qualquer um vê os leads e gasta os teus créditos. |
| `APP_USER` | não | Utilizador do login (default `admin`). |
| `CONTEXT_DEV_API_KEY` | **sim** | No servidor (sem Playwright) é o motor que traz CustoJusto/OLX. |
| `APIFY_TOKEN` | **sim** | Facebook Marketplace + Idealista. |
| `APIFY_FB_LOCATION` | não | ID de zona do Marketplace (default Funchal `110189845667755`). |
| `APIFY_FB_MAX`, `APIFY_FB_DETAILS` | não | Teto de itens / trazer descrição+fotos. |
| `APIFY_IDEALISTA_ACTOR`, `APIFY_IDEALISTA_MAX` | não | Só se usares Idealista (agências). |
| `SCRAPE_COUNTRY` | não | `pt`. |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | não | Extração com IA (opcional). |
| `SEARCH_API_KEY` | não | Descoberta via Google (opcional). |

**Nunca** ponhas segredos no repo — só no painel do Railway/Render.

---

## 3) Notas importantes

- **Motores no servidor.** A imagem NÃO instala o Chromium (evita OOM). O motor
  `playwright` falha em silêncio e usa-se **context.dev** (CustoJusto/OLX) +
  **Apify** (Facebook/Idealista). Para ativar o Playwright real, descomenta a
  linha `RUN playwright install --with-deps chromium` no `Dockerfile` e usa uma
  instância com **≥ 2 GB RAM** (não cabe na free).
- **Custo do scraping contínuo.** Cada ciclo com Facebook/Idealista gasta
  créditos Apify, e context.dev gasta créditos por pedido. Num site 24/7 isto
  soma — usa intervalo alto (30–60 min) e liga o contínuo só quando precisas.
- **RGPD.** Recolhes dados pessoais (nome/telefone). O site fica atrás de
  password (`APP_PASSWORD`) e cada lead guarda fonte + data de recolha. Define a
  base legal do tratamento e informa o titular no 1.º contacto.
- **Estado em memória.** Os leads acumulados vivem na RAM do processo; se o
  serviço reiniciar, recomeça do zero (exporta o CSV para guardar). Para
  persistência real, seria preciso uma base de dados (passo futuro).
