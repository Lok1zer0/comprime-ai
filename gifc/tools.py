"""Localização das ferramentas externas (ffmpeg, gifsicle, oxipng)."""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Congelado, o app procura ao lado do próprio executável; solto, na raiz do projeto.
def _pasta_do_app() -> Path:
    """A pasta do executável quando congelado; a raiz do projeto fora."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


APP_DIR = _pasta_do_app()
BIN_DIR = APP_DIR / "bin"

GIFSICLE_URL = (
    "https://eternallybored.org/misc/gifsicle/releases/gifsicle-1.95-win64.zip"
)
GIFSICLE_SIZE = 230_072
GIFSICLE_SHA256 = "7e47dd0bfd5ee47f911464c57faeed89a8709a7625dd1c449b16579889539ee8"

# oxipng (licença MIT). A tag é versionada, então o zip e o hash são estáveis.
OXIPNG_URL = (
    "https://github.com/oxipng/oxipng/releases/download/v10.2.1/"
    "oxipng-10.2.1-x86_64-pc-windows-msvc.zip"
)
OXIPNG_ZIP_SIZE = 497_302
OXIPNG_SHA256 = "7e940f83ee46874b73f53031f96a15834cb70b220af27391fb06fe7b4dd798e1"

# ffmpeg (build LGPL da BtbN, versão 9.0). O binário é espelhado nos releases
# deste projeto (tag `ferramentas-v1`, com a procedência e o aviso LGPL no
# corpo) e NÃO baixado direto da BtbN: ela apaga autobuilds antigos, e a URL
# anterior, fixada numa tag datada, morreu em menos de quatro semanas. O
# espelho mantém o build reproduzível. Não aponte de volta para o upstream.
# É o `ffmpeg.exe` puro, sem zip: ffprobe e ffplay ficam de fora de propósito.
FFMPEG_URL = (
    "https://github.com/Lok1zer0/comprime-ai/releases/download/"
    "ferramentas-v1/ffmpeg.exe"
)
FFMPEG_SIZE = 132_290_560
FFMPEG_SHA256 = "b0429a63ce777566bd65e8b267813a547e8df9df3a4865d769460f2e0fbab535"

# Downloads já verificados, fora do git (`build/` é ignorado), para que um
# build limpo não retransfira os 132 MB do ffmpeg.
CACHE_DIR = PROJECT_ROOT / "build" / "cache-de-pacotes"


class MissingToolError(Exception):
    """Uma ferramenta externa obrigatória não foi encontrada."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        if getattr(sys, "frozen", False):
            dica = (
                "A instalação parece incompleta. Extraia o programa de novo "
                "por inteiro, sem tirar nada de dentro da pasta, ou baixe-o outra vez."
            )
        else:
            dica = "Instale-a e garanta que está no PATH."
        super().__init__(f"Ferramenta não encontrada: {tool}. {dica}")


@dataclass(frozen=True)
class Tools:
    """Caminhos das ferramentas externas.

    `gifsicle` é None quando o binário não está disponível — nesse caso o
    app roda em modo degradado, sem o estágio de compressão lossy. O mesmo
    vale para `oxipng`, que no PNG é o estágio de recompressão sem perda.
    """

    ffmpeg: str
    gifsicle: str | None
    oxipng: str | None = None


def _nome_exe(base: str) -> str:
    return f"{base}.exe" if sys.platform == "win32" else base


def gifsicle_path(bin_dir: Path) -> Path:
    """Caminho onde o gifsicle portátil fica dentro da pasta do app."""
    return bin_dir / _nome_exe("gifsicle")


def oxipng_path(bin_dir: Path) -> Path:
    """Caminho onde o oxipng portátil fica dentro da pasta do app."""
    return bin_dir / _nome_exe("oxipng")


def ffmpeg_path(bin_dir: Path) -> Path:
    """Caminho onde o ffmpeg portátil fica dentro da pasta do app."""
    return bin_dir / _nome_exe("ffmpeg")


def dirs_de_busca(bin_dir: Path) -> list[Path]:
    """Pastas onde procurar as ferramentas, da mais para a menos prioritária.

    Congelado: a pasta do executável, depois `sys._MEIPASS` (`_internal/` no
    `onedir`, pasta temporária no `onefile`); em seguida `bin_dir`. O PATH
    fica por último, em `find_tools`.
    """
    pastas: list[Path] = []
    if getattr(sys, "frozen", False):
        pastas.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            pastas.append(Path(meipass))
    pastas.append(bin_dir)
    return pastas


