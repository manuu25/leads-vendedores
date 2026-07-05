import { defineConfig } from 'astro/config';

// Frontend estático que consome a API FastAPI (Python) em http://localhost:8000.
// Podes mudar a base da API com a variável PUBLIC_API_BASE (ficheiro .env do Astro).
export default defineConfig({
  server: { port: 4321, host: true },
});
