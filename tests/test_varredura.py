"""Testes da orquestração: o funil que decide quando gastar uma chamada."""

import asyncio
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from varredura_voos.cache import CacheDisco
from varredura_voos.cliente_amadeus import ClienteAmadeus
from varredura_voos.config import Config, Credenciais, carregar_config
from varredura_voos.modelos import Janela
from varredura_voos.orcamento import Orcamento
from varredura_voos.varredura import montar_consultas, varrer

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "flight_offers_cac_gru.json").read_text(encoding="utf-8")
)
CRED = Credenciais(client_id="k", client_secret="s")
TOKEN = {"access_token": "t", "expires_in": 1799}

JANELAS = [
    Janela(
        ida=date(2026, 9, 4), volta=date(2026, 9, 7), motivo="feriado: Independência", prioridade=0
    ),
    Janela(ida=date(2026, 9, 11), volta=date(2026, 9, 14), motivo="fim de semana", prioridade=10),
]


@pytest.fixture
def cfg() -> Config:
    """Config reduzida: uma origem e quatro destinos, para a conta fechar na mão."""
    return replace(
        carregar_config(),
        origens=("CAC",),
        destinos=("GRU", "CWB", "MAO", "BEL"),
    )


class Rede:
    def __init__(self, respostas: dict[str, httpx.Response] | None = None) -> None:
        self.chamadas = 0
        self.pares: list[str] = []
        self.respostas: dict[str, httpx.Response] = respostas or {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json=TOKEN)
        self.chamadas += 1
        destino = req.url.params["destinationLocationCode"]
        self.pares.append(f"{req.url.params['originLocationCode']}-{destino}")
        if destino in self.respostas:
            return self.respostas[destino]
        return httpx.Response(200, json=FIXTURE)


def montar(
    rede: Rede, cfg: Config, tmp_path: Path, *, teto: int = 1000
) -> tuple[httpx.AsyncClient, ClienteAmadeus, CacheDisco, Orcamento]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(rede))
    cliente = ClienteAmadeus(http, CRED, semaforo=asyncio.Semaphore(cfg.concorrencia))
    cache = CacheDisco(tmp_path / "cache", ttl=cfg.ttl_cache)
    orcamento = Orcamento(tmp_path / "cota.json", teto=teto, mes=date(2026, 8, 29))
    return http, cliente, cache, orcamento


# --- montagem das consultas ---------------------------------------------------------
def test_consultas_sao_o_produto_de_origens_destinos_e_janelas(cfg: Config) -> None:
    consultas = montar_consultas(cfg, JANELAS)
    assert len(consultas) == 1 * 4 * 2


def test_poda_agressiva_remove_pares_antes_de_qualquer_chamada(cfg: Config) -> None:
    consultas = montar_consultas(cfg, JANELAS, agressiva=True)
    destinos = {c.destino for c in consultas}
    assert destinos == {"GRU", "CWB"}, "MAO e BEL não cabem em 6h nem no melhor caso"


def test_consultas_saem_com_feriado_na_frente(cfg: Config) -> None:
    consultas = montar_consultas(cfg, JANELAS)
    assert consultas[0].janela.prioridade == 0
    assert [c.janela.prioridade for c in consultas] == sorted(
        c.janela.prioridade for c in consultas
    )


# --- varredura ----------------------------------------------------------------------
async def test_varredura_completa_filtra_por_duracao(cfg: Config, tmp_path: Path) -> None:
    rede = Rede()
    http, cliente, cache, orcamento = montar(rede, cfg, tmp_path)
    async with http:
        r = await varrer(cfg, JANELAS, cliente, cache, orcamento)
    assert rede.chamadas == 8
    assert r.chamadas == 8
    # 8 consultas x 3 ofertas na fixture, menos a de 12h10 que o teto derruba.
    assert len(r.ofertas) == 16
    assert all(o.duracao_maxima <= cfg.max_duracao for o in r.ofertas)


async def test_rota_podada_nao_consome_orcamento(cfg: Config, tmp_path: Path) -> None:
    rede = Rede()
    http, cliente, cache, orcamento = montar(rede, cfg, tmp_path)
    async with http:
        r = await varrer(cfg, JANELAS, cliente, cache, orcamento, agressiva=True)
    assert rede.chamadas == 4, "só GRU e CWB, nas duas janelas"
    assert r.podadas == 4
    assert orcamento.gastas() == 4


async def test_cache_hit_nao_consome_orcamento(cfg: Config, tmp_path: Path) -> None:
    rede = Rede()
    http, cliente, cache, orcamento = montar(rede, cfg, tmp_path)
    async with http:
        await varrer(cfg, JANELAS, cliente, cache, orcamento)
        gastas_apos_primeira = orcamento.gastas()
        r2 = await varrer(cfg, JANELAS, cliente, cache, orcamento)
    assert gastas_apos_primeira == 8
    assert r2.chamadas == 0
    assert r2.cache_hits == 8
    assert orcamento.gastas() == 8, "a segunda varredura não pode custar cota"
    assert len(r2.ofertas) == 16, "e ainda assim devolve as ofertas"


async def test_orcamento_esgotado_para_e_relata_o_que_faltou(cfg: Config, tmp_path: Path) -> None:
    rede = Rede()
    http, cliente, cache, orcamento = montar(rede, cfg, tmp_path, teto=3)
    async with http:
        r = await varrer(cfg, JANELAS, cliente, cache, orcamento)
    assert r.chamadas == 3
    assert len(r.nao_varridas) == 5
    assert r.orcamento_esgotado is True
    # as três gastas têm de ser as de feriado, que vêm primeiro
    assert all(c.janela.prioridade == 0 for c in r.consultas_feitas)


async def test_erro_num_par_nao_derruba_a_varredura(cfg: Config, tmp_path: Path) -> None:
    corpo = {"errors": [{"status": 400, "detail": "origem não atendida"}]}
    rede = Rede({"MAO": httpx.Response(400, json=corpo)})
    http, cliente, cache, orcamento = montar(rede, cfg, tmp_path)
    async with http:
        r = await varrer(cfg, JANELAS, cliente, cache, orcamento)
    assert len(r.erros) == 2, "as duas janelas de MAO falharam"
    assert len(r.ofertas) == 12, "os outros três destinos seguiram"


async def test_pares_sem_oferta_dentro_do_teto_sao_relatados(cfg: Config, tmp_path: Path) -> None:
    """Serve para o usuário podar a lista de destinos com dado, não com palpite."""
    so_longa = {"data": [FIXTURE["data"][2]]}  # a de 12h10
    rede = Rede({"BEL": httpx.Response(200, json=so_longa)})
    http, cliente, cache, orcamento = montar(rede, cfg, tmp_path)
    async with http:
        r = await varrer(cfg, JANELAS, cliente, cache, orcamento)
    assert ("CAC", "BEL") in r.pares_sem_oferta
    assert ("CAC", "GRU") not in r.pares_sem_oferta
