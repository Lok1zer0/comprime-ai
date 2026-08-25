"""Localização das ferramentas externas (ffmpeg, ffprobe, gifsicle)."""

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
    app roda em modo degradado, sem o estágio de compressão lossy.
    """

    ffmpeg: str
    ffprobe: str
    gifsicle: str | None


def gifsicle_path(bin_dir: Path) -> Path:
    """Caminho onde o gifsicle portátil fica dentro da pasta do app."""
    name = "gifsicle.exe" if sys.platform == "win32" else "gifsicle"
    return bin_dir / name


def find_tools(bin_dir: Path = BIN_DIR) -> Tools:
    """Localiza as ferramentas. Levanta MissingToolError se faltar ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise MissingToolError("ffmpeg")

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise MissingToolError("ffprobe")

    local = gifsicle_path(bin_dir)
    gifsicle = str(local) if local.is_file() else shutil.which("gifsicle")

    return Tools(ffmpeg=ffmpeg, ffprobe=ffprobe, gifsicle=gifsicle)


class DownloadError(Exception):
    """Falha ao obter ou extrair o gifsicle."""


def extract_gifsicle(zip_bytes: bytes, bin_dir: Path) -> Path:
    """Extrai o executável do gifsicle de dentro do zip baixado.

    `bin_dir.mkdir` e a escrita do arquivo final tocam o sistema de
    arquivos, então podem falhar por motivos alheios ao conteúdo do zip —
    uma sincronização do OneDrive travando o diretório, antivírus
    segurando o arquivo, permissão negada. Qualquer `OSError` nesse trecho
    vira `DownloadError`, para que quem chama (a GUI) sempre veja o mesmo
    tipo de falha e caia no modo degradado em vez de propagar um traceback.
    """
    destino = gifsicle_path(bin_dir)
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(
            f"Não foi possível criar a pasta {bin_dir}: {exc}"
        ) from exc

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        alvo = next(
            (n for n in z.namelist() if Path(n).name.lower() == "gifsicle.exe"),
            None,
        )
        if alvo is None:
            raise DownloadError(
                "O arquivo baixado não contém gifsicle.exe."
            )
        # Proteção contra zip-slip: rejeita qualquer entrada cujo caminho
        # contenha "..". Isso é defesa em profundidade — a garantia real de
        # segurança é que `destino` acima é sempre `gifsicle_path(bin_dir)`,
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


def download_gifsicle(
    bin_dir: Path = BIN_DIR,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Baixa o gifsicle portátil para `bin_dir` e devolve o caminho do exe.

    `on_progress`, quando fornecido, recebe `(baixado, total)` a cada trecho
    lido. Se o servidor não enviar `Content-Length`, `total` chega como 0 —
    quem for calcular uma porcentagem a partir disso deve tratar esse caso
    por conta própria, para não dividir por zero.
    """
    limite = GIFSICLE_SIZE * 4
    try:
        with urllib.request.urlopen(GIFSICLE_URL, timeout=60) as resp:
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
                        "O download do gifsicle excedeu o tamanho máximo "
                        f"esperado ({limite} bytes)."
                    )
                if on_progress is not None:
                    on_progress(baixado, total)
            dados = b"".join(pedacos)
    except OSError as exc:
        raise DownloadError(f"Não foi possível baixar o gifsicle: {exc}") from exc

    digest = hashlib.sha256(dados).hexdigest()
    if digest != GIFSICLE_SHA256:
        raise DownloadError(
            "O gifsicle baixado não confere com o hash esperado.\n"
            f"esperado: {GIFSICLE_SHA256}\nobtido:   {digest}"
        )

    return extract_gifsicle(dados, bin_dir)
