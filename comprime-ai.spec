# -*- mode: python ; coding: utf-8 -*-
"""Configuração do PyInstaller para o `comprime-ai`.

Chame por `python tools/build.py`, não à mão: é ele que garante os três
binários em `bin/`, recusa o build com o `.exe` aberto, constrói numa pasta de
estágio e só então substitui `dist/`.

O modo vem da variável de ambiente `COMPRIME_AI_MODO` (`onedir` ou `onefile`)
porque um `.spec` não recebe argumentos próprios na linha de comando: o
PyInstaller executa este arquivo como script e tudo depois do nome do spec é
argumento dele, não nosso. Variável de ambiente é o canal que resta.

Decisões que custaram para descobrir e não devem ser desfeitas sem medir:

- **`tkdnd` vai nos `datas`, em `tkinterdnd2/tkdnd`.** O PyInstaller não tem
  hook para o `tkinterdnd2`, e o `_require` dele monta o caminho da biblioteca
  nativa a partir de `os.path.dirname(tkinterdnd2.__file__)`. Qualquer outro
  destino faz o app compilar, abrir, e arrastar arquivo não fazer nada --
  sem erro, sem log, nada.
- **Os três binários vão em `binaries` com destino `.`**, que é a raiz do
  `sys._MEIPASS` (`_internal/` no `onedir`, pasta temporária no `onefile`).
  É a segunda pasta que `tools.dirs_de_busca` consulta, então o mesmo código
  encontra as ferramentas nos dois modos.
- **O `.ico` vai nos `datas`, em `assets/`.** No `onedir` os `datas` ficam em
  `_internal/`, NÃO ao lado do `.exe`; quem resolve isso em runtime é
  `tools.icone_path`, que olha o `sys._MEIPASS`. O `icon=` do `EXE` é outra
  coisa: aquele só serve para o Explorer desenhar o arquivo.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH vem do PyInstaller
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

NOME = "comprime-ai"

MODO = os.environ.get("COMPRIME_AI_MODO", "onedir")
if MODO not in {"onedir", "onefile"}:
    raise SystemExit(
        f"COMPRIME_AI_MODO inválido: {MODO!r}. Use 'onedir' ou 'onefile'."
    )

# ---------------------------------------------------------------- ícone ----
# Derivado de `assets/cai-logo.png`, nunca commitado. Falha macio: sem ícone
# o programa funciona igual, e derrubar o build por enfeite é desproporcional.
icone = None
try:
    from tools.gerar_icone import gerar

    icone = str(gerar())
except Exception as exc:  # noqa: BLE001 - ícone é enfeite, não derruba o build
    print(f"[spec] sem ícone ({exc}); o pacote sai com o ícone padrão")

# ----------------------------------------------------------------- tkdnd ---
import tkinterdnd2  # noqa: E402 - depende do sys.path montado acima

TKDND = Path(tkinterdnd2.__file__).resolve().parent / "tkdnd"
if not TKDND.is_dir():
    raise SystemExit(
        f"A pasta nativa do tkdnd não está em {TKDND}. Reinstale o tkinterdnd2 "
        "(`pip install -r requirements.txt`): sem ela o arrastar-e-soltar "
        "compila e não funciona."
    )

# ------------------------------------------------------------- binários ----
from gifc.tools import ffmpeg_path, gifsicle_path, oxipng_path  # noqa: E402

BIN = RAIZ / "bin"
EXIGIDOS = [ffmpeg_path(BIN), gifsicle_path(BIN), oxipng_path(BIN)]
faltando = [p.name for p in EXIGIDOS if not p.is_file()]
if faltando:
    raise SystemExit(
        "Faltam binários em bin/: "
        + ", ".join(faltando)
        + ".\nRode `python tools/build.py`, que baixa o que falta (do cache em "
        "build/cache-de-pacotes quando houver), ou ponha os arquivos à mão em "
        f"{BIN}."
    )

datas = [(str(TKDND), "tkinterdnd2/tkdnd")]
if icone is not None:
    datas.append((icone, "assets"))

binaries = [(str(p), ".") for p in EXIGIDOS]

# ---------------------------------------------------------------- cortes ---
# O PyInstaller segue imports e arrasta o que nada usa em runtime. Medido
# nesta máquina (o relatório da Tarefa 3 traz os números): o grosso vem do
# `numpy`, que entra porque o hook do `PIL.ImageFilter` o declara como import
# escondido -- e o app não usa `ImageFilter` nem `numpy` em nenhum caminho.
#
# `tkinter` NÃO entra nesta lista: é a interface inteira.
EXCLUDES = [
    # O caminho opcional do Pillow que puxa a pilha científica.
    "numpy",
    "scipy",
    "matplotlib",
    "pandas",
    # Toolkits gráficos alternativos: o hook do Pillow tenta os quatro.
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    # Ferramentas de desenvolvimento, nunca de runtime.
    "pytest",
    "_pytest",
    "pluggy",
    "iniconfig",
    "py",
    "setuptools",
    "pkg_resources",
    "pip",
    "IPython",
    # Módulos de teste e documentação da própria stdlib.
    "test",
    "unittest",
    "doctest",
    "pydoc",
    "pydoc_data",
    "lib2to3",
    "tkinter.test",
]

a = Analysis(  # noqa: F821 - classes injetadas pelo PyInstaller
    ["main.py"],
    pathex=[str(RAIZ)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

# `upx=False` de propósito: o UPX não está instalado nesta máquina, comprimir
# 132 MB de ffmpeg custaria minutos a cada build, e binário empacotado com UPX
# é um dos padrões que mais acende antivírus -- o SmartScreen já basta.
COMUM = dict(
    name=NOME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # `--noconsole`: sem janela de terminal preta atrás da interface.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icone,
)

if MODO == "onefile":
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        exclude_binaries=False,
        runtime_tmpdir=None,
        **COMUM,
    )
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **COMUM,
    )
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=NOME,
    )
