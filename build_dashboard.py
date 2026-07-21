# -*- coding: utf-8 -*-
"""Gera o dados.json do Dashboard Comercial LaraFy puxando TUDO da API Leads2b.
Roda no GitHub Actions (cron). Substitui o n8n.

Fontes:
  - API Leads2b: leads, oportunidades (active/won/lost), atividades, reunioes (meet),
                 vendas (won LaraTAX/LaraFY), parceiros (pipeline "Parceiros").
  - sheets_data.json (commitado): reunioes LaraFy (calendario) + entregas MKT.

IA: as reunioes LaraFy (calendario) passam por um classificador
    cliente/interno/pessoal. So 'cliente' conta como reuniao no painel.
    Sem ANTHROPIC_API_KEY, cai na heuristica (conservador: ambiguo=cliente).

Variaveis de ambiente:
  LEADS2B_TOKEN      (obrigatorio)
  LEADS2B_BASE       (opcional, default producao)
  ANTHROPIC_API_KEY  (opcional, liga a IA das reunioes)
  START_DATE         (opcional, default 2024-11-01)
  OUT                (opcional, default dados.json)
"""
import os, io, sys, json, re, datetime as dt, unicodedata
from collections import Counter
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TOKEN = os.environ["LEADS2B_TOKEN"]
BASE = os.environ.get("LEADS2B_BASE", "https://app.leads2b.com/api/v1")
# Janela: START_DATE fixo, ou MONTHS_BACK meses atras (default 12). Full history = pesado.
_START_ENV = os.environ.get("START_DATE")
if _START_ENV:
    START = _START_ENV
else:
    _mb = int(os.environ.get("MONTHS_BACK", "12"))
    START = (dt.date.today() - dt.timedelta(days=_mb * 31)).strftime("%Y-%m-%d")
OUT = os.environ.get("OUT", "dados.json")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
LIMIT = 200
TODAY = dt.date.today()

