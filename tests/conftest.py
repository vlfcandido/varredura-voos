"""Fixtures compartilhadas. Nenhum teste toca a rede."""

from datetime import date

import pytest

from varredura_voos.modelos import Consulta, Janela


@pytest.fixture
def janela() -> Janela:
    return Janela(ida=date(2026, 9, 4), volta=date(2026, 9, 7), motivo="feriado: Independência")


@pytest.fixture
def consulta(janela: Janela) -> Consulta:
    return Consulta(origem="CAC", destino="GRU", janela=janela, adultos=2, moeda="BRL")
