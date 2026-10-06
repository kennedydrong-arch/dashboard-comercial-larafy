# -*- coding: utf-8 -*-
"""Le do PDF do contrato o que o CRM nao tem: mensalidade e taxa de implantacao.

POR QUE ISSO EXISTE
-------------------
O valor que o CRM registra nao e' a mensalidade. Conferindo 10 contratos em que havia
valor no CRM para comparar, em 8 deles o numero era a **taxa de implantacao** — que e'
cobranca UNICA. O painel pegava isso, multiplicava por 12 e chamava de receita anual.
E na maioria das vendas o CRM nao tem valor nenhum (13 de 17 em agosto), porque o
casamento por nome falha.

O contrato tem os dois valores, em frases padronizadas. Ancorar na frase e' confiavel;
pegar "o maior numero do documento" nao e' — numa amostra de 10 isso errou 2, porque o
maior valor as vezes e' a implantacao, as vezes uma multa.

OS TRES PADROES
---------------
  mensalidade : "O valor do pacote [mensal] contratado e' de R$ X"      (32 de 33)
  implantacao : "R$ Y, a titulo de Implantacao"                         (18 de 33)
  recorrencia : contrato mensal diz "as demais venciveis sempre no dia ...";
                o anual diz apenas "a mensalidade devida no dia X", sem as demais.
                Sem essa distincao, o Tower (R$ 38.400/ANO) entraria como mensal e
                sozinho inflaria a receita recorrente em 34%.

CACHE
-----
Cada PDF tem ~240 KB e o build ja leva ~13 min. O resultado fica em
`valores_contratos.json` (commitado pelo workflow): contrato ja lido nao e' baixado de
novo. Por execucao ha um teto de downloads novos (`MAX_POR_BUILD`), entao o cache se
completa em algumas rodadas sem nenhuma delas estourar o tempo.
"""

import io
import json
import os
import re
import time

import requests

BASE = os.environ.get("B4_BASE", "https://assinador.somosb4.com.br").rstrip("/")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "valores_contratos.json")
MAX_POR_BUILD = int(os.environ.get("B4_MAX_PDF", "12"))

_VAL = r"R\$\s?([\d.]+,\d{2})"
RE_PACOTE = re.compile(r"valor\s+do\s+pacote(?:\s+mensal)?\s+contratado\s+(?:e|é)\s*de\s*" + _VAL, re.I)
RE_MODULO = re.compile(r"valor\s+mensal\s+de\s*" + _VAL, re.I)
RE_IMPL = re.compile(_VAL + r"\s*,?\s*a\s+t[ií]tulo\s+de\s+Implanta", re.I)
RE_RECORRE = re.compile(r"demais\s+venc[ií]veis", re.I)


def _num(s):
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def carrega_cache():
    try:
        with io.open(CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def grava_cache(d):
    try:
        with io.open(CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        return True
    except Exception as e:
        print("[b4val] nao gravei o cache:", str(e)[:120])
        return False


def _texto_pdf(doc_id, key, timeout=90, tentativas=3):
    """Baixa o PDF e devolve o texto, ou None. O endpoint e' /content (nao /download)."""
    r = None
    for i in range(tentativas):
        try:
            r = requests.get(BASE + "/api/documents/%s/content" % doc_id,
                             headers={"X-Api-Key": key}, timeout=timeout)
            r.raise_for_status()
            break
        except Exception:
            r = None
            if i < tentativas - 1:
                time.sleep(3 * (i + 1))
    if r is None:
        return None
    dados = r.content or b""
    if dados[:4] != b"%PDF":          # a API responde 200 com JSON de erro em alguns casos
        return None
    texto = None
    try:
        import fitz                   # PyMuPDF, quando disponivel
        doc = fitz.open(stream=dados, filetype="pdf")
        texto = "\n".join(p.get_text() for p in doc)
        doc.close()
    except ImportError:
        try:
            import pypdf
            leitor = pypdf.PdfReader(io.BytesIO(dados))
            texto = "\n".join((p.extract_text() or "") for p in leitor.pages)
        except ImportError:
            print("[b4val] sem pymupdf nem pypdf -> nao leio PDF")
            return None
    except Exception:
        return None
    return _juntar(texto) if texto else None


def _juntar(texto):
    """Desfaz a quebra de LAYOUT do PDF. Um numero pode vir partido em duas linhas
    ("R$ 2.160," + "00"), e sem juntar o padrao nao casa."""
    saida = []
    for linha in texto.split("\n"):
        atual = linha.strip()
        if not atual:
            saida.append("")
            continue
        if saida and saida[-1]:
            ant = saida[-1]
            if ant.endswith("-"):
                saida[-1] = ant[:-1] + atual
                continue
            if (atual[:1].islower() or atual[:1].isdigit()) and not ant.endswith((".", ";", ":")):
                saida[-1] = ant + " " + atual
                continue
        saida.append(atual)
    return "\n".join(saida)


def le_texto(texto):
    """Mensalidade, implantacao e se e' recorrente. So do texto — sem rede."""
    pac = [v for v in (_num(m.group(1)) for m in RE_PACOTE.finditer(texto)) if v]
    mod = [v for v in (_num(m.group(1)) for m in RE_MODULO.finditer(texto)) if v]
    imp = [v for v in (_num(m.group(1)) for m in RE_IMPL.finditer(texto)) if v]
    base = pac[0] if pac else 0.0
    return {
        # mensalidade = pacote + modulos avulsos cobrados por mes (ex.: Retificacao)
        "mensal": round(base + sum(mod), 2) if (pac or mod) else None,
        "implantacao": round(imp[0], 2) if imp else 0.0,
        "recorrente": bool(RE_RECORRE.search(texto)),
    }


def valores(contratos, key, max_novos=None):
    """Para cada contrato [{id, nome}], devolve {id: {mensal, implantacao, recorrente}}.

    Usa o cache e baixa no maximo `max_novos` PDFs nesta execucao. Contrato que nao
    abriu NAO entra no cache — fica para a proxima rodada tentar de novo, senao uma
    falha de rede viraria "sem valor" para sempre.
    """
    cache = carrega_cache()
    teto = MAX_POR_BUILD if max_novos is None else max_novos
    novos = falhas = 0
    for c in contratos:
        cid = c.get("id")
        if not cid or cid in cache:
            continue
        if novos >= teto:
            break
        texto = _texto_pdf(cid, key)
        if texto is None:
            falhas += 1
            continue
        d = le_texto(texto)
        d["doc"] = str(c.get("nome") or "")[:90]      # so para humano conferir o cache
        d["lido_em"] = time.strftime("%Y-%m-%d")
        cache[cid] = d
        novos += 1
    if novos:
        grava_cache(cache)
    faltam = len([c for c in contratos if c.get("id") and c["id"] not in cache])
    print("[b4val] contratos no cache: %d | lidos agora: %d | nao abriram: %d | ainda faltam: %d"
          % (len(cache), novos, falhas, faltam))
    return cache
