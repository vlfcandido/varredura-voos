"""Contador persistente de chamadas à API, por mês-calendário.

O ambiente de teste da Amadeus é gratuito mas tem cota mensal, e uma varredura
completa de 2 origens por 32 destinos por ~38 janelas pede mais de duas mil
chamadas — o suficiente para queimar o mês inteiro numa execução só. O orçamento
existe para que a ferramenta pare por decisão, e diga o que deixou de varrer, em
vez de morrer com erro de cota no meio.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path


class OrcamentoEsgotado(RuntimeError):
    """A cota de chamadas do mês acabou."""


class Orcamento:
    """Controla e persiste quantas chamadas já foram gastas no mês.

    O arquivo guarda um contador por mês (`"2026-08"`), então a virada do mês
    zera o disponível sem apagar o histórico do mês anterior.

    Attributes:
        caminho: Arquivo JSON onde o contador é persistido.
        teto: Número máximo de chamadas permitidas no mês.
    """

    def __init__(self, caminho: Path, /, *, teto: int, mes: date) -> None:
        """Carrega o contador do mês informado.

        Args:
            caminho: Arquivo JSON de persistência. Não precisa existir.
            teto: Máximo de chamadas no mês.
            mes: Qualquer data dentro do mês em questão.

        Raises:
            ValueError: Se o teto não for positivo.
        """
        if teto <= 0:
            msg = f"teto de chamadas precisa ser positivo, veio {teto}"
            raise ValueError(msg)
        self.caminho = caminho
        self.teto = teto
        self._chave = f"{mes.year:04d}-{mes.month:02d}"
        self._contadores = self._ler()

    def _ler(self) -> dict[str, int]:
        """Lê os contadores do disco, tolerando ausência e corrupção.

        Returns:
            Mapa de mês para número de chamadas gastas.
        """
        try:
            bruto = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(bruto, dict):
            return {}
        return {str(k): int(v) for k, v in bruto.items() if isinstance(v, int)}

    def _gravar(self) -> None:
        """Persiste os contadores em disco, de forma atômica."""
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho.with_suffix(".tmp")
        temporario.write_text(json.dumps(self._contadores, ensure_ascii=False), encoding="utf-8")
        temporario.replace(self.caminho)

    def gastas(self) -> int:
        """Quantas chamadas já foram gastas no mês corrente.

        Returns:
            Número de chamadas já consumidas.
        """
        return self._contadores.get(self._chave, 0)

    def restante(self) -> int:
        """Quantas chamadas ainda cabem no mês.

        Returns:
            Número de chamadas disponíveis, nunca negativo.
        """
        return max(0, self.teto - self.gastas())

    def consumir(self, n: int = 1, /) -> None:
        """Debita chamadas do orçamento do mês.

        O débito é tudo-ou-nada: um pedido que não cabe não consome nada, para
        que o chamador possa decidir o que fazer sem ter gasto pela metade.

        Args:
            n: Número de chamadas a debitar.

        Raises:
            ValueError: Se `n` não for positivo.
            OrcamentoEsgotado: Se o pedido não couber no restante do mês.
        """
        if n <= 0:
            msg = f"consumo precisa ser positivo, veio {n}"
            raise ValueError(msg)
        if n > self.restante():
            msg = (
                f"cota do mês {self._chave} esgotada: pedidas {n}, "
                f"restam {self.restante()} de {self.teto}"
            )
            raise OrcamentoEsgotado(msg)
        self._contadores[self._chave] = self.gastas() + n
        self._gravar()
