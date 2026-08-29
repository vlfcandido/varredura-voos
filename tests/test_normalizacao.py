"""Testes do parsing de uma resposta da Amadeus até o modelo de domínio."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from varredura_voos.filtros import aplicar_filtros
from varredura_voos.modelos import Consulta
from varredura_voos.normalizacao import PassageirosInesperados, normalizar_resposta

FIXTURE = Path(__file__).parent / "fixtures" / "flight_offers_cac_gru.json"


@pytest.fixture
def bruto() -> dict[str, Any]:
    carregado: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return carregado


def test_normaliza_as_tres_ofertas(bruto: dict[str, Any], consulta: Consulta) -> None:
    ofertas = normalizar_resposta(bruto, consulta)
    assert len(ofertas) == 3
    assert {o.origem for o in ofertas} == {"CAC"}
    assert {o.destino for o in ofertas} == {"GRU"}


def test_campos_da_oferta_direta(bruto: dict[str, Any], consulta: Consulta) -> None:
    direta = next(
        o for o in normalizar_resposta(bruto, consulta) if o.preco_total == Decimal("1834.56")
    )
    assert direta.moeda == "BRL"
    assert direta.companhia == "AD"
    assert direta.ida.duracao == timedelta(hours=1, minutes=35)
    assert direta.volta.duracao == timedelta(hours=1, minutes=40)
    assert direta.ida.conexoes == 0
    assert direta.volta.conexoes == 0
    assert direta.ida.partida_em == datetime(2026, 9, 4, 19, 15)
    assert direta.volta.chegada_em == datetime(2026, 9, 7, 9, 40)
    assert direta.motivo == consulta.janela.motivo


def test_oferta_com_conexao_conta_uma_conexao(bruto: dict[str, Any], consulta: Consulta) -> None:
    via_cwb = next(
        o for o in normalizar_resposta(bruto, consulta) if o.preco_total == Decimal("1520.00")
    )
    assert via_cwb.ida.conexoes == 1
    assert via_cwb.volta.conexoes == 0
    assert via_cwb.ida.origem == "CAC"
    assert via_cwb.ida.destino == "GRU"


def test_preco_ja_e_o_total_dos_dois_adultos(bruto: dict[str, Any], consulta: Consulta) -> None:
    """`price.total` soma todos os viajantes — não se multiplica por 2."""
    direta = next(
        o
        for o in normalizar_resposta(bruto, consulta)
        if o.companhia == "AD" and o.ida.conexoes == 0
    )
    por_adulto = Decimal("917.28")
    assert direta.preco_total == por_adulto * 2


def test_contagem_de_passageiros_divergente_falha(
    bruto: dict[str, Any], consulta: Consulta
) -> None:
    bruto["data"][0]["travelerPricings"].pop()
    with pytest.raises(PassageirosInesperados, match="2"):
        normalizar_resposta(bruto, consulta)


def test_o_filtro_de_duracao_derruba_a_oferta_mais_barata(
    bruto: dict[str, Any], consulta: Consulta
) -> None:
    """A de 12h10 é a mais barata (R$ 990) e tem de cair mesmo assim."""
    ofertas = normalizar_resposta(bruto, consulta)
    assert min(o.preco_total for o in ofertas) == Decimal("990.00")
    sobreviventes = aplicar_filtros(ofertas, teto_duracao=timedelta(hours=6))
    assert len(sobreviventes) == 2
    assert Decimal("990.00") not in {o.preco_total for o in sobreviventes}
    assert sobreviventes[0].preco_total == Decimal("1520.00"), "ordenado por preço"


def test_resposta_vazia_nao_quebra(consulta: Consulta) -> None:
    assert normalizar_resposta({"data": []}, consulta) == []
    assert normalizar_resposta({}, consulta) == []


def test_oferta_so_de_ida_e_ignorada(bruto: dict[str, Any], consulta: Consulta) -> None:
    """A busca é sempre ida e volta; oferta com um itinerário só não serve."""
    bruto["data"][0]["itineraries"].pop()
    ofertas = normalizar_resposta(bruto, consulta)
    assert len(ofertas) == 2