# ───────────────────────── API helpers ─────────────────────────
import time
_last = [0.0]
def api_get(path, params):
    # throttle: no maximo ~180/min pra nao tomar 429 (limite e 200/min)
    delta = time.time() - _last[0]
    if delta < 0.34: time.sleep(0.34 - delta)
    _last[0] = time.time()
    url = BASE + path
    r = requests.get(url, headers=HEADERS, params=params, timeout=60)
    if r.status_code == 429:      # ainda assim estourou -> espera e repete
        time.sleep(30); r = requests.get(url, headers=HEADERS, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def result_items(d):
    r = d.get("result")
    if isinstance(r, dict):
        k = list(r.keys()); return r.get(k[0]) if k else []
    return r or []

def sweep(path, base_params, id_key, di, df):
    """Coleta recursiva: se a janela estourar (>=limit) ou der erro, divide no meio."""
    out = {}
    a = di.strftime("%Y-%m-%d 00:00:00"); b = df.strftime("%Y-%m-%d 23:59:59")
    p = dict(base_params); p.update(start_at=a, finish_at=b, limit=LIMIT)
    try:
        its = result_items(api_get(path, p))
    except requests.HTTPError:
        its = None
    dias = (df - di).days
    if its is None or (len(its) >= LIMIT and dias >= 1):
        if dias <= 0:
            for h in range(24):
                hp = dict(base_params)
                hp.update(start_at=di.strftime(f"%Y-%m-%d {h:02d}:00:00"),
                          finish_at=di.strftime(f"%Y-%m-%d {h:02d}:59:59"), limit=LIMIT)
                try:
                    for x in result_items(api_get(path, hp)): out[x.get(id_key)] = x
                except requests.HTTPError: pass
            return out
        mid = di + dt.timedelta(days=dias // 2)
        out.update(sweep(path, base_params, id_key, di, mid))
        out.update(sweep(path, base_params, id_key, mid + dt.timedelta(days=1), df))
        return out
    for x in its: out[x.get(id_key)] = x
    return out

def d10(s): return str(s or "")[:10]
def fnum(x):
    try: return float(x)
    except: return 0.0
def is_fy(p): return "larafy" in str(p or "").lower()
def brand_resp(r):
    r = str(r or "").lower()
    return "LaraFy" if any(n in r for n in ["andrey", "kaoana", "kaona"]) else "LaraTAX"

di = dt.datetime.strptime(START, "%Y-%m-%d")
df = dt.datetime(TODAY.year, TODAY.month, TODAY.day)
print(f"[build] janela {START} -> {TODAY}  base={BASE}")

# ───────────────────────── coleta ─────────────────────────
opps_all = {}
for st in ["active", "won", "lost"]:
    got = sweep("/opportunities/list", {"status": st}, "opportunity_id", di, df)
    for k, v in got.items(): v["_st"] = st; opps_all.setdefault(k, v)
    print(f"[build] opps {st}: {len(got)}")
leads_raw = sweep("/leads/list", {}, "lead_id", di, df)
print(f"[build] leads: {len(leads_raw)}")
acts_raw = sweep("/activities/list", {"date_filter_by": "action_date"}, "id", di, df)
print(f"[build] activities: {len(acts_raw)}")

# ───────────────────────── monta blocos ─────────────────────────
stmap = {"won": "Ganha", "lost": "Perdida", "active": "Ativa"}
opps, vendas, vendasLaraFy = [], [], []
parc_raw = {}
for o in opps_all.values():
    pipe = o.get("pipeline") or ""
    st = o.get("_st")
    opps.append({
        "d": d10(o.get("opportunity_created_at")), "s": stmap.get(st, st),
        "step": o.get("pipeline_step") or "", "v": fnum(o.get("opportunity_total_value")),
        "o": o.get("origin") or "", "r": o.get("opportunity_responsible") or "",
        "c": o.get("company_name") or "", "p": pipe, "dias": o.get("days_in_pipeline") or 0,
        "motivo": o.get("loss_reason") or o.get("loss_details") or "", "tags": o.get("tags") or [],
        "leadId": o.get("lead_id"),
    })
    if str(pipe) == "Parceiros":
        parc_raw[o.get("opportunity_id")] = o
    if st == "won":
        if is_fy(pipe):
            vendasLaraFy.append({
                "d": d10(o.get("won_at")), "grupo": o.get("company_name") or "", "pct": None,
                "parceiroNome": "Sem parceria", "parceiroSigla": "", "parceiroNomeCanonico": "Sem parceria",
                "vendedor": o.get("opportunity_responsible") or "", "tipo": "Consultoria",
            })
        elif "laratax" in pipe.lower():
            v = fnum(o.get("opportunity_total_value"))
            vendas.append({
                "d": d10(o.get("won_at")), "cl": o.get("opportunity_responsible") or "", "v": v,
                "va": round(v * 12), "o": o.get("origin") or "", "tl": "", "i": "", "s": "Ativo",
                "c": o.get("company_name") or "", "cnpj": o.get("contact_cnpj") or "",
                "uf": o.get("contact_state") or o.get("customer_state") or "", "pl": "", "df": "",
            })

# parceiros (dedup por 1o token do nome)
parceiros, seen = [], {}
for o in parc_raw.values():
    nome = o.get("company_name") or o.get("opportunity_name") or ""
    key = nome.lower().split()[0] if nome else str(o.get("opportunity_id"))
    if key in seen: continue
    seen[key] = 1
    parceiros.append({
        "sigla": "", "nome": nome, "nomeKey": key, "pct": None, "pctRaw": "Não informado",
        "resp": (o.get("opportunity_responsible") or "").split()[0] if o.get("opportunity_responsible") else "",
        "contato": o.get("contact_phone") or o.get("main_phone") or "",
    })

# leads
leads = []
for l in leads_raw.values():
    opp_st = str(l.get("opportunity_status") or "").lower()
    lead_st = str(l.get("lead_status") or "").lower()
    if "won" in opp_st or "ganh" in lead_st: s = "Lead ganho"
    elif "lost" in opp_st or l.get("lost_at") or "perd" in lead_st: s = "Lead perdido"
    else: s = "Em andamento"
    leads.append({
        "d": d10(l.get("lead_created_at")), "o": l.get("origin") or "", "r": l.get("lead_responsable") or "",
        "uf": l.get("state") or "", "p": l.get("pipeline") or "", "s": s,
        "motivo": l.get("loss_reason") or "", "oppId": l.get("opportunity_id") or "",
        "oppStatus": stmap.get(opp_st, l.get("opportunity_status") or ""), "valor": 0,
    })

# reunioes TAX (API, so nao-LaraFy) + atividades
reunioes, atividades = [], []
for a in acts_raw.values():
    tp = str(a.get("type"))
    if tp == "meet":
        if brand_resp(a.get("deal_responsible") or a.get("user_name_responsible")) == "LaraFy":
            continue  # LaraFy vem do Sheets
        reunioes.append({
            "d": d10(a.get("action_date")), "u": a.get("user_name_responsible") or "", "dur": 30,
            "s": "done" if str(a.get("status")) == "done" else "agendada",
            "sub": a.get("deal_title") or "", "p": "LaraTAX", "o": "Não informado",
            "cn": a.get("deal_title") or "", "e": a.get("entity") or "", "idEnt": str(a.get("id_entity") or ""),
        })
    elif tp == "action":
        atividades.append({
            "d": d10(a.get("action_date")), "u": a.get("user_name_responsible") or "",
            "n": a.get("action_name") or "", "t": "action", "e": bool(a.get("close_date")),
            "p": brand_resp(a.get("deal_responsible") or a.get("user_name_responsible")), "o": "",
            "msg": a.get("message") or "", "ent": a.get("entity") or "", "idEnt": str(a.get("id_entity") or ""),
        })

# ───────────── reunioes LaraFy (Sheets) + IA cliente/interno/pessoal ─────────────
from classificador import classificar_reunioes  # noqa
# fonte das 2 pecas de Sheets: ao vivo (gviz) com fallback pro snapshot
snap = json.load(open("sheets_data.json", encoding="utf-8")) if os.path.exists("sheets_data.json") else {}
reun_fy_raw = snap.get("reunioesLaraFy", [])
entregasMkt_src = snap.get("entregasMkt", [])
try:
    import sheets_live
    rf_live, mkt_live = sheets_live.carregar()
    if len(rf_live) > 10:  # reunioes LaraFy ao vivo
        reun_fy_raw = rf_live
    if len(mkt_live) > 10:  # entregas MKT: gviz as vezes so le 1 linha (formula) -> mantem snapshot
        entregasMkt_src = mkt_live
    print(f"[build] Sheets ao vivo: reunioes LaraFy={len(rf_live)} (usando {len(reun_fy_raw)}), "
          f"MKT ao vivo={len(mkt_live)} (usando {len(entregasMkt_src)})")
except Exception as e:
    print("[build] Sheets ao vivo falhou, usando snapshot:", str(e)[:120])
reun_fy_ok, resumo_ia = classificar_reunioes(reun_fy_raw, os.environ.get("ANTHROPIC_API_KEY"))
reunioes += reun_fy_ok
entregasMkt = entregasMkt_src
print(f"[build] reunioes LaraFy: {len(reun_fy_raw)} -> {len(reun_fy_ok)} cliente | IA: {resumo_ia}")

# ───────────────────────── counts/filtros/meta ─────────────────────────
def uniq_sorted(vals): return sorted({v for v in vals if v})
filtros = {
    "vendedores": uniq_sorted([o["r"] for o in opps] + [l["r"] for l in leads]),
    "origens": uniq_sorted([o["o"] for o in opps] + [l["o"] for l in leads]),
    "vendaStatus": ["Ativo", "Inativo", "Suspenso"],
    "pipelineSteps": uniq_sorted([o["step"] for o in opps]),
    "leadStatus": ["Em andamento", "Lead ganho", "Lead perdido"],
}
counts = {"leads": len(leads), "opps": len(opps), "vendas": len(vendas), "reunioes": len(reunioes),
          "atividades": len(atividades), "parceiros": len(parceiros), "vendasLaraFy": len(vendasLaraFy)}
alld = [x["d"] for x in leads + opps + reunioes + atividades if x.get("d")]
agora = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
DASH = {
    "dataAtualizacao": "atualizado " + agora, "minDate": min(alld) if alld else START,
    "maxDate": max(alld) if alld else str(TODAY), "counts": counts,
    "diagnostico": {k: {"aceitas": counts.get(k, 0)} for k in ["vendas", "leads", "opps", "reunioes", "atividades"]},
    "diagVendedores": {}, "leads": leads, "opps": opps, "vendas": vendas, "reunioes": reunioes,
    "atividades": atividades, "parceiros": parceiros, "vendasLaraFy": vendasLaraFy,
    "entregasMkt": entregasMkt, "filtros": filtros,
}
json.dump(DASH, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print(f"[build] OK -> {OUT}  counts={counts}")
