"""Testes do orçamento de chamadas — a proteção contra queimar a cota do mês."""

from datetime import date
from pathlib import Path

import pytest

from varredura_voos.orcamento import Orcamento, OrcamentoEsgotado


@pytest.fixture
def caminho(tmp_path: Path) -> Path:
    return tmp_path / "cota.json"


def test_comeca_com_o_teto_inteiro(caminho: Path) -> None:
    o = Orcamento(caminho, teto=100, mes=date(2026, 8, 1))
    assert o.restante() == 100
    assert o.gastas() == 0


def test_consumir_reduz_o_restante(caminho: Path) -> None:
    o = Orcamento(caminho, teto=100, mes=date(2026, 8, 15))
    o.consumir(3)
    assert o.gastas() == 3
    assert o.restante() == 97


def test_esgotar_levanta(caminho: Path) -> None:
    o = Orcamento(caminho, teto=2, mes=date(2026, 8, 1))
    o.consumir()
    o.consumir()
    with pytest.raises(OrcamentoEsgotado, match="cota"):
        o.consumir()
    assert o.restante() == 0


def test_persiste_entre_instancias(caminho: Path) -> None:
    Orcamento(caminho, teto=100, mes=date(2026, 8, 1)).consumir(7)
    assert Orcamento(caminho, teto=100, mes=date(2026, 8, 1)).gastas() == 7


def test_vira_o_mes_e_o_contador_zera(caminho: Path) -> None:
    Orcamento(caminho, teto=100, mes=date(2026, 8, 20)).consumir(90)
    setembro = Orcamento(caminho, teto=100, mes=date(2026, 9, 1))
    assert setembro.gastas() == 0
    assert setembro.restante() == 100
    # e agosto continua registrado
    assert Orcamento(caminho, teto=100, mes=date(2026, 8, 31)).gastas() == 90


def test_nao_consome_alem_do_teto_mesmo_pedindo_de_uma_vez(caminho: Path) -> None:
    o = Orcamento(caminho, teto=5, mes=date(2026, 8, 1))
    with pytest.raises(OrcamentoEsgotado):
        o.consumir(6)
    assert o.gastas() == 0, "consumo que não cabe não pode debitar nada"
