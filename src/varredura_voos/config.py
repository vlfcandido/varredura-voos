"""Configuração da varredura, carregada por função explícita.

Nada aqui roda no import: o arquivo só é lido quando alguém chama
`carregar_config`, e as credenciais só são lidas em `carregar_credenciais`.
Isso mantém o módulo importável em teste sem tocar em disco nem em ambiente.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from varredura_voos.modelos import Coordenada

_ARQUIVO_PADRAO = "varredura.toml"
_LOCAL_USUARIO = Path("config") / _ARQUIVO_PADRAO


class ConfigInvalida(ValueError):
    """A configuração existe mas não descreve uma varredura executável."""


class CredenciaisAusentes(RuntimeError):
    """As variáveis de ambiente da Amadeus não estão definidas."""


@dataclass(frozen=True, slots=True)
class Credenciais:
    """Par de credenciais OAuth2 da Amadeus Self-Service.

    Attributes:
        client_id: A API Key da aplicação.
        client_secret: O API Secret da aplicação.
    """

    client_id: str
    client_secret: str

    def __repr__(self) -> str:
        """Representação sem vazar o segredo em log ou traceback."""
        return f"Credenciais(client_id={self.client_id[:4]}***, client_secret=***)"


@dataclass(frozen=True, slots=True)
class Config:
    """Parâmetros completos de uma varredura.

    Attributes:
        origens: Códigos IATA de origem, na ordem de preferência.
        destinos: Códigos IATA de destino a varrer.
        adultos: Número de adultos por consulta.
        moeda: Código ISO 4217 do preço pedido à API.
        classe: Cabine pedida à API.
        max_duracao: Teto de duração por perna.
        noites_fim_de_semana: Noites da janela de fim de semana.
        noites_feriado: Noites aceitas nas janelas de feriado.
        max_chamadas_mes: Teto de chamadas à API por mês-calendário.
        concorrencia: Consultas simultâneas — mantenha abaixo de 10 TPS.
        ttl_cache: Validade das respostas em cache.
        velocidade_cruzeiro_kmh: Velocidade usada na estimativa de duração.
        overhead_voo: Tempo fixo somado a cada voo (taxi, subida, descida).
        conexao_minima: Conexão usada no limite inferior seguro.
        conexao_realista: Conexão usada na poda agressiva.
        feriados_excluidos: Nomes de feriados que não geram viagem.
        rotas_diretas: Destinos servidos sem escala a partir de cada origem.
        aeroportos: Coordenadas de cada código IATA conhecido.
    """

    origens: tuple[str, ...]
    destinos: tuple[str, ...]
    adultos: int
    moeda: str
    classe: str
    max_duracao: timedelta
    noites_fim_de_semana: int
    noites_feriado: tuple[int, ...]
    max_chamadas_mes: int
    concorrencia: int
    ttl_cache: timedelta
    velocidade_cruzeiro_kmh: float
    overhead_voo: timedelta
    conexao_minima: timedelta
    conexao_realista: timedelta
    feriados_excluidos: frozenset[str]
    rotas_diretas: Mapping[str, tuple[str, ...]]
    aeroportos: Mapping[str, Coordenada]


def caminho_padrao() -> Path:
    """Resolve qual arquivo de configuração usar quando nenhum é informado.

    A ordem é: `./config/varredura.toml` no diretório de trabalho, para o
    usuário poder editar sem mexer no pacote; senão o padrão embarcado.

    Returns:
        Caminho do arquivo que será lido.
    """
    if _LOCAL_USUARIO.is_file():
        return _LOCAL_USUARIO
    return Path(__file__).parent / "dados" / _ARQUIVO_PADRAO


def carregar_config(caminho: Path | None = None, /) -> Config:
    """Lê a configuração de um TOML e devolve o objeto imutável.

    Args:
        caminho: Arquivo a ler. Se `None`, usa `caminho_padrao()`. Para
            sobrescrever campos depois da leitura — vindos de flags da linha de
            comando, por exemplo — use `dataclasses.replace` no resultado.

    Returns:
        A configuração validada.

    Raises:
        FileNotFoundError: Se o arquivo não existir.
        ConfigInvalida: Se faltar seção ou se um aeroporto citado em `origens`,
            `destinos` ou `rotas_diretas` não tiver coordenada.
    """
    alvo = caminho if caminho is not None else caminho_padrao()
    if not alvo.is_file():
        msg = f"configuração não encontrada: {alvo}"
        raise FileNotFoundError(msg)

    try:
        bruto = tomllib.loads(alvo.read_text(encoding="utf-8"))
        viagem = bruto["viagem"]
        cota = bruto["cota"]
        est = bruto["estimativa"]
        aeroportos = {
            iata: Coordenada(latitude=lat, longitude=lon)
            for iata, (lat, lon) in bruto["aeroportos"].items()
        }
        cfg = Config(
            origens=tuple(viagem["origens"]),
            destinos=tuple(bruto["destinos"]["lista"]),
            adultos=int(viagem["adultos"]),
            moeda=str(viagem["moeda"]),
            classe=str(viagem["classe"]),
            max_duracao=timedelta(hours=float(viagem["max_duracao_horas"])),
            noites_fim_de_semana=int(viagem["noites_fim_de_semana"]),
            noites_feriado=tuple(viagem["noites_feriado"]),
            max_chamadas_mes=int(cota["max_chamadas_mes"]),
            concorrencia=int(cota["concorrencia"]),
            ttl_cache=timedelta(hours=float(cota["ttl_cache_horas"])),
            velocidade_cruzeiro_kmh=float(est["velocidade_cruzeiro_kmh"]),
            overhead_voo=timedelta(minutes=float(est["overhead_voo_min"])),
            conexao_minima=timedelta(minutes=float(est["conexao_minima_min"])),
            conexao_realista=timedelta(minutes=float(est["conexao_realista_min"])),
            feriados_excluidos=frozenset(bruto["feriados"]["excluidos"]),
            rotas_diretas={o: tuple(d) for o, d in bruto["rotas_diretas"].items()},
            aeroportos=aeroportos,
        )
    except KeyError as erro:
        msg = f"configuração incompleta em {alvo}: falta {erro}"
        raise ConfigInvalida(msg) from erro

    _validar(cfg, alvo)
    return cfg


def _validar(cfg: Config, origem_do_arquivo: Path, /) -> None:
    """Confere que todo IATA citado tem coordenada e que a origem tem rotas.

    Args:
        cfg: Configuração a validar.
        origem_do_arquivo: Caminho do arquivo, usado só na mensagem de erro.

    Raises:
        ConfigInvalida: Quando algum código IATA está sem coordenada, ou quando
            uma origem não declara rotas diretas.
    """
    citados = set(cfg.origens) | set(cfg.destinos)
    for destinos in cfg.rotas_diretas.values():
        citados |= set(destinos)
    faltando = sorted(citados - set(cfg.aeroportos))
    if faltando:
        msg = f"{origem_do_arquivo}: sem coordenada para {', '.join(faltando)}"
        raise ConfigInvalida(msg)
    sem_rotas = sorted(set(cfg.origens) - set(cfg.rotas_diretas))
    if sem_rotas:
        msg = f"{origem_do_arquivo}: origem sem rotas diretas declaradas: {', '.join(sem_rotas)}"
        raise ConfigInvalida(msg)
    if not cfg.destinos:
        msg = f"{origem_do_arquivo}: lista de destinos vazia"
        raise ConfigInvalida(msg)


def carregar_credenciais(ambiente: Mapping[str, str], /) -> Credenciais:
    """Extrai as credenciais da Amadeus do ambiente, falhando explicitamente.

    Args:
        ambiente: Mapa de variáveis de ambiente, tipicamente `os.environ`.

    Returns:
        As credenciais encontradas.

    Raises:
        CredenciaisAusentes: Se `AMADEUS_CLIENT_ID` ou `AMADEUS_CLIENT_SECRET`
            estiver ausente ou vazia. A mensagem nunca inclui o valor lido.
    """
    faltando = [
        nome
        for nome in ("AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET")
        if not ambiente.get(nome, "").strip()
    ]
    if faltando:
        msg = (
            f"credenciais ausentes: {', '.join(faltando)}. "
            "Defina no ambiente (veja .env.example) — nunca no código."
        )
        raise CredenciaisAusentes(msg)
    return Credenciais(
        client_id=ambiente["AMADEUS_CLIENT_ID"].strip(),
        client_secret=ambiente["AMADEUS_CLIENT_SECRET"].strip(),
    )
