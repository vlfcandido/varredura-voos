"""Orquestração das consultas — o funil que decide quando gastar uma chamada.

A ordem importa, e é sempre a mesma, do mais barato para o mais caro:

1. **Poda geográfica** — nem o melhor caso concebível cabe no teto: custo zero.
2. **Cache** — a mesma consulta já foi feita e ainda vale: custo zero.
3. **Orçamento** — ainda há cota no mês? Se não, para e relata o que faltou.
4. **API** — só aqui se gasta chamada.

As consultas saem ordenadas por prioridade da janela, feriados primeiro. Quando
a cota acaba no meio, o que sobrou de fora é a parte menos valiosa da varredura,
não uma fatia aleatória.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from varredura_voos.cliente_amadeus import ErroAmadeus
from varredura_voos.filtros import aplicar_filtros
from varredura_voos.geo import rota_viavel
from varredura_voos.modelos import Consulta
from varredura_voos.normalizacao import RespostaIlegivel, normalizar_resposta
from varredura_voos.orcamento import OrcamentoEsgotado

if TYPE_CHECKING:
    from collections.abc import Sequence

    from varredura_voos.cache import CacheDisco
    from varredura_voos.cliente_amadeus import ClienteAmadeus
    from varredura_voos.config import Config
    from varredura_voos.modelos import Janela, Oferta
    from varredura_voos.orcamento import Orcamento


@dataclass(frozen=True, slots=True)
class FalhaConsulta:
    """Uma consulta que não pôde ser respondida.

    Attributes:
        consulta: A consulta que falhou.
        motivo: Descrição legível do erro.
    """

    consulta: Consulta
    motivo: str


@dataclass(slots=True)
class ResultadoVarredura:
    """O que uma varredura produziu, incluindo o que ela deixou de fazer.

    Attributes:
        ofertas: Ofertas aprovadas pelos filtros, ordenadas por preço.
        chamadas: Quantas requisições à API foram realmente feitas.
        cache_hits: Quantas consultas foram servidas pelo cache.
        podadas: Quantas consultas a poda geográfica dispensou.
        consultas_feitas: Consultas que chegaram a gastar chamada.
        nao_varridas: Consultas que ficaram de fora por falta de cota.
        pares_sem_oferta: Pares origem-destino que responderam mas não
            produziram nenhuma oferta dentro do teto de duração.
        erros: Falhas por consulta, que não interrompem a varredura.
        orcamento_esgotado: Se a varredura parou por falta de cota.
    """

    ofertas: list[Oferta] = field(default_factory=list)
    chamadas: int = 0
    cache_hits: int = 0
    podadas: int = 0
    consultas_feitas: list[Consulta] = field(default_factory=list)
    nao_varridas: list[Consulta] = field(default_factory=list)
    pares_sem_oferta: set[tuple[str, str]] = field(default_factory=set)
    erros: list[FalhaConsulta] = field(default_factory=list)
    orcamento_esgotado: bool = False


def montar_consultas(
    cfg: Config,
    janelas: Sequence[Janela],
    /,
    *,
    agressiva: bool = False,
) -> list[Consulta]:
    """Produz as consultas a fazer, já podadas e ordenadas por prioridade.

    Args:
        cfg: Configuração da varredura.
        janelas: Janelas de datas candidatas.
        agressiva: Se `True`, usa a poda geográfica agressiva, que corta mais
            e pode esconder oferta de borda.

    Returns:
        Consultas na ordem em que devem consumir a cota: janelas de feriado
        primeiro, depois por data.
    """
    consultas = [
        Consulta(
            origem=origem,
            destino=destino,
            janela=janela,
            adultos=cfg.adultos,
            moeda=cfg.moeda,
        )
        for janela in janelas
        for origem in cfg.origens
        for destino in cfg.destinos
        if destino != origem
        and rota_viavel(origem, destino, cfg, teto=cfg.max_duracao, agressiva=agressiva)
    ]
    return sorted(consultas, key=lambda c: (c.janela.prioridade, c.janela.ida, c.origem, c.destino))


def _total_possivel(cfg: Config, janelas: Sequence[Janela]) -> int:
    """Quantas consultas existiriam sem poda nenhuma.

    Args:
        cfg: Configuração da varredura.
        janelas: Janelas candidatas.

    Returns:
        O produto de origens, destinos válidos e janelas.
    """
    return sum(
        1
        for _ in janelas
        for origem in cfg.origens
        for destino in cfg.destinos
        if destino != origem
    )


async def varrer(
    cfg: Config,
    janelas: Sequence[Janela],
    cliente: ClienteAmadeus,
    cache: CacheDisco,
    orcamento: Orcamento,
    /,
    *,
    agressiva: bool = False,
) -> ResultadoVarredura:
    """Executa a varredura inteira, respeitando cache, cota e concorrência.

    Um erro num par não derruba os outros: fica registrado em `erros` e a
    varredura segue. Quando a cota acaba, o restante vai para `nao_varridas` e
    a varredura para — de propósito, e com o que faltou declarado.

    Args:
        cfg: Configuração da varredura.
        janelas: Janelas de datas candidatas.
        cliente: Cliente da Amadeus já montado.
        cache: Cache em disco das respostas.
        orcamento: Contador de cota do mês.
        agressiva: Se `True`, aplica a poda geográfica agressiva.

    Returns:
        O resultado, incluindo o que ficou de fora e por quê.
    """
    consultas = montar_consultas(cfg, janelas, agressiva=agressiva)
    resultado = ResultadoVarredura(
        podadas=_total_possivel(cfg, janelas) - len(consultas),
    )
    com_resposta: set[tuple[str, str]] = set()
    trava = asyncio.Lock()

    async def processar(consulta: Consulta, /) -> list[Oferta] | None:
        """Resolve uma consulta pelo cache ou pela API, respeitando a cota."""
        bruto = await cache.obter(consulta)
        if bruto is not None:
            async with trava:
                resultado.cache_hits += 1
            return _extrair(consulta, bruto, cfg, resultado)

        async with trava:
            if resultado.orcamento_esgotado:
                resultado.nao_varridas.append(consulta)
                return None
            try:
                orcamento.consumir()
            except OrcamentoEsgotado:
                resultado.orcamento_esgotado = True
                resultado.nao_varridas.append(consulta)
                return None
            resultado.chamadas += 1
            resultado.consultas_feitas.append(consulta)

        try:
            bruto = await cliente.buscar_ofertas(consulta)
        except ErroAmadeus as erro:
            async with trava:
                resultado.erros.append(FalhaConsulta(consulta, str(erro)))
            return None
        await cache.guardar(consulta, bruto)
        return _extrair(consulta, bruto, cfg, resultado)

    for consulta in consultas:
        ofertas = await processar(consulta)
        if ofertas is None:
            continue
        com_resposta.add((consulta.origem, consulta.destino))
        resultado.ofertas.extend(ofertas)

    resultado.ofertas = aplicar_filtros(resultado.ofertas, teto_duracao=cfg.max_duracao)
    aprovados = {(o.origem, o.destino) for o in resultado.ofertas}
    resultado.pares_sem_oferta = com_resposta - aprovados
    return resultado


def _extrair(
    consulta: Consulta,
    bruto: dict[str, object],
    cfg: Config,
    resultado: ResultadoVarredura,
    /,
) -> list[Oferta]:
    """Normaliza e filtra a resposta de uma consulta, registrando falha de parse.

    Args:
        consulta: Consulta que originou a resposta.
        bruto: Corpo cru devolvido pela API ou pelo cache.
        cfg: Configuração, de onde sai o teto de duração.
        resultado: Acumulador, onde uma falha de leitura é registrada.

    Returns:
        As ofertas dessa consulta que passaram no teto de duração.
    """
    try:
        ofertas = normalizar_resposta(bruto, consulta)
    except (RespostaIlegivel, ValueError) as erro:
        resultado.erros.append(FalhaConsulta(consulta, str(erro)))
        return []
    return aplicar_filtros(ofertas, teto_duracao=cfg.max_duracao)
