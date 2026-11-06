import dataclasses
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

from gifc.tools import (
    APP_DIR,
    BIN_DIR,
    PROJECT_ROOT,
    _pasta_do_app,
    _baixar_zip,
    _gravar_atomico,
    GIFSICLE_SHA256,
    GIFSICLE_URL,
    OXIPNG_SHA256,
    OXIPNG_URL,
    OXIPNG_ZIP_SIZE,
    FFMPEG_SHA256,
    FFMPEG_URL,
    FFMPEG_SIZE,
    DownloadError,
    MissingToolError,
    Tools,
    download_gifsicle,
    dirs_de_busca,
    download_ffmpeg,
    download_oxipng,
    ffmpeg_path,
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
    assert "ffprobe" not in dir(tools)
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
    t = Tools(ffmpeg="a", gifsicle=None)
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
    assert Tools("a", None).oxipng is None


@pytest.mark.integration
def test_download_real_do_oxipng_bate_o_hash(tmp_path):
    destino = download_oxipng(tmp_path)
    assert destino.is_file()
    assert destino.stat().st_size > 100_000


# --- ffmpeg ---------------------------------------------------------------

def test_ffmpeg_url_e_https_e_fixada_numa_tag():
    assert FFMPEG_URL.startswith("https://")
    assert "/releases/download/ferramentas-v" in FFMPEG_URL
    assert "latest" not in FFMPEG_URL


def test_ffmpeg_path_fica_no_bin_dir(tmp_path):
    p = ffmpeg_path(tmp_path)
    assert p.parent == tmp_path
    assert p.stem == "ffmpeg"


def test_ffmpeg_sha256_tem_formato_valido():
    assert len(FFMPEG_SHA256) == 64
    int(FFMPEG_SHA256, 16)


def test_ffmpeg_url_e_o_espelho_do_projeto():
    """Apontar de volta para o upstream reintroduz a URL que morreu."""
    assert "Lok1zer0/comprime-ai/releases/download/" in FFMPEG_URL
    assert "BtbN" not in FFMPEG_URL


def test_ffmpeg_size_e_plausivel():
    assert 50_000_000 < FFMPEG_SIZE < 400_000_000


BINARIO = b"MZ-ffmpeg-falso"


def _fixar_binario_falso(monkeypatch) -> None:
    import hashlib
    monkeypatch.setattr("gifc.tools.FFMPEG_SHA256", hashlib.sha256(BINARIO).hexdigest())
    monkeypatch.setattr("gifc.tools.FFMPEG_SIZE", len(BINARIO))


def test_download_ffmpeg_grava_o_exe_e_guarda_no_cache(tmp_path, monkeypatch):
    _fixar_binario_falso(monkeypatch)
    chamadas = []
    monkeypatch.setattr("gifc.tools._baixar_zip",
                        lambda *a, **k: chamadas.append(a) or BINARIO)
    cache = tmp_path / "cache"
    destino = download_ffmpeg(tmp_path / "bin", cache_dir=cache)
    assert destino == ffmpeg_path(tmp_path / "bin")
    assert destino.read_bytes() == BINARIO
    assert len(chamadas) == 1
    assert [p.read_bytes() for p in cache.iterdir()] == [BINARIO]
    assert [p.name for p in destino.parent.iterdir()] == [destino.name]


def test_download_ffmpeg_reaproveita_o_cache_cujo_hash_bate(tmp_path, monkeypatch):
    _fixar_binario_falso(monkeypatch)
    cache = tmp_path / "cache"
    monkeypatch.setattr("gifc.tools._baixar_zip", lambda *a, **k: BINARIO)
    download_ffmpeg(tmp_path / "bin1", cache_dir=cache)

    def _proibido(*a, **k):
        raise AssertionError("rebaixou apesar do cache válido")
    monkeypatch.setattr("gifc.tools._baixar_zip", _proibido)
    destino = download_ffmpeg(tmp_path / "bin2", cache_dir=cache)
    assert destino.read_bytes() == BINARIO


def test_download_ffmpeg_ignora_cache_corrompido_e_rebaixa(tmp_path, monkeypatch):
    _fixar_binario_falso(monkeypatch)
    cache = tmp_path / "cache"
    monkeypatch.setattr("gifc.tools._baixar_zip", lambda *a, **k: BINARIO)
    download_ffmpeg(tmp_path / "bin1", cache_dir=cache)
    (arquivo,) = cache.iterdir()
    arquivo.write_bytes(b"x" * len(BINARIO))  # mesmo tamanho, outro conteúdo

    chamadas = []
    monkeypatch.setattr("gifc.tools._baixar_zip",
                        lambda *a, **k: chamadas.append(a) or BINARIO)
    destino = download_ffmpeg(tmp_path / "bin2", cache_dir=cache)
    assert len(chamadas) == 1
    assert arquivo.read_bytes() == BINARIO  # o cache foi refeito
    assert destino.read_bytes() == BINARIO


def test_download_ffmpeg_nao_guarda_no_cache_um_download_recusado(tmp_path, monkeypatch):
    _fixar_binario_falso(monkeypatch)

    def _falha(*a, **k):
        raise DownloadError("hash errado")
    monkeypatch.setattr("gifc.tools._baixar_zip", _falha)
    cache = tmp_path / "cache"
    with pytest.raises(DownloadError):
        download_ffmpeg(tmp_path / "bin", cache_dir=cache)
    assert not cache.exists() or list(cache.iterdir()) == []
    assert not ffmpeg_path(tmp_path / "bin").exists()


def test_download_ffmpeg_falha_do_cache_nao_impede_a_instalacao(tmp_path, monkeypatch):
    _fixar_binario_falso(monkeypatch)
    monkeypatch.setattr("gifc.tools._baixar_zip", lambda *a, **k: BINARIO)
    bloqueio = tmp_path / "cache"
    bloqueio.write_bytes(b"sou um arquivo, nao uma pasta")
    destino = download_ffmpeg(tmp_path / "bin", cache_dir=bloqueio)
    assert destino.read_bytes() == BINARIO


@pytest.mark.integration
def test_download_real_do_ffmpeg(tmp_path):
    destino = download_ffmpeg(tmp_path / "bin", cache_dir=PROJECT_ROOT / "build" / "cache-de-pacotes")
    assert destino.stat().st_size == FFMPEG_SIZE
    assert [p.name for p in destino.parent.iterdir()] == [destino.name]


# --- busca com precedência de bundle -------------------------------------

def _congelar(monkeypatch, exe_dir: Path, meipass: Path | None = None) -> None:
    monkeypatch.setattr("gifc.tools.sys.frozen", True, raising=False)
    monkeypatch.setattr("gifc.tools.sys.executable", str(exe_dir / "app.exe"))
    if meipass is not None:
        monkeypatch.setattr("gifc.tools.sys._MEIPASS", str(meipass), raising=False)


def _solto(monkeypatch) -> None:
    """Garante o modo de desenvolvimento, mesmo rodando dentro de um bundle."""
    monkeypatch.delattr("gifc.tools.sys.frozen", raising=False)
    monkeypatch.delattr("gifc.tools.sys._MEIPASS", raising=False)


def _falso(pasta: Path, nome: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / (f"{nome}.exe" if sys.platform == "win32" else nome)
    arq.write_bytes(b"x")
    return arq


def test_tools_nao_tem_mais_ffprobe():
    assert "ffprobe" not in {f.name for f in dataclasses.fields(Tools)}


def test_dirs_de_busca_fora_do_bundle_e_so_o_bin_dir(tmp_path, monkeypatch):
    _solto(monkeypatch)
    assert dirs_de_busca(tmp_path) == [tmp_path]


def test_dirs_de_busca_congelado_poe_a_pasta_do_exe_primeiro(tmp_path, monkeypatch):
    _solto(monkeypatch)
    exe = tmp_path / "exe"
    _congelar(monkeypatch, exe)
    assert dirs_de_busca(tmp_path / "bin") == [exe, tmp_path / "bin"]


def test_dirs_de_busca_inclui_meipass(tmp_path, monkeypatch):
    _solto(monkeypatch)
    exe, mei = tmp_path / "exe", tmp_path / "exe" / "_internal"
    _congelar(monkeypatch, exe, mei)
    assert dirs_de_busca(tmp_path / "bin") == [exe, mei, tmp_path / "bin"]


def test_meipass_sem_frozen_e_ignorado(tmp_path, monkeypatch):
    _solto(monkeypatch)
    monkeypatch.setattr("gifc.tools.sys._MEIPASS", str(tmp_path / "m"), raising=False)
    assert dirs_de_busca(tmp_path) == [tmp_path]


def test_app_dir_fora_do_bundle_e_a_raiz_do_projeto():
    assert APP_DIR == PROJECT_ROOT
    assert BIN_DIR == APP_DIR / "bin"


def test_find_tools_prefere_o_bundle_ao_path(tmp_path, monkeypatch):
    _solto(monkeypatch)
    exe, mei = tmp_path / "exe", tmp_path / "exe" / "_internal"
    _congelar(monkeypatch, exe, mei)
    monkeypatch.setattr("gifc.tools.shutil.which", lambda n: f"/path/{n}")
    ff = _falso(mei, "ffmpeg")
    gs = _falso(exe, "gifsicle")
    ox = _falso(tmp_path / "bin", "oxipng")
    t = find_tools(tmp_path / "bin")
    assert (t.ffmpeg, t.gifsicle, t.oxipng) == (str(ff), str(gs), str(ox))


def test_find_tools_pasta_do_exe_vence_meipass(tmp_path, monkeypatch):
    _solto(monkeypatch)
    exe, mei = tmp_path / "exe", tmp_path / "exe" / "_internal"
    _congelar(monkeypatch, exe, mei)
    monkeypatch.setattr("gifc.tools.shutil.which", lambda n: None)
    ao_lado = _falso(exe, "ffmpeg")
    _falso(mei, "ffmpeg")
    assert find_tools(tmp_path / "bin").ffmpeg == str(ao_lado)


def test_find_tools_cai_no_path_quando_nao_ha_bundle(tmp_path, monkeypatch):
    _solto(monkeypatch)
    monkeypatch.setattr("gifc.tools.shutil.which", lambda n: f"/path/{n}")
    t = find_tools(tmp_path)
    assert (t.ffmpeg, t.gifsicle, t.oxipng) == (
        "/path/ffmpeg", "/path/gifsicle", "/path/oxipng")


def test_find_tools_sem_ffmpeg_em_lugar_nenhum_levanta(tmp_path, monkeypatch):
    _solto(monkeypatch)
    monkeypatch.setattr("gifc.tools.shutil.which", lambda n: None)
    with pytest.raises(MissingToolError) as exc:
        find_tools(tmp_path)
    assert exc.value.tool == "ffmpeg"


def test_pasta_do_app_congelado_e_a_do_executavel(tmp_path, monkeypatch):
    _congelar(monkeypatch, tmp_path / "exe")
    assert _pasta_do_app() == (tmp_path / "exe").resolve()


def test_pasta_do_app_solto_e_a_raiz_do_projeto(monkeypatch):
    _solto(monkeypatch)
    assert _pasta_do_app() == PROJECT_ROOT


def test_find_tools_pasta_do_exe_vence_bin_dir_com_o_mesmo_nome(tmp_path, monkeypatch):
    _solto(monkeypatch)
    exe = tmp_path / "exe"
    _congelar(monkeypatch, exe)
    monkeypatch.setattr("gifc.tools.shutil.which", lambda n: None)
    novo = _falso(exe, "ffmpeg")
    velho = _falso(tmp_path / "bin", "ffmpeg")
    novo.write_bytes(b"novo")
    velho.write_bytes(b"velho")
    assert find_tools(tmp_path / "bin").ffmpeg == str(novo)


def test_find_tools_meipass_vence_bin_dir_com_o_mesmo_nome(tmp_path, monkeypatch):
    _solto(monkeypatch)
    exe, mei = tmp_path / "exe", tmp_path / "mei"
    _congelar(monkeypatch, exe, mei)
    monkeypatch.setattr("gifc.tools.shutil.which", lambda n: None)
    novo = _falso(mei, "ffmpeg")
    velho = _falso(tmp_path / "bin", "ffmpeg")
    novo.write_bytes(b"novo")
    velho.write_bytes(b"velho")
    assert find_tools(tmp_path / "bin").ffmpeg == str(novo)


def test_erro_congelado_nao_manda_mexer_no_path(monkeypatch):
    monkeypatch.setattr("gifc.tools.sys.frozen", True, raising=False)
    msg = str(MissingToolError("ffmpeg"))
    assert "ffmpeg" in msg and "PATH" not in msg and "Extraia" in msg


def test_erro_fora_do_bundle_manda_checar_o_path(monkeypatch):
    _solto(monkeypatch)
    msg = str(MissingToolError("ffmpeg"))
    assert "ffmpeg" in msg and "PATH" in msg


# --- o que `download_ffmpeg` entrega a `_baixar_zip` -----------------------
# Os testes acima trocam `_baixar_zip` por um lambda que aceita qualquer
# argumento, então nenhum deles notaria `download_ffmpeg` passando a URL do
# gifsicle ou o tamanho do oxipng. Sem este teste, um argumento trocado só
# apareceria no teste de integração, que baixa 132 MB da rede e por isso não
# roda a cada alteração.


def test_download_ffmpeg_passa_a_url_o_tamanho_e_o_hash_do_proprio_ffmpeg(
    tmp_path, monkeypatch
):
    capturado = {}

    def _espiao(nome, url, tamanho, sha256, on_progress):
        capturado.update(nome=nome, url=url, tamanho=tamanho, sha256=sha256)
        raise DownloadError("parando aqui; o que importa são os argumentos")

    monkeypatch.setattr("gifc.tools._baixar_zip", _espiao)
    with pytest.raises(DownloadError):
        download_ffmpeg(tmp_path / "bin", cache_dir=tmp_path / "cache")
    assert capturado == {
        "nome": "ffmpeg",
        "url": FFMPEG_URL,
        "tamanho": FFMPEG_SIZE,
        "sha256": FFMPEG_SHA256,
    }


# --- escrita atômica -------------------------------------------------------


def test_gravar_atomico_nao_deixa_parcial_quando_a_escrita_falha(
    tmp_path, monkeypatch
):
    """Disco cheio no meio da escrita não pode deixar lixo no `bin/`.

    O `.parcial` que sobrasse não engana o `find_tools`, mas o empacotamento
    copia o `bin/` inteiro, então ele viajaria dentro do pacote publicado.
    """
    destino = tmp_path / "bin" / "ffmpeg.exe"
    original = Path.write_bytes

    def _enche_o_disco(self, dados):
        original(self, dados[: len(dados) // 2])  # grava um pedaço e desiste
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _enche_o_disco)
    with pytest.raises(OSError):
        _gravar_atomico(destino, b"M" * 64)
    monkeypatch.undo()
    assert sorted(p.name for p in destino.parent.iterdir()) == []


def test_gravar_atomico_substitui_o_arquivo_que_ja_estava_la(tmp_path):
    destino = tmp_path / "ffmpeg.exe"
    destino.write_bytes(b"versao antiga")
    _gravar_atomico(destino, b"versao nova")
    assert destino.read_bytes() == b"versao nova"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ffmpeg.exe"]


# --- teto de tamanho do download ------------------------------------------


class _RespostaFalsa:
    """Mínimo de `urlopen` que o `_baixar_zip` usa: headers, read, context."""

    def __init__(self, corpo: bytes, content_length: int | None = None):
        self._restante = corpo
        tamanho = len(corpo) if content_length is None else content_length
        self.headers = {"Content-Length": str(tamanho)}

    def read(self, n: int) -> bytes:
        pedaco, self._restante = self._restante[:n], self._restante[n:]
        return pedaco

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _servir(monkeypatch, corpo: bytes, content_length: int | None = None) -> None:
    monkeypatch.setattr(
        "gifc.tools.urllib.request.urlopen",
        lambda url, timeout=None: _RespostaFalsa(corpo, content_length),
    )


def test_baixar_zip_aceita_o_arquivo_de_tamanho_exato(monkeypatch):
    """O teto é o tamanho esperado, então o arquivo certo passa raspando."""
    corpo = b"x" * 200_000  # mais de um pedaço de 64 KB
    _servir(monkeypatch, corpo)
    sha = hashlib.sha256(corpo).hexdigest()
    assert _baixar_zip("teste", "http://exemplo", len(corpo), sha, None) == corpo


def test_baixar_zip_desiste_assim_que_passa_do_tamanho_esperado(monkeypatch):
    """Um byte além do tamanho fixado já impede o hash de bater.

    Parar na hora é o que mantém o pico de memória perto do tamanho do
    arquivo: com o teto antigo de 4x, um servidor trocado podia nos fazer
    acumular meio giga antes de a conferência reprovar.
    """
    esperado = 200_000
    corpo = b"x" * (esperado * 3)
    _servir(monkeypatch, corpo)
    with pytest.raises(DownloadError, match="passou do tamanho esperado"):
        _baixar_zip("teste", "http://exemplo", esperado, "0" * 64, None)


def test_baixar_zip_recusa_o_arquivo_de_tamanho_certo_e_conteudo_errado(monkeypatch):
    corpo = b"y" * 1000
    _servir(monkeypatch, corpo)
    outro = hashlib.sha256(b"z" * 1000).hexdigest()
    with pytest.raises(DownloadError, match="hash esperado"):
        _baixar_zip("teste", "http://exemplo", len(corpo), outro, None)
