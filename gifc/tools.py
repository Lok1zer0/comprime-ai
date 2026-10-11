"""Localização das ferramentas externas (ffmpeg, ffprobe, gifsicle, oxipng)."""

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
BIN_DIR = PROJECT_ROOT / "bin"

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


class MissingToolError(Exception):
    """Uma ferramenta externa obrigatória não foi encontrada."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(
            f"Ferramenta não encontrada: {tool}. "
            f"Instale-a e garanta que está no PATH."
        )


@dataclass(frozen=True)
class Tools:
    """Caminhos das ferramentas externas.

    `gifsicle` é None quando o binário não está disponível — nesse caso o
    app roda em modo degradado, sem o estágio de compressão lossy. O mesmo
    vale para `oxipng`, que no PNG é o estágio de recompressão sem perda.
    """

    ffmpeg: str
    ffprobe: str
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


def find_tools(bin_dir: Path = BIN_DIR) -> Tools:
    """Localiza as ferramentas. Levanta MissingToolError se faltar ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise MissingToolError("ffmpeg")

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise MissingToolError("ffprobe")

    gifsicle = _local_ou_path(gifsicle_path(bin_dir), "gifsicle")
    oxipng = _local_ou_path(oxipng_path(bin_dir), "oxipng")

    return Tools(ffmpeg=ffmpeg, ffprobe=ffprobe, gifsicle=gifsicle, oxipng=oxipng)


def _local_ou_path(local: Path, nome: str) -> str | None:
    """O binário portátil de `bin/`, senão o do PATH, senão None."""
    return str(local) if local.is_file() else shutil.which(nome)


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
    limite = tamanho * 4
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
                        f"O download do {nome} excedeu o tamanho máximo "
                        f"esperado ({limite} bytes)."
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
