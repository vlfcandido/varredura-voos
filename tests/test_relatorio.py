"""Testes da exportação em CSV e do resumo no terminal."""

import csv
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from varredura_voos.modelos import Oferta, Perna
from varredura_voos.relatorio import Colunas, exportar_csv, resumo_terminal


def _oferta(destino: str, preco: str, horas: float = 2.0) -> Oferta:
    partida = datetime(2026, 9, 4, 19, 0)
    perna = Perna(
        origem="CAC",
        destino=destino,
        partida_em=partida,
        chegada_em=partida + timedelta(hours=horas),
        duracao=timedelta(hours=horas),
        conexoes=0,
        companhia="AD",
    )
    volta = perna.model_copy(
        update={
            "origem": destino,
            "destino": "CAC",
            "partida_em": datetime(2026, 9, 7, 8, 0),
            "chegada_em": datetime(2026, 9, 7, 8, 0) + timedelta(hours=horas),
        }
    )
    return Oferta(
        origem="CAC",
        destino=destino,
        ida=perna,
        volta=volta,
        preco_total=Decimal(preco),
        moeda="BRL",
        companhia="AD",
        motivo="feriado: Independência",
    )


@pytest.fixture
def ofertas() -> list[Oferta]:
    return [_oferta("GRU", "1800.00"), _oferta("CWB", "900.50"), _oferta("FLN", "1200.00")]


def test_csv_sai_ordenado_por_preco(tmp_path: Path, ofertas: list[Oferta]) -> None:
    saida = tmp_path / "out.csv"
    exportar_csv(ofertas, saida)
    with saida.open(encoding="utf-8", newline="") as f:
        linhas = list(csv.DictReader(f))
    assert [linha["destino"] for linha in linhas] == ["CWB", "FLN", "GRU"]
    assert [linha["preco_total"] for linha in linhas] == ["900.50", "1200.00", "1800.00"]


def test_csv_tem_todas_as_colunas_do_contrato(tmp_path: Path, ofertas: list[Oferta]) -> None:
    saida = tmp_path / "out.csv"
    exportar_csv(ofertas, saida)
    with saida.open(encoding="utf-8", newline="") as f:
        cabecalho = next(csv.reader(f))
    assert cabecalho == list(Colunas)
    for obrigatoria in (
        "origem",
        "destino",
        "data_ida",
        "data_volta",
        "preco_total",
        "moeda",
        "companhia",
        "conexoes_ida",
        "conexoes_volta",
        "duracao_ida",
        "duracao_volta",
        "partida_ida",
        "chegada_ida",
        "partida_volta",
        "chegada_volta",
    ):
        assert obrigatoria in cabecalho


def test_csv_vazio_ainda_escreve_o_cabecalho(tmp_path: Path) -> None:
    saida = tmp_path / "out.csv"
    exportar_csv([], saida)
    assert saida.read_text(encoding="utf-8").strip().startswith("origem,")


def test_duracao_sai_legivel(tmp_path: Path) -> None:
    saida = tmp_path / "out.csv"
    exportar_csv([_oferta("GRU", "100.00", horas=5.5)], saida)
    with saida.open(encoding="utf-8", newline="") as f:
        linha = next(csv.DictReader(f))
    assert linha["duracao_ida"] == "5h30"


def test_resumo_traz_no_maximo_quinze(ofertas: list[Oferta]) -> None:
    muitas = [_oferta("GRU", f"{1000 + i}.00") for i in range(40)]
    texto = resumo_terminal(muitas, limite=15)
    assert texto.count("\n") <= 20
    assert "1000.00" in texto
    assert "1039.00" not in texto, "a 40ª não cabe no top 15"


def test_resumo_de_lista_vazia_explica(ofertas: list[Oferta]) -> None:
    texto = resumo_terminal([], limite=15)
    assert "nenhuma" in texto.lower()
