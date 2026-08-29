"""Testes da geração de janelas de viagem."""

from datetime import date

from varredura_voos.janelas import (
    feriados_nacionais,
    gerar_janelas,
    gerar_janelas_feriado,
    gerar_janelas_fim_de_semana,
)

# Tabela apurada à mão para 2026-2027. Serve de fixture de regressão contra a
# lib `holidays`: se a lib mudar, o teste acusa.
FERIADOS_ESPERADOS: dict[date, str] = {
    date(2026, 9, 7): "Independência do Brasil",
    date(2026, 10, 12): "Nossa Senhora Aparecida",
    date(2026, 11, 2): "Finados",
    date(2026, 11, 15): "Proclamação da República",
    date(2026, 11, 20): "Dia Nacional de Zumbi e da Consciência Negra",
    date(2026, 12, 25): "Natal",
    date(2027, 1, 1): "Confraternização Universal",
    date(2027, 2, 8): "Carnaval",
    date(2027, 3, 26): "Sexta-feira Santa",
    date(2027, 4, 21): "Tiradentes",
}

EXCLUIDOS = frozenset(
    {"Véspera de Natal", "Véspera de Ano-Novo", "Dia do Servidor Público", "Início da Quaresma"}
)


# --- 4. janelas de fim de semana ----------------------------------------------------
def test_fim_de_semana_conta_e_primeira_sexta() -> None:
    janelas = gerar_janelas_fim_de_semana(date(2026, 8, 29), date(2026, 9, 30))
    assert [j.ida for j in janelas] == [
        date(2026, 9, 4),
        date(2026, 9, 11),
        date(2026, 9, 18),
        date(2026, 9, 25),
    ]
    assert all(j.volta == j.ida.replace(day=j.ida.day + 3) for j in janelas)
    assert all(j.noites == 3 for j in janelas)


def test_fim_de_semana_exige_a_volta_dentro_do_intervalo() -> None:
    """Sexta 04/09 com volta em 07/09: entra se o intervalo alcança a segunda."""
    assert gerar_janelas_fim_de_semana(date(2026, 9, 1), date(2026, 9, 6)) == []
    assert len(gerar_janelas_fim_de_semana(date(2026, 9, 1), date(2026, 9, 7))) == 1


def test_fim_de_semana_intervalo_vazio() -> None:
    assert gerar_janelas_fim_de_semana(date(2026, 9, 8), date(2026, 9, 8)) == []


# --- 5. feriados --------------------------------------------------------------------
def test_feriados_batem_com_a_tabela_apurada() -> None:
    apurados = feriados_nacionais(date(2026, 9, 1), date(2027, 4, 30), excluidos=EXCLUIDOS)
    for dia, nome in FERIADOS_ESPERADOS.items():
        assert dia in apurados, f"faltou {nome} em {dia}"
        assert apurados[dia] == nome


def test_carnaval_aparece_apesar_de_ser_ponto_facultativo() -> None:
    """Carnaval é OPTIONAL na lib `holidays`, não PUBLIC. Sem as duas categorias, some."""
    apurados = feriados_nacionais(date(2027, 2, 1), date(2027, 2, 28), excluidos=EXCLUIDOS)
    assert date(2027, 2, 8) in apurados
    assert apurados[date(2027, 2, 8)] == "Carnaval"


def test_facultativos_sem_viagem_sao_excluidos() -> None:
    apurados = feriados_nacionais(date(2026, 12, 1), date(2027, 1, 5), excluidos=EXCLUIDOS)
    assert date(2026, 12, 24) not in apurados, "Véspera de Natal não deve gerar janela"
    assert date(2026, 12, 31) not in apurados, "Véspera de Ano-Novo não deve gerar janela"
    assert date(2026, 12, 25) in apurados


def test_janela_de_feriado_emenda_a_segunda() -> None:
    """Independência cai na segunda 07/09: a janela óbvia é sexta 04 -> segunda 07."""
    janelas = gerar_janelas_feriado(
        date(2026, 9, 1), date(2026, 9, 30), feriados={date(2026, 9, 7): "Independência do Brasil"}
    )
    pares = {(j.ida, j.volta) for j in janelas}
    assert (date(2026, 9, 4), date(2026, 9, 7)) in pares
    assert (date(2026, 9, 3), date(2026, 9, 7)) in pares  # quinta, 4 noites
    assert all(3 <= j.noites <= 4 for j in janelas)
    assert all("Independência" in j.motivo for j in janelas)


def test_feriado_no_meio_da_semana_nao_emenda() -> None:
    """Tiradentes 2027 cai na quarta: nenhuma janela sai de quinta ou sexta e o contém."""
    janelas = gerar_janelas_feriado(
        date(2027, 4, 1), date(2027, 4, 30), feriados={date(2027, 4, 21): "Tiradentes"}
    )
    assert janelas == []


def test_toda_janela_de_feriado_contem_o_feriado() -> None:
    janelas = gerar_janelas_feriado(date(2026, 9, 1), date(2027, 3, 1), feriados=FERIADOS_ESPERADOS)
    assert janelas
    for j in janelas:
        assert any(j.ida <= d <= j.volta for d in FERIADOS_ESPERADOS)


# --- integração: dedup e prioridade -------------------------------------------------
def test_gerar_janelas_deduplica_e_poe_feriado_na_frente() -> None:
    janelas = gerar_janelas(date(2026, 9, 1), date(2026, 9, 30), excluidos=EXCLUIDOS)
    pares = [(j.ida, j.volta) for j in janelas]
    assert len(pares) == len(set(pares)), "não pode haver janela repetida"
    # 04/09 -> 07/09 sai dos dois geradores; tem de sobreviver uma vez só, como feriado.
    por_datas = {(j.ida, j.volta): j for j in janelas}
    assert "feriado" in por_datas[(date(2026, 9, 4), date(2026, 9, 7))].motivo
    # Feriados vêm antes de qualquer fim de semana comum.
    assert "feriado" in janelas[0].motivo
    assert [j.prioridade for j in janelas] == sorted(j.prioridade for j in janelas)


def test_gerar_janelas_num_horizonte_de_seis_meses() -> None:
    janelas = gerar_janelas(date(2026, 8, 29), date(2027, 2, 28), excluidos=EXCLUIDOS)
    assert 30 <= len(janelas) <= 60, f"contagem fora do esperado: {len(janelas)}"
    assert len({(j.ida, j.volta) for j in janelas}) == len(janelas)
