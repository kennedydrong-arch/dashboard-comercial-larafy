# -*- coding: utf-8 -*-
"""Vendas pela fonte real: contratos ASSINADOS no B4 (assinador.somosb4.com.br).
Usado de JUNHO/2026 em diante (antes era PandaDoc -> fica o 'ganho' do CRM).
Cruza cada contrato com a oportunidade do CRM (por nome) p/ recuperar valor/closer/origem.
"""
import os, re, unicodedata, requests

BASE = os.environ.get("B4_BASE", "https://assinador.somosb4.com.br").rstrip("/")

def _concluidos(key):
    H = {"X-Api-Key": key, "Accept": "application/json"}
    out, off = [], 0
    while True:
        d = requests.get(BASE + "/api/documents", headers=H,
                         params={"Status": "Concluded", "Limit": 100, "Offset": off}, timeout=60).json()
        its = d.get("items", []); out += its
        if len(out) >= d.get("totalCount", 0) or not its: break
        off += 100
    return out

def _excl(n):
    n = n.lower()
    return any(x in n for x in ["nda", "teste", "aditivo", "modelo"])

def _cliente(name):
    parts = [p.strip() for p in re.split(r"\s[-–]\s", name)]
    for p in parts:
        if not re.search(r"contrato|autoriza|consultoria|contabil|parceria|estudos", p, re.I):
            return p
    return parts[-1] if parts else name

def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(ltda|me|epp|eireli|sa|s a|advogados|advocacia|sociedade|contadores|associados|"
               r"consultoria|gestao|empresarial|e|de|do|da)\b", " ", s)
    return [t for t in re.sub(r"[^a-z0-9]", " ", s).split() if len(t) > 2]

def _match(cli, opps):
    kc = _norm(cli)
    if not kc: return None
    best = None
    for o in opps:
        ko = _norm(o.get("c"))
        if ko and (set(kc) & set(ko)) and (kc[0] in ko or ko[0] in kc):
            sc = len(set(kc) & set(ko))
            if not best or sc > best[0]: best = (sc, o)
    return best[1] if best else None

def _nome_vendedor(s):
    # "ANDREY IUNSKOVSKI"/"Bárbara Barbosa" -> "Andrey Iunskovski"/"Barbara Barbosa"
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(w.capitalize() for w in s.split())

def _created_by(doc_id, key):
    """Quem criou/enviou o contrato no B4 = o vendedor (fonte confiável)."""
    try:
        H = {"X-Api-Key": key, "Accept": "application/json"}
        det = requests.get(BASE + f"/api/documents/{doc_id}/signatures-details", headers=H, timeout=30).json()
        return _nome_vendedor((det.get("createdBy") or {}).get("name"))
    except Exception:
        return ""

def contratos(key, marca, desde, opps):
    """marca: 'LaraTAX'|'LaraFy'. desde: 'YYYY-MM-DD'. opps: lista compacta do CRM p/ cruzar.
    Retorna [{d, cliente, vendedor(=createdBy do B4), op(=oportunidade casada p/ valor, ou None)}]."""
    if not key: return []
    out = []
    for d in _concluidos(key):
        name = d.get("name") or ""
        dt = str(d.get("updateDate") or "")[:10]
        if not dt or dt < desde or _excl(name): continue
        if marca == "LaraTAX" and "contrato laratax" not in name.lower(): continue
        cli = _cliente(name)
        out.append({"d": dt, "cliente": cli, "vendedor": _created_by(d.get("id"), key), "op": _match(cli, opps)})
    return out
