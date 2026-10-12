import io
import zipfile
from pathlib import Path

import pytest

from gifc.tools import (
    GIFSICLE_SHA256,
    GIFSICLE_URL,
    OXIPNG_SHA256,
    OXIPNG_URL,
    OXIPNG_ZIP_SIZE,
    DownloadError,
    MissingToolError,
    Tools,
    download_gifsicle,
    download_oxipng,
    extract_gifsicle,
    extract_oxipng,
    find_tools,
    gifsicle_path,
    oxipng_path,
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


# --- oxipng ---------------------------------------------------------------


def test_oxipng_url_e_https_e_versionada():
    assert OXIPNG_URL.startswith("https://")
    assert "oxipng" in OXIPNG_URL
    # Tag fixa (v<major>.<minor>.<patch>), nunca "latest": é o que torna o
    # hash estável.
    assert "/download/v10.2.1/" in OXIPNG_URL
    assert "latest" not in OXIPNG_URL


def test_oxipng_path_fica_no_bin_dir(tmp_path):
    p = oxipng_path(tmp_path)
    assert p.parent == tmp_path
    assert p.stem == "oxipng"


def test_extrai_o_oxipng_do_zip(tmp_path):
    conteudo = b"binario-falso"
    dados = _zip_com({"oxipng-10.2.1-x86_64-pc-windows-msvc/oxipng.exe": conteudo})
    destino = extract_oxipng(dados, tmp_path)
    assert destino == oxipng_path(tmp_path)
    assert destino.read_bytes() == conteudo


def test_zip_sem_oxipng_levanta_downloaderror(tmp_path):
    dados = _zip_com({"leiame.txt": b"nada aqui"})
    with pytest.raises(DownloadError, match="oxipng"):
        extract_oxipng(dados, tmp_path)


def test_extract_oxipng_rejeita_caminho_de_escape(tmp_path):
    dados = _zip_com({"../../oxipng.exe": b"malicioso"})
    with pytest.raises(DownloadError):
        extract_oxipng(dados, tmp_path)


def test_extract_oxipng_nao_escreve_o_gifsicle(tmp_path):
    """Os dois downloads dividem a extração: cada um grava só o seu exe."""
    extract_oxipng(_zip_com({"oxipng.exe": b"x"}), tmp_path)
    assert not gifsicle_path(tmp_path).exists()


def test_oxipng_sha256_tem_formato_valido():
    assert len(OXIPNG_SHA256) == 64
    int(OXIPNG_SHA256, 16)  # levanta ValueError se não for hex


def test_oxipng_zip_size_e_positivo_e_plausivel():
    assert 100_000 < OXIPNG_ZIP_SIZE < 10_000_000


def test_find_tools_sem_oxipng_devolve_none(tmp_path, monkeypatch):
    monkeypatch.setattr("gifc.tools.shutil.which",
                        lambda n: None if n == "oxipng" else f"/x/{n}")
    assert find_tools(tmp_path).oxipng is None


def test_find_tools_acha_oxipng_no_bin_dir(tmp_path):
    fake = oxipng_path(tmp_path)
    fake.write_bytes(b"nao-e-um-binario-de-verdade")
    assert find_tools(tmp_path).oxipng == str(fake)


def test_tools_aceita_construcao_sem_oxipng():
    """Quem monta `Tools` à mão com os três campos antigos continua válido."""
    assert Tools("a", "b", None).oxipng is None


@pytest.mark.integration
def test_download_real_do_oxipng_bate_o_hash(tmp_path):
    destino = download_oxipng(tmp_path)
    assert destino.is_file()
    assert destino.stat().st_size > 100_000
