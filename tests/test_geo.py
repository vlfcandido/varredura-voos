"""Testes do pré-filtro geográfico.

O ponto sensível: o limite inferior tem de ser **otimista de propósito**. Ele só
pode podar quando nem no melhor caso concebível a rota cabe no teto. Um teste
aqui existe justamente para provar que a poda segura NÃO corta rota de borda.
"""

from datetime import timedelta

import pytest

from varredura_voos.config import Config, carregar_config
from varredura_voos.geo import duracao_minima_estimada, rota_viavel

TETO = timedelta(hours=6)


@pytest.fixture
def cfg() -> Config:
    return carregar_config()


def test_distancia_conhecida_cac_gru(cfg: Config) -> None:
    km = cfg.aeroportos["CAC"].distancia_km(cfg.aeroportos["GRU"])
    assert 700 < km < 760, km


def test_rota_direta_usa_o_tempo_do_voo_direto(cfg: Config) -> None:
    """CAC->GRU é direto: a estimativa não pode embutir conexão nenhuma."""
    direto = duracao_minima_estimada("CAC", "GRU", cfg)
    assert timedelta(hours=1) < direto < timedelta(hours=1, minutes=30)


def test_rota_sem_direto_passa_por_um_destino_direto_da_origem(cfg: Config) -> None:
    """CAC não voa para SSA: a estimativa tem de somar duas pernas e uma conexão."""
    via = duracao_minima_estimada("CAC", "SSA", cfg)
    assert via > duracao_minima_estimada("CAC", "GRU", cfg)
    assert timedelta(hours=4) < via < timedelta(hours=6)


def test_estimativa_e_otimista_por_construcao(cfg: Config) -> None:
    """A estimativa tem de ser menor que a mesma rota calculada de forma realista.

    Não se testa contra um horário de voo real — esse número não está disponível
    offline e chutá-lo já produziu um teste errado. Testa-se a propriedade: o
    modelo usa a linha reta na velocidade de cruzeiro máxima e a menor conexão
    possível, então tem de ficar abaixo de qualquer parametrização realista.
    """
    from dataclasses import replace

    realista = replace(
        cfg,
        velocidade_cruzeiro_kmh=700.0,
        overhead_voo=timedelta(minutes=45),
        conexao_minima=timedelta(minutes=90),
    )
    for destino in ("GRU", "SSA", "MAO"):
        assert duracao_minima_estimada("CAC", destino, cfg) < duracao_minima_estimada(
            "CAC", destino, realista
        ), destino


def test_conexao_so_aumenta_a_estimativa(cfg: Config) -> None:
    """Rota com escala nunca pode estimar menos que a perna direta que a inicia."""
    via_gru_ate_ssa = duracao_minima_estimada("CAC", "SSA", cfg)
    assert via_gru_ate_ssa > duracao_minima_estimada("CAC", "GRU", cfg) + cfg.conexao_minima


def test_poda_segura_nao_corta_rota_de_borda(cfg: Config) -> None:
    """Medido: com conexão mínima de 45min nada é podado no teto de 6h.

    Está aqui como documentação executável do limite do método — se algum dia
    a poda segura começar a cortar, é porque o modelo mudou e alguém precisa
    reavaliar se ainda é um limite inferior honesto.
    """
    podados = [d for d in cfg.destinos if d != "CAC" and not rota_viavel("CAC", d, cfg, teto=TETO)]
    assert podados == [], f"poda segura não deveria cortar nada, cortou: {podados}"


def test_poda_agressiva_corta_o_norte_e_o_nordeste_distante(cfg: Config) -> None:
    podados = {
        d
        for d in cfg.destinos
        if d != "CAC" and not rota_viavel("CAC", d, cfg, teto=TETO, agressiva=True)
    }
    assert {"MAO", "BEL", "FOR", "NAT", "SLZ"} <= podados
    assert "GRU" not in podados
    assert "CWB" not in podados


def test_aeroporto_desconhecido_falha_explicito(cfg: Config) -> None:
    with pytest.raises(KeyError, match="XYZ"):
        duracao_minima_estimada("CAC", "XYZ", cfg)


def test_origem_sem_rotas_diretas_declaradas_falha_explicito(cfg: Config) -> None:
    with pytest.raises(KeyError, match="GRU"):
        duracao_minima_estimada("GRU", "SSA", cfg)
