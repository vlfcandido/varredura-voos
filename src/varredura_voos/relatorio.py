"""Saída da varredura: arquivo CSV e resumo no terminal.

O CSV é o entregável — abre no Excel e ordena por preço. O resumo no terminal é
para a leitura de dois segundos: as melhores opções, com duração à vista, porque
para três noites o tempo de voo decide tanto quanto o preço.
"""

from __future__ import annotations

import csv
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta
    from pathlib import Path

    from varredura_voos.modelos import Oferta


class Colunas(StrEnum):
    """Colunas do CSV, na ordem em que são escritas."""

    ORIGEM = "origem"
    DESTINO = "destino"
    DATA_IDA = "data_ida"
    DATA_VOLTA = "data_volta"
    NOITES = "noites"
    PRECO_TOTAL = "preco_total"
    MOEDA = "moeda"
    COMPANHIA = "companhia"
    DURACAO_IDA = "duracao_ida"
    DURACAO_VOLTA = "duracao_volta"
    CONEXOES_IDA = "conexoes_ida"
    CONEXOES_VOLTA = "conexoes_volta"
    PARTIDA_IDA = "partida_ida"
    CHEGADA_IDA = "chegada_ida"
    PARTIDA_VOLTA = "partida_volta"
    CHEGADA_VOLTA = "chegada_volta"
    MOTIVO = "motivo"


def formatar_duracao(duracao: timedelta, /) -> str:
    """Formata uma duração como `5h30`, legível de relance.

    Args:
        duracao: Duração a formatar.

    Returns:
        A duração em horas e minutos.
    """
    minutos_totais = int(duracao.total_seconds() // 60)
    return f"{minutos_totais // 60}h{minutos_totais % 60:02d}"


def _linha(oferta: Oferta, /) -> dict[str, str]:
    """Converte uma oferta na linha correspondente do CSV.

    Args:
        oferta: Oferta a serializar.

    Returns:
        Mapa de coluna para valor já formatado.
    """
    noites = (oferta.volta.partida_em.date() - oferta.ida.partida_em.date()).days
    return {
        Colunas.ORIGEM: oferta.origem,
        Colunas.DESTINO: oferta.destino,
        Colunas.DATA_IDA: oferta.ida.partida_em.date().isoformat(),
        Colunas.DATA_VOLTA: oferta.volta.partida_em.date().isoformat(),
        Colunas.NOITES: str(noites),
        Colunas.PRECO_TOTAL: f"{oferta.preco_total:.2f}",
        Colunas.MOEDA: oferta.moeda,
        Colunas.COMPANHIA: oferta.companhia,
        Colunas.DURACAO_IDA: formatar_duracao(oferta.ida.duracao),
        Colunas.DURACAO_VOLTA: formatar_duracao(oferta.volta.duracao),
        Colunas.CONEXOES_IDA: str(oferta.ida.conexoes),
        Colunas.CONEXOES_VOLTA: str(oferta.volta.conexoes),
        Colunas.PARTIDA_IDA: oferta.ida.partida_em.isoformat(sep=" "),
        Colunas.CHEGADA_IDA: oferta.ida.chegada_em.isoformat(sep=" "),
        Colunas.PARTIDA_VOLTA: oferta.volta.partida_em.isoformat(sep=" "),
        Colunas.CHEGADA_VOLTA: oferta.volta.chegada_em.isoformat(sep=" "),
        Colunas.MOTIVO: oferta.motivo,
    }


def exportar_csv(ofertas: Sequence[Oferta], caminho: Path, /) -> int:
    """Escreve as ofertas em CSV, ordenadas por preço crescente.

    Escreve o cabeçalho mesmo quando não há oferta nenhuma: um arquivo vazio é
    ambíguo, um arquivo só com cabeçalho diz "rodou e não achou".

    Args:
        ofertas: Ofertas a exportar.
        caminho: Arquivo de saída. O diretório é criado se preciso.

    Returns:
        Número de linhas de dados escritas.
    """
    ordenadas = sorted(ofertas, key=lambda o: (o.preco_total, o.duracao_maxima))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=[str(c) for c in Colunas])
        escritor.writeheader()
        for oferta in ordenadas:
            escritor.writerow(_linha(oferta))
    return len(ordenadas)


def resumo_terminal(ofertas: Sequence[Oferta], /, *, limite: int = 15) -> str:
    """Monta o resumo das melhores ofertas para imprimir no terminal.

    Args:
        ofertas: Ofertas já filtradas.
        limite: Quantas linhas mostrar.

    Returns:
        Texto pronto para imprimir.
    """
    if not ofertas:
        return (
            "Nenhuma oferta dentro do teto de duração. Tente ampliar --max-duracao ou o horizonte."
        )

    melhores = sorted(ofertas, key=lambda o: (o.preco_total, o.duracao_maxima))[:limite]
    cabecalho = (
        f"{'rota':<9} {'ida':<10} {'volta':<10} {'preço':>10}  "
        f"{'ida':>6} {'volta':>6} {'cx':>3}  companhia"
    )
    linhas = [cabecalho, "-" * len(cabecalho)]
    for o in melhores:
        rota = f"{o.origem}-{o.destino}"
        conexoes = max(o.ida.conexoes, o.volta.conexoes)
        linhas.append(
            f"{rota:<9} {o.ida.partida_em.date().isoformat():<10} "
            f"{o.volta.partida_em.date().isoformat():<10} "
            f"{o.moeda} {o.preco_total:>9.2f}  "
            f"{formatar_duracao(o.ida.duracao):>6} {formatar_duracao(o.volta.duracao):>6} "
            f"{conexoes:>3}  {o.companhia}"
        )
    return "\n".join(linhas)
