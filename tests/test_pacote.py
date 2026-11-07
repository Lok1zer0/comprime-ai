"""Verifica o pacote construído — o arquivo que o usuário baixa (Tarefa 4).

**Por que isto não olha arquivos dentro de `dist/`.** A Tarefa 4 foi escrita
supondo `onedir`, em que os binários, o `tkdnd` e os `.pyd` do Pillow ficam
como arquivos em `dist/comprime-ai/_internal/`. A medição da Tarefa 3 escolheu
`onefile` (2,0 s de abertura contra o limite de 10 s), e no `onefile` nada
disso existe como arquivo: `dist/` tem só o `.exe`, e o conteúdo só aparece
extraído no `_MEIPASS` enquanto o programa roda.

Então estes testes abrem o arquivo embutido do próprio `.exe` com o leitor do
PyInstaller, conferem o TOC e **extraem** o que precisam para uma pasta
temporária. Com isso cada afirmação é sobre o artefato da release, não sobre
um primo dele: a alternativa — construir o `onedir` e testar a árvore dele —
provaria coisas sobre um arquivo que ninguém vai baixar, e dependeria de os
dois modos saírem iguais da mesma spec para a conclusão valer.

Todos marcados `integration` e pulam com mensagem clara quando o pacote não
existe, para a suíte seguir verde numa máquina que não construiu nada.
"""

from __future__ import annotations

import importlib.util
import marshal
import os
import subprocess
import sys
import sysconfig
import time
import tkinter
from pathlib import Path

import pytest

from gifc import tools as gifc_tools
from gifc.compress import SUFIXO, Alvo, CompressError, compress_file
from gifc.ladder import Toggles
from gifc.probe import Formato
from gifc.tools import PROJECT_ROOT, Tools
from tests.imagens import captura_de_tela, foto
from tools.build import NOME, _familia, _mata_arvore, _tem_janela, exe_do_pacote

pytestmark = pytest.mark.integration

DIST = PROJECT_ROOT / "dist"
ASSET = exe_do_pacote(DIST, "onefile")

# Dentro do arquivo embutido os nomes usam a barra invertida do Windows.
TKDND_NO_PACOTE = "tkinterdnd2\\tkdnd"
# O Tcl desta máquina decide qual subpasta o tkinterdnd2 vai pedir; a 8.6 usa
# `win-x64` e a 9 usa `win-x64-tcl9`. As duas vão no pacote.
PLATAFORMA_TKDND = "win-x64"


def _por_que_nao_da() -> str | None:
    """A razão de pular, em português, ou None quando o pacote está lá."""
    if ASSET.is_file():
        return None
    pasta_onedir = exe_do_pacote(DIST, "onedir").parent
    if pasta_onedir.is_dir():
        return (
            f"em {DIST} está publicado o `onedir` ({pasta_onedir.name}/), e o "
            f"asset da release é o `onefile`. Rode "
            f"`python tools/build.py --modo onefile` para publicar o que estes "
            f"testes verificam."
        )
    return (
        f"o pacote não existe em {ASSET}. Rode `python tools/build.py` para "
        f"construí-lo (são ~70 MB e alguns minutos)."
    )


def _exige_pacote() -> None:
    motivo = _por_que_nao_da()
    if motivo:
        pytest.skip(motivo)


# ---------------------------------------------------------------- fixtures --


@pytest.fixture(scope="session")
def arquivo_embutido():
    """O leitor do arquivo que o PyInstaller embutiu no `.exe`."""
    _exige_pacote()
    from PyInstaller.archive.readers import CArchiveReader

    return CArchiveReader(str(ASSET))


@pytest.fixture(scope="session")
def toc(arquivo_embutido) -> dict:
    """Nome -> entrada, de tudo que viaja dentro do `.exe`."""
    return arquivo_embutido.toc


@pytest.fixture(scope="session")
def pyz(arquivo_embutido):
    """O arquivo de módulos Python (`PYZ.pyz`) de dentro do `.exe`."""
    return arquivo_embutido.open_embedded_archive("PYZ.pyz")


