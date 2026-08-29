"""Conversão da resposta crua da Amadeus para o modelo de domínio.

Módulo próprio — e não dentro de `modelos` ou de `filtros` — porque depende dos
dois: precisa dos modelos de transporte e das regras de leitura de duração e de
conexão. Deixá-lo em qualquer um deles criaria import circular.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from varredura_voos.filtros import conexoes, duracao_iso
from varredura_voos.modelos import (
    ItinerarioAmadeus,
    Oferta,
    OfertaAmadeus,
    Perna,
    RespostaOfertas,
)

if TYPE_CHECKING:
    from varredura_voos.modelos import Consulta

_ITINERARIOS_IDA_E_VOLTA = 2


class RespostaIlegivel(ValueError):
    """A resposta não tem o formato da Flight Offers Search v2."""


class PassageirosInesperados(ValueError):
    """A oferta precifica um número de viajantes diferente do que foi pedido."""


def _perna(itinerario: ItinerarioAmadeus, /) -> Perna:
    """Converte um itinerário cru na perna correspondente.

    Args:
        itinerario: Itinerário cru da Amadeus.

    Returns:
        A perna normalizada, com duração e conexões já calculadas.

    Raises:
        ValueError: Se a duração declarada não for ISO 8601 válida.
    """
    primeiro, ultimo = itinerario.segments[0], itinerario.segments[-1]
    return Perna(
        origem=primeiro.departure.iata_code,
        destino=ultimo.arrival.iata_code,
        partida_em=primeiro.departure.at,
        chegada_em=ultimo.arrival.at,
        duracao=duracao_iso(itinerario.duration),
        conexoes=conexoes(itinerario),
        companhia=primeiro.carrier_code,
    )


def normalizar_oferta(crua: OfertaAmadeus, consulta: Consulta, /) -> Oferta | None:
    """Converte uma oferta crua em oferta de domínio.

    Args:
        crua: Oferta como veio da API.
        consulta: Consulta que a originou, usada para conferir os passageiros
            e propagar o motivo da janela.

    Returns:
        A oferta normalizada, ou `None` se ela não for de ida e volta — a busca
        é sempre de ida e volta, e uma oferta com um itinerário só não serve.

    Raises:
        PassageirosInesperados: Se a oferta precificar um número de viajantes
            diferente do pedido. Indica que a consulta não foi a que se pensa,
            e o preço não pode ser comparado com o das outras.
    """
    if len(crua.itineraries) != _ITINERARIOS_IDA_E_VOLTA:
        return None
    if crua.traveler_pricings and len(crua.traveler_pricings) != consulta.adultos:
        msg = (
            f"oferta {crua.id} precifica {len(crua.traveler_pricings)} viajantes, "
            f"mas a consulta pediu {consulta.adultos}"
        )
        raise PassageirosInesperados(msg)

    ida, volta = (_perna(it) for it in crua.itineraries)
    companhia = crua.validating_airline_codes[0] if crua.validating_airline_codes else ida.companhia
    return Oferta(
        origem=consulta.origem,
        destino=consulta.destino,
        ida=ida,
        volta=volta,
        preco_total=crua.price.a_pagar,
        moeda=crua.price.currency,
        companhia=companhia,
        motivo=consulta.janela.motivo,
    )


def normalizar_resposta(bruto: dict[str, Any], consulta: Consulta, /) -> list[Oferta]:
    """Converte o corpo inteiro de uma resposta em ofertas de domínio.

    Args:
        bruto: Corpo JSON devolvido pela Flight Offers Search v2.
        consulta: Consulta que originou a resposta.

    Returns:
        As ofertas de ida e volta encontradas, na ordem em que vieram.

    Raises:
        RespostaIlegivel: Se o corpo não validar contra o esquema da API.
        PassageirosInesperados: Propagado de `normalizar_oferta`.
    """
    try:
        resposta = RespostaOfertas.model_validate(bruto)
    except ValidationError as erro:
        msg = f"resposta fora do formato da Flight Offers Search v2: {erro.error_count()} erro(s)"
        raise RespostaIlegivel(msg) from erro

    ofertas: list[Oferta] = []
    for crua in resposta.data:
        normalizada = normalizar_oferta(crua, consulta)
        if normalizada is not None:
            ofertas.append(normalizada)
    return ofertas
