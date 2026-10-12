import json
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from PIL import Image

from gifc import gifmeta
from gifc.probe import FPS_PADRAO, Formato, ImagemInfo, ProbeError, probe

LARGURA, ALTURA = 37, 21  # não quadrado, para pegar largura/altura trocadas


def _quadro(i: int, tam=(LARGURA, ALTURA)) -> Image.Image:
    """Quadros distintos entre si: o Pillow funde quadros idênticos num GIF."""
    return Image.new("RGB", tam, ((i * 40) % 256, (i * 90) % 256, (i * 130) % 256))


def _salvar_gif(destino: Path, delays_ms, tam=(LARGURA, ALTURA)) -> Path:
    quadros = [_quadro(i, tam) for i in range(len(delays_ms))]
    quadros[0].save(
        destino, save_all=True, append_images=quadros[1:],
        duration=list(delays_ms), loop=0, format="GIF",
    )
    return destino


def _salvar(destino: Path, formato: Formato) -> Path:
    """Escreve um arquivo estático real do formato pedido, com o nome dado."""
    if formato is Formato.GIF:
        return _salvar_gif(destino, [100])
    pillow = {Formato.PNG: "PNG", Formato.JPEG: "JPEG", Formato.WEBP: "WEBP"}[formato]
    _quadro(1).save(destino, format=pillow)
    return destino


# --- detecção de formato ------------------------------------------------

@pytest.mark.parametrize("formato", list(Formato))
def test_detecta_gif_png_jpeg_webp(tmp_path, formato):
    arquivo = _salvar(tmp_path / f"a.{formato.value}", formato)
    assert probe(arquivo).formato is formato


@pytest.mark.parametrize("real", list(Formato))
@pytest.mark.parametrize("extensao", [".gif", ".png", ".jpg", ".webp"])
def test_deteccao_e_por_conteudo_nao_por_extensao(tmp_path, real, extensao):
    """Review Focus 3: usuários renomeiam arquivos. Vale para todas as
    combinações de formato real x extensão, inclusive as coincidentes."""
    arquivo = _salvar(tmp_path / f"foto{extensao}", real)
    info = probe(arquivo)
    assert info.formato is real
    assert (info.width, info.height) == (LARGURA, ALTURA)


def test_extensao_desconhecida_mas_conteudo_valido_e_aceito(tmp_path):
    arquivo = _salvar(tmp_path / "arquivo.dat", Formato.PNG)
    assert probe(arquivo).formato is Formato.PNG


def test_arquivo_sem_extensao_e_aceito(tmp_path):
    arquivo = _salvar(tmp_path / "semextensao", Formato.JPEG)
    assert probe(arquivo).formato is Formato.JPEG


def test_conteudo_nao_reconhecido_levanta_probeerror(tmp_path):
    ruim = tmp_path / "lixo.png"  # extensão honesta não salva conteúdo ruim
    ruim.write_bytes(b"isto nao e uma imagem de nenhum formato conhecido")
    with pytest.raises(ProbeError, match=r"lixo\.png.*não reconhec"):
        probe(ruim)


@pytest.mark.parametrize("conteudo", [b"", b"GIF", b"\x89PNG", b"RIFF"])
def test_arquivo_vazio_ou_curto_demais_levanta_probeerror(tmp_path, conteudo):
    curto = tmp_path / "curto.bin"
    curto.write_bytes(conteudo)
    with pytest.raises(ProbeError, match="curto.bin"):
        probe(curto)


def test_riff_que_nao_e_webp_nao_e_reconhecido(tmp_path):
    """RIFF também é WAV/AVI: só 'WEBP' nos bytes 8-12 vale como WebP."""
    wav = tmp_path / "som.webp"
    wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32)
    with pytest.raises(ProbeError, match="som.webp"):
        probe(wav)


