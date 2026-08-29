"""Testes da CLI. Só caminhos que não tocam a rede."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from varredura_voos.cli import app, interpretar_duracao, janela_de_meses

runner = CliRunner()


@pytest.mark.parametrize(
    ("texto", "horas"),
    [("6h", 6.0), ("6", 6.0), ("90m", 1.5), ("6h30", 6.5), ("6h30m", 6.5), ("0h45", 0.75)],
)
def test_interpretar_duracao(texto: str, horas: float) -> None:
    assert interpretar_duracao(texto).total_seconds() == pytest.approx(horas * 3600)


@pytest.mark.parametrize("lixo", ["", "abacaxi", "-3h", "h"])
def test_interpretar_duracao_rejeita_lixo(lixo: str) -> None:
    with pytest.raises(ValueError, match="duração"):
        interpretar_duracao(lixo)


def test_janela_de_meses_atravessa_a_virada_do_ano() -> None:
    from datetime import date

    inicio, fim = janela_de_meses(6, hoje=date(2026, 8, 29))
    assert inicio == date(2026, 8, 29)
    assert fim == date(2027, 2, 28)


def test_simular_nao_precisa_de_credencial(tmp_path: Path) -> None:
    """--simular tem de rodar sem AMADEUS_* e sem tocar a rede."""
    r = runner.invoke(
        app,
        ["--origens", "CAC", "--meses", "2", "--max-duracao", "6h", "--simular"],
        env={"AMADEUS_CLIENT_ID": "", "AMADEUS_CLIENT_SECRET": ""},
    )
    assert r.exit_code == 0, r.output
    assert "chamadas" in r.output.lower()
    assert "janelas" in r.output.lower()


def test_simular_avisa_quando_estoura_a_cota(tmp_path: Path) -> None:
    r = runner.invoke(
        app,
        ["--origens", "CAC,IGU", "--meses", "6", "--max-duracao", "6h", "--simular"],
        env={"AMADEUS_CLIENT_ID": "", "AMADEUS_CLIENT_SECRET": ""},
    )
    assert r.exit_code == 0, r.output
    assert "cota" in r.output.lower()


def test_sem_credencial_falha_com_mensagem_clara(tmp_path: Path) -> None:
    r = runner.invoke(
        app,
        ["--origens", "CAC", "--meses", "1", "--saida", str(tmp_path / "o.csv")],
        env={"AMADEUS_CLIENT_ID": "", "AMADEUS_CLIENT_SECRET": ""},
    )
    assert r.exit_code != 0
    assert "AMADEUS_CLIENT_ID" in r.output


def test_origem_desconhecida_falha_antes_de_qualquer_chamada(tmp_path: Path) -> None:
    r = runner.invoke(
        app,
        ["--origens", "XYZ", "--meses", "1", "--simular"],
        env={"AMADEUS_CLIENT_ID": "", "AMADEUS_CLIENT_SECRET": ""},
    )
    assert r.exit_code != 0
    assert "XYZ" in r.output
