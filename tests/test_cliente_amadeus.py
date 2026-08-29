"""Testes do cliente da Amadeus. Nenhum toca a rede: tudo via MockTransport."""

import asyncio
from typing import Any

import httpx
import pytest

from varredura_voos.cliente_amadeus import ClienteAmadeus, ErroAmadeus
from varredura_voos.config import Credenciais
from varredura_voos.modelos import Consulta

BASE = "https://test.api.amadeus.com"
CRED = Credenciais(client_id="minha-chave", client_secret="meu-segredo")
TOKEN_OK = {"access_token": "tok-123", "expires_in": 1799, "type": "amadeusOAuth2Token"}
OFERTAS_OK = {"data": [], "meta": {"count": 0}}


class Servidor:
    """Servidor falso que registra as requisições e devolve respostas roteirizadas."""

    def __init__(self, roteiro: list[httpx.Response] | None = None) -> None:
        self.requisicoes: list[httpx.Request] = []
        self.roteiro = roteiro or []
        self.tokens_emitidos = 0

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        self.requisicoes.append(requisicao)
        if requisicao.url.path.endswith("/oauth2/token"):
            self.tokens_emitidos += 1
            return httpx.Response(200, json=TOKEN_OK)
        if self.roteiro:
            return self.roteiro.pop(0)
        return httpx.Response(200, json=OFERTAS_OK)

    @property
    def buscas(self) -> list[httpx.Request]:
        return [r for r in self.requisicoes if "flight-offers" in r.url.path]


def montar(servidor: Servidor, **kwargs: Any) -> tuple[ClienteAmadeus, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(servidor), base_url=BASE)
    dormidas: list[float] = kwargs.pop("dormidas", [])

    async def dormir(s: float) -> None:
        dormidas.append(s)

    cliente = ClienteAmadeus(http, CRED, semaforo=asyncio.Semaphore(8), dormir=dormir, **kwargs)
    return cliente, http


async def test_busca_pede_token_uma_vez_e_reaproveita(consulta: Consulta) -> None:
    servidor = Servidor()
    cliente, http = montar(servidor)
    async with http:
        await cliente.buscar_ofertas(consulta)
        await cliente.buscar_ofertas(consulta)
    assert servidor.tokens_emitidos == 1
    assert len(servidor.buscas) == 2


async def test_token_vai_no_header_authorization(consulta: Consulta) -> None:
    servidor = Servidor()
    cliente, http = montar(servidor)
    async with http:
        await cliente.buscar_ofertas(consulta)
    assert servidor.buscas[0].headers["authorization"] == "Bearer tok-123"


async def test_parametros_da_busca(consulta: Consulta) -> None:
    servidor = Servidor()
    cliente, http = montar(servidor)
    async with http:
        await cliente.buscar_ofertas(consulta)
    p = servidor.buscas[0].url.params
    assert p["originLocationCode"] == "CAC"
    assert p["destinationLocationCode"] == "GRU"
    assert p["departureDate"] == "2026-09-04"
    assert p["returnDate"] == "2026-09-07"
    assert p["adults"] == "2"
    assert p["currencyCode"] == "BRL"


async def test_429_provoca_retry_e_acaba_dando_certo(consulta: Consulta) -> None:
    dormidas: list[float] = []
    servidor = Servidor(
        [httpx.Response(429), httpx.Response(429), httpx.Response(200, json=OFERTAS_OK)]
    )
    cliente, http = montar(servidor, dormidas=dormidas)
    async with http:
        assert await cliente.buscar_ofertas(consulta) == OFERTAS_OK
    assert len(servidor.buscas) == 3
    # `dormidas` também recebe as esperas de rate limit (<= 100 ms). O backoff
    # começa em 0,5 s, então filtrar por isso isola o que este teste julga.
    backoff = [d for d in dormidas if d >= 0.5]
    assert len(backoff) == 2
    assert backoff[1] > backoff[0], "backoff tem de crescer"


async def test_500_provoca_retry(consulta: Consulta) -> None:
    servidor = Servidor([httpx.Response(503), httpx.Response(200, json=OFERTAS_OK)])
    cliente, http = montar(servidor)
    async with http:
        await cliente.buscar_ofertas(consulta)
    assert len(servidor.buscas) == 2


