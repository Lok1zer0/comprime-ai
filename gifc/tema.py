"""Tema escuro da interface.

CÓPIA do `tema.py` do ViralCut, não uma dependência: são dois projetos
pessoais que evoluem separados, e um pacote compartilhado obrigaria a
publicar e versionar algo só para isto. Se o original melhorar, a
ressincronização é manual.

Removido da cópia, por não existir aqui: `RAIO_ABA`, `_png_canto_aba` e
`arredonda_abas` (não há Notebook nesta janela), e `desenha_tesoura` /
`desenha_filme` (ícones da identidade do ViralCut). Alguns comentários
abaixo ainda citam `arredonda_abas` ao explicar por que o LabelFrame usa
outro caminho — a explicação continua válida, só a função não veio.

Três decisões que valem antes do código:

**O tema ttk vira `clam`.** No Windows o padrão é `vista`, que desenha os
widgets com a API nativa e simplesmente IGNORA `background` na maioria deles —
o resultado seria uma janela escura salpicada de caixas brancas. O `clam` é
desenhado pelo próprio Tk e aceita todas as cores.

**Os ícones são desenhados, não escritos.** Tesoura e claquete existem em
Unicode, mas dependem de a fonte do sistema ter o glifo; quando não tem, sai um
quadradinho. Um punhado de linhas num canvas sempre aparece igual.

**Cantos arredondados são imagem, não configuração.** O Tk/ttk não tem
`border-radius`: nenhum widget tem uma opção que arredonde a borda. Botão e
campo de entrada usam o caminho que funciona em geral -- gerar a borda como
PNG (Pillow, já empacotado), registrá-la como elemento do tema via
`element_create(..., "image", ...)` fatiado em 9 partes (`border=`), e trocar
o layout do estilo para usar esse elemento. Ver `_registra_bordas_arredondadas`
— não tente "simplificar" isso removendo a imagem por algo mais direto: a
opção direta não existe.

O LabelFrame ("as caixas") é arredondado por um caminho DIFERENTE,
`arredonda_labelframe`: o mesmo truque de imagem nele, medido, deixava a
janela levando dezenas de segundos para abrir (ver o motivo detalhado na
docstring da função) — então as caixas ganham 4 cantinhos desenhados por
cima via `place()`, que não participam da negociação de geometria do ttk.

O título da caixa também não usa o `text=` nativo do `ttk.Labelframe`: aquele
mecanismo estica o fundo do título até a largura reservada para ele (que o
ttk arredonda para cima, não para o texto), e o resultado é um retângulo reto
cortando o canto arredondado do corpo. `cria_labelframe` desenha um badge do
tamanho exato do texto (`_rotulo_labelframe`) e usa `labelwidget=` no lugar.
"""


from __future__ import annotations

import io
import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# paleta
# --------------------------------------------------------------------------
# Mesma identidade do painel da API (`admin/dashboard.php`), para que o app e a
# área do cliente pareçam o mesmo produto. Lá a paleta vive em sete variáveis
# CSS, e quatro delas são `rgba()` sobre um fundo OPACO -- então achatar cada
# uma contra `--bg` dá exatamente a cor que o navegador pinta. É por isso que a
# tradução é fiel e não uma aproximação: o Tk trata qualquer alfa diferente de
# zero como opaco, mas aqui não se perde nada, porque não há nada atrás.
#
# Escurecendo do fundo para a frente: janela, caixa, campo. Três níveis bastam
# para dar profundidade sem virar listra. O dashboard usa um `--panel` quase
# invisível (`rgba(255,140,0,.04)`) porque separa os blocos com borda e sombra;
# no Tk não há sombra, então o degrau do meio vem do mesmo branco translúcido
# que o dashboard já usa nos campos, só que com alfa menor.
FUNDO = "#080912"        # --bg
PAINEL = "#13141d"       # rgba(255,255,255,.045) sobre --bg
CAMPO = "#171820"        # rgba(255,255,255,.06) sobre --bg
BORDA = "#39230e"        # --border: rgba(255,140,0,.2) sobre --bg
BORDA_CLARA = "#4e2f10"  # rgba(255,140,0,.3) sobre --bg

TEXTO = "#ffffff"        # --text
APAGADO = "#77787d"      # --dim: rgba(255,255,255,.45) sobre --bg

# Laranja de lâmina: é a cor de playhead e de marcador em quase toda mesa de
# edição, e aqui marca o que é ação e o que é título de seção. Passou do laranja
# avermelhado para o `--accent` do dashboard, que é o mesmo laranja da marca.
LAMINA = "#FF8C00"       # --accent
LAMINA_ESCURA = "#e07700"

# Estados. Mais claros que os originais porque contraste sobre fundo escuro
# funciona ao contrário: o que era legível no branco some aqui. São as cores de
# TEXTO do dashboard, não as de fundo: `--green` (#0b9b8a) é um teal de painel e
# cai para 3,65:1 de contraste como texto, abaixo do mínimo de corpo de texto --
# e o vermelho pinta o parágrafo inteiro de erro do login.
VERDE = "#5dff7a"
VERMELHO = "#ff6b6b"
AMARELO = "#f0c040"

FONTE_UI = "Segoe UI"

# Usada pelo badge do título (`_rotulo_labelframe`) para medir o texto e
# desenhar na mesma fonte. Aumentada de 9 para 11 a pedido de quem olhou a
# tela achando o título pequeno demais.
_FONTE_ROTULO_LABELFRAME = (FONTE_UI, 11, "bold")



# Marcadores de etapa. O canvas do Tk nao tem anti-aliasing: linha diagonal sai
# escadinha, e num simbolo de 16 px a escadinha e metade do desenho -- o check
# parecia partido ao meio. Desenhar com o Pillow em 8x e reduzir com LANCZOS
# resolve, pelo mesmo caminho que `_png_retangulo_arredondado` ja usa.
#
# O resultado fica em cache porque o estado "andando" se redesenha a cada 80 ms:
# gerar um PNG por quadro custaria mais que o desenho todo. Sao 12 angulos (o
# giro anda de 30 em 30 graus) mais tres estados parados, entao o cache satura
# em 15 imagens por tamanho e nunca mais desenha nada.
_MARCADORES: dict[tuple[str, int, int], tk.PhotoImage] = {}
_INTERPRETADOR_DOS_MARCADORES: object | None = None

