"""Prova do critério de aceite 1: a CLI roda de ponta a ponta e gera o CSV.

Um servidor HTTP de verdade em localhost faz o papel da Amadeus, para que o
caminho exercitado seja o mesmo de produção — cliente httpx real, OAuth2 real,
retry real —, sem depender da internet nem de credencial.
"""

import csv
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from varredura_voos.cli import app

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "flight_offers_cac_gru.json").read_text(encoding="utf-8")
)


class AmadeusFalsa(BaseHTTPRequestHandler):
    """Responde token e ofertas como a API real, sem nenhum log no terminal."""

    def log_message(self, *args: object) -> None:
        return

    def _responder(self, corpo: dict[str, object]) -> None:
        dados = json.dumps(corpo).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._responder({"access_token": "tok", "expires_in": 1799})

    def do_GET(self) -> None:
        self._responder(FIXTURE)


@pytest.fixture
def servidor() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), AmadeusFalsa)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_criterio_de_aceite_1_gera_o_csv(tmp_path: Path, servidor: str) -> None:
    saida = tmp_path / "out.csv"
    resultado = CliRunner().invoke(
        app,
        [
            "--origens",
            "CAC,IGU",
            "--destinos",
            "GRU,CWB",
            "--meses",
            "2",
            "--max-duracao",
            "6h",
            "--saida",
            str(saida),
            "--estado",
            str(tmp_path / "estado"),
            "--base-url",
            servidor,
        ],
        env={"AMADEUS_CLIENT_ID": "chave", "AMADEUS_CLIENT_SECRET": "segredo"},
    )
    assert resultado.exit_code == 0, resultado.output
    assert saida.is_file()

    with saida.open(encoding="utf-8", newline="") as f:
        linhas = list(csv.DictReader(f))
    assert linhas, "o CSV precisa ter linhas"
    precos = [float(linha["preco_total"]) for linha in linhas]
    assert precos == sorted(precos), "CSV tem de sair ordenado por preço"
    assert all(float(linha["preco_total"]) != 990.0 for linha in linhas), "a de 12h10 caiu"
    assert {linha["origem"] for linha in linhas} == {"CAC", "IGU"}
    assert "ofertas em" in resultado.output


def test_cache_faz_a_segunda_rodada_nao_gastar_chamada(tmp_path: Path, servidor: str) -> None:
    estado = tmp_path / "estado"
    argumentos = [
        "--origens",
        "CAC",
        "--destinos",
        "GRU",
        "--meses",
        "1",
        "--max-duracao",
        "6h",
        "--saida",
        str(tmp_path / "o.csv"),
        "--estado",
        str(estado),
        "--base-url",
        servidor,
    ]
    ambiente = {"AMADEUS_CLIENT_ID": "chave", "AMADEUS_CLIENT_SECRET": "segredo"}
    runner = CliRunner()
    primeira = runner.invoke(app, argumentos, env=ambiente)
    segunda = runner.invoke(app, argumentos, env=ambiente)
    assert primeira.exit_code == 0 and segunda.exit_code == 0, segunda.output
    assert "0 chamadas" in segunda.output
    cota = json.loads((estado / "cota.json").read_text(encoding="utf-8"))
    gastas = sum(cota.values())
    assert gastas > 0
    assert "do cache" in segunda.output