@pytest.fixture(scope="session")
def extraido(arquivo_embutido, toc, pyz, tmp_path_factory) -> Path:
    """O conteúdo do `.exe` que os testes precisam, extraído para o disco.

    Extrai os três binários, a árvore do `tkdnd` e o Pillow inteiro — os
    `.pyd` de codec saem do arquivo embutido e os módulos Python saem do PYZ,
    remontados como `.pyc` para que o `import` normal os encontre. Custa meio
    segundo (o ffmpeg é 132 MB descomprimidos) e vale por sessão.
    """
    destino = tmp_path_factory.mktemp("pacote-extraido")

    for nome in (f"{f}.exe" for f in ("ffmpeg", "gifsicle", "oxipng")):
        (destino / nome).write_bytes(arquivo_embutido.extract(nome))

    for nome in toc:
        norm = nome.replace("\\", "/")
        if norm.startswith("PIL/") or norm.startswith("tkinterdnd2/tkdnd/"):
            alvo = destino / norm
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(arquivo_embutido.extract(nome))

    PACOTE = 1  # PYZ_ITEM_PKG, do carregador do PyInstaller
    for nome, entrada in pyz.toc.items():
        if nome != "PIL" and not nome.startswith("PIL."):
            continue
        partes = nome.split(".")
        if entrada[0] == PACOTE:
            alvo = destino.joinpath(*partes) / "__init__.pyc"
        else:
            alvo = destino.joinpath(*partes[:-1]) / f"{partes[-1]}.pyc"
        alvo.parent.mkdir(parents=True, exist_ok=True)
        # Cabeçalho de `.pyc` sem fonte ao lado: mágica + flags/mtime/tamanho
        # zerados. Sem `.py` por perto, nada disso é conferido na carga.
        alvo.write_bytes(
            importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(pyz.extract(nome))
        )

    return destino


@pytest.fixture(scope="session")
def ferramentas_do_pacote(extraido) -> Tools:
    """`Tools` apontando para os binários que vieram de dentro do `.exe`."""
    return Tools(
        ffmpeg=str(extraido / "ffmpeg.exe"),
        gifsicle=str(extraido / "gifsicle.exe"),
        oxipng=str(extraido / "oxipng.exe"),
    )


@pytest.fixture
def sem_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """`PATH` vazio: o que funcionar aqui funcionou com o que veio no pacote.

    Sem isto, um teste passaria numa máquina que tem ffmpeg instalado mesmo
    que o empacotamento tivesse esquecido o binário — provaria o ambiente de
    quem construiu, não o pacote.
    """
    monkeypatch.setenv("PATH", "")


def amostra(formato: Formato, pasta: Path, ffmpeg: str, nome: str = "amostra") -> Path:
    """Um arquivo real de `formato` em `pasta`."""
    pasta.mkdir(parents=True, exist_ok=True)
    if formato is Formato.GIF:
        destino = pasta / f"{nome}.gif"
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=size=64x64:rate=10:duration=1", str(destino)],
            check=True, capture_output=True,
        )
        return destino
    if formato is Formato.PNG:
        destino = pasta / f"{nome}.png"
        captura_de_tela(400, 300).save(destino, "PNG", optimize=True)
        return destino
    if formato is Formato.JPEG:
        destino = pasta / f"{nome}.jpg"
        foto(320, 240).save(destino, "JPEG", quality=95)
        return destino
    destino = pasta / f"{nome}.webp"
    foto(320, 240).save(destino, "WEBP", quality=90)
    return destino


# ------------------------------------------------- o que viaja no artefato --


def test_o_asset_publicado_e_o_onefile() -> None:
    """`--modo ambos` deixa em `dist/` só o último modo construído.

    O asset da release é o `.exe` único; achar a pasta do `onedir` aqui
    significa que o build terminou publicando o modo errado.
    """
    _exige_pacote()
    assert ASSET.is_file()
    assert not exe_do_pacote(DIST, "onedir").parent.is_dir(), (
        f"{DIST} tem a pasta do onedir publicada junto; o asset da release é "
        f"{ASSET.name}"
    )


def test_os_tres_binarios_estao_no_pacote(toc) -> None:
    for nome in ("ffmpeg.exe", "gifsicle.exe", "oxipng.exe"):
        assert nome in toc, f"{nome} não está dentro de {ASSET.name}"


def test_o_ffmpeg_do_pacote_e_o_build_fixado(toc) -> None:
    """O tamanho descomprimido tem de ser o do binário que `tools.py` fixa.

    "O pacote que você testou tem de ser o pacote que o cliente recebe": um
    ffmpeg diferente do fixado passaria nos testes de comportamento e ainda
    assim seria outro programa.
    """
    _, _, descomprimido, _, _ = toc["ffmpeg.exe"]
    assert descomprimido == gifc_tools.FFMPEG_SIZE