_SUPER = 8  # fator de superamostragem


def _png_marcador(estado: str, lado: int, giro: int) -> bytes:
    px = lado * _SUPER
    u = px / 16.0  # uma unidade do quadro canonico de 16, onde as formas foram desenhadas
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    grosso = round(2 * u)
    caixa = (3 * u, 3 * u, px - 3 * u, px - 3 * u)

    def check(k: float, largura: float) -> tuple[list[tuple[float, float]], int]:
        pontos = [(3, 8), (6, 12), (13, 4)]
        return (
            [((x - 8) * k * u + px / 2, (y - 8) * k * u + px / 2) for x, y in pontos],
            round(largura * u),
        )

    if estado == "espera":
        d.ellipse(caixa, outline=BORDA_CLARA, width=grosso)
    elif estado == "andando":
        d.ellipse(caixa, outline=BORDA, width=grosso)
        # O Pillow mede angulo em graus horarios a partir das 3 horas, como o
        # canvas do Tk media -- so que ali o sentido era anti-horario, dai o
        # sinal trocado para o giro continuar rodando para o mesmo lado.
        d.arc(caixa, start=-giro, end=-giro + 90, fill=LAMINA, width=grosso)
    elif estado == "feito":
        # O simbolo interno e vazado na cor do PAINEL, nao branco: sobre o verde
        # o branco vibra, e o recorte na cor do fundo faz o circulo parecer
        # perfurado. So funciona porque estes marcadores vivem todos sobre
        # PAINEL -- num fundo diferente o vazado apareceria como mancha.
        d.ellipse(caixa, fill=VERDE)
        pontos, largura = check(0.46, 1.5)
        d.line(pontos, fill=PAINEL, width=largura, joint="curve")
    elif estado == "falhou":
        d.ellipse(caixa, fill=VERMELHO)
        r, largura = 2.0 * u, round(1.5 * u)
        for a, b in (((-1, -1), (1, 1)), ((1, -1), (-1, 1))):
            d.line(
                [
                    (px / 2 + a[0] * r, px / 2 + a[1] * r),
                    (px / 2 + b[0] * r, px / 2 + b[1] * r),
                ],
                fill=PAINEL,
                width=largura,
            )

    buf = io.BytesIO()
    img.resize((lado, lado), Image.LANCZOS).save(buf, format="PNG")
    return buf.getvalue()


def marcador(pai: tk.Misc, estado: str, lado: int, giro: int = 0) -> tk.PhotoImage:
    """O marcador de uma etapa do andamento, pronto para `create_image`.

    Estados: "espera" (circulo vazio), "andando" (circulo com arco girando),
    "feito" (circulo verde cheio com check) e "falhou" (circulo vermelho com x).
    Os quatro ocupam o mesmo circulo, entao o marcador nao muda de silhueta
    quando a etapa avanca -- so de cor.

    `giro` so importa em "andando"; nos outros e ignorado para os tres estados
    parados nao ocuparem uma entrada de cache por angulo.
    """
    global _INTERPRETADOR_DOS_MARCADORES
    # Um `PhotoImage` pertence ao interpretador Tcl que o criou e morre com ele.
    # A suite de testes cria e destroi varias raizes, e reaproveitar imagem de
    # uma raiz morta e TclError -- entao o cache se esvazia quando a raiz troca.
    if _INTERPRETADOR_DOS_MARCADORES is not pai.tk:
        _MARCADORES.clear()
        _INTERPRETADOR_DOS_MARCADORES = pai.tk

    chave = (estado, lado, giro % 360 if estado == "andando" else 0)
    imagem = _MARCADORES.get(chave)
    if imagem is None:
        imagem = tk.PhotoImage(
            master=pai, data=_png_marcador(estado, lado, chave[2]), format="png"
        )
        _MARCADORES[chave] = imagem
    return imagem


