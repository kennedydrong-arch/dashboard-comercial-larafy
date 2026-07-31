# -*- coding: utf-8 -*-
"""Classifica reunioes LaraFy (dump de calendario) em: cliente / interno / pessoal.
So 'cliente' conta como reuniao no painel.

Estrategia: regra barata resolve a maioria; o que sobra (ambiguo) e deduplicado
e mandado em lote pro Claude. Sem chave, ambiguo vira 'cliente' (conservador).
"""
import re, json, requests

# ─────────────── heuristica ───────────────
_INTERNO = re.compile(r"registro das reuni|follow-?ups? comerciais|atualiza[çc][aã]o de fluxo|"
                      r"\binterno\b|reuni[aã]o interna|equipe|alinhamento|1:1|onboarding|treinamento|"
                      r"\bscrum\b|marketing\s*[&e]\s*vendas|revis[aã]o roteiro|revis[aã]o.*\bppt\b|roteiro e ppt|"
                      r"ajustes crm|caf[eé] com dire[çc][aã]o|technology talk|fotos \(|reserva de sala|"
                      r"reserva reuni|relat[oó]rio|cronograma|criar grupo|contratos? (e termos|oliveto)", re.I)
_PESSOAL = re.compile(r"almo[çc]o|m[eé]dic|dentista|bucomaxil|anivers|niver|p[aá]scoa|feriado|f[eé]rias|"
                      r"folga|carro na oficina|deixar o carro|buscar o carro|tirar os pontos|"
                      r"\boficina\b|deslocamento|<3|studio", re.I)

def heuristica(sub, entity):
    s = (sub or "").strip(); sl = s.lower()
    if not s: return "ambiguo"
    if sl in ("teste", "test") or sl.startswith("teste "): return "descartar"
    # interno/pessoal vem ANTES do "<>" (senao "SCRUM <> Marketing" passava como cliente)
    if _PESSOAL.search(sl): return "pessoal"
    if _INTERNO.search(sl): return "interno"
    if str(entity) == "OPPORTUNITY": return "cliente"
    if "<>" in s or "&lt;>" in s: return "cliente"          # padrao "Empresa <> LaraFy"
    return "ambiguo"

# ─────────────── IA (Claude) para os ambiguos ───────────────
def _claude_classifica(subjects, api_key):
    """Recebe lista de assuntos unicos, devolve dict assunto->label."""
    modelo = "claude-haiku-4-5-20251001"
    lista = "\n".join(f"{i+1}. {s}" for i, s in enumerate(subjects))
    prompt = (
        "Voce classifica titulos de eventos de agenda de um time comercial (LaraFy).\n"
        "Para cada titulo, responda UMA categoria:\n"
        "- cliente  = reuniao/ligacao/follow-up/apresentacao com cliente ou prospect\n"
        "- interno  = reuniao interna, tarefa, relatorio, evento da empresa\n"
        "- pessoal  = compromisso pessoal (medico, carro, feriado, aniversario)\n\n"
        f"Titulos:\n{lista}\n\n"
        "Responda SO um array JSON de strings, na ordem, ex.: [\"cliente\",\"pessoal\",...]"
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": modelo, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
        timeout=90,
    )
    r.raise_for_status()
    txt = r.json()["content"][0]["text"]
    m = re.search(r"\[.*\]", txt, re.S)
    labels = json.loads(m.group(0))
    out = {}
    for s, lab in zip(subjects, labels):
        lab = str(lab).lower().strip()
        out[s] = lab if lab in ("cliente", "interno", "pessoal") else "cliente"
    return out

def classificar_reunioes(reunioes, api_key=None):
    """reunioes: lista no formato do painel (usa campos 'sub'/'cn' e 'e').
    Retorna (so as de cliente, resumo)."""
    labels = []
    ambiguos = []
    for r in reunioes:
        lab = heuristica(r.get("sub") or r.get("cn"), r.get("e"))
        labels.append(lab)
        if lab == "ambiguo":
            ambiguos.append((r.get("sub") or r.get("cn") or "").strip())

    # dedup + IA
    resolvido = {}
    unicos = sorted({a for a in ambiguos if a})
    if unicos and api_key:
        try:
            # lotes de 80
            for i in range(0, len(unicos), 80):
                resolvido.update(_claude_classifica(unicos[i:i+80], api_key))
        except Exception as e:
            print("[classificador] IA falhou, usando fallback conservador:", str(e)[:120])
    # aplica
    out, resumo = [], {"cliente": 0, "interno": 0, "pessoal": 0, "descartar": 0, "ia": len(unicos) if api_key else 0}
    for r, lab in zip(reunioes, labels):
        if lab == "ambiguo":
            sub = (r.get("sub") or r.get("cn") or "").strip()
            lab = resolvido.get(sub, "cliente")   # sem IA -> conservador: cliente
        resumo[lab] = resumo.get(lab, 0) + 1
        if lab == "cliente":
            out.append(r)
    return out, resumo
