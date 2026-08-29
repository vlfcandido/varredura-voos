"""Testes do coração da ferramenta: duração, conexões e o teto de 6h."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from varredura_voos.filtros import conexoes, dentro_do_teto, duracao_iso
from varredura_voos.modelos import ItinerarioAmadeus, Oferta, Perna


# --- 1. parsing de duração ISO 8601 -------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("PT6H", timedelta(hours=6)),
        ("PT5H59M", timedelta(hours=5, minutes=59)),
        ("PT45M", timedelta(minutes=45)),
        ("PT1H30M", timedelta(hours=1, minutes=30)),
        ("P1DT2H", timedelta(days=1, hours=2)),
        ("PT12H10M", timedelta(hours=12, minutes=10)),
    ],
)
def test_duracao_iso_converte(texto: str, esperado: timedelta) -> None:
    assert duracao_iso(texto) == esperado


@pytest.mark.parametrize("lixo", ["", "6H", "PT", "abacaxi", "PTXH"])
def test_duracao_iso_rejeita_lixo(lixo: str) -> None:
    with pytest.raises(ValueError, match="duração"):
        duracao_iso(lixo)


# --- 3. conexões != numberOfStops ---------------------------------------------------
def _seg(origem: str, destino: str, paradas: int = 0) -> dict[str, object]:
    return {
        "departure": {"iataCode": origem, "at": "2026-09-04T19:00:00"},
        "arrival": {"iataCode": destino, "at": "2026-09-04T20:20:00"},
        "carrierCode": "AD",
        "duration": "PT1H20M",
        "numberOfStops": paradas,
    }


def test_conexoes_voo_direto_e_zero() -> None:
    it = ItinerarioAmadeus.model_validate({"duration": "PT1H20M", "segments": [_seg("CAC", "GRU")]})
    assert conexoes(it) == 0


def test_conexoes_uma_escala_e_um() -> None:
    it = ItinerarioAmadeus.model_validate(
        {"duration": "PT4H", "segments": [_seg("CAC", "GRU"), _seg("GRU", "SSA")]}
    )
    assert conexoes(it) == 1


def test_number_of_stops_e_parada_tecnica_nao_conexao() -> None:
    """`numberOfStops` é reabastecimento, não conexão. Um segmento = zero conexões."""
    it = ItinerarioAmadeus.model_validate(
        {"duration": "PT5H", "segments": [_seg("CAC", "MAO", paradas=2)]}
    )
    assert conexoes(it) == 0


# --- 2. o teto de duração, com as bordas --------------------------------------------
def _perna(horas: float) -> Perna:
    partida = datetime(2026, 9, 4, 19, 0)
    return Perna(
        origem="CAC",
        destino="GRU",
        partida_em=partida,
        chegada_em=partida + timedelta(hours=horas),
        duracao=timedelta(hours=horas),
        conexoes=0,
        companhia="AD",
    )


def _oferta(horas_ida: float, horas_volta: float) -> Oferta:
    return Oferta(
        origem="CAC",
        destino="GRU",
        ida=_perna(horas_ida),
        volta=_perna(horas_volta),
        preco_total=Decimal("1234.56"),
        moeda="BRL",
        companhia="AD",
    )


TETO = timedelta(hours=6)


@pytest.mark.parametrize(
    ("ida", "volta", "passa"),
    [
        (5.0, 5.0, True),
        (5.9833, 5.0, True),  # 5h59 na ida
        (6.0, 6.0, True),  # borda: 6h00 exatos cabem
        (6.0167, 5.0, False),  # 6h01 na ida reprova
        (5.0, 6.0167, False),  # 6h01 na VOLTA também reprova a oferta inteira
        (12.0, 1.0, False),
    ],
)
def test_dentro_do_teto_bordas(ida: float, volta: float, passa: bool) -> None:
    assert dentro_do_teto(_oferta(ida, volta), TETO) is passa


# --- filtros opcionais: conexões e preço --------------------------------------------
def test_aplicar_filtros_limita_conexoes() -> None:
    from varredura_voos.filtros import aplicar_filtros

    direta = _oferta(2.0, 2.0)
    com_escala = direta.model_copy(
        update={"ida": direta.ida.model_copy(update={"conexoes": 2})}
    )
    aprovadas = aplicar_filtros([direta, com_escala], teto_duracao=TETO, max_conexoes=1)
    assert aprovadas == [direta]


def test_aplicar_filtros_limita_preco() -> None:
    from decimal import Decimal

    from varredura_voos.filtros import aplicar_filtros

    barata = _oferta(2.0, 2.0).model_copy(update={"preco_total": Decimal("500.00")})
    cara = _oferta(2.0, 2.0).model_copy(update={"preco_total": Decimal("5000.00")})
    aprovadas = aplicar_filtros([cara, barata], teto_duracao=TETO, preco_maximo=1000.0)
    assert aprovadas == [barata]


def test_aplicar_filtros_ordena_por_preco_e_desempata_por_duracao() -> None:
    from decimal import Decimal

    from varredura_voos.filtros import aplicar_filtros

    lenta = _oferta(5.0, 5.0).model_copy(update={"preco_total": Decimal("1000.00")})
    rapida = _oferta(2.0, 2.0).model_copy(update={"preco_total": Decimal("1000.00")})
    assert aplicar_filtros([lenta, rapida], teto_duracao=TETO) == [rapida, lenta]