def _procurar(nome: str, pastas: list[Path]) -> str | None:
    """O binário `nome` na primeira pasta que o tiver, senão no PATH."""
    for pasta in pastas:
        candidato = pasta / _nome_exe(nome)
        if candidato.is_file():
            return str(candidato)
    return shutil.which(nome)


def find_tools(bin_dir: Path = BIN_DIR) -> Tools:
    """Localiza as ferramentas. Levanta MissingToolError se faltar ffmpeg."""
    pastas = dirs_de_busca(bin_dir)
    ffmpeg = _procurar("ffmpeg", pastas)
    if ffmpeg is None:
        raise MissingToolError("ffmpeg")
    return Tools(
        ffmpeg=ffmpeg,
        gifsicle=_procurar("gifsicle", pastas),
        oxipng=_procurar("oxipng", pastas),
    )


class DownloadError(Exception):
    """Falha ao obter ou extrair uma ferramenta baixada (gifsicle, oxipng)."""


def _extrair_exe(zip_bytes: bytes, destino: Path) -> Path:
    """Extrai de dentro do zip o executável `nome` para `destino`.

    `destino.parent.mkdir` e a escrita do arquivo final tocam o sistema de
    arquivos, então podem falhar por motivos alheios ao conteúdo do zip —
    uma sincronização do OneDrive travando o diretório, antivírus
    segurando o arquivo, permissão negada. Qualquer `OSError` nesse trecho
    vira `DownloadError`, para que quem chama (a GUI) sempre veja o mesmo
    tipo de falha e caia no modo degradado em vez de propagar um traceback.
    """
    bin_dir = destino.parent
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(
            f"Não foi possível criar a pasta {bin_dir}: {exc}"
        ) from exc

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        alvo = next(
            (n for n in z.namelist() if Path(n).name.lower() == destino.name.lower()),
            None,
        )
        if alvo is None:
            raise DownloadError(
                f"O arquivo baixado não contém {destino.name}."
            )
        # Proteção contra zip-slip: rejeita qualquer entrada cujo caminho
        # contenha "..". Isso é defesa em profundidade — a garantia real de
        # segurança é que `destino` é sempre `<ferramenta>_path(bin_dir)`,
        # nunca um caminho derivado da entrada do zip, então o local de
        # escrita é fixo estruturalmente, independente do que o zip alegar.
        if ".." in Path(alvo).parts:
            raise DownloadError(f"Caminho inseguro no zip: {alvo}")

        try:
            destino.write_bytes(z.read(alvo))
        except OSError as exc:
            raise DownloadError(
                f"Não foi possível gravar {destino}: {exc}"
            ) from exc

    return destino


def extract_gifsicle(zip_bytes: bytes, bin_dir: Path) -> Path:
    """Extrai o executável do gifsicle de dentro do zip baixado."""
    return _extrair_exe(zip_bytes, gifsicle_path(bin_dir))


def extract_oxipng(zip_bytes: bytes, bin_dir: Path) -> Path:
    """Extrai o executável do oxipng de dentro do zip baixado."""
    return _extrair_exe(zip_bytes, oxipng_path(bin_dir))


