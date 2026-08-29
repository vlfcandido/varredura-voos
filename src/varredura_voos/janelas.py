"""Geração das janelas de datas candidatas.

Dois formatos interessam, nesta ordem de prioridade:

1. **Feriado prolongado** — janela de 3 ou 4 noites que contém um feriado
   nacional e parte de uma quinta ou de uma sexta. É a janela escassa: some
   rápido e vale mais, então é varrida primeiro.
2. **Fim de semana** — sexta à noite até segunda de manhã, 3 noites.

Os feriados são derivados em runtime pela lib `holidays`, e não de uma tabela
fixa que envelhece. É obrigatório pedir as categorias `PUBLIC` **e** `OPTIONAL`:
o Carnaval é ponto facultativo e não aparece na categoria `PUBLIC`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

from varredura_voos.modelos import Janela

_SEXTA = 4
_DIAS_DE_PARTIDA = frozenset({3, 4})  # quinta e sexta
_PRIORIDADE_FERIADO = 0
_PRIORIDADE_FIM_DE_SEMANA = 10


def feriados_nacionais(
    inicio: date,
    fim: date,
    /,
    *,
    excluidos: frozenset[str] = frozenset(),
) -> dict[date, str]:
    """Lista os feriados nacionais do período, incluindo os pontos facultativos.

    Args:
        inicio: Primeiro dia do período, inclusive.
        fim: Último dia do período, inclusive.
        excluidos: Nomes de feriados a descartar — tipicamente facultativos que
            não geram viagem, como "Véspera de Natal".

    Returns:
        Mapa de data para nome do feriado, restrito ao período.

    Raises:
        ValueError: Se `fim` for anterior a `inicio`.
    """
    if fim < inicio:
        msg = f"período inválido: {fim} é anterior a {inicio}"
        raise ValueError(msg)

    import holidays
    from holidays.constants import OPTIONAL, PUBLIC

    anos = list(range(inicio.year, fim.year + 1))
    tabela = holidays.country_holidays("BR", years=anos, categories=(PUBLIC, OPTIONAL))
    return {
        dia: str(nome)
        for dia, nome in tabela.items()
        if inicio <= dia <= fim and str(nome) not in excluidos
    }


def gerar_janelas_fim_de_semana(inicio: date, fim: date, /, *, noites: int = 3) -> list[Janela]:
    """Gera uma janela por sexta-feira do período.

    Args:
        inicio: Primeiro dia do período, inclusive.
        fim: Último dia do período, inclusive. A volta precisa caber dentro dele.
        noites: Número de noites da viagem. O padrão de 3 é sexta a segunda.

    Returns:
        Janelas ordenadas por data de ida.
    """
    janelas: list[Janela] = []
    dia = inicio
    while dia <= fim:
        if dia.weekday() == _SEXTA and dia + timedelta(days=noites) <= fim:
            janelas.append(
                Janela(
                    ida=dia,
                    volta=dia + timedelta(days=noites),
                    motivo="fim de semana",
                    prioridade=_PRIORIDADE_FIM_DE_SEMANA,
                )
            )
        dia += timedelta(days=1)
    return janelas


def gerar_janelas_feriado(
    inicio: date,
    fim: date,
    /,
    *,
    noites: Sequence[int] = (3, 4),
    feriados: Mapping[date, str] | None = None,
    excluidos: frozenset[str] = frozenset(),
) -> list[Janela]:
    """Gera janelas de 3 a 4 noites que encostam em feriado nacional.

    A janela precisa satisfazer três condições ao mesmo tempo: conter o feriado,
    partir de uma quinta ou de uma sexta (ninguém sai de terça-feira para um fim
    de semana prolongado) e caber inteira no período pedido. Um feriado no meio
    da semana que não emenda — Tiradentes numa quarta, por exemplo — não gera
    janela nenhuma, e é isso que se espera.

    Args:
        inicio: Primeiro dia do período, inclusive.
        fim: Último dia do período, inclusive.
        noites: Durações aceitas, em noites.
        feriados: Mapa de feriados a usar. Se `None`, são derivados da lib
            `holidays` para o período. Injetável para teste.
        excluidos: Nomes de feriados a descartar, quando `feriados` é `None`.

    Returns:
        Janelas ordenadas por data de ida, sem repetição.
    """
    tabela = feriados_nacionais(inicio, fim, excluidos=excluidos) if feriados is None else feriados
    vistas: dict[tuple[date, date], Janela] = {}
    for feriado, nome in sorted(tabela.items()):
        for n in noites:
            for recuo in range(n + 1):
                ida = feriado - timedelta(days=recuo)
                volta = ida + timedelta(days=n)
                if ida.weekday() not in _DIAS_DE_PARTIDA:
                    continue
                if ida < inicio or volta > fim:
                    continue
                vistas.setdefault(
                    (ida, volta),
                    Janela(
                        ida=ida,
                        volta=volta,
                        motivo=f"feriado: {nome}",
                        prioridade=_PRIORIDADE_FERIADO,
                    ),
                )
    return sorted(vistas.values(), key=lambda j: (j.ida, j.volta))


def gerar_janelas(
    inicio: date,
    fim: date,
    /,
    *,
    noites_fim_de_semana: int = 3,
    noites_feriado: Sequence[int] = (3, 4),
    excluidos: frozenset[str] = frozenset(),
    feriados: Mapping[date, str] | None = None,
) -> list[Janela]:
    """Reúne janelas de feriado e de fim de semana, sem repetição.

    Quando a mesma dupla de datas sai dos dois geradores — uma sexta a segunda
    que também é feriado —, vence a versão de feriado, que tem prioridade maior.

    Args:
        inicio: Primeiro dia do período, inclusive.
        fim: Último dia do período, inclusive.
        noites_fim_de_semana: Noites das janelas de fim de semana.
        noites_feriado: Noites aceitas nas janelas de feriado.
        excluidos: Nomes de feriados a descartar.
        feriados: Mapa de feriados a usar, ou `None` para derivar da lib.

    Returns:
        Janelas ordenadas por prioridade e depois por data de ida: feriados
        primeiro, porque são a oportunidade escassa.
    """
    por_datas: dict[tuple[date, date], Janela] = {}
    for janela in gerar_janelas_feriado(
        inicio, fim, noites=noites_feriado, feriados=feriados, excluidos=excluidos
    ):
        por_datas[(janela.ida, janela.volta)] = janela
    for janela in gerar_janelas_fim_de_semana(inicio, fim, noites=noites_fim_de_semana):
        por_datas.setdefault((janela.ida, janela.volta), janela)
    return sorted(por_datas.values(), key=lambda j: (j.prioridade, j.ida))