def test_tkdnd_esta_no_pacote(toc) -> None:
    """A falha silenciosa mais provável de todo este trabalho.

    O `_require` do tkinterdnd2 monta o caminho da biblioteca nativa a partir
    de `os.path.dirname(tkinterdnd2.__file__)`, então o destino dentro do
    pacote tem de ser exatamente `tkinterdnd2/tkdnd/<plataforma>`. Em qualquer
    outro lugar o app compila, abre, e arrastar arquivo não faz nada — sem
    erro, sem log, nada.
    """
    prefixo = f"{TKDND_NO_PACOTE}\\{PLATAFORMA_TKDND}\\"
    dentro = [n for n in toc if n.startswith(prefixo)]
    assert dentro, f"nada em {prefixo} dentro de {ASSET.name}"
    assert f"{prefixo}pkgIndex.tcl" in toc
    assert any(n.endswith(".dll") for n in dentro), (
        f"a pasta {prefixo} veio sem a biblioteca nativa: {dentro}"
    )


def test_o_tkdnd_do_pacote_carrega_de_verdade(extraido) -> None:
    """Não basta estar lá: um Tcl de verdade tem de conseguir carregá-lo.

    É o mesmo `lappend auto_path` + `package require tkdnd` que o
    tkinterdnd2 faz ao abrir a janela. DLL truncada na extração ou compilada
    para outra arquitetura passaria no teste de presença e falharia aqui.
    """
    pasta = extraido / "tkinterdnd2" / "tkdnd" / PLATAFORMA_TKDND
    raiz = tkinter.Tk()
    try:
        raiz.withdraw()
        raiz.tk.call("lappend", "auto_path", str(pasta))
        versao = raiz.tk.call("package", "require", "tkdnd")
        assert str(versao).startswith("2."), versao
        # O que o app chama de verdade ao receber um arrasto de arquivos.
        assert raiz.tk.call("tkdnd::platform_independent_types", "CF_HDROP")
    finally:
        raiz.destroy()


def test_os_plugins_de_formato_do_pillow_estao_no_pyz(pyz) -> None:
    """Os quatro formatos da v1, cada um com o módulo que o decodifica."""
    for plugin in (
        "PIL.GifImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.JpegImagePlugin",
        "PIL.WebPImagePlugin",
    ):
        assert plugin in pyz.toc, f"{plugin} não está no PYZ de {ASSET.name}"


def test_pillow_do_pacote_tem_os_codecs_em_runtime(extraido, toc) -> None:
    """Review Focus 5, em runtime e não por inventário.

    Importa o Pillow **do pacote** (os `.pyd` do arquivo embutido mais os
    módulos do PYZ) num interpretador separado e codifica de verdade nos
    quatro formatos. O WebP é o que mais some no empacotamento e falharia só
    em tempo de execução, num formato só — o tipo de defeito que nenhum
    inventário de arquivos pega, porque o arquivo pode estar lá e não carregar.

    O `assert` sobre `PIL.__file__` é o que torna o teste honesto: sem ele, o
    subprocesso poderia estar usando o Pillow instalado nesta máquina.
    """
    sufixo = sysconfig.get_config_var("EXT_SUFFIX")
    if f"PIL\\_webp{sufixo}" not in toc:
        pytest.skip(
            f"o pacote foi construído para outro Python (não achei "
            f"PIL\\_webp{sufixo} dentro dele); rode o build com este "
            f"interpretador antes"
        )

    codigo = f"""
import io, sys
sys.path.insert(0, {str(extraido)!r})
import PIL
assert PIL.__file__.startswith({str(extraido)!r}), PIL.__file__
from PIL import Image, features
for formato in ("WEBP", "JPEG", "PNG", "GIF"):
    alvo = io.BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(alvo, formato)
    assert alvo.getvalue(), formato
    Image.open(io.BytesIO(alvo.getvalue())).load()
print("webp", features.check("webp"))
print("jpg", features.check_codec("jpg"))
print("zlib", features.check_codec("zlib"))
print("arquivo", PIL.__file__)
"""
    r = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, text=True
    )
    assert r.returncode == 0, f"o Pillow do pacote não rodou:\n{r.stderr[-2000:]}"
    assert "webp True" in r.stdout, r.stdout
    assert "jpg True" in r.stdout, r.stdout
    assert "zlib True" in r.stdout, r.stdout


VERSAO_FIXADA = {
    "ffmpeg.exe": ("-hide_banner", "-version", "n9.0"),
    "gifsicle.exe": ("--version", None, "Gifsicle 1.95"),
    "oxipng.exe": ("--version", None, "oxipng 10.2.1"),
}


