"""Entrypoint de linha de comando.

Tudo acontece dentro das funções: nada de leitura de configuração, de ambiente
ou de criação de cliente HTTP no nível do módulo. Importar `cli` não faz nada.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

from varredura_voos import __about__ as sobre

app = typer.Typer(
    add_completion=False,
    help="Varre passagens aéreas nacionais saindo do oeste do Paraná.",
)

_DURACAO = re.compile(
    r"^(?:(?P<horas>\d+)\s*h\s*(?P<minutos_h>\d+)?\s*m?|(?P<so_min>\d+)\s*m|(?P<so_horas>\d+))$"
)


def interpretar_duracao(texto: str, /) -> timedelta:
    """Interpreta uma duração escrita à mão, como `6h`, `6h30` ou `90m`.

    Args:
        texto: Duração informada na linha de comando.

    Returns:
        A duração equivalente.

    Raises:
        ValueError: Se o texto não for uma duração reconhecível.
    """
    casou = _DURACAO.match(texto.strip().lower())
    if casou is None:
        msg = f"duração não reconhecida: {texto!r}. Use 6h, 6h30 ou 90m."
        raise ValueError(msg)
    g = casou.groupdict()
    if g["so_min"]:
        return timedelta(minutes=int(g["so_min"]))
    if g["so_horas"]:
        return timedelta(hours=int(g["so_horas"]))
    return timedelta(hours=int(g["horas"] or 0), minutes=int(g["minutos_h"] or 0))


def janela_de_meses(meses: int, /, *, hoje: date | None = None) -> tuple[date, date]:
    """Calcula o intervalo de datas a varrer a partir de hoje.

    Args:
        meses: Quantos meses à frente varrer.
        hoje: Data de referência. Injetável para teste.

    Returns:
        Par (início, fim) do horizonte.

    Raises:
        ValueError: Se `meses` não for positivo.
    """
    if meses <= 0:
        msg = f"o horizonte precisa ser de pelo menos um mês, veio {meses}"
        raise ValueError(msg)
    inicio = hoje if hoje is not None else date.today()
    ano, mes = divmod(inicio.month - 1 + meses, 12)
    alvo_ano, alvo_mes = inicio.year + ano, mes + 1
    dia = min(
        inicio.day,
        [31, 29 if alvo_ano % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][alvo_mes - 1],
    )
    return inicio, date(alvo_ano, alvo_mes, dia)


@app.callback(invoke_without_command=True)
def varrer_cmd(
    ctx: typer.Context,
    origens: Annotated[
        str, typer.Option("--origens", help="Códigos IATA separados por vírgula.")
    ] = "CAC,IGU",
    destinos: Annotated[
        str | None, typer.Option("--destinos", help="Sobrescreve a lista da configuração.")
    ] = None,
    meses: Annotated[int, typer.Option("--meses", help="Horizonte em meses a partir de hoje.")] = 6,
    max_duracao: Annotated[
        str, typer.Option("--max-duracao", help="Teto por perna: 6h, 6h30, 90m.")
    ] = "6h",
    saida: Annotated[Path, typer.Option("--saida", help="Arquivo CSV de saída.")] = Path(
        "ofertas.csv"
    ),
    config: Annotated[Path | None, typer.Option("--config", help="TOML de configuração.")] = None,
    limite: Annotated[int, typer.Option("--limite", help="Linhas no resumo do terminal.")] = 15,
    max_chamadas: Annotated[
        int | None, typer.Option("--max-chamadas", help="Teto de chamadas neste mês.")
    ] = None,
    agressiva: Annotated[
        bool,
        typer.Option("--poda-agressiva", help="Poda mais rotas; pode esconder oferta de borda."),
    ] = False,
    simular: Annotated[
        bool, typer.Option("--simular", help="Só conta o custo em chamadas, sem consultar nada.")
    ] = False,
    producao: Annotated[
        bool, typer.Option("--producao", help="Usa a API de produção em vez da de teste.")
    ] = False,
    estado: Annotated[
        Path, typer.Option("--estado", help="Diretório de cache e contador de cota.")
    ] = Path("estado"),
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url", hidden=True, help="Sobrescreve a raiz da API. Uso interno e de teste."
        ),
    ] = None,
) -> None:
    """Varre as janelas de fim de semana e feriado e exporta as melhores ofertas."""
    if ctx.invoked_subcommand is not None:
        return

    from varredura_voos.config import (
        ConfigInvalida,
        CredenciaisAusentes,
        carregar_config,
        carregar_credenciais,
    )
    from varredura_voos.janelas import gerar_janelas
    from varredura_voos.varredura import montar_consultas

    try:
        cfg = carregar_config(config)
        cfg = replace(
            cfg,
            origens=tuple(o.strip().upper() for o in origens.split(",") if o.strip()),
            max_duracao=interpretar_duracao(max_duracao),
        )
        if destinos:
            cfg = replace(
                cfg, destinos=tuple(d.strip().upper() for d in destinos.split(",") if d.strip())
            )
        if max_chamadas is not None:
            cfg = replace(cfg, max_chamadas_mes=max_chamadas)
        inicio, fim = janela_de_meses(meses)
    except (ValueError, ConfigInvalida, FileNotFoundError) as erro:
        typer.secho(f"erro de configuração: {erro}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from erro

    janelas = gerar_janelas(
        inicio,
        fim,
        noites_fim_de_semana=cfg.noites_fim_de_semana,
        noites_feriado=cfg.noites_feriado,
        excluidos=cfg.feriados_excluidos,
    )
    try:
        consultas = montar_consultas(cfg, janelas, agressiva=agressiva)
    except KeyError as erro:
        typer.secho(f"erro de configuração: {erro}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from erro

    typer.echo(f"{sobre.NOME} — horizonte {inicio} a {fim}")
    typer.echo(
        f"  {len(janelas)} janelas ({sum(1 for j in janelas if j.prioridade == 0)} de feriado)"
    )
    typer.echo(f"  {len(cfg.origens)} origens x {len(cfg.destinos)} destinos")
    typer.echo(f"  {len(consultas)} chamadas necessárias (teto do mês: {cfg.max_chamadas_mes})")
    if len(consultas) > cfg.max_chamadas_mes:
        typer.secho(
            f"  atenção: isso excede a cota configurada em {len(consultas) - cfg.max_chamadas_mes} "
            "chamadas. A varredura vai parar no teto e relatar o que ficou de fora.",
            fg=typer.colors.YELLOW,
        )

    if simular:
        raise typer.Exit(code=0)

    try:
        credenciais = carregar_credenciais(os.environ)
    except CredenciaisAusentes as erro:
        typer.secho(str(erro), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from erro

    total = asyncio.run(
        _executar(
            cfg,
            janelas,
            credenciais,
            saida,
            estado,
            limite=limite,
            agressiva=agressiva,
            producao=producao,
            base_url=base_url,
        )
    )
    raise typer.Exit(code=0 if total else 3)


async def _executar(
    cfg: object,
    janelas: object,
    credenciais: object,
    saida: Path,
    estado: Path,
    /,
    *,
    limite: int,
    agressiva: bool,
    producao: bool,
    base_url: str | None = None,
) -> int:
    """Monta as dependências, roda a varredura e escreve a saída.

    Args:
        cfg: Configuração da varredura.
        janelas: Janelas candidatas.
        credenciais: Credenciais da Amadeus.
        saida: Caminho do CSV.
        estado: Diretório de cache e contador de cota.
        limite: Linhas do resumo no terminal.
        agressiva: Se aplica a poda agressiva.
        producao: Se usa a API de produção.
        base_url: Raiz da API a usar no lugar do padrão. Uso interno e de teste.

    Returns:
        Quantas ofertas foram exportadas.
    """
    import asyncio as aio

    import httpx

    from varredura_voos.cache import CacheDisco
    from varredura_voos.cliente_amadeus import BASE_PRODUCAO, BASE_TESTE, ClienteAmadeus
    from varredura_voos.orcamento import Orcamento
    from varredura_voos.relatorio import exportar_csv, resumo_terminal
    from varredura_voos.varredura import varrer

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        cliente = ClienteAmadeus(
            http,
            credenciais,  # type: ignore[arg-type]
            semaforo=aio.Semaphore(cfg.concorrencia),  # type: ignore[attr-defined]
            base_url=base_url or (BASE_PRODUCAO if producao else BASE_TESTE),
        )
        cache = CacheDisco(estado / "cache", ttl=cfg.ttl_cache)  # type: ignore[attr-defined]
        orcamento = Orcamento(
            estado / "cota.json",
            teto=cfg.max_chamadas_mes,  # type: ignore[attr-defined]
            mes=date.today(),
        )
        resultado = await varrer(cfg, janelas, cliente, cache, orcamento, agressiva=agressiva)  # type: ignore[arg-type]

    total = exportar_csv(resultado.ofertas, saida)
    typer.echo("")
    typer.echo(resumo_terminal(resultado.ofertas, limite=limite))
    typer.echo("")
    typer.echo(
        f"{total} ofertas em {saida} | {resultado.chamadas} chamadas, "
        f"{resultado.cache_hits} do cache, {resultado.podadas} podadas"
    )
    if resultado.pares_sem_oferta:
        pares = ", ".join(f"{o}-{d}" for o, d in sorted(resultado.pares_sem_oferta))
        typer.echo(f"sem nenhuma oferta dentro do teto: {pares}")
        typer.echo("  (candidatos a sair da lista de destinos na configuração)")
    if resultado.erros:
        typer.secho(f"{len(resultado.erros)} consultas falharam", fg=typer.colors.YELLOW)
    if resultado.orcamento_esgotado:
        typer.secho(
            f"cota do mês esgotada: {len(resultado.nao_varridas)} consultas não foram feitas",
            fg=typer.colors.YELLOW,
        )
    return total
