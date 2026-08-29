"""Cliente da Amadeus Self-Service: OAuth2, chamadas, rate limit e retry.

O cliente HTTP é **injetado**: o módulo não cria `httpx.AsyncClient` nenhum no
import, nem lê variável de ambiente. Quem monta o cliente é o entrypoint, e o
teste injeta um `MockTransport` no lugar da rede.

Limites do ambiente de teste, conforme a documentação: 10 transações por segundo
por usuário e no máximo uma requisição a cada 100 ms. Em produção sobe para 40
TPS, mantendo os 100 ms. O semáforo e o intervalo mínimo entre chamadas saem da
configuração, e o padrão fica com folga abaixo do limite.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from varredura_voos.config import Credenciais
    from varredura_voos.modelos import Consulta

BASE_TESTE = "https://test.api.amadeus.com"
BASE_PRODUCAO = "https://api.amadeus.com"

_ROTA_TOKEN = "/v1/security/oauth2/token"
_ROTA_OFERTAS = "/v2/shopping/flight-offers"
_ROTA_INSPIRACAO = "/v1/shopping/flight-destinations"

_MARGEM_TOKEN_S = 60.0
_INTERVALO_MINIMO_S = 0.1  # a doc exige no máximo 1 requisição a cada 100 ms
_STATUS_QUE_MERECEM_RETRY = frozenset({429, 500, 502, 503, 504})
_STATUS_TOKEN_INVALIDO = 401


class ErroAmadeus(RuntimeError):
    """A API respondeu com erro que não vale a pena repetir, ou esgotou o retry.

    Attributes:
        status: Código HTTP devolvido.
        detalhe: Texto do campo `errors[].detail`, quando existe.
    """

    def __init__(self, status: int, detalhe: str) -> None:
        """Monta o erro.

        Args:
            status: Código HTTP.
            detalhe: Descrição vinda da API. Nunca inclui credencial.
        """
        super().__init__(f"Amadeus respondeu {status}: {detalhe}")
        self.status = status
        self.detalhe = detalhe


def _detalhe_do_erro(resposta: httpx.Response, /) -> str:
    """Extrai a explicação de uma resposta de erro da Amadeus.

    Args:
        resposta: Resposta HTTP de erro.

    Returns:
        O primeiro `detail` ou `title` encontrado, ou o motivo HTTP.
    """
    try:
        corpo = resposta.json()
        erros = corpo.get("errors") or []
        if erros:
            primeiro = erros[0]
            return str(primeiro.get("detail") or primeiro.get("title") or resposta.reason_phrase)
    except (ValueError, AttributeError, TypeError):
        pass
    return resposta.reason_phrase or "sem detalhe"


class ClienteAmadeus:
    """Fachada assíncrona sobre as APIs de voo da Amadeus Self-Service."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        credenciais: Credenciais,
        /,
        *,
        semaforo: asyncio.Semaphore,
        base_url: str = BASE_TESTE,
        max_tentativas: int = 4,
        backoff_base_s: float = 0.5,
        dormir: Callable[[float], Awaitable[None]] | None = None,
        agora: Callable[[], float] | None = None,
    ) -> None:
        """Monta o cliente sem fazer requisição nenhuma.

        Args:
            http: Cliente HTTP assíncrono já configurado. O chamador é dono dele
                e responsável por fechá-lo.
            credenciais: Par client_id/client_secret da Amadeus.
            semaforo: Limita quantas chamadas correm ao mesmo tempo. Mantenha
                abaixo de 10 no ambiente de teste.
            base_url: Raiz da API. Use `BASE_PRODUCAO` para produção.
            max_tentativas: Total de tentativas por chamada, contando a primeira.
            backoff_base_s: Base do backoff exponencial entre tentativas.
            dormir: Função de espera. Injetável para teste; padrão `asyncio.sleep`.
            agora: Relógio monotônico em segundos. Injetável para teste.
        """
        self._http = http
        self._credenciais = credenciais
        self._semaforo = semaforo
        self._base_url = base_url.rstrip("/")
        self._max_tentativas = max(1, max_tentativas)
        self._backoff_base_s = backoff_base_s
        self._dormir = dormir if dormir is not None else asyncio.sleep
        self._agora = agora if agora is not None else time.monotonic
        self._token: str | None = None
        self._token_expira_em = 0.0
        self._trava_token = asyncio.Lock()
        self._ultima_chamada = 0.0
        self._trava_ritmo = asyncio.Lock()

    async def _obter_token(self, *, forcar: bool = False) -> str:
        """Devolve um token válido, buscando um novo só quando preciso.

        A trava garante que uma rajada de consultas simultâneas peça um token
        só, em vez de uma por consulta.

        Args:
            forcar: Se `True`, descarta o token atual e busca outro. Usado
                quando a API responde 401.

        Returns:
            O `access_token` corrente.

        Raises:
            ErroAmadeus: Se a autenticação falhar.
        """
        async with self._trava_token:
            if not forcar and self._token is not None and self._agora() < self._token_expira_em:
                return self._token
            resposta = await self._http.post(
                f"{self._base_url}{_ROTA_TOKEN}",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._credenciais.client_id,
                    "client_secret": self._credenciais.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resposta.status_code != httpx.codes.OK:
                raise ErroAmadeus(resposta.status_code, _detalhe_do_erro(resposta))
            corpo = resposta.json()
            self._token = str(corpo["access_token"])
            self._token_expira_em = self._agora() + float(corpo["expires_in"]) - _MARGEM_TOKEN_S
            return self._token

    async def _respeitar_ritmo(self) -> None:
        """Garante o intervalo mínimo de 100 ms entre requisições."""
        async with self._trava_ritmo:
            espera = self._ultima_chamada + _INTERVALO_MINIMO_S - self._agora()
            if espera > 0:
                await self._dormir(espera)
            self._ultima_chamada = self._agora()

    async def _pedir(self, rota: str, parametros: dict[str, Any], /) -> httpx.Response:
        """Executa um GET autenticado com rate limit e retry.

        Repete apenas em 429 e 5xx, com backoff exponencial, e renova o token
        uma vez em caso de 401. Um 4xx de pedido malformado não é repetido:
        insistir não conserta, só queima cota.

        Args:
            rota: Caminho da API, começando com barra.
            parametros: Query string da requisição.

        Returns:
            A resposta bem-sucedida.

        Raises:
            ErroAmadeus: Em erro não repetível ou depois de esgotar as tentativas.
        """
        renovou_token = False
        ultima: httpx.Response | None = None
        for tentativa in range(self._max_tentativas):
            token = await self._obter_token()
            async with self._semaforo:
                await self._respeitar_ritmo()
                resposta = await self._http.get(
                    f"{self._base_url}{rota}",
                    params=parametros,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resposta.status_code == httpx.codes.OK:
                return resposta
            ultima = resposta
            if resposta.status_code == _STATUS_TOKEN_INVALIDO and not renovou_token:
                renovou_token = True
                await self._obter_token(forcar=True)
                continue
            if resposta.status_code not in _STATUS_QUE_MERECEM_RETRY:
                raise ErroAmadeus(resposta.status_code, _detalhe_do_erro(resposta))
            if tentativa < self._max_tentativas - 1:
                await self._dormir(self._backoff_base_s * (2**tentativa))
        assert ultima is not None
        raise ErroAmadeus(ultima.status_code, _detalhe_do_erro(ultima))

    async def buscar_ofertas(
        self, consulta: Consulta, /, *, max_ofertas: int = 50
    ) -> dict[str, Any]:
        """Consulta a Flight Offers Search v2 para um par origem-destino e datas.

        Args:
            consulta: Origem, destino, janela, passageiros e moeda.
            max_ofertas: Teto do parâmetro `max` da API. Não afeta a cota, só o
                tamanho da resposta.

        Returns:
            O corpo JSON cru da resposta.

        Raises:
            ErroAmadeus: Em erro não repetível ou retry esgotado.
        """
        parametros: dict[str, Any] = {
            "originLocationCode": consulta.origem,
            "destinationLocationCode": consulta.destino,
            "departureDate": consulta.janela.ida.isoformat(),
            "returnDate": consulta.janela.volta.isoformat(),
            "adults": consulta.adultos,
            "currencyCode": consulta.moeda,
            "max": max_ofertas,
        }
        resposta = await self._pedir(_ROTA_OFERTAS, parametros)
        corpo: dict[str, Any] = resposta.json()
        return corpo

    async def inspirar(self, origem: str, /) -> list[str] | None:
        """Tenta descobrir destinos pelo Flight Inspiration Search.

        Otimização opcional, e com expectativa baixa de propósito: a API roda
        sobre um cache das rotas mais buscadas e não cobre aeroportos pequenos.
        Há relato de `ORIGIN AND DESTINATION NOT SUPPORTED` até para aeroportos
        grandes como BER e FCO, então CAC e IGU quase certamente não funcionam.
        A falha é esperada e não interrompe a varredura.

        Args:
            origem: Código IATA da origem.

        Returns:
            Lista de códigos IATA de destino, ou `None` se a origem não for
            coberta — caso em que a varredura segue pela lista explícita.
        """
        try:
            resposta = await self._pedir(_ROTA_INSPIRACAO, {"origin": origem})
        except ErroAmadeus:
            return None
        dados = resposta.json().get("data") or []
        return [str(item["destination"]) for item in dados if "destination" in item]
