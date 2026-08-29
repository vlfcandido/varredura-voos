"""Pré-filtro geográfico: descarta rotas impossíveis antes de gastar chamada.

O método estima um **limite inferior** de duração para o par origem-destino e
poda quando nem esse melhor caso concebível cabe no teto. Ser um limite inferior
é o que torna a poda segura: ela nunca esconde uma oferta que existiria.

Limitação medida, e importante: com conexão mínima de 45 min e 800 km/h, o
limite é frouxo demais para cortar qualquer par dos 32 destinos num teto de 6h.
A poda segura serve de rede de proteção para configurações mais apertadas, não
de economia de cota. Quem corta de fato é a poda agressiva (`agressiva=True`),
que usa uma conexão realista de 90 min — e aí deixa de ser limite inferior e
passa a poder esconder oferta de borda. Por isso vive atrás de uma flag.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from varredura_voos.config import Config


def _coordenada(iata: str, cfg: Config, /) -> object:
    """Busca a coordenada de um IATA, com erro explícito se faltar.

    Args:
        iata: Código IATA do aeroporto.
        cfg: Configuração com a tabela de aeroportos.

    Returns:
        A coordenada do aeroporto.

    Raises:
        KeyError: Se o código não estiver na configuração.
    """
    try:
        return cfg.aeroportos[iata]
    except KeyError as erro:
        msg = f"aeroporto sem coordenada na configuração: {iata}"
        raise KeyError(msg) from erro


def duracao_voo_direto(origem: str, destino: str, cfg: Config, /) -> timedelta:
    """Estima a duração mínima de um voo direto entre dois aeroportos.

    Usa distância de grande círculo dividida pela velocidade de cruzeiro, mais
    um tempo fixo de taxi, subida e descida. É deliberadamente otimista: o voo
    real percorre mais que a linha reta e enfrenta espera de tráfego.

    Args:
        origem: Código IATA de origem.
        destino: Código IATA de destino.
        cfg: Configuração com coordenadas e parâmetros de estimativa.

    Returns:
        Duração mínima estimada do voo direto.

    Raises:
        KeyError: Se algum dos aeroportos não estiver na configuração.
    """
    a = cfg.aeroportos[origem] if origem in cfg.aeroportos else _coordenada(origem, cfg)
    b = cfg.aeroportos[destino] if destino in cfg.aeroportos else _coordenada(destino, cfg)
    km = a.distancia_km(b)  # type: ignore[attr-defined]
    horas = km / cfg.velocidade_cruzeiro_kmh
    return timedelta(hours=horas) + cfg.overhead_voo


def duracao_minima_estimada(
    origem: str,
    destino: str,
    cfg: Config,
    /,
    *,
    agressiva: bool = False,
) -> timedelta:
    """Estima o limite inferior de duração de uma perna, direta ou com conexão.

    Se existe rota direta declarada, o limite é a duração do voo direto — não há
    itinerário mais rápido possível. Se não existe, o voo precisa começar por um
    dos destinos diretos da origem, e o limite é o melhor par perna-conexão-perna
    entre eles. Assume que o segundo trecho é direto; se não for, o real só pode
    ser maior, o que preserva a propriedade de limite inferior.

    Args:
        origem: Código IATA de origem.
        destino: Código IATA de destino.
        cfg: Configuração com coordenadas, rotas diretas e parâmetros.
        agressiva: Se `True`, usa a conexão realista em vez da mínima. Deixa de
            ser um limite inferior seguro.

    Returns:
        Duração mínima estimada da perna.

    Raises:
        KeyError: Se algum aeroporto não tiver coordenada, ou se a origem não
            declarar rotas diretas na configuração.
    """
    _coordenada(origem, cfg)
    _coordenada(destino, cfg)
    if origem == destino:
        return timedelta(0)

    try:
        diretos = cfg.rotas_diretas[origem]
    except KeyError as erro:
        msg = f"origem sem rotas diretas declaradas na configuração: {origem}"
        raise KeyError(msg) from erro

    if destino in diretos:
        return duracao_voo_direto(origem, destino, cfg)

    conexao = cfg.conexao_realista if agressiva else cfg.conexao_minima
    candidatos = [
        duracao_voo_direto(origem, escala, cfg) + conexao + duracao_voo_direto(escala, destino, cfg)
        for escala in diretos
        if escala != destino and escala in cfg.aeroportos
    ]
    if not candidatos:
        msg = f"nenhuma escala conhecida liga {origem} a {destino}"
        raise KeyError(msg)
    return min(candidatos)


def rota_viavel(
    origem: str,
    destino: str,
    cfg: Config,
    /,
    *,
    teto: timedelta,
    agressiva: bool = False,
) -> bool:
    """Diz se vale a pena gastar uma chamada de API com este par.

    Args:
        origem: Código IATA de origem.
        destino: Código IATA de destino.
        cfg: Configuração da varredura.
        teto: Duração máxima aceita por perna.
        agressiva: Se `True`, usa a poda agressiva.

    Returns:
        `False` só quando nem a estimativa mais otimista cabe no teto.

    Raises:
        KeyError: Nos mesmos casos de `duracao_minima_estimada`.
    """
    return duracao_minima_estimada(origem, destino, cfg, agressiva=agressiva) <= teto
