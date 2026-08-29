"""Testes do cache em disco: hit, miss e expiração."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from varredura_voos.cache import CacheDisco
from varredura_voos.modelos import Consulta, Janela


class RelogioFalso:
    """Relógio injetável, para testar TTL sem esperar e sem monkeypatch."""

    def __init__(self, agora: datetime) -> None:
        self.agora = agora

    def __call__(self) -> datetime:
        return self.agora

    def avancar(self, delta: timedelta) -> None:
        self.agora += delta


@pytest.fixture
def relogio() -> RelogioFalso:
    return RelogioFalso(datetime(2026, 8, 29, 12, 0, 0))


@pytest.fixture
def cache(tmp_path: Path, relogio: RelogioFalso) -> CacheDisco:
    return CacheDisco(tmp_path / "cache", ttl=timedelta(hours=12), relogio=relogio)


RESPOSTA = {"data": [{"id": "1", "price": {"total": "1234.56"}}]}


async def test_miss_devolve_none(cache: CacheDisco, consulta: Consulta) -> None:
    assert await cache.obter(consulta) is None


async def test_hit_depois_de_guardar(cache: CacheDisco, consulta: Consulta) -> None:
    await cache.guardar(consulta, RESPOSTA)
    assert await cache.obter(consulta) == RESPOSTA


async def test_expira_depois_do_ttl(
    cache: CacheDisco, consulta: Consulta, relogio: RelogioFalso
) -> None:
    await cache.guardar(consulta, RESPOSTA)
    relogio.avancar(timedelta(hours=11, minutes=59))
    assert await cache.obter(consulta) == RESPOSTA, "ainda dentro do TTL"
    relogio.avancar(timedelta(minutes=2))
    assert await cache.obter(consulta) is None, "passou do TTL"


async def test_consultas_diferentes_nao_colidem(cache: CacheDisco, consulta: Consulta) -> None:
    outra = consulta.model_copy(update={"destino": "CWB"})
    await cache.guardar(consulta, RESPOSTA)
    assert await cache.obter(outra) is None


def test_chave_depende_de_origem_destino_datas_pax_e_moeda(cache: CacheDisco) -> None:
    base = Consulta(
        origem="CAC",
        destino="GRU",
        janela=Janela(ida=date(2026, 9, 4), volta=date(2026, 9, 7), motivo="x"),
        adultos=2,
        moeda="BRL",
    )
    chave = cache.chave(base)
    assert chave == cache.chave(
        base.model_copy(
            update={"janela": base.janela.model_copy(update={"motivo": "outro motivo qualquer"})}
        )
    ), "o motivo da janela não entra na chave"
    for mudanca in (
        {"origem": "IGU"},
        {"destino": "CWB"},
        {"adultos": 1},
        {"moeda": "USD"},
    ):
        assert cache.chave(base.model_copy(update=mudanca)) != chave, mudanca
    nova_data = base.janela.model_copy(update={"volta": date(2026, 9, 8)})
    assert cache.chave(base.model_copy(update={"janela": nova_data})) != chave


async def test_arquivo_corrompido_e_tratado_como_miss(
    cache: CacheDisco, consulta: Consulta
) -> None:
    await cache.guardar(consulta, RESPOSTA)
    caminho = cache.caminho(consulta)
    caminho.write_text("{isso não é json", encoding="utf-8")
    assert await cache.obter(consulta) is None
