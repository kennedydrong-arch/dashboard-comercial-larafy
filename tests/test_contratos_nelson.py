import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import b4_vendas as b4


TITULOS = [
    "Contrato LaraTAX - ASCENPRIME - ESTRATEGIA E NEGOCIOS LTDA",
    "Contrato LaraTAX - CONASSCON S-C LTDA",
    "Contrato LaraTAX - MONTALVAO & SOUZA LIMA SOCIEDADE DE ADVOGADOS",
    "Contrato LaraTAX - NOROESTE GESTAO EMPRESARIAL",
    "Contrato LaraTAX - RIBEIRO FIALHO ADVOGADOS",
    "Contrato-LaraTAX - NEVES-E-LELIS",
]


class ContratosNelson(unittest.TestCase):
    def contratos(self, documentos, marca="LaraTAX", vendedor="Barbara Barbosa", opps=()):
        with patch.object(b4, "_concluidos", return_value=documentos), \
             patch.object(b4, "_detalhe", return_value=(vendedor, "2026-11-17", False)), \
             contextlib.redirect_stdout(io.StringIO()):
            return b4.contratos("chave-ficticia", marca, "2026-06-01", opps)

    def documento(self, nome, id_doc="neves"):
        return {"id": id_doc, "name": nome, "creationDate": "2026-11-17",
                "updateDate": "2026-11-17"}

    def test_seis_titulos_entram_uma_vez(self):
        docs = [self.documento(n, str(i)) for i, n in enumerate(TITULOS)]
        vendas = self.contratos(docs, vendedor="Nelson Mocelin")
        self.assertEqual(len(vendas), 6)
        self.assertEqual(len({v["id"] for v in vendas}), 6)
        self.assertTrue(all(v["vendedor"] == "Nelson Mocelin" for v in vendas))

    def test_neves_corrige_remetente_e_preserva_oportunidade_existente(self):
        op = {"c": "Neves E Lelis Sociedade De Advogados", "r": "Barbara Barbosa", "v": 3000}
        vendas = self.contratos([self.documento(TITULOS[-1])], opps=[op])
        self.assertEqual(len(vendas), 1)
        self.assertEqual(vendas[0]["vendedor"], "Nelson Mocelin")
        self.assertIs(vendas[0]["op"], op)
        self.assertEqual(op["r"], "Barbara Barbosa")

    def test_variacoes_do_separador(self):
        for nome in ["Contrato LaraTAX - X", "Contrato-LaraTAX - X",
                     "CONTRATO—LARATAX - X", "Contrato_LaraTAX - X",
                     "Contrato  LaraTAX - X", "Contrato – LaraTAX - X"]:
            with self.subTest(nome=nome):
                self.assertTrue(b4._titulo_laratax(nome))

    def test_outras_marcas_e_palavras_parciais_nao_entram(self):
        for nome in ["Contrato LaraFy - X", "Contrato LaraTAXExtra - X",
                     "Precontrato LaraTAX - X", "Autorizacao LaraTAX - X"]:
            with self.subTest(nome=nome):
                self.assertFalse(b4._titulo_laratax(nome))

    def test_exclusoes_permanecem(self):
        for prefixo in ["Aditivo", "Distrato", "Rescisao", "Parceria", "Modelo", "Teste"]:
            with self.subTest(prefixo=prefixo):
                self.assertEqual(self.contratos([self.documento(prefixo + " " + TITULOS[-1])]), [])

    def test_atribuicao_pelo_titulo_inteiro_sem_afetar_outros(self):
        docs = [self.documento("Contrato LaraTAX - OUTRA EMPRESA", "outro"),
                self.documento(TITULOS[-1] + " FILIAL", "filial")]
        self.assertTrue(all(v["vendedor"] == "Barbara Barbosa" for v in self.contratos(docs)))
        self.assertEqual(b4._chave_documento(TITULOS[-1]),
                         b4._chave_documento("contrato laratax - Neves e Lelis.PDF"))

    def test_nao_cria_venda_sem_documento_concluido(self):
        self.assertEqual(self.contratos([]), [])

    def test_larafy_preserva_regra_e_vendedor(self):
        vendas = self.contratos([self.documento("Autorizacao Consultoria - X")], marca="LaraFy")
        self.assertEqual(len(vendas), 1)
        self.assertEqual(vendas[0]["vendedor"], "Barbara Barbosa")

    def test_configuracao_invalida_nao_e_aplicada_em_silencio(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "vendedores.json"
            p.write_text(json.dumps({"atribuicoes": [
                {"documento": "Contrato-LaraTAX - X", "vendedor": "A"},
                {"documento": "Contrato LaraTAX - X", "vendedor": "B"}]}))
            with self.assertRaises(ValueError):
                b4.vendedores_documentos(p)


if __name__ == "__main__":
    unittest.main()
