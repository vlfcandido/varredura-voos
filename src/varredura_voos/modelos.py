"""Modelos de domínio e de transporte da varredura.

Divide-se em duas camadas:

* **Amadeus** (`*Amadeus`): espelham a resposta crua da Flight Offers Search v2,
  em camelCase, e servem só para validar o que chega da rede.
* **Domínio** (`Perna`, `Oferta`, `Janela`, `Consulta`): o vocabulário da
  ferramenta, já normalizado e em português.

Nenhum modelo executa I/O; o módulo é puro e importável sem efeito colateral.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_RAIO_TERRA_KM = 6371.0088


class _Base(BaseModel):
    """Base imutável comum a todos os modelos."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------------- geografia
class Coordenada(_Base):
    """Ponto geográfico em graus decimais.

    Attributes:
        latitude: Latitude em graus decimais, negativa no hemisfério sul.
        longitude: Longitude em graus decimais, negativa a oeste de Greenwich.
    """

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)

    def distancia_km(self, outra: Coordenada, /) -> float:
        """Distância de grande círculo até outra coordenada.

        Args:
            outra: Coordenada de destino.

        Returns:
            Distância em quilômetros pela fórmula de Haversine.
        """
        lat1, lon1 = math.radians(self.latitude), math.radians(self.longitude)
        lat2, lon2 = math.radians(outra.latitude), math.radians(outra.longitude)
        h = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
        return 2 * _RAIO_TERRA_KM * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- domínio
class Janela(_Base):
    """Uma janela de viagem candidata: data de ida, data de volta e o motivo.

    Attributes:
        ida: Data do voo de ida.
        volta: Data do voo de volta.
        motivo: Descrição legível ("fim de semana", "feriado: Natal").
        prioridade: Menor valor é varrido primeiro. Feriados vêm antes.
    """

    ida: date
    volta: date
    motivo: str
    prioridade: int = 100

    @model_validator(mode="after")
    def _valida_ordem(self) -> Self:
        if self.volta <= self.ida:
            msg = f"janela inválida: volta {self.volta} não é depois da ida {self.ida}"
            raise ValueError(msg)
        return self

    @property
    def noites(self) -> int:
        """Número de noites da janela."""
        return (self.volta - self.ida).days


class Consulta(_Base):
    """Uma consulta única à API: um par origem-destino numa janela.

    Attributes:
        origem: Código IATA do aeroporto de origem.
        destino: Código IATA do aeroporto de destino.
        janela: Janela de datas da viagem.
        adultos: Número de passageiros adultos.
        moeda: Código ISO 4217 da moeda desejada.
    """

    origem: str = Field(pattern=r"^[A-Z]{3}$")
    destino: str = Field(pattern=r"^[A-Z]{3}$")
    janela: Janela
    adultos: int = Field(ge=1, le=9)
    moeda: str = Field(pattern=r"^[A-Z]{3}$")

    def como_chave(self) -> str:
        """Representação canônica usada como chave de cache.

        Returns:
            String determinística com origem, destino, datas, pax e moeda.
        """
        return (
            f"{self.origem}|{self.destino}|{self.janela.ida.isoformat()}"
            f"|{self.janela.volta.isoformat()}|{self.adultos}|{self.moeda}"
        )


class Perna(_Base):
    """Uma perna da viagem (a ida inteira ou a volta inteira), já normalizada.

    Attributes:
        origem: IATA de embarque da perna.
        destino: IATA de desembarque da perna.
        partida_em: Data e hora locais da partida.
        chegada_em: Data e hora locais da chegada.
        duracao: Duração total da perna, porta a porta do avião.
        conexoes: Número de trocas de avião (segmentos menos um).
        companhia: Código IATA da companhia do primeiro segmento.
    """

    origem: str
    destino: str
    partida_em: datetime
    chegada_em: datetime
    duracao: timedelta
    conexoes: int = Field(ge=0)
    companhia: str


class Oferta(_Base):
    """Uma oferta de ida e volta já normalizada e pronta para filtrar e ranquear.

    Attributes:
        origem: IATA de origem da viagem.
        destino: IATA de destino da viagem.
        ida: Perna de ida.
        volta: Perna de volta.
        preco_total: Preço total para todos os passageiros da consulta.
        moeda: Código ISO 4217 do preço.
        companhia: Companhia validadora da tarifa.
        motivo: Motivo da janela, propagado para o relatório.
    """

    origem: str
    destino: str
    ida: Perna
    volta: Perna
    preco_total: Decimal
    moeda: str
    companhia: str
    motivo: str = ""

    @property
    def duracao_maxima(self) -> timedelta:
        """A maior das duas pernas — é ela que o teto de duração julga."""
        return max(self.ida.duracao, self.volta.duracao)


# --------------------------------------------------------------------------- Amadeus
class PontoAmadeus(_Base):
    """Ponto de partida ou chegada de um segmento."""

    iata_code: str = Field(alias="iataCode")
    at: datetime


class SegmentoAmadeus(_Base):
    """Um trecho sem troca de avião.

    Nota:
        `number_of_stops` é parada técnica (reabastecimento), **não** conexão.
        O número de conexões vem da contagem de segmentos.
    """

    departure: PontoAmadeus
    arrival: PontoAmadeus
    carrier_code: str = Field(alias="carrierCode")
    duration: str
    number_of_stops: int = Field(alias="numberOfStops", default=0)


class ItinerarioAmadeus(_Base):
    """Uma perna completa da viagem, com um ou mais segmentos."""

    duration: str
    segments: list[SegmentoAmadeus] = Field(min_length=1)


class PrecoAmadeus(_Base):
    """Bloco de preço de uma oferta.

    Nota:
        `total` e `grand_total` já somam **todos** os viajantes da consulta.
        Não multiplicar pelo número de adultos.
    """

    currency: str
    total: Decimal
    grand_total: Decimal | None = Field(alias="grandTotal", default=None)

    @property
    def a_pagar(self) -> Decimal:
        """Valor efetivamente cobrado: `grandTotal` quando existe, senão `total`."""
        return self.grand_total if self.grand_total is not None else self.total


class TarifaViajanteAmadeus(_Base):
    """Tarifa de um viajante — usada só para conferir a contagem de passageiros."""

    traveler_id: str = Field(alias="travelerId")
    traveler_type: str = Field(alias="travelerType")


class OfertaAmadeus(_Base):
    """Uma oferta crua da Flight Offers Search v2."""

    id: str
    itineraries: list[ItinerarioAmadeus] = Field(min_length=1)
    price: PrecoAmadeus
    validating_airline_codes: list[str] = Field(
        alias="validatingAirlineCodes", default_factory=list
    )
    traveler_pricings: list[TarifaViajanteAmadeus] = Field(
        alias="travelerPricings", default_factory=list
    )


class RespostaOfertas(_Base):
    """Envelope da resposta da Flight Offers Search v2."""

    data: list[OfertaAmadeus] = Field(default_factory=list)
