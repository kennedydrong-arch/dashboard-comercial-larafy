# -*- coding: utf-8 -*-
"""Vendas pela fonte real: contratos ASSINADOS no B4 (assinador.somosb4.com.br).
Usado de JUNHO/2026 em diante (antes era PandaDoc -> fica o 'ganho' do CRM).
Cruza cada contrato com a oportunidade do CRM (por nome) p/ recuperar valor/closer/origem.
"""
import os, io, re, json, unicodedata, datetime as dt, requests

BASE = os.environ.get("B4_BASE", "https://assinador.somosb4.com.br").rstrip("/")

def _get(url, H, params):
    import time as _t
    ult = None
    for tent in range(4):   # B4 às vezes fica lento -> tenta de novo antes de desistir
        try:
            return requests.get(url, headers=H, params=params, timeout=90).json()
        except Exception as e:
            ult = e; _t.sleep(3 * (tent + 1))
    raise ult

_CACHE_DOCS = {}


def _concluidos(key):
    """Todos os documentos concluidos da conta. Guardado em memoria: a mesma listagem
    e' pedida por contratos() e por rescindidos() no mesmo build — sem o cache seriam
    4 paginacoes completas por execucao em vez de 2, e o build ja leva ~7 min."""
    if key in _CACHE_DOCS:
        return _CACHE_DOCS[key]
    H = {"X-Api-Key": key, "Accept": "application/json"}
    out, off = [], 0
    while True:
        d = _get(BASE + "/api/documents", H, {"Status": "Concluded", "Limit": 100, "Offset": off})
        its = d.get("items", []); out += its
        if len(out) >= d.get("totalCount", 0) or not its: break
        off += 100
    _CACHE_DOCS[key] = out
    return out

def _excl(n):
    n = n.lower()
    # NAO contam como venda no painel:
    #  - parceria/parceiro: contrato de parceria, nao de cliente
    #  - rescis/distrato/cancel: CANCELAMENTO (distrato assinado tambem vira doc "concluido" no B4)
    return any(x in n for x in ["nda", "teste", "aditivo", "modelo", "parceria", "parceiro",
                                "rescis", "distrato", "cancelamento", "cancelacao"])

def _cliente(name):
    parts = [p.strip() for p in re.split(r"\s[-–]\s", name)]
    for p in parts:
        if not re.search(r"contrato|autoriza|consultoria|contabil|parceria|estudos", p, re.I):
            return p
    return parts[-1] if parts else name

# Palavras que NAO identificam a empresa: forma juridica + jargao do ramo.
# Duas empresas diferentes dividem "solucoes tributarias" o tempo todo. Se isso
# valesse como prova de identidade, o painel colaria o valor de um cliente no outro
# — e colava: "FS Solucoes Tributarias" casou com "Braga Solucoes Tributarias" e
# herdou R$ 5.580 que nao eram dela.
_GENERICAS = {
    "ltda", "epp", "eireli", "advogados", "advocacia", "sociedade", "contadores",
    "associados", "consultoria", "gestao", "empresarial", "empresa",
    "empresas", "solucoes", "solucao", "tributaria", "tributarias", "tributario",
    "tributarios", "assessoria", "contabil", "contabeis", "contabilidade",
    "recuperacao", "negocios", "servicos", "grupo", "brasil", "inteligencia",
    "auditoria", "treinamentos", "planejamento", "administrativo", "apoio",
    "individual", "intermediacao", "tax", "consult", "fiscal", "fiscais",
}


