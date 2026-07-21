# -*- coding: utf-8 -*-
"""Le ao vivo (sem login) as 2 abas do Sheets que NAO existem na API Leads2b:
  - "Reunioes LaraFY"  (dump de calendario -> reunioes LaraFy)
  - "Entregas mkt"     (entregas de marketing)
Usa o export publico gviz CSV. Se falhar, o build cai no snapshot sheets_data.json.
"""
import csv, io, re, requests
from urllib.parse import quote

SHEET_ID = "17Cto_uHK0UmhmMl79Opl1hhma7kUmnW5sgijC3QlUcI"

def _csv(tab):
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={quote(tab)}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))

def _int(x):
    try: return int(float(str(x).strip()))
    except: return 0

def _prazo_br(s):
    s = str(s or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        d, mo, y = m.groups(); return f"{y}-{int(mo):02d}-{int(d):02d}"
    if re.match(r"\d{4}-\d{2}-\d{2}", s): return s[:10]
    return ""

def reunioes_larafy():
    out = []
    for r in _csv("Reuniões LaraFY"):
        data = str(r.get("data") or "")[:10]
        if not data: continue
        vend = (r.get("vendedor") or "").strip()
        if not vend: continue
        titulo = (r.get("titulo") or "").strip()[:60]
        st = "done" if str(r.get("status") or "").strip().lower() == "confirmed" else "pending"
        out.append({"d": data, "u": vend, "dur": _int(r.get("duracao_min")), "s": st,
                    "sub": titulo, "p": "LaraFy", "o": "Calendar", "cn": titulo,
                    "e": "meeting", "idEnt": (r.get("id") or "").strip()})
    return out

def entregas_mkt():
    out = []
    for e in _csv("Entregas mkt"):
        nome = (e.get("Demanda") or "").strip()[:80]
        if not nome: continue
        out.append({
            "nome": nome, "resp": (e.get("Responsável") or e.get("Responsavel") or "Não atribuído").strip(),
            "tipo": (e.get("Tipo de Demanda") or "Outros").strip(), "marca": (e.get("Marca") or "A definir").strip(),
            "marcaConfirmada": "sim" in str(e.get("Marca etiquetada?") or "").lower(),
            "status": (e.get("Status") or "A definir").strip(), "prazo": _prazo_br(e.get("Prazo")),
            "noPrazo": (e.get("Entregue no Prazo?") or "A confirmar").strip(),
        })
    return out

def carregar():
    """Retorna (reunioesLaraFy, entregasMkt). Lanca excecao se nao conseguir ler."""
    return reunioes_larafy(), entregas_mkt()