async def test_400_nao_provoca_retry(consulta: Consulta) -> None:
    corpo = {"errors": [{"status": 400, "code": 32171, "detail": "originLocationCode inválido"}]}
    servidor = Servidor([httpx.Response(400, json=corpo)])
    cliente, http = montar(servidor)
    async with http:
        with pytest.raises(ErroAmadeus) as info:
            await cliente.buscar_ofertas(consulta)
    assert len(servidor.buscas) == 1, "400 é erro do pedido: repetir não conserta"
    assert info.value.status == 400
    assert "originLocationCode inválido" in str(info.value)


async def test_retry_esgotado_levanta(consulta: Consulta) -> None:
    servidor = Servidor([httpx.Response(429) for _ in range(10)])
    cliente, http = montar(servidor, max_tentativas=3)
    async with http:
        with pytest.raises(ErroAmadeus) as info:
            await cliente.buscar_ofertas(consulta)
    assert info.value.status == 429
    assert len(servidor.buscas) == 3


async def test_token_expirado_e_renovado_uma_vez(consulta: Consulta) -> None:
    servidor = Servidor([httpx.Response(401), httpx.Response(200, json=OFERTAS_OK)])
    cliente, http = montar(servidor)
    async with http:
        await cliente.buscar_ofertas(consulta)
    assert servidor.tokens_emitidos == 2, "401 tem de forçar um token novo"
    assert len(servidor.buscas) == 2


async def test_chamadas_concorrentes_pedem_um_token_so(consulta: Consulta) -> None:
    """Sem trava, oito consultas simultâneas pediriam oito tokens."""
    servidor = Servidor()
    cliente, http = montar(servidor)
    async with http:
        await asyncio.gather(*(cliente.buscar_ofertas(consulta) for _ in range(8)))
    assert servidor.tokens_emitidos == 1
    assert len(servidor.buscas) == 8


async def test_semaforo_limita_a_concorrencia(consulta: Consulta) -> None:
    em_voo = 0
    pico = 0

    def handler(requisicao: httpx.Request) -> httpx.Response:
        nonlocal em_voo, pico
        if requisicao.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json=TOKEN_OK)
        em_voo += 1
        pico = max(pico, em_voo)
        em_voo -= 1
        return httpx.Response(200, json=OFERTAS_OK)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE)
    cliente = ClienteAmadeus(http, CRED, semaforo=asyncio.Semaphore(3))
    async with http:
        await asyncio.gather(*(cliente.buscar_ofertas(consulta) for _ in range(12)))
    assert pico <= 3


async def test_segredo_nunca_aparece_em_erro(consulta: Consulta) -> None:
    servidor = Servidor([httpx.Response(400, json={"errors": [{"detail": "x"}]})])
    cliente, http = montar(servidor)
    async with http:
        with pytest.raises(ErroAmadeus) as info:
            await cliente.buscar_ofertas(consulta)
    texto = f"{info.value!r} {info.value!s} {CRED!r}"
    assert "meu-segredo" not in texto
    assert "minha-chave" not in texto


async def test_credenciais_vao_no_corpo_do_token_e_nao_na_url(consulta: Consulta) -> None:
    servidor = Servidor()
    cliente, http = montar(servidor)
    async with http:
        await cliente.buscar_ofertas(consulta)
    pedido_token = servidor.requisicoes[0]
    assert "meu-segredo" not in str(pedido_token.url)
    assert b"meu-segredo" in pedido_token.content


async def test_inspiracao_devolve_none_quando_origem_nao_e_suportada() -> None:
    """CAC e IGU não são cobertos pelo Inspiration Search; a falha é esperada."""
    corpo = {"errors": [{"status": 500, "detail": "ORIGIN AND DESTINATION NOT SUPPORTED"}]}
    servidor = Servidor([httpx.Response(500, json=corpo)])
    cliente, http = montar(servidor, max_tentativas=1)
    async with http:
        assert await cliente.inspirar("CAC") is None


async def test_inspiracao_devolve_destinos_quando_funciona() -> None:
    corpo = {"data": [{"destination": "GRU"}, {"destination": "CWB"}]}
    servidor = Servidor([httpx.Response(200, json=corpo)])
    cliente, http = montar(servidor)
    async with http:
        assert await cliente.inspirar("GRU") == ["GRU", "CWB"]
