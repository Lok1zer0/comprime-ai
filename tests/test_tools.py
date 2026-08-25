import io
import zipfile
from pathlib import Path

import pytest

from gifc.tools import (
    GIFSICLE_SHA256,
    GIFSICLE_URL,
    DownloadError,
    MissingToolError,
    Tools,
    download_gifsicle,
    extract_gifsicle,
    find_tools,
    gifsicle_path,
)


def test_gifsicle_path_fica_dentro_do_bin_dir(tmp_path):
    p = gifsicle_path(tmp_path)
    assert p.parent == tmp_path
    assert p.stem == "gifsicle"


def test_find_tools_acha_ffmpeg_no_path(tmp_path):
    tools = find_tools(tmp_path)
    assert tools.ffmpeg
    assert tools.ffprobe
    assert Path(tools.ffmpeg).name.startswith("ffmpeg")


def test_find_tools_sem_gifsicle_devolve_none(tmp_path):
    # tmp_path vazio: nenhum gifsicle instalado ali
    tools = find_tools(tmp_path)
    assert tools.gifsicle is None


def test_find_tools_acha_gifsicle_no_bin_dir(tmp_path):
    fake = gifsicle_path(tmp_path)
    fake.write_bytes(b"nao-e-um-binario-de-verdade")
    tools = find_tools(tmp_path)
    assert tools.gifsicle == str(fake)


def test_tools_e_imutavel():
    t = Tools(ffmpeg="a", ffprobe="b", gifsicle=None)
    with pytest.raises(Exception):
        t.ffmpeg = "c"  # type: ignore[misc]


def test_missing_tool_error_nomeia_a_ferramenta():
    err = MissingToolError("ffmpeg")
    assert "ffmpeg" in str(err)


def _zip_com(nomes: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, dados in nomes.items():
            z.writestr(nome, dados)
    return buf.getvalue()


def test_url_e_https_e_aponta_para_gifsicle():
    assert GIFSICLE_URL.startswith("https://")
    assert "gifsicle" in GIFSICLE_URL


def test_extract_gifsicle_acha_o_exe_em_subpasta(tmp_path):
    conteudo = b"binario-falso"
    dados = _zip_com({"gifsicle-1.95-win64/gifsicle.exe": conteudo})
    destino = extract_gifsicle(dados, tmp_path)
    assert destino.is_file()
    assert destino.read_bytes() == conteudo


def test_extract_gifsicle_sem_o_exe_levanta_erro(tmp_path):
    dados = _zip_com({"leiame.txt": b"nada aqui"})
    with pytest.raises(DownloadError, match="gifsicle"):
        extract_gifsicle(dados, tmp_path)


def test_extract_gifsicle_rejeita_caminho_de_escape(tmp_path):
    # zip-slip: entrada tentando escrever fora do destino
    dados = _zip_com({"../../gifsicle.exe": b"malicioso"})
    with pytest.raises(DownloadError):
        extract_gifsicle(dados, tmp_path)


def test_extract_gifsicle_com_bin_dir_ocupado_por_arquivo_vira_download_error(
    tmp_path,
):
    """`OSError` durante a extração (mkdir, write_bytes) não pode escapar
    como traceback cru — precisa virar `DownloadError`, o único tipo que
    a GUI sabe tratar caindo para o modo degradado.

    Um arquivo comum no lugar onde `bin_dir` deveria ser uma pasta é uma
    forma simples e portável de forçar `mkdir` a levantar OSError (o
    caso real seria OneDrive, antivírus ou permissão negada).
    """
    bin_dir_ocupado = tmp_path / "bin_ocupado"
    bin_dir_ocupado.write_bytes(b"isto e um arquivo, nao uma pasta")

    dados = _zip_com({"gifsicle.exe": b"binario-falso"})
    with pytest.raises(DownloadError):
        extract_gifsicle(dados, bin_dir_ocupado)


def test_sha256_registrado_tem_formato_valido():
    assert len(GIFSICLE_SHA256) == 64
    int(GIFSICLE_SHA256, 16)  # levanta ValueError se não for hex


@pytest.mark.integration
def test_download_real_bate_o_hash(tmp_path):
    destino = download_gifsicle(tmp_path)
    assert destino.is_file()
    assert destino.stat().st_size > 50_000