def identidade_no_windows(id_do_app: str = "Lok1zer0.ComprimeAi") -> bool:
    """Diz ao Windows que este processo é um aplicativo próprio.

    Sem um AppUserModelID, a barra de tarefas trata o processo como mais uma
    janela do interpretador que o hospeda e resolve o ícone do botão pela
    associação desse interpretador -- é assim que a pena do Python aparece num
    programa que não tem nada a ver com ela.

    Tem de ser chamada ANTES de a primeira janela existir, senão o Windows já
    agrupou o botão e não reavalia.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(id_do_app)
        return True
    except Exception:  # noqa: BLE001 - identidade e enfeite, nao pode derrubar a janela
        log.debug("nao consegui definir o AppUserModelID", exc_info=True)
        return False


def icone_da_janela(janela: tk.Misc, caminho: Path) -> bool:
    """Põe o `.ico` na janela pela API do Windows. True se conseguiu.

    O `wm iconbitmap` do Tk não basta: medido nesta máquina, com ele a janela
    do executável respondia 0 aos três `WM_GETICON` -- ou seja, não tinha ícone
    nenhum, e o Windows caía no ícone da CLASSE que o Tk registrou, que é a
    pena. O ícone embutido no `.exe` também não resolve: aquele só vale para o
    Explorer desenhar o arquivo.

    Quem manda no botão da barra de tarefas e na barra de título é o ícone da
    JANELA, e ele se define mandando `WM_SETICON` para ela. São dois tamanhos
    diferentes, carregados separados: o grande alimenta a barra de tarefas e o
    Alt+Tab, o pequeno a barra de título. Carregar um só e deixar o Windows
    reduzir dá o resultado borrado que aparecia antes.

    Falha em silêncio: é enfeite, e num sistema que não seja Windows
    simplesmente não se aplica.
    """
    if sys.platform != "win32":
        return False
    if not caminho.exists():
        log.debug("icone %s nao existe", caminho)
        return False
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        # Sem declarar o tipo de retorno o ctypes trunca o handle para 32 bits,
        # e num processo de 64 bits o HICON resultante e invalido.
        u32.LoadImageW.restype = wintypes.HANDLE
        u32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        u32.SendMessageW.restype = ctypes.c_void_p
        u32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            ctypes.c_void_p,
        ]

        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        SM_CXICON, SM_CYICON, SM_CXSMICON, SM_CYSMICON = 11, 12, 49, 50

        janela.update_idletasks()
        # `winfo_id` devolve a janela FILHA; a moldura, e portanto o icone, e da
        # janela de topo -- mesmo detalhe de `barra_escura`.
        hwnd = u32.GetParent(janela.winfo_id())

        tamanhos = (
            (ICON_BIG, u32.GetSystemMetrics(SM_CXICON), u32.GetSystemMetrics(SM_CYICON)),
            (ICON_SMALL, u32.GetSystemMetrics(SM_CXSMICON), u32.GetSystemMetrics(SM_CYSMICON)),
        )
        vivos = []
        for qual, cx, cy in tamanhos:
            h = u32.LoadImageW(None, str(caminho), IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
            if not h:
                return False
            u32.SendMessageW(hwnd, WM_SETICON, qual, h)
            vivos.append(h)
        # Os handles nao podem ser destruidos enquanto a janela existir; guardar
        # a referencia no widget deixa isso explicito para quem ler depois.
        janela._icones_vivos = vivos  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001 - icone e enfeite, nao pode derrubar a janela
        log.debug("nao consegui por o icone da janela", exc_info=True)
        return False


def barra_escura(janela: tk.Misc) -> bool:
    """Pinta a barra de título do Windows de escuro. True se conseguiu.

    A moldura da janela é desenhada pelo SISTEMA, não pelo Tk — nenhuma cor do
    tema chega nela, e o programa ficava escuro com uma faixa branca em cima.
    Quem manda nisso é o gerenciador de janelas, pelo atributo
    `DWMWA_USE_IMMERSIVE_DARK_MODE` do dwmapi.

    Dois detalhes que custam para descobrir:

    O `winfo_id()` do Tk devolve o handle da janela FILHA, e o gerenciador só
    aceita o da janela de topo — daí o `GetParent`.

    O número do atributo mudou: é 20 a partir da build 19041, e era 19 nas
    versões anteriores, quando o recurso ainda não era oficial.

    E mesmo com o atributo aceito, a moldura só se redesenha no próximo evento
    de janela; o `SetWindowPos` com `FRAMECHANGED` força isso na hora, senão a
    faixa só escurece quando o usuário move a janela.

    Falha em silêncio: é enfeite, e num Windows antigo ou noutro sistema
    operacional simplesmente não se aplica.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        janela.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(janela.winfo_id())
        atributo = 20 if sys.getwindowsversion().build >= 19041 else 19
        ligado = ctypes.c_int(1)
        r = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, atributo, ctypes.byref(ligado), ctypes.sizeof(ligado)
        )
        if r != 0:
            return False
        # SWP_NOSIZE | SWP_NOMOVE | SWP_FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0020)
        return True
    except Exception:  # noqa: BLE001 - enfeite nao pode derrubar a janela
        log.debug("nao consegui escurecer a barra de titulo", exc_info=True)
        return False