@pytest.mark.parametrize("binario", sorted(VERSAO_FIXADA))
def test_os_binarios_do_pacote_rodam_nas_versoes_fixadas(
    binario: str, extraido: Path, sem_path: None
) -> None:
    """Rodam, e são as versões que o projeto fixou.

    Estar no TOC não é estar funcionando: binário truncado na montagem ou
    compilado para outra arquitetura aparece no inventário e só falha no
    primeiro uso, na máquina de quem baixou. E conferir a versão é o que
    sustenta "o pacote que você testou é o pacote que o cliente recebe" — um
    gifsicle diferente passaria em todos os testes de comportamento e ainda
    assim seria outro programa.
    """
    primeiro, segundo, esperado = VERSAO_FIXADA[binario]
    comando = [str(extraido / binario), primeiro]
    if segundo:
        comando.append(segundo)
    r = subprocess.run(comando, capture_output=True, text=True, errors="replace")
    saida = r.stdout + r.stderr
    assert esperado in saida, f"{binario} respondeu: {saida[:300]!r}"


def test_o_ffmpeg_do_pacote_tem_os_filtros_de_paleta(
    extraido: Path, sem_path: None
) -> None:
    """O pipeline de GIF é `palettegen`/`paletteuse`; o plano manda não supor.

    A Tarefa 2 conferiu isto no binário de `bin/`. Aqui é no que viajou dentro
    do `.exe`, que é o que o usuário executa.
    """
    r = subprocess.run(
        [str(extraido / "ffmpeg.exe"), "-hide_banner", "-filters"],
        capture_output=True, text=True, errors="replace",
    )
    saida = r.stdout + r.stderr
    assert "palettegen" in saida
    assert "paletteuse" in saida


def test_o_ffmpeg_do_pacote_e_o_build_lgpl(extraido: Path, sem_path: None) -> None:
    """O pacote declara ffmpeg LGPL; o binário tem de ser esse.

    `--enable-gpl` presente tornaria falsa a seção de licenças da release. O
    pacote inteiro segue GPL por causa do gifsicle, mas o que se afirma sobre
    cada dependência tem de ser verdade sobre o arquivo entregue.
    """
    r = subprocess.run(
        [str(extraido / "ffmpeg.exe"), "-hide_banner", "-version"],
        capture_output=True, text=True, errors="replace",
    )
    saida = r.stdout + r.stderr
    assert "--enable-version3" in saida, "não achei a marca do build LGPL"
    assert "--enable-gpl" not in saida, "este é um build GPL, não o LGPL fixado"


# --------------------------------------------- comportamento, com o pacote --


@pytest.mark.slow
@pytest.mark.parametrize("formato", list(Formato))
def test_comprime_cada_formato_usando_so_os_binarios_do_pacote(
    formato: Formato, tmp_path: Path, ferramentas_do_pacote: Tools, sem_path: None
) -> None:
    """Os quatro formatos, com `PATH` vazio e os binários vindos do `.exe`."""
    src = amostra(formato, tmp_path, ferramentas_do_pacote.ffmpeg)
    resultado = compress_file(src, Alvo.relativo(50), Toggles(), ferramentas_do_pacote)
    assert resultado.dst.is_file()
    assert resultado.final_bytes < resultado.original_bytes
    assert resultado.formato_entrada is formato


@pytest.mark.slow
@pytest.mark.parametrize("formato", [Formato.GIF, Formato.PNG])
def test_comprime_em_caminho_com_acentos_e_espacos(
    formato: Formato, tmp_path: Path, ferramentas_do_pacote: Tools, sem_path: None
) -> None:
    """Review Focus 1: a área de trabalho do dono do projeto tem acento.

    GIF e PNG porque são os dois que passam o caminho para um binário
    externo por linha de comando (ffmpeg, gifsicle, oxipng); JPEG e WebP são
    Pillow puro e não arriscam a codificação do ambiente do subprocesso.
    """
    pasta = tmp_path / "Área de Trabalho ção" / "com espaços e ação"
    src = amostra(formato, pasta, ferramentas_do_pacote.ffmpeg)
    resultado = compress_file(src, Alvo.relativo(50), Toggles(), ferramentas_do_pacote)
    assert resultado.dst.is_file()
    assert resultado.final_bytes < resultado.original_bytes
    assert "ção" in str(resultado.dst)


