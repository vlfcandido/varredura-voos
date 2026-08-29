"""Cache em disco das respostas da Amadeus, com validade.

Existe por um motivo só: a cota mensal gratuita do ambiente de teste é pequena
perto do tamanho de uma varredura completa. Repetir a mesma consulta no mesmo
dia não pode custar chamada.

A chave é derivada de origem, destino, datas, número de passageiros e moeda —
tudo que muda a resposta, e nada mais. O motivo da janela, por exemplo, não
entra: a mesma dupla de datas devolve a mesma coisa venha ela de feriado ou de
fim de semana.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta
    from pathlib import Path

    from varredura_voos.modelos import Consulta

_CAMPO_GRAVADO_EM = "gravado_em"
_CAMPO_RESPOSTA = "resposta"


class CacheDisco:
    """Cache de respostas cruas em arquivos JSON, com TTL.

    Attributes:
        raiz: Diretório onde os arquivos são gravados.
        ttl: Tempo de validade de cada entrada.
    """

    def __init__(
        self,
        raiz: Path,
        /,
        *,
        ttl: timedelta,
        relogio: Callable[[], datetime] | None = None,
    ) -> None:
        """Monta o cache sem tocar em disco.

        O diretório só é criado na primeira gravação, para que instanciar o
        cache continue sendo uma operação sem efeito colateral.

        Args:
            raiz: Diretório raiz do cache.
            ttl: Validade de cada entrada.
            relogio: Função que devolve o instante atual. Injetável para teste;
                o padrão é `datetime.now`.
        """
        self.raiz = raiz
        self.ttl = ttl
        self._relogio = relogio if relogio is not None else datetime.now
        self._trava = asyncio.Lock()

    def chave(self, consulta: Consulta, /) -> str:
        """Deriva a chave determinística de uma consulta.

        Args:
            consulta: Consulta a identificar.

        Returns:
            Hash hexadecimal SHA-256 da forma canônica da consulta.
        """
        return hashlib.sha256(consulta.como_chave().encode("utf-8")).hexdigest()

    def caminho(self, consulta: Consulta, /) -> Path:
        """Caminho do arquivo que guarda a resposta de uma consulta.

        Args:
            consulta: Consulta a localizar.

        Returns:
            Caminho do arquivo, existindo ou não.
        """
        chave = self.chave(consulta)
        return self.raiz / chave[:2] / f"{chave}.json"

    async def obter(self, consulta: Consulta, /) -> dict[str, Any] | None:
        """Lê a resposta em cache, se existir e ainda estiver válida.

        Um arquivo ilegível ou corrompido é tratado como ausência: o cache é uma
        otimização, e nunca deve ser motivo de falha da varredura.

        Args:
            consulta: Consulta a procurar.

        Returns:
            A resposta crua, ou `None` em caso de ausência, expiração ou
            arquivo corrompido.
        """
        caminho = self.caminho(consulta)
        try:
            bruto = await asyncio.to_thread(caminho.read_text, encoding="utf-8")
            envelope = json.loads(bruto)
            gravado_em = datetime.fromisoformat(envelope[_CAMPO_GRAVADO_EM])
            resposta = envelope[_CAMPO_RESPOSTA]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if self._relogio() - gravado_em > self.ttl:
            return None
        return resposta if isinstance(resposta, dict) else None

    async def guardar(self, consulta: Consulta, resposta: dict[str, Any], /) -> None:
        """Grava a resposta de uma consulta, criando o diretório se preciso.

        A escrita é atômica: grava num temporário e renomeia, para que uma
        interrupção no meio não deixe um arquivo pela metade.

        Args:
            consulta: Consulta que produziu a resposta.
            resposta: Corpo cru devolvido pela API.
        """
        caminho = self.caminho(consulta)
        envelope = {
            _CAMPO_GRAVADO_EM: self._relogio().isoformat(),
            "consulta": consulta.como_chave(),
            _CAMPO_RESPOSTA: resposta,
        }
        conteudo = json.dumps(envelope, ensure_ascii=False)

        def _escrever() -> None:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            temporario = caminho.with_suffix(".tmp")
            temporario.write_text(conteudo, encoding="utf-8")
            temporario.replace(caminho)

        async with self._trava:
            await asyncio.to_thread(_escrever)