def aplica(raiz: tk.Misc) -> ttk.Style:
    """Configura o tema escuro na janela inteira."""
    estilo = ttk.Style(raiz)
    estilo.theme_use("clam")

    raiz.configure(background=FUNDO)
    # O `option_add` pega os widgets tk crus (Canvas, Text, Label de amostra de
    # fonte), que nao passam pelo ttk e ficariam brancos.
    raiz.option_add("*Canvas.background", PAINEL)
    raiz.option_add("*Label.background", PAINEL)
    raiz.option_add("*Label.foreground", TEXTO)
    raiz.option_add("*Text.background", CAMPO)
    raiz.option_add("*Text.foreground", TEXTO)

    estilo.configure(".", background=PAINEL, foreground=TEXTO, font=(FONTE_UI, 9))
    estilo.configure("TFrame", background=PAINEL)
    estilo.configure("Fundo.TFrame", background=FUNDO)
    estilo.configure("TLabel", background=PAINEL, foreground=TEXTO)
    estilo.configure("Fundo.TLabel", background=FUNDO, foreground=TEXTO)
    estilo.configure("Apagado.TLabel", background=PAINEL, foreground=APAGADO)

    estilo.configure(
        "Titulo.TLabel", background=FUNDO, foreground=TEXTO, font=(FONTE_UI, 19, "bold")
    )
    estilo.configure("Sub.TLabel", background=FUNDO, foreground=APAGADO)

    # Caixas: fundo um degrau acima do da janela; SEM borda de linha (ver o
    # motivo em `arredonda_labelframe` -- em resumo, o titulo em badge nao
    # cobre a largura inteira do topo, entao a linha de borda do ttk
    # continuava depois dele e aparecia cortada bem ali, um traco solto. O
    # fundo mais claro (PAINEL sobre FUNDO) e os 4 cantos arredondados ja
    # delimitam a caixa sem precisar de contorno.
    estilo.configure(
        "TLabelframe",
        background=PAINEL,
        relief="flat",
        borderwidth=0,
    )
    # Nao ha "TLabelframe.Label" configurado aqui: nenhuma caixa usa mais o
    # `text=` nativo do ttk (ver a nota no topo do arquivo) -- toda caixa
    # nasce por `cria_labelframe`, que desenha o titulo como badge.

    # "background" aqui NAO e o preenchimento visivel do botao -- esse vem da
    # imagem arredondada registrada em `_registra_bordas_arredondadas`. E a
    # cor que o clam pinta como fundo do WIDGET INTEIRO por baixo de qualquer
    # elemento (inclusive do elemento de imagem), e ela aparecia exatamente
    # nos 4 cantos que a imagem deixa transparentes -- um quadrado reto atras
    # do botao "matando" o arredondamento. Sem estado nenhum mapeado: por ser
    # so o que aparece por baixo do canto, tem de ser sempre a cor de quem
    # fica atras do botao (o PAINEL das caixas), nunca a cor de preenchimento.
    estilo.configure(
        "TButton",
        background=PAINEL,
        foreground=TEXTO,
        bordercolor=BORDA_CLARA,
        lightcolor=CAMPO,
        darkcolor=CAMPO,
        focuscolor=LAMINA,
        relief="flat",
        padding=(10, 5),
    )
    estilo.map("TButton", foreground=[("disabled", APAGADO)])

    # O botao principal usa a lamina cheia: numa janela escura, um botao com a
    # mesma cor dos outros nao diz qual e o que comeca o trabalho.
    #
    # "background=FUNDO" pelo mesmo motivo do TButton acima: e o CTA que fica
    # direto sobre `Fundo.TFrame` (a area fora das caixas), entao e FUNDO --
    # nao PAINEL -- que precisa aparecer atras do canto transparente da
    # imagem. Antes disto era LAMINA (a mesma cor do preenchimento): os dois
    # quadrados coincidiam pixel a pixel e o botao saia com cara de reto.
    estilo.configure(
        "Lamina.TButton",
        background=FUNDO,
        foreground="#1a1a1a",
        bordercolor=LAMINA,
        lightcolor=LAMINA,
        darkcolor=LAMINA,
        font=(FONTE_UI, 10, "bold"),
        padding=(16, 7),
    )
    estilo.map(
        "Lamina.TButton",
        foreground=[("disabled", APAGADO)],
    )

    for classe in ("TCheckbutton", "TRadiobutton"):
        estilo.configure(
            classe,
            background=PAINEL,
            foreground=TEXTO,
            indicatorbackground=CAMPO,
            indicatorforeground=LAMINA,
            focuscolor=PAINEL,
            padding=2,
        )
        estilo.map(
            classe,
            background=[("active", PAINEL)],
            foreground=[("disabled", APAGADO)],
            indicatorbackground=[("selected", LAMINA), ("disabled", PAINEL)],
        )

    estilo.configure(
        "TEntry",
        fieldbackground=CAMPO,
        foreground=TEXTO,
        insertcolor=LAMINA,
        bordercolor=BORDA,
        lightcolor=BORDA,
        darkcolor=BORDA,
        padding=5,
    )
    estilo.map("TEntry", bordercolor=[("focus", LAMINA)])

    # "Vertical.TScrollbar" explicito: configurar so "TScrollbar" nao alcanca a
    # barra vertical, que tem estilo proprio -- ela continuava branca no meio da
    # janela escura, e so apareceu quando capturei a janela INTEIRA em vez de um
    # recorte.
    for nome in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        estilo.configure(
            nome,
            background=BORDA,
            troughcolor=FUNDO,
            bordercolor=FUNDO,
            lightcolor=BORDA,
            darkcolor=BORDA,
            arrowcolor=APAGADO,
            relief="flat",
        )
        estilo.map(nome, background=[("active", BORDA_CLARA), ("pressed", LAMINA)])

    # O tema `vista` do Windows ignora `background` no Notebook e devolve abas
    # cinza-claro no meio do fundo escuro. O `clam` obedece -- e ele ja e o tema
    # que o resto da janela usa.
    #
    # borderwidth=1 com a lamina nas tres cores de borda: e a moldura do
    # CORPO do notebook (a area cujo conteudo muda com a aba) -- pedido
    # explicito para marcar em laranja o que e "conteudo que troca" vs as
    # caixas compartilhadas em volta, que ficam com a borda BORDA de sempre.
    #
    # A cor e LAMINA_ESCURA, nao LAMINA: com a cor cheia nas tres bordas
    # (bordercolor/lightcolor/darkcolor) o clam desenha um contorno que
    # compete com o conteudo -- pedido do usuario para deixar sutil sem trocar
    # de familia de cor. LAMINA_ESCURA e a mesma lamina, so mais escura, entao
    # ainda marca "isto e o notebook" sem gritar.
    estilo.configure(
        "TNotebook",
        background=FUNDO,
        borderwidth=1,
        bordercolor=LAMINA_ESCURA,
        lightcolor=LAMINA_ESCURA,
        darkcolor=LAMINA_ESCURA,
        tabmargins=(2, 4, 2, 0),
    )
    estilo.configure(
        "TNotebook.Tab",
        background=PAINEL,
        foreground=APAGADO,
        padding=(18, 8),
        borderwidth=0,
        # `bordercolor`/`lightcolor`/`darkcolor`: o `clam` desenha um bisel na
        # aba com essas tres cores mesmo com `borderwidth=0` na config acima
        # (confirmado consultando o tema puro: vem #9e9a91/#cfcdc8, cinza
        # claro, sobrando como risca clara no topo da aba sobre o fundo
        # escuro). Igualar as tres ao preenchimento da aba faz o bisel virar
        # invisivel -- e o que `arredonda_abas` (abaixo) depende para colar
        # os cantinhos arredondados sem entalhe.
        bordercolor=PAINEL,
        lightcolor=PAINEL,
        darkcolor=PAINEL,
        # Negrito e um pouco maior: os rotulos "Verticais"/"Horizontais" sao
        # a unica navegacao da janela, e no tamanho da fonte de corpo (9)
        # ficavam discretos demais para esse papel.
        font=(FONTE_UI, 10, "bold"),
    )
    estilo.map(
        "TNotebook.Tab",
        background=[("selected", CAMPO)],
        foreground=[("selected", LAMINA)],
        bordercolor=[("selected", CAMPO)],
        lightcolor=[("selected", CAMPO)],
        darkcolor=[("selected", CAMPO)],
        expand=[("selected", (0, 0, 0, 2))],
    )

    return estilo


# --------------------------------------------------------------------------
# cantos arredondados
# --------------------------------------------------------------------------
# O Tk/ttk nao tem `border-radius`. O caminho que funciona e gerar a borda
# como imagem -- ver o aviso no topo do arquivo.
#
# Botao e campo de entrada NAO fatiam a imagem em 9 partes (`border=`) mais.
# Fatiavam ate esta correcao, e ERA A CAUSA do travamento ao digitar/clicar
# (ver `botao_arredondado`/`campo_arredondado` abaixo para a medicao e a
# explicacao): o elemento "image" do Tk 8.6.15 nesta maquina leva ~100-170ms
# para redesenhar quando usa `border=` (fatiamento em 9 partes esticado),
# CONSTANTE a cada redesenho -- nao so na primeira vez, nao proporcional ao
# numero de widgets da janela. Sem `border=` (um blit unico do tamanho
# exato do widget), o mesmo redesenho custa ~0,5-1ms. Por isso agora cada
# botao/campo desenha a imagem JA no seu tamanho final e so a re-desenha
# quando o proprio tamanho muda (`<Configure>`, raro), em vez de deixar o
# Tk esticar uma borda pequena a cada tecla ou clique (constante).