@pytest.mark.slow
def test_caminho_longo_proximo_do_limite_do_windows(
    tmp_path: Path, ferramentas_do_pacote: Tools, sem_path: None
) -> None:
    """Review Focus 2: erro claro em português, não `OSError` cru.

    O gatilho é o sufixo: um nome de 252 caracteres é aceito como entrada e o
    destino, com `_comprimido`, passa dos 255 que o NTFS permite por
    componente de caminho. Esse limite não depende do `LongPathsEnabled` do
    registro — é por isso que o teste mede o componente e não o caminho
    inteiro: nesta máquina a chave está ligada e caminhos de 591 caracteres
    funcionam nos quatro formatos, então um teste "perto dos 260" passaria
    aqui e esconderia o defeito.

    Este é o teste de pacote; o mesmo defeito tem cobertura mais fina em
    `tests/test_compress.py` (a sondagem do destino, a ordem em relação à
    codificação e as duas redes de segurança). Aqui o que se verifica é que
    o comportamento chega inteiro ao caminho que usa os binários do `.exe`.
    """
    nome = "a" * (255 - len(SUFIXO) - len(".png") + 1)
    src = tmp_path / f"{nome}.png"
    captura_de_tela(200, 150).save(src, "PNG", optimize=True)
    assert len(src.name) <= 255, "a entrada tem de ser aceitável"

    with pytest.raises(CompressError) as erro:
        compress_file(src, Alvo.relativo(50), Toggles(), ferramentas_do_pacote)
    msg = str(erro.value)
    assert "mais curto" in msg, f"não diz o que fazer: {msg}"
    assert "WinError" not in msg and "Errno" not in msg, (
        f"texto do sistema vazou para a interface: {msg}"
    )
    assert not list(tmp_path.glob(f"*{SUFIXO}*")), (
        "sobrou arquivo de destino depois da falha"
    )


# --------------------------------------------------------- o executável abre --


@pytest.mark.slow
def test_executavel_abre_sem_crash() -> None:
    """Lança o asset, confirma janela viva, encerra a árvore e confere.

    O plano pedia "processo vivo e stderr sem traceback". Com `--noconsole`
    não há stderr observável: o PyInstaller não anexa console, e um traceback
    de inicialização iria para uma caixa de diálogo do Windows, não para um
    descritor que o teste possa ler. A substituição é mais forte que ler
    stderr: exige **janela visível** com o título certo, o que só acontece se
    a importação, o Tk, o `tkinterdnd2` e a construção da janela inteira
    tiverem passado. Um processo que vive mas falhou na inicialização não tem
    janela; um que crashou não tem processo.
    """
    _exige_pacote()
    processo = subprocess.Popen([str(ASSET)], cwd=str(ASSET.parent))
    familia_vista: set[int] = {processo.pid}
    try:
        limite = time.monotonic() + 120
        while time.monotonic() < limite:
            familia_vista = _familia(processo.pid)
            if _tem_janela(familia_vista):
                break
            assert processo.poll() is None or familia_vista != {processo.pid}, (
                f"o {ASSET.name} encerrou com código {processo.returncode} "
                f"antes de abrir janela"
            )
            time.sleep(0.05)
        else:
            pytest.fail(f"o {ASSET.name} não abriu janela em 120 s")

        # Fica de pé depois de aberto, e não fecha sozinho um segundo depois.
        time.sleep(2.0)
        assert _tem_janela(_familia(processo.pid)), (
            "a janela desapareceu depois de abrir"
        )
        assert _titulos(_familia(processo.pid)) == ["Comprime Aí"]
    finally:
        for alvo in sorted(familia_vista | _familia(processo.pid) | {processo.pid}):
            _mata_arvore(alvo)
        processo.wait(timeout=30)

    assert processo.poll() is not None
    assert not _tem_janela(_familia(processo.pid)), (
        "sobrou janela do app depois do encerramento"
    )


def _titulos(pids: set[int]) -> list[str]:
    """Títulos das janelas de topo visíveis dos `pids`."""
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    achados: list[str] = []
    CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visita(hwnd: int, _lparam: int) -> bool:
        dono = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(dono))
        if dono.value in pids and u32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, buf, 256)
            if buf.value:
                achados.append(buf.value)
        return True

    u32.EnumWindows(CALLBACK(visita), 0)
    return achados


def test_o_nome_do_pacote_e_o_esperado() -> None:
    """Guarda contra renomear o executável sem atualizar quem o procura."""
    assert ASSET.name == f"{NOME}.exe"
    assert os.name == "nt", "o pacote é win64; estes testes não valem fora dele"