def _baixar_zip(
    nome: str,
    url: str,
    tamanho: int,
    sha256: str,
    on_progress: Callable[[int, int], None] | None,
) -> bytes:
    """Baixa `url` na memória, com teto de tamanho, e confere o SHA256.

    `on_progress`, quando fornecido, recebe `(baixado, total)` a cada trecho
    lido. Se o servidor não enviar `Content-Length`, `total` chega como 0 —
    quem for calcular uma porcentagem a partir disso deve tratar esse caso
    por conta própria, para não dividir por zero.
    """
    # O teto é o tamanho exato que se espera, não um múltiplo dele: `tamanho`
    # é fixado junto do SHA256, então qualquer byte além dele já garante que o
    # hash não vai bater. Parar ali é tão correto quanto conferir no fim e
    # mantém o pico de memória perto do tamanho do arquivo em vez de 4x ele —
    # com o ffmpeg de 132 MB, a diferença é entre ~264 MB e ~1 GB.
    limite = tamanho
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            pedacos: list[bytes] = []
            baixado = 0
            while True:
                pedaco = resp.read(64 * 1024)
                if not pedaco:
                    break
                pedacos.append(pedaco)
                baixado += len(pedaco)
                if baixado > limite:
                    raise DownloadError(
                        f"O download do {nome} passou do tamanho esperado "
                        f"({limite} bytes); o arquivo no servidor não é o "
                        f"que esta versão espera."
                    )
                if on_progress is not None:
                    on_progress(baixado, total)
            dados = b"".join(pedacos)
    except OSError as exc:
        raise DownloadError(f"Não foi possível baixar o {nome}: {exc}") from exc

    digest = hashlib.sha256(dados).hexdigest()
    if digest != sha256:
        raise DownloadError(
            f"O {nome} baixado não confere com o hash esperado.\n"
            f"esperado: {sha256}\nobtido:   {digest}"
        )
    return dados


def download_gifsicle(
    bin_dir: Path = BIN_DIR,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Baixa o gifsicle portátil para `bin_dir` e devolve o caminho do exe."""
    dados = _baixar_zip(
        "gifsicle", GIFSICLE_URL, GIFSICLE_SIZE, GIFSICLE_SHA256, on_progress
    )
    return extract_gifsicle(dados, bin_dir)


def download_oxipng(
    bin_dir: Path = BIN_DIR,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Baixa o oxipng portátil para `bin_dir` e devolve o caminho do exe."""
    dados = _baixar_zip(
        "oxipng", OXIPNG_URL, OXIPNG_ZIP_SIZE, OXIPNG_SHA256, on_progress
    )
    return extract_oxipng(dados, bin_dir)


def _binario_em_cache(arquivo: Path, tamanho: int, sha256: str) -> bytes | None:
    """O conteúdo do binário em cache, ou None se faltar ou não bater o hash."""
    try:
        if arquivo.stat().st_size != tamanho:
            return None
        dados = arquivo.read_bytes()
    except OSError:
        return None
    return dados if hashlib.sha256(dados).hexdigest() == sha256 else None


def _gravar_atomico(destino: Path, dados: bytes) -> None:
    """Grava `dados` em `destino` via arquivo temporário, sem deixar lixo.

    A limpeza do `.parcial` não é zelo estético: `destino` costuma ser o
    `bin/`, que o empacotamento copia inteiro. Um `ffmpeg.parcial` de 100 MB
    sobrando de um disco que encheu no meio da escrita iria para dentro do
    pacote publicado. O `find_tools` não se enganaria com ele (procura
    `ffmpeg.exe`), então o estrago é só peso — mas é peso enviado ao usuário.
    """
    parcial = destino.with_suffix(".parcial")
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        parcial.write_bytes(dados)
        parcial.replace(destino)
    except OSError:
        parcial.unlink(missing_ok=True)
        raise


def download_ffmpeg(
    bin_dir: Path = BIN_DIR,
    on_progress: Callable[[int, int], None] | None = None,
    cache_dir: Path = CACHE_DIR,
) -> Path:
    """Baixa o ffmpeg LGPL para `bin_dir` e devolve o caminho do exe.

    O binário verificado fica em `cache_dir` e é reaproveitado enquanto o
    hash bater; um cache truncado ou adulterado é descartado e rebaixado.
    Só um download que passou na conferência chega a ser gravado no cache.
    """
    arquivo = cache_dir / f"ffmpeg-{FFMPEG_SHA256[:12]}.exe"
    dados = _binario_em_cache(arquivo, FFMPEG_SIZE, FFMPEG_SHA256)
    if dados is None:
        dados = _baixar_zip(
            "ffmpeg", FFMPEG_URL, FFMPEG_SIZE, FFMPEG_SHA256, on_progress
        )
        try:
            _gravar_atomico(arquivo, dados)
        except OSError:
            pass  # sem cache o build só fica mais lento; não é falha
    destino = ffmpeg_path(bin_dir)
    try:
        _gravar_atomico(destino, dados)
    except OSError as exc:
        raise DownloadError(f"Não foi possível gravar {destino}: {exc}") from exc
    return destino