RAIO_CANTO = 10

# Suavizacao: desenha 4x maior e reduz com Lanczos. Sem isso o canto de ~10px
# sai visivelmente serrilhado -- elemento de imagem no Tk nao tem
# antialiasing proprio.
_ESCALA_SUPERAMOSTRA = 4


def _rgba(cor: str) -> tuple[int, int, int, int]:
    """"#rrggbb" -> tupla RGBA opaca, o formato que o Pillow espera."""
    cor = cor.lstrip("#")
    return (int(cor[0:2], 16), int(cor[2:4], 16), int(cor[4:6], 16), 255)


def _fundo_de(widget: tk.Misc) -> str:
    """A cor de quem esta ATRAS do widget, para assar no canto do PNG.

    Existe porque o ttk NAO compoe a transparencia do elemento de imagem: pixel
    vazado no PNG sai BRANCO na tela. Medido -- com a borda branca do hover, o
    canto vazado do botao arredondado virava um QUADRADO branco; no estado
    parado passava despercebido so porque a franja de anti-serrilhado era da
    cor do proprio botao, e o Tk trata QUALQUER alfa nao-zero como opaco.

    Sobe pela arvore ate achar quem declare uma cor: widget classico responde
    `cget("background")`, widget ttk so pela consulta ao estilo. Nao achando
    nada, PAINEL -- e o fundo da maioria das caixas.
    """
    alvo: tk.Misc | None = widget
    estilo = ttk.Style(widget)
    while alvo is not None:
        try:
            cor = str(alvo.cget("background"))
            if cor.startswith("#"):
                return cor
        except tk.TclError:
            pass
        try:
            nome = str(alvo.cget("style")) or alvo.winfo_class()
            cor = str(estilo.lookup(nome, "background"))
            if cor.startswith("#"):
                return cor
        except tk.TclError:
            pass
        alvo = alvo.master
    return PAINEL


def _png_retangulo_arredondado(
    largura: int,
    altura: int,
    cor_fundo: str,
    cor_borda: str | None = None,
    espessura: int = 1,
    raio_topo: bool = True,
    raio_base: bool = True,
    cor_externa: str = PAINEL,
) -> bytes:
    """Bytes PNG de um retângulo do TAMANHO EXATO pedido -- sem fatiamento
    em 9 partes (ver o motivo no cabeçalho da seção). `raio_topo`/`raio_base`
    deixados de fora arredondam só os dois cantos do outro lado -- usado
    pelo fundo do título da caixa (`10px 10px 0 0`, CSS) e pela própria
    caixa (`0 10px 10px 10px`), ver `cria_labelframe`.
    """
    escala = _ESCALA_SUPERAMOSTRA
    largura, altura = max(1, largura), max(1, altura)
    raio = min(RAIO_CANTO, largura // 2, altura // 2)
    grande = (largura * escala, altura * escala)
    img = Image.new("RGBA", grande, (0, 0, 0, 0))
    r = raio * escala
    x1, y1 = grande[0] - 1, grande[1] - 1
    # `rounded_rectangle` do Pillow nao aceita raio por canto -- desenha o
    # retangulo cheio e "quadra" os cantos que devem ficar retos por cima,
    # cobrindo o quarto de circulo daquele lado com um retangulo reto.
    desenho = ImageDraw.Draw(img)
    desenho.rounded_rectangle(
        (0, 0, x1, y1),
        radius=r,
        fill=_rgba(cor_fundo),
        outline=_rgba(cor_borda) if cor_borda else None,
        width=max(1, espessura * escala) if cor_borda else 0,
    )
    if not raio_topo:
        desenho.rectangle((0, 0, x1, r), fill=_rgba(cor_fundo))
    if not raio_base:
        desenho.rectangle((0, y1 - r, x1, y1), fill=_rgba(cor_fundo))
    pequena = img.resize((largura, altura), Image.LANCZOS)
    # SEM canal alfa na saida: o ttk nao compoe a transparencia do elemento de
    # imagem (ver `_fundo_de`), entao o canto tem de sair ja pintado da cor de
    # quem esta atras. De quebra, isto e o que preserva o anti-serrilhado -- o
    # Tk trata qualquer alfa nao-zero como opaco, entao a suavidade so
    # sobrevive se vier resolvida em cor, nao em transparencia.
    fundo = Image.new("RGBA", pequena.size, _rgba(cor_externa))
    fundo.alpha_composite(pequena)
    buf = io.BytesIO()
    fundo.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def botao_arredondado(
    pai: tk.Misc, texto: str, *, lamina: bool = False, **kwargs: object
) -> ttk.Button:
    """`ttk.Button` com cantos arredondados -- substitui `ttk.Button(pai,
    text=texto, style="TButton"|"Lamina.TButton", **kwargs)` em toda a
    janela.

    Antes desta função, a borda arredondada vinha de um elemento "image" do
    ttk fatiado em 9 partes (`border=`), compartilhado por TODOS os botões
    via `estilo.layout("TButton"/"Lamina.TButton", ...)`. Media-se ~165ms
    POR CLIQUE (o par pressed/active dispara dois redesenhos) -- é o
    "delay ao clicar nos CTAs" que o usuário reportou, e a mesma técnica no
    campo de link custava ~388ms por tecla (ver `campo_arredondado`).
    Isolado com um botão de teste na janela real: layout nativo do `clam`
    (sem elemento de imagem) = 0,2-0,4ms por clique; MESMO botão com o
    elemento de imagem SEM `border=` (blit único, sem fatiar) = 0,5-1ms;
    o mesmo elemento COM `border=` = 108-170ms -- variando o tamanho
    mínimo do elemento (`width=`/`height=`) ou removendo o canal alfa da
    imagem não mudou nada, então a causa é o fatiamento em si, não o
    conteúdo da imagem. Widgets fora deste arquivo (Label, Frame) redesenham
    normalmente (~0,3-0,4ms) mesmo nesta mesma janela -- não é "a janela tem
    228 widgets", é especificamente este elemento.

    Cada botão ganha um estilo PRÓPRIO (`Arred{n}.TButton` ou
    `Arred{n}.Lamina.TButton`) para poder ter sua própria imagem de fundo
    no seu próprio tamanho -- o sufixo (`.TButton`/`.Lamina.TButton`) faz o
    ttk herdar cor/fonte/padding/foreground-quando-disabled de "TButton"/
    "Lamina.TButton" (já configurados em `aplica()`), então só o elemento e
    o layout precisam ser definidos aqui.
    """
    estilo = ttk.Style(pai)
    n = _proximo_id()
    nome_estilo = f"Arred{n}.Lamina.TButton" if lamina else f"Arred{n}.TButton"
    nome_elem = f"Arred{n}.Button.border"

    base = LAMINA if lamina else CAMPO
    cores = (
        {"": LAMINA, "disabled": BORDA, "pressed": LAMINA_ESCURA, "active": base}
        if lamina
        else {"": CAMPO, "disabled": PAINEL, "pressed": BORDA, "active": base}
    )
    # Sob o cursor o botao NAO muda de cor: ganha so um contorno branco de 2px.
    # Trocar o preenchimento fazia o botao "piscar" de cor a cada passagem do
    # mouse, o que ficava pesado numa tela com varios botoes; o contorno marca
    # o alvo sem repintar a peca inteira.
    HOVER_BORDA, HOVER_ESPESSURA = "#ffffff", 2
    bordas = {chave: (None, 1) for chave in cores}
    bordas["active"] = (HOVER_BORDA, HOVER_ESPESSURA)
    # 1x1 por enquanto -- o primeiro `<Configure>` (antes do botao aparecer
    # na tela, ver `_redesenha`) substitui pelo tamanho de verdade.
    fundo = _fundo_de(pai)
    imagens = {
        chave: tk.PhotoImage(
            master=pai,
            data=_png_retangulo_arredondado(1, 1, cor, *bordas[chave], cor_externa=fundo),
            format="png",
        )
        for chave, cor in cores.items()
    }

    estilo.element_create(
        nome_elem,
        "image",
        imagens[""],
        *[(chave, imagens[chave]) for chave in ("disabled", "pressed", "active")],
        sticky="nsew",
    )
    estilo.layout(
        nome_estilo,
        [
            (
                nome_elem,
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Button.focus",
                            {
                                "sticky": "nswe",
                                "children": [
                                    (
                                        "Button.padding",
                                        {
                                            "sticky": "nswe",
                                            "children": [("Button.label", {"sticky": "nswe"})],
                                        },
                                    )
                                ],
                            },
                        )
                    ],
                },
            )
        ],
    )

    botao = ttk.Button(pai, text=texto, style=nome_estilo, **kwargs)  # type: ignore[arg-type]
    # As PhotoImage tem de sobreviver ao coletor de lixo do Python -- ver o
    # mesmo aviso em `arredonda_labelframe`.
    botao._imagens_arredondadas = imagens  # type: ignore[attr-defined]

    def _redesenha(evento: tk.Event) -> None:
        if evento.width <= 1 or evento.height <= 1:
            return
        for chave, cor in cores.items():
            imagens[chave].configure(
                data=_png_retangulo_arredondado(
                    evento.width, evento.height, cor, *bordas[chave], cor_externa=fundo
                ),
                format="png",
            )

    botao.bind("<Configure>", _redesenha)
    return botao