@pytest.mark.parametrize(
    "cabecalho", [b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff\xe0", b"RIFF\x04\x00\x00\x00WEBP"]
)
def test_assinatura_valida_com_corpo_corrompido_levanta_probeerror(tmp_path, cabecalho):
    """Passa pela detecção, falha no Pillow: o erro deve ser ProbeError,
    nunca uma exceção do Pillow vazando."""
    quebrado = tmp_path / "quebrado.img"
    quebrado.write_bytes(cabecalho + b"\x00" * 20)
    with pytest.raises(ProbeError, match="quebrado.img"):
        probe(quebrado)


def test_arquivo_inexistente(tmp_path):
    with pytest.raises(ProbeError, match="nao-existe.gif"):
        probe(tmp_path / "nao-existe.gif")


def test_diretorio_nao_e_arquivo(tmp_path):
    with pytest.raises(ProbeError):
        probe(tmp_path)


# --- dimensões, tamanho, imutabilidade ----------------------------------

@pytest.mark.parametrize("formato", list(Formato))
def test_probe_le_dimensoes(tmp_path, formato):
    info = probe(_salvar(tmp_path / "a.img", formato))
    assert info.width == LARGURA
    assert info.height == ALTURA


@pytest.mark.parametrize("formato", list(Formato))
def test_probe_le_tamanho_do_arquivo_e_guarda_o_caminho(tmp_path, formato):
    arquivo = _salvar(tmp_path / "a.img", formato)
    info = probe(arquivo)
    assert info.size_bytes == arquivo.stat().st_size > 0
    assert info.path == arquivo


@pytest.mark.parametrize("formato", list(Formato))
def test_probe_nao_altera_o_arquivo(tmp_path, formato):
    arquivo = _salvar(tmp_path / "a.img", formato)
    antes = arquivo.read_bytes()
    mtime = arquivo.stat().st_mtime_ns
    probe(arquivo)
    assert arquivo.read_bytes() == antes
    assert arquivo.stat().st_mtime_ns == mtime


def test_imagem_info_e_imutavel():
    info = ImagemInfo(
        path=Path("x.gif"), formato=Formato.GIF, width=1, height=1, fps=1.0,
        frame_count=1, duration=1.0, size_bytes=1,
    )
    with pytest.raises(FrozenInstanceError):
        info.width = 2  # type: ignore[misc]


def test_formato_tem_exatamente_os_quatro_formatos():
    assert {f.name for f in Formato} == {"GIF", "PNG", "JPEG", "WEBP"}


# --- GIF: fps e duração vêm dos delays ----------------------------------

def test_fps_derivado_dos_delays_do_gif(tmp_path):
    arquivo = _salvar_gif(tmp_path / "a.gif", [100] * 10)  # 10 quadros, 10 cs
    info = probe(arquivo)
    assert info.frame_count == 10
    assert info.duration == pytest.approx(1.0)
    assert info.fps == pytest.approx(10.0)


def test_fps_nao_e_o_padrao_quando_os_delays_dizem_outra_coisa(tmp_path):
    """5 quadros de 4 cs = 0,2 s = 25 fps. Um probe que devolvesse sempre
    FPS_PADRAO passaria no teste de cima por coincidência; este não."""
    info = probe(_salvar_gif(tmp_path / "a.gif", [40] * 5))
    assert info.duration == pytest.approx(0.2)
    assert info.fps == pytest.approx(25.0)
    assert info.fps != FPS_PADRAO


def test_duracao_e_a_soma_dos_delays_de_quadros_desiguais(tmp_path):
    """10+20+30 cs = 0,6 s; fps é a média, 3/0,6."""
    info = probe(_salvar_gif(tmp_path / "a.gif", [100, 200, 300]))
    assert info.frame_count == 3
    assert info.duration == pytest.approx(0.6)
    assert info.fps == pytest.approx(5.0)


def test_delay_de_um_centesimo_nao_e_limitado(tmp_path):
    """Sem clamp: 1 cs por quadro é 100 fps, como o ffprobe reporta."""
    arquivo = _salvar_gif(tmp_path / "a.gif", [10] * 4)
    assert gifmeta.parse(arquivo.read_bytes()).total_delay_cs == 4
    info = probe(arquivo)
    assert info.duration == pytest.approx(0.04)
    assert info.fps == pytest.approx(100.0)


def test_gif_sem_delays_cai_no_padrao_sem_inventar_duracao(tmp_path):
    arquivo = _salvar_gif(tmp_path / "a.gif", [0] * 6)
    bruto = gifmeta.parse(arquivo.read_bytes())
    assert bruto.frame_count == 6 and bruto.total_delay_cs == 0  # pré-condição
    info = probe(arquivo)
    assert info.frame_count == 6
    assert info.fps == FPS_PADRAO
    assert info.duration == 0.0


def test_gif_animado_e_aceito(tmp_path):
    info = probe(_salvar_gif(tmp_path / "anim.gif", [50] * 8))
    assert info.formato is Formato.GIF
    assert info.frame_count == 8


def test_gif_de_quadro_unico(tmp_path):
    info = probe(_salvar_gif(tmp_path / "um.gif", [0]))
    assert info.frame_count == 1
    assert info.fps == FPS_PADRAO
    assert info.duration == 0.0


def test_traduz_gifmetaerror_para_probeerror(tmp_path, monkeypatch):
    """O módulo interno não vaza: nem a exceção, nem a classe como tipo."""
    arquivo = _salvar_gif(tmp_path / "a.gif", [100] * 3)
    original = gifmeta.GifMetaError("falha simulada no parser")

    def explode(dados):
        raise original

    monkeypatch.setattr(gifmeta, "parse", explode)
    with pytest.raises(ProbeError, match=r"a\.gif.*falha simulada") as exc:
        probe(arquivo)
    assert not isinstance(exc.value, gifmeta.GifMetaError)
    assert exc.value.__cause__ is original


def test_gif_truncado_de_verdade_vira_probeerror(tmp_path):
    inteiro = _salvar_gif(tmp_path / "a.gif", [100] * 3).read_bytes()
    cortado = tmp_path / "cortado.gif"
    cortado.write_bytes(inteiro[: len(inteiro) // 2])
    with pytest.raises(ProbeError, match="cortado.gif") as exc:
        probe(cortado)
    assert not isinstance(exc.value, gifmeta.GifMetaError)


# --- formatos estáticos --------------------------------------------------

@pytest.mark.parametrize("formato", [Formato.PNG, Formato.JPEG, Formato.WEBP])
def test_estatico_tem_valores_neutros_de_animacao(tmp_path, formato):
    info = probe(_salvar(tmp_path / "a.img", formato))
    assert info.frame_count == 1
    assert info.duration == 0.0
    assert info.fps == FPS_PADRAO


def test_jpeg_multi_imagem_mpo_nao_e_confundido_com_animacao(tmp_path):
    """Fotos de celular são MPO (várias imagens num JPEG) e o Pillow reporta
    n_frames > 1 nelas. Não é animação: tem de ser aceito como JPEG."""
    arquivo = tmp_path / "celular.jpg"
    _quadro(1).save(
        arquivo, format="MPO", save_all=True, append_images=[_quadro(2)]
    )
    with Image.open(arquivo) as img:  # pré-condição: o cenário existe de fato
        assert getattr(img, "n_frames", 1) > 1
    info = probe(arquivo)
    assert info.formato is Formato.JPEG
    assert info.frame_count == 1
    assert (info.width, info.height) == (LARGURA, ALTURA)


# --- animação em PNG/WebP é recusada ------------------------------------

def test_webp_animado_e_recusado(tmp_path):
    """Review Focus 5: o Pillow abre sem reclamar e comprimiria só o primeiro
    quadro, perdendo a animação em silêncio."""
    arquivo = tmp_path / "anim.webp"
    quadros = [_quadro(i) for i in range(4)]
    quadros[0].save(
        arquivo, save_all=True, append_images=quadros[1:], duration=100, lossless=True
    )
    with Image.open(arquivo) as img:  # pré-condição: é mesmo animado
        assert img.n_frames == 4
    with pytest.raises(ProbeError, match=r"anim\.webp.*WebP animado"):
        probe(arquivo)


def test_png_animado_apng_e_recusado(tmp_path):
    arquivo = tmp_path / "anim.png"
    quadros = [_quadro(i) for i in range(4)]
    quadros[0].save(
        arquivo, format="PNG", save_all=True, append_images=quadros[1:], duration=100
    )
    with Image.open(arquivo) as img:
        assert img.n_frames == 4
    with pytest.raises(ProbeError, match=r"anim\.png.*PNG animado"):
        probe(arquivo)


def test_recusa_de_animacao_nao_altera_o_arquivo(tmp_path):
    arquivo = tmp_path / "anim.webp"
    quadros = [_quadro(i) for i in range(3)]
    quadros[0].save(arquivo, save_all=True, append_images=quadros[1:], lossless=True)
    antes = arquivo.read_bytes()
    with pytest.raises(ProbeError):
        probe(arquivo)
    assert arquivo.read_bytes() == antes


# --- teste diferencial contra o ffprobe ---------------------------------

def _ffprobe_gif(ffprobe: str, arquivo: Path) -> dict:
    proc = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=width,height,nb_read_frames,duration",
            "-print_format", "json", str(arquivo),
        ],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(proc.stdout)["streams"][0]


_TAMANHOS = [(16, 16), (64, 48), (321, 177)]
_TAXAS = [1, 10, 25, 30, 100]  # 30 e 100 forçam delays desiguais e de 1 cs
_QUADROS = [1, 3, 20]


@pytest.mark.integration
@pytest.mark.parametrize("quadros", _QUADROS)
@pytest.mark.parametrize("taxa", _TAXAS)
@pytest.mark.parametrize("tamanho", _TAMANHOS, ids=lambda t: f"{t[0]}x{t[1]}")
def test_concorda_com_o_ffprobe_em_gifs(tmp_path, tamanho, taxa, quadros):
    """Teste diferencial: prova que o parser em Python puro concorda com a
    ferramenta que ele aposenta. Inteiros batem exatamente; a duração, com
    tolerância de 1 centésimo."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe ausentes do PATH")

    largura, altura = tamanho
    arquivo = tmp_path / "caso.gif"
    subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", "-f", "lavfi",
            "-i", f"testsrc=size={largura}x{altura}:rate={taxa}",
            "-frames:v", str(quadros), str(arquivo),
        ],
        check=True, capture_output=True, timeout=60,
    )

    esperado = _ffprobe_gif(ffprobe, arquivo)
    info = probe(arquivo)

    assert int(esperado["nb_read_frames"]) == quadros  # a matriz é o que diz ser
    assert (info.width, info.height) == (largura, altura)
    assert info.width == int(esperado["width"])
    assert info.height == int(esperado["height"])
    assert info.frame_count == int(esperado["nb_read_frames"])
    assert info.duration == pytest.approx(float(esperado["duration"]), abs=0.011)
