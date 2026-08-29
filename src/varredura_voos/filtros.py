"""Regras que decidem se uma oferta serve — o coração da ferramenta.

Para uma viagem de três noites, o tempo de voo pesa mais que o preço: um
itinerário de doze horas com duas conexões inviabiliza a viagem mesmo barato.
Por isso o teto de duração é aplicado por perna, e reprova a oferta inteira.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from varredura_voos.modelos import ItinerarioAmadeus, Oferta

_ISO_DURACAO = re.compile(
    r"^P(?!$)(?:(?P<dias>\d+)D)?(?:T(?!$)(?:(?P<horas>\d+)H)?"
    r"(?:(?P<minutos>\d+)M)?(?:(?P<segundos>\d+)S)?)?$"
)


def duracao_iso(texto: str, /) -> timedelta:
    """Converte uma duração ISO 8601 da Amadeus em `timedelta`.

    A API devolve durações no formato `PnDTnHnMnS`, por exemplo `PT5H30M`
    para cinco horas e meia ou `P1DT2H` para um dia e duas horas.

    Args:
        texto: Duração no formato ISO 8601.

    Returns:
        A duração equivalente.

    Raises:
        ValueError: Se o texto não for uma duração ISO 8601 reconhecível ou
            não contiver nenhum componente de tempo.
    """
    casou = _ISO_DURACAO.match(texto)
    if casou is None:
        msg = f"duração ISO 8601 inválida: {texto!r}"
        raise ValueError(msg)
    partes = {chave: int(valor) for chave, valor in casou.groupdict(default="0").items()}
    if not any(partes.values()):
        msg = f"duração ISO 8601 sem componentes: {texto!r}"
        raise ValueError(msg)
    return timedelta(
        days=partes["dias"],
        hours=partes["horas"],
        minutes=partes["minutos"],
        seconds=partes["segundos"],
    )


def conexoes(itinerario: ItinerarioAmadeus, /) -> int:
    """Conta as trocas de avião de um itinerário.

    Não confundir com o campo `numberOfStops` de cada segmento: aquele conta
    paradas técnicas (reabastecimento), em que o passageiro não troca de avião.
    Uma conexão é uma fronteira entre dois segmentos.

    Args:
        itinerario: Itinerário cru vindo da Amadeus.

    Returns:
        Número de conexões: a quantidade de segmentos menos um.
    """
    return len(itinerario.segments) - 1


def dentro_do_teto(oferta: Oferta, teto: timedelta, /) -> bool:
    """Diz se a oferta respeita o teto de duração em **ambas** as pernas.

    Uma volta longa reprova a oferta inteira: quem chega em casa doze horas
    depois perdeu o fim de semana do mesmo jeito.

    Args:
        oferta: Oferta normalizada.
        teto: Duração máxima aceita por perna, inclusive.

    Returns:
        `True` se ida e volta cabem no teto.
    """
    return oferta.duracao_maxima <= teto


def aplicar_filtros(
    ofertas: Iterable[Oferta],
    /,
    *,
    teto_duracao: timedelta,
    max_conexoes: int | None = None,
    preco_maximo: float | None = None,
) -> list[Oferta]:
    """Aplica as regras de descarte e devolve as ofertas sobreviventes.

    Args:
        ofertas: Ofertas normalizadas a filtrar.
        teto_duracao: Duração máxima por perna.
        max_conexoes: Limite de conexões por perna, ou `None` para não limitar.
        preco_maximo: Preço total máximo, ou `None` para não limitar.

    Returns:
        Lista das ofertas aprovadas, ordenada por preço crescente e, no empate,
        pela menor duração.
    """
    aprovadas: list[Oferta] = []
    for oferta in ofertas:
        if not dentro_do_teto(oferta, teto_duracao):
            continue
        if (
            max_conexoes is not None
            and max(oferta.ida.conexoes, oferta.volta.conexoes) > max_conexoes
        ):
            continue
        if preco_maximo is not None and float(oferta.preco_total) > preco_maximo:
            continue
        aprovadas.append(oferta)
    return sorted(aprovadas, key=lambda o: (o.preco_total, o.duracao_maxima))