def campo_arredondado(pai: tk.Misc, **kwargs: object) -> ttk.Entry:
    """`ttk.Entry` com cantos arredondados -- mesma técnica e mesmo motivo
    de `botao_arredondado` (ver lá a medição completa).

    Este era especificamente o campo que reproduzia o travamento ao
    digitar: ~388ms por tecla (mediana medida, 43 de 43 teclas acima de
    100ms) -- cada tecla muda a variável ligada ao campo, o Tk marca o
    Entry sujo, e o redesenho do elemento "image" fatiado (`border=`) era
    o que consumia esse tempo. Só existe UM campo assim no programa (o
    link do vídeo), então a correção não precisou de um `tk.Entry` cru
    substituindo o `ttk.Entry` -- a causa raiz (o fatiamento em si, ver
    `botao_arredondado`) tinha conserto direto sem trocar de widget.
    """
    estilo = ttk.Style(pai)
    n = _proximo_id()
    nome_estilo = f"Arred{n}.TEntry"
    nome_elem = f"Arred{n}.Entry.field"

    img_normal = tk.PhotoImage(
        master=pai,
        data=_png_retangulo_arredondado(1, 1, CAMPO, BORDA, cor_externa=_fundo_de(pai)),
        format="png",
    )
    img_foco = tk.PhotoImage(
        master=pai,
        data=_png_retangulo_arredondado(1, 1, CAMPO, LAMINA, cor_externa=_fundo_de(pai)),
        format="png",
    )

    estilo.element_create(nome_elem, "image", img_normal, ("focus", img_foco), sticky="nsew")
    estilo.layout(
        nome_estilo,
        [
            (
                nome_elem,
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Entry.padding",
                            {
                                "sticky": "nswe",
                                "children": [("Entry.textarea", {"sticky": "nswe"})],
                            },
                        )
                    ],
                },
            )
        ],
    )

    campo = ttk.Entry(pai, style=nome_estilo, **kwargs)  # type: ignore[arg-type]
    campo._imagens_arredondadas = (img_normal, img_foco)  # type: ignore[attr-defined]

    def _redesenha(evento: tk.Event) -> None:
        if evento.width <= 1 or evento.height <= 1:
            return
        img_normal.configure(
            data=_png_retangulo_arredondado(
                evento.width, evento.height, CAMPO, BORDA, cor_externa=_fundo_de(pai)
            ),
            format="png",
        )
        img_foco.configure(
            data=_png_retangulo_arredondado(
                evento.width, evento.height, CAMPO, LAMINA, cor_externa=_fundo_de(pai)
            ),
            format="png",
        )

    campo.bind("<Configure>", _redesenha)
    return campo


