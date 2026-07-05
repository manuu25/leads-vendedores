# Imagem para Railway/Render — servidor FastAPI (dashboard + API + scraping
# contínuo). O backend serve o próprio painel em "/", por isso NÃO é preciso o
# frontend Astro em produção.
FROM python:3.12-slim

WORKDIR /app

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright REAL (motor "playwright") — DESLIGADO por defeito porque o Chromium
# torna a imagem ~1 GB maior e precisa de instância com +1 GB de RAM (as tiers
# grátis costumam fazer OOM). Sem isto, o motor playwright falha em silêncio e
# o scraping usa context.dev (CustoJusto/OLX) + Apify (Facebook/Idealista).
# Para o ativar, descomenta a linha seguinte e usa uma instância maior:
# RUN playwright install --with-deps chromium

# Código da app
COPY . .

ENV PORT=8000
EXPOSE 8000

# Railway/Render injetam a variável PORT.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
