# Dashboard Comercial — deploy pela API do Leads2b (sem n8n)

O dashboard passa a puxar **tudo da API do Leads2b**, montado por um robô grátis no
**GitHub Actions** (roda a cada ~15 min) que gera o `dados.json`. A página (GitHub Pages)
lê esse arquivo e se atualiza sozinha — sem servidor, sem máquina ligada, sem n8n.

## O que vai no repositório (o mesmo do site: `dashboard-comercial-larafy`)

```
index.html                      ← a página (já aponta pra ./dados.json)
dados.json                      ← gerado pelo robô (pode subir 1x vazio; o cron reescreve)
build_dashboard.py              ← monta o dados.json a partir da API
classificador.py                ← IA que separa reunião de cliente × pessoal/interno
sheets_data.json                ← reuniões LaraFy + entregas MKT (o que não vem da API)
.github/workflows/dashboard.yml ← o cron
```

## Passo a passo (uma vez só)

1. **Copie estes arquivos** pra dentro do repo `dashboard-comercial-larafy` (raiz) e dê commit/push.
   - Isso **substitui** o `index.html` atual pelo novo (que lê a API). Guarde o antigo se quiser.

2. **Secrets** (Settings → Secrets and variables → Actions → New repository secret):
   - `LEADS2B_TOKEN` = o token da API (Leads2b → Automação → Integrações → Chave de API).
   - `ANTHROPIC_API_KEY` = *(opcional)* chave da Claude, pra ligar a IA que limpa as reuniões LaraFy.
     Sem ela, o robô usa só a regra (conservador: na dúvida, conta como cliente).

3. **Permissão de escrita do Actions** (Settings → Actions → General → Workflow permissions):
   marque **Read and write permissions** (pro robô conseguir commitar o `dados.json`).

4. **Pages** já está ligado (é o site atual). O `index.html` novo lê `./dados.json` na mesma origem.

5. **Rode na mão a 1ª vez**: aba **Actions → "Atualiza dashboard (Leads2b)" → Run workflow**.
   Em ~1–2 min ele gera o `dados.json` e a página passa a mostrar dado ao vivo (inclui julho).

## O que ainda vem do Sheets (não existe na API)
- **Reuniões LaraFy** (dump de calendário) e **Entregas MKT** estão no `sheets_data.json`.
  Por ora é um snapshot. Pra deixar 100% ao vivo, dá pra ligar a leitura direta da planilha
  depois (precisa compartilhar a aba ou usar uma conta de serviço).

## Ajustes rápidos
- **Frequência**: mude o `cron` no `dashboard.yml` (`*/15` = 15 min; `*/30` = 30 min).
- **Período**: `START_DATE` no workflow (default `2024-11-01` = histórico completo).
- **Custo**: sweep completo leva ~1–3 min por execução; API tem limite de 200 req/min (já tratado).