_CONTADOR_ID = 0


def _proximo_id() -> int:
    global _CONTADOR_ID
    _CONTADOR_ID += 1
    return _CONTADOR_ID


def _canto_mascarado(pai: tk.Misc, raio: int, cx: float, cy: float) -> tk.Canvas:
    """Um cantinho `raio` x `raio`: `FUNDO` por fora do arco, `PAINEL`
    dentro -- o pedaço que, sobreposto num canto reto, faz ele parecer
    arredondado.

    Sem contorno: a caixa toda deixou de ter linha de borda (`TLabelframe` em
    `aplica`, motivo lá) -- desenhar um arco com contorno aqui destoaria do
    resto da caixa, que agora não tem nenhum traço, só a mudança de cor entre
    as duas cores.

    `(cx, cy)` é o CENTRO do círculo de referência (raio `raio`) relativo a
    este cantinho: cada canto do retângulo usa um centro diferente para que
    o arco visível dentro do quadradinho seja o quarto de círculo certo --
    ver as quatro chamadas em `arredonda_labelframe` (cantos da caixa, sempre
    PAINEL/FUNDO -- as únicas cores que este canto usa, por isso não são mais
    parâmetro).

    Usada só para os 4 cantos do `Labelframe` -- os cantos da aba do Notebook
    (`arredonda_abas`) precisam de PAINEL/CAMPO por ESTADO e de virar um
    elemento de estilo ttk, não um `tk.Canvas`; usam `_png_canto_aba`, a
    mesma matemática gerando PNG em vez de desenhar num widget.
    """
    c = tk.Canvas(pai, width=raio, height=raio, highlightthickness=0, background=FUNDO)
    c.create_oval(cx - raio, cy - raio, cx + raio, cy + raio, fill=PAINEL, outline="")
    return c


def arredonda_labelframe(
    caixa: ttk.Labelframe,
    raio: int = RAIO_CANTO,
    altura_titulo: int = 0,
    largura_titulo: int = 0,
) -> None:
    """Arredonda os 4 cantos de um `ttk.LabelFrame` mascarando-os com um
    canto desenhado (ver `_canto_mascarado`), NÃO com o mesmo elemento de
    imagem do ttk usado em `_registra_bordas_arredondadas`.

    O motivo de ser diferente: medido nesta máquina, uma caixa que recebe
    filhos aos poucos via `pack()` (todo LabelFrame da janela) dispara um
    recálculo do elemento de imagem A CADA filho novo, e com `border` do
    tamanho do raio (~10px) isso custou ~50-80 ms por widget quando a caixa
    mora dentro do canvas rolável da janela -- com dezenas de widgets numa
    caixa só, abrir a janela passou de segundos para mais de 40s. Um
    mascaramento por `place()` não entra na negociação de geometria do ttk
    (place não redimensiona quem o contém) e custa o mesmo fixo, não importa
    quantos filhos a caixa ganhe depois.

    `altura_titulo` empurra os cantos de cima para baixo da faixa
    reservada ao `labelwidget` (ver `cria_labelframe`) -- sem isso, mascarar
    a partir de y=0 (como os outros dois cantos) deixaria um "buraco"
    arredondado flutuando sobre o título e o retângulo da caixa continuaria
    com as quinas de cima retas, sem eliminar o defeito. Com
    `altura_titulo=0` (padrão), os cantos ficam simétricos -- uso correto
    para uma caixa sem título.

    Só TRÊS cantos são mascarados, não quatro: o canto superior ESQUERDO
    fica reto de propósito (pedido do usuário, `0 10px 10px 10px` em CSS)
    -- é onde o título "encosta" na caixa, e o quadrado reto ali é o que
    faz título e corpo parecerem uma peça só (uma aba presa ao conteúdo,
    não um rótulo flutuando por cima). Os outros três continuam
    arredondados para combinar com o badge do título (`_imagem_badge`, que
    por sua vez só arredonda os dois cantos DE CIMA -- ver lá).

    `largura_titulo`, se maior que zero, também cobre com FUNDO a faixa que
    sobra à DIREITA do badge, na altura dele -- ver o comentário logo abaixo
    (medição pixel a pixel do defeito que isso corrige).

    Chamar logo após criar o `ttk.LabelFrame` (dentro de `cria_labelframe`),
    antes de empacotar filhos dentro dele -- a posição dos cantos é amarrada
    à borda da CAIXA (via `place(in_=caixa, ...)`), não ao conteúdo.
    """
    pai = caixa.master
    # `bordermode="outside"` e OBRIGATORIO: o padrao do `place` ("inside")
    # mede a partir de DENTRO da borda do LabelFrame -- ou seja, depois do
    # padding. Com o padrao, (x=0, y=0) caia perto de onde o PRIMEIRO widget
    # de conteudo comeca (a uns 30px do canto de verdade, nao nos 10x10 do
    # canto), e o mascaramento cortava a letra inicial desse widget em vez
    # do canto reto da caixa.
    opcoes = {"bordermode": "outside"}

    _canto_mascarado(pai, raio, 0, raio).place(
        in_=caixa, relx=1.0, y=altura_titulo, anchor="ne", **opcoes
    )
    _canto_mascarado(pai, raio, raio, 0).place(in_=caixa, x=0, rely=1.0, anchor="sw", **opcoes)
    _canto_mascarado(pai, raio, 0, 0).place(
        in_=caixa, relx=1.0, rely=1.0, anchor="se", **opcoes
    )

    if altura_titulo and largura_titulo:
        # Medido pixel a pixel na janela real (ver o relato do defeito): o
        # `ttk.Labelframe` pinta o preenchimento "TLabelframe" (PAINEL) no
        # retangulo INTEIRO do widget, inclusive por baixo da faixa
        # reservada ao `labelwidget` -- nao so a partir de onde a "borda de
        # verdade" comeca, como o comentario de `altura_titulo` faz parecer.
        # O resultado: ao lado do badge, na mesma altura dele, sobra uma
        # faixa PAINEL identica a cor do resto da caixa -- varredura
        # horizontal na altura do balao saia PAINEL de ponta a ponta, e uma
        # vertical mostrava o PAINEL comecando na MESMA altura debaixo do
        # titulo e ao lado dele. Sem essa faixa distinta, o balao "funde"
        # numa tarja cinza continua em vez de parecer uma aba por cima da
        # caixa. Cobrimos o resto da faixa (do fim do badge ate a quina
        # direita) com FUNDO -- a cor de quem fica atras da caixa -- pelo
        # mesmo truque de sobrepor um widget via `place()` que os cantos já
        # usam acima, então sem custo de negociação de geometria do ttk.
        tk.Frame(pai, background=FUNDO, highlightthickness=0).place(
            in_=caixa,
            x=largura_titulo,
            y=0,
            relwidth=1.0,
            width=-largura_titulo,
            height=altura_titulo,
            anchor="nw",
            **opcoes,
        )