def _norm(s):
    """Nome da empresa reduzido as palavras que REALMENTE identificam ela."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 2 and t not in _GENERICAS]

def _palavras(s):
    """Nome em palavras simples, sem acento nem pontuacao: "A & S Consult." -> [a, s, consult]"""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return [p for p in re.split(r"[^a-z0-9]+", s) if p]


def _apelido_bate(apelido, nome):
    """O apelido aparece no nome como PALAVRA(S) inteira(s)?

    Comparar por pedaco solto de texto junta cliente errado: o apelido "rose" casaria
    dentro de "Rosemary" e duas empresas diferentes virariam uma so. Aqui "rose" bate
    em "Rose Ltda" e NAO bate em "Rosemary Consultoria".
    """
    a, n = _palavras(apelido), _palavras(nome)
    if not a or len(a) > len(n):
        return False
    return any(n[i:i + len(a)] == a for i in range(len(n) - len(a) + 1))


def grupos_apelidos(caminho=None):
    """Le clientes_apelidos.json: cada linha e' uma lista de jeitos de escrever a
    MESMA empresa (o nome muda entre CRM e B4).

    Caminho fixo ao lado deste arquivo — nao depende de onde o processo foi iniciado.
    Se o arquivo nao existir ou estiver torto, AVISA no log e segue sem apelido: falha
    calada aqui e' pior que erro, porque o build termina "com sucesso" e o operador
    acha que o cadastro dele valeu.
    """
    if caminho is None:
        caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clientes_apelidos.json")
    if not os.path.exists(caminho):
        print("[b4] AVISO: clientes_apelidos.json nao encontrado -> nenhum apelido aplicado")
        return []
    try:
        with io.open(caminho, encoding="utf-8-sig") as f:   # utf-8-sig: aguenta BOM do Bloco de Notas
            d = json.load(f)
        grupos = d.get("mesma_empresa", []) if isinstance(d, dict) else d
        out = [[str(n).strip() for n in g if str(n).strip()]
               for g in grupos if isinstance(g, list)]
    except Exception as e:
        print("[b4] AVISO: clientes_apelidos.json invalido (%s) -> nenhum apelido aplicado" % str(e)[:90])
        return []
    return [g for g in out if g]


def mesmo_cliente(a, b, grupos=()):
    """Mesma empresa? Criterio ESTRITO — usado para DESCONTAR venda (rescisao).

    Aqui um falso positivo apaga a venda de um cliente que nao rescindiu, entao nao
    vale heuristica: ou as palavras que identificam a empresa sao exatamente as
    mesmas, ou os dois nomes estao na mesma linha do clientes_apelidos.json.
    Comparar por "tem palavra em comum" apagaria, por exemplo, tres ANDRE diferentes
    quando um ANDRE rescindisse.
    """
    for g in grupos:
        if any(_apelido_bate(n, a) for n in g) and any(_apelido_bate(n, b) for n in g):
            return True
    ka, kb = set(_norm(a)), set(_norm(b))
    return bool(ka) and ka == kb


def _match(cli, opps, grupos=()):
    """Acha no CRM a oportunidade deste cliente (p/ recuperar valor/origem).

    Ordem: nome exatamente igual > apelido cadastrado > palavras distintivas em comum.
    O desempate por placar precisa ser estavel — antes, empate em 1 palavra era
    decidido pela ordem da lista, entao a mesma venda podia trocar de valor entre um
    build e outro sem nada mudar na fonte.
    """
    kc = _norm(cli)
    # 1) nome identico ganha de qualquer heuristica. Compara o nome INTEIRO (_palavras),
    #    nao so as palavras distintivas: "Capital Consultoria e Gestao Empresarial" e
    #    "Ps Capital" reduzem as duas a {capital} e nao sao a mesma empresa — casar por
    #    ai colava o valor de uma na outra.
    pc = _palavras(cli)
    for o in opps:
        if pc and _palavras(o.get("c")) == pc:
            return o
    # 2) apelido cadastrado na mao: mesma linha do arquivo = mesma empresa.
    #    entre os candidatos do grupo, prefere a oportunidade de maior valor (a de R$ 0
    #    costuma ser cadastro duplicado) e desempata pelo nome, p/ nao variar por ordem.
    for g in grupos:
        if any(_apelido_bate(n, cli) for n in g):
            cands = [o for o in opps if any(_apelido_bate(n, o.get("c")) for n in g)]
            if cands:
                return sorted(cands, key=lambda o: (-float(o.get("v") or 0), str(o.get("c") or "")))[0]
    if not kc: return None
    # 3) palavras distintivas em comum, com desempate estavel
    best = None
    for o in opps:
        ko = _norm(o.get("c"))
        if ko and (set(kc) & set(ko)) and (kc[0] in ko or ko[0] in kc):
            sc = len(set(kc) & set(ko))
            chave = (-sc, str(o.get("c") or ""))
            if best is None or chave < best[0]:
                best = (chave, o)
    return best[1] if best else None

def _nome_vendedor(s):
    # "ANDREY IUNSKOVSKI"/"Bárbara Barbosa" -> "Andrey Iunskovski"/"Barbara Barbosa"
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(w.capitalize() for w in s.split())

def _data_iso(v):
    """So aceita data no formato AAAA-MM-DD. Qualquer outra coisa vira "".

    Sem isso, um epoch ("1750000000") ou "15/06/2026" passariam direto e a comparacao
    de texto dt < desde descartaria TODOS os contratos — o painel zeraria de junho em
    diante sem erro nenhum, so com um numero menor no log.
    """
    s = str(v or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit() \
            and s[5:7].isdigit() and s[8:10].isdigit():
        return s
    return ""


def _detalhe(doc_id, key):
    """Devolve (vendedor, data_da_assinatura, falhou).

    vendedor = quem enviou o contrato (createdBy) — fonte confiavel.
    data     = a ULTIMA assinatura do documento (o contrato so fecha quando todos
               assinam), lida do certificado digital de cada signatario.
    falhou   = True se a CHAMADA nao completou. Diferente de "documento sem
               assinatura legivel": falha de rede nao pode custar a venda inteira.

    NAO usar updateDate como data da venda: e' "ultima vez que o documento foi
    mexido". Renomear, mover de pasta ou manutencao interna do proprio B4 reescreve
    o campo e jogaria a venda para o dia da mexida. Foi exatamente o que aconteceu em
    03/09/2026: o B4 tocou a conta LaraTAX inteira as 12:26:50 e os 28 contratos de
    jun/jul/ago viraram "venda de hoje" no painel.
    """
    H = {"X-Api-Key": key, "Accept": "application/json"}
    try:
        # _get tem 4 tentativas com espera — o B4 fica lento e esta chamada agora
        # decide se a venda entra ou nao, entao precisa da mesma teimosia da listagem.
        det = _get(BASE + "/api/documents/%s/signatures-details" % doc_id, H, None)
    except Exception:
        return "", "", True
    if not isinstance(det, dict) or "signers" not in det:
        return "", "", True          # 401/500 com corpo JSON nao levanta excecao
    vend = _nome_vendedor((det.get("createdBy") or {}).get("name"))
    ts = [_data_iso(s.get("signingTime")) for s in (det.get("signers") or [])]
    ts = [t for t in ts if t]
    return vend, (max(ts) if ts else ""), False

def contratos(key, marca, desde, opps):
    """marca: 'LaraTAX'|'LaraFy'. desde: 'YYYY-MM-DD'. opps: lista compacta do CRM p/ cruzar.
    Retorna [{d, cliente, vendedor(=createdBy do B4), op(=oportunidade casada p/ valor, ou None)}]."""
    if not key: return []
    grupos = grupos_apelidos()
    # Pre-filtro barato com MARGEM: em tese assinar mexe no documento (updateDate >=
    # assinatura), mas essa premissa e' justamente a que falhou hoje — nao da' pra
    # apostar nela no corte exato. 6 meses de folga custam algumas chamadas a mais e
    # evitam sumir com contrato criado bem antes de ser assinado.
    piso = (dt.date(int(desde[:4]), int(desde[5:7]), 1) - dt.timedelta(days=185)).strftime("%Y-%m-%d")
    out, sem_assin, falhas = [], 0, 0
    for d in _concluidos(key):
        name = d.get("name") or ""
        if _excl(name): continue
        if marca == "LaraTAX" and "contrato laratax" not in name.lower(): continue
        du = _data_iso(d.get("updateDate"))
        if du and du < piso: continue
        vendedor, dt_assin, falhou = _detalhe(d.get("id"), key)
        if falhou:
            falhas += 1
        # sem assinatura legivel: creationDate (envio p/ assinar) erra por dias;
        # updateDate erra por meses. Fica com o menos errado.
        data = dt_assin or _data_iso(d.get("creationDate"))
        if not dt_assin and not falhou:
            sem_assin += 1
        if not data or data < desde: continue
        cli = _cliente(name)
        out.append({"d": data, "cliente": cli, "vendedor": vendedor, "op": _match(cli, opps, grupos)})
    if sem_assin:
        print("[b4] %s: %d contrato(s) sem assinatura legivel -> datados pelo envio" % (marca, sem_assin))
    if falhas:
        # nao e' detalhe: cada falha aqui pode ser uma venda que nao entrou no painel
        print("[b4] %s: ATENCAO, %d consulta(s) de assinatura falharam -> venda pode estar faltando" % (marca, falhas))
    return out


_PALAVRAS_DE_TERMO = {"termo", "de", "do", "da", "e", "contrato", "contratual", "aditivo",
                      "instrumento", "particular", "distrato", "cancelamento", "cancelacao",
                      "parceria", "parceiro", "laratax", "larafy", "lara", "fy", "tax"}


def _so_cliente_resc(name):
    """Tira do nome do documento as palavras do proprio termo, sobrando o cliente.
    "Catarinaco- Termo Rescisao" -> "Catarinaco". Sem isso, palavras como "termo",
    "rescisao" ou a marca ("LaraTAX") entrariam na comparacao: sobraria so a marca,
    que nao casa com cliente nenhum, e a rescisao nao descontaria nada — em silencio.
    """
    bruto = re.sub("[-\u2013_]+", " ", str(name or ""))
    fica = []
    for p in bruto.split():
        k = unicodedata.normalize("NFKD", p).encode("ascii", "ignore").decode().lower()
        if k in _PALAVRAS_DE_TERMO or k.startswith("rescis"):
            continue
        fica.append(p)
    return " ".join(fica)


# Nomes que assinaram RESCISAO/DISTRATO. O CRM costuma manter a oportunidade como
# "ganha" mesmo depois do cliente sair — sem esta lista, o painel voltaria a contar
# como venda algo que ja foi desfeito.
def rescindidos(key, desde="2000-01-01"):
    """Quem assinou RESCISAO/DISTRATO. O CRM mantem a oportunidade como "ganha" mesmo
    depois do cliente sair — sem esta lista o painel conta como venda algo desfeito.

    Devolve so o que da' para identificar: documento cujo nome, tiradas as palavras do
    termo, nao sobra nada aproveitavel fica de fora (com aviso), porque um nome vazio
    casaria com qualquer coisa.
    """
    if not key:
        return []
    fora, ilegiveis = [], []
    for d in _concluidos(key):
        name = (d.get("name") or "")
        n = name.lower()
        if not any(x in n for x in ["rescis", "distrato", "cancelamento", "cancelacao"]):
            continue
        # cancelamento de PARCERIA nao e' cliente saindo: nao pode descontar venda
        if any(x in n for x in ["parceria", "parceiro", "nda", "teste", "modelo"]):
            continue
        dt = _data_iso(d.get("updateDate"))
        if not dt or dt < desde:
            continue
        cli = _so_cliente_resc(name)
        if not _norm(cli):
            ilegiveis.append(name)
            continue
        fora.append({"d": dt, "cliente": cli, "doc": name})
    if ilegiveis:
        print("[b4] rescisao sem cliente identificavel (nao descontada): %s" % ilegiveis[:6])
    return fora