# Havia uma folga de 4px aqui (`_MARGEM_ROTULO`) entre o badge e a quina
# esquerda da caixa -- removida: media-se o balao comecando em x~18 contra a
# caixa comecando em x~13, os dois deveriam ser a MESMA borda para parecerem
# uma peca so (pedido do usuario). O badge agora comeca exatamente em x=0
# relativo a caixa, sem margem nenhuma.
_PAD_ROTULO_X = 10
_PAD_ROTULO_Y = 4


def _imagem_badge(raiz: tk.Misc, largura: int, altura: int, cor_fundo: str) -> tk.PhotoImage:
    """PNG do badge do título, do TAMANHO EXATO do texto -- ver
    `_png_retangulo_arredondado` (mesmo desenho, sem fatiamento em 9 partes,
    pelo mesmo motivo: cada título tem uma largura diferente).

    Só os dois cantos DE CIMA são arredondados (`raio_base=False`) -- pedido
    do usuário para o título parecer uma aba presa à caixa (`arredonda_labelframe`
    deixa o canto superior esquerdo DA CAIXA reto pelo mesmo motivo, os dois
    lados do mesmo encontro). Antes, o badge era uma cápsula arredondada nos
    4 cantos.
    """
    return tk.PhotoImage(
        master=raiz,
        data=_png_retangulo_arredondado(
            largura, altura, cor_fundo, raio_base=False, cor_externa=_fundo_de(raiz)
        ),
        format="png",
    )


def _rotulo_labelframe(pai: tk.Misc, texto: str) -> tk.Canvas:
    """Título da caixa: um badge arredondado do tamanho do texto -- não o
    retângulo esticado até a outra lateral que o `text=` nativo do
    `ttk.Labelframe` produz (ver o motivo em `arredonda_labelframe`).

    Um `tk.Canvas` com uma imagem colada, no mesmo estilo dos outros ícones
    do tema: é um título ESTÁTICO (sem estado de hover/pressed), então não
    precisa da máquina de elemento ttk que botão e campo usam.

    Sem margem à esquerda (ver o comentário de `_PAD_ROTULO_X` acima): o
    canvas tem exatamente a largura do badge, que o `ttk.Labelframe` posiciona
    encostado na quina esquerda de verdade da caixa -- é isso que faz balão e
    caixa alinharem na mesma borda.
    """
    fonte = tkfont.Font(pai, _FONTE_ROTULO_LABELFRAME)
    largura_badge = fonte.measure(texto) + 2 * _PAD_ROTULO_X
    altura_badge = fonte.metrics("linespace") + 2 * _PAD_ROTULO_Y

    c = tk.Canvas(
        pai,
        width=largura_badge,
        height=altura_badge,
        highlightthickness=0,
        # FUNDO, nao PAINEL: e a cor de quem fica atras da caixa (todo
        # LabelFrame da janela mora sobre um frame FUNDO). Sem margem
        # sobrando, esta cor de fundo do canvas nem aparece mais -- fica so
        # atras da imagem do badge, que cobre o canvas inteiro -- mas
        # continua FUNDO por ser o correto caso o texto mude e o badge fique
        # com bordas nao inteiras apos o resize do <Configure>.
        background=FUNDO,
    )
    # Pergunta a cor ao PAI do Labelframe, nao a ele mesmo: `_fundo_de` nao
    # acha `background` num widget ttk, cai na consulta ao estilo
    # `TLabelframe` e devolveria a cor do INTERIOR da caixa. O canto do badge
    # sairia recortado em PAINEL sobre um fundo FUNDO -- um retangulo, que foi
    # o defeito. Medido: o pixel do canto vinha #13141d onde devia ser #080912.
    imagem = _imagem_badge(pai.master or pai, largura_badge, altura_badge, PAINEL)
    c._imagem_viva = imagem  # type: ignore[attr-defined] # sem isto o GC apaga a imagem
    c.create_image(0, 0, image=imagem, anchor="nw")
    c.create_text(
        largura_badge / 2,
        altura_badge / 2,
        text=texto,
        fill=LAMINA,
        font=_FONTE_ROTULO_LABELFRAME,
    )
    return c


def cria_labelframe(pai: tk.Misc, texto: str, **kwargs: object) -> ttk.Labelframe:
    """`ttk.Labelframe` com título em badge e os 4 cantos arredondados --
    substitui `ttk.LabelFrame(pai, text=..., **kwargs)` em toda a janela,
    para nenhuma caixa nova esquecer um dos dois (ver `_rotulo_labelframe` e
    `arredonda_labelframe`).
    """
    caixa = ttk.Labelframe(pai, **kwargs)  # type: ignore[arg-type]
    rotulo = _rotulo_labelframe(caixa, texto)
    caixa.configure(labelwidget=rotulo)
    # Altura e largura do badge (nao estimativas por metrica de fonte) sao o
    # tamanho EXATO da faixa que o ttk reserva para o labelwidget -- servem
    # tanto para os cantos (`altura_titulo`) quanto para a faixa cinza que
    # sobra ao lado do badge (`largura_titulo`, ver `arredonda_labelframe`).
    arredonda_labelframe(
        caixa,
        altura_titulo=rotulo.winfo_reqheight(),
        largura_titulo=rotulo.winfo_reqwidth(),
    )
    return caixa
