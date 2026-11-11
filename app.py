"""
LRS Ambiental — Canhoto Digital (Streamlit)
App público (sem login) para o coletor registrar coletas no celular.
Lê os clientes da aba LISTA e grava cada coleta na aba BD_COLETAS da
planilha "Controle total" no Google Sheets, usando uma Conta de Serviço
do Google (as credenciais ficam só no servidor, nunca no navegador do
coletor). Assinatura e foto sobem para uma pasta no Google Drive.

Configuração necessária (uma vez só): ver README.md
"""
import base64
import io
import json
import re
import unicodedata
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components
import gspread
import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from streamlit_drawable_canvas import st_canvas
from fpdf import FPDF
from PIL import Image, ImageOps

# ----------------------------------------------------------------------
# Configuração fixa
# ----------------------------------------------------------------------
SHEET_ID = "1skzK1B-OkNG7uyTku2904dthtn9Wc0RllMSHJTYgCQY"
FUSO = timezone(timedelta(hours=-3))  # América/Sao_Paulo (sem horário de verão)

# A conta de serviço lê/grava a planilha e lê a agenda de coletas. Assinatura,
# foto e PDF vão para o Drive por um Apps Script rodando na conta da empresa
# (ver apps_script_Code.gs).
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar.readonly",
]
AGENDA_CLIENTES_ID = "lrsambientalrs@gmail.com"  # agenda "LRS CLIENTES"
COLUNA_LINK_PDF = 12  # coluna L da BD_COLETAS

st.set_page_config(
    page_title="LRS Canhoto Digital",
    page_icon="♻️",
    layout="centered",
)

# Evita o erro "removeChild" causado pela tradução automática do navegador
# (o Streamlit declara a página como inglês; o Chrome tenta traduzir e
# quebra a árvore do React). Declaramos a página como pt-BR e sem tradução.
components.html("""
<script>
  try {
    const doc = window.parent.document;
    doc.documentElement.setAttribute('lang', 'pt-BR');
    doc.documentElement.setAttribute('translate', 'no');
    doc.documentElement.classList.add('notranslate');
    doc.body.setAttribute('translate', 'no');
    doc.body.classList.add('notranslate');
    if (!doc.querySelector('meta[name="google"][content="notranslate"]')) {
      const meta = doc.createElement('meta');
      meta.name = 'google';
      meta.content = 'notranslate';
      doc.head.appendChild(meta);
    }
  } catch (e) {}
</script>
""", height=0)

# ----------------------------------------------------------------------
# Visual — mesma identidade do protótipo anterior (verde, moderno, limpo)
# ----------------------------------------------------------------------
st.markdown("""
<style>
:root{
  --verde:#0E8A57; --verde-forte:#0B6B44; --verde-suave:#E1F3EA;
}
.stApp{background:#F4F6F5;}
.cabecalho-lrs{
  background:linear-gradient(160deg,#0B6B44,#0E8A57);
  color:#fff;padding:20px 22px;border-radius:0 0 22px 22px;margin:-1rem -1rem 1.2rem -1rem;
}
.cabecalho-lrs h1{font-size:20px;margin:0;font-weight:800}
.cabecalho-lrs p{font-size:12.5px;margin:2px 0 0;opacity:.9}
div[data-testid="stForm"]{background:#fff;border-radius:16px;padding:18px;box-shadow:0 1px 2px rgba(16,24,20,.05),0 8px 20px rgba(16,24,20,.06);}
.cartao-cliente{background:var(--verde-suave);border-radius:14px;padding:14px;margin:8px 0 4px}
.cartao-cliente, .cartao-cliente b{color:#151B18}
.cartao-cliente b{font-size:15px}
.stButton>button, .stDownloadButton>button{
  border-radius:12px;font-weight:700;padding:.6rem 1rem;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="cabecalho-lrs">
  <h1>LRS Ambiental</h1>
  <p>Canhoto digital · comprovante de coleta</p>
</div>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------------
# Conexão com Google (Conta de Serviço)
# ----------------------------------------------------------------------
def extrair_credenciais(segredos):
    """Acha as credenciais do Google nos Secrets, aceitando vários jeitos de colar."""
    def parece_credencial(d):
        return "private_key" in d and "client_email" in d

    # 1) formato esperado: gcp_service_account (tabela ou JSON colado inteiro)
    if "gcp_service_account" in segredos:
        bruto = segredos["gcp_service_account"]
        if isinstance(bruto, str):
            return json.loads(bruto, strict=False)
        return dict(bruto)

    # 2) campos colados soltos, sem o cabeçalho [gcp_service_account]
    if parece_credencial(segredos):
        return {k: segredos[k] for k in segredos.keys()}

    # 3) qualquer entrada que contenha as credenciais (nome diferente)
    for chave in segredos.keys():
        valor = segredos[chave]
        if isinstance(valor, Mapping) and parece_credencial(valor):
            return dict(valor)
        if isinstance(valor, str) and "private_key" in valor:
            try:
                return json.loads(valor, strict=False)
            except ValueError:
                pass

    raise KeyError(
        "Não encontrei as credenciais do Google nos Secrets. "
        "Nomes encontrados: " + (", ".join(segredos.keys()) or "nenhum")
    )


@st.cache_resource
def obter_credenciais():
    info = extrair_credenciais(st.secrets)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


@st.cache_resource
def obter_clientes_google():
    return gspread.authorize(obter_credenciais())


def planilha():
    gc = obter_clientes_google()
    return gc.open_by_key(SHEET_ID)


@st.cache_data(ttl=120, show_spinner=False)
def carregar_clientes():
    """Lê a aba LISTA (cabeçalho na linha 2) e devolve lista de dicts."""
    aba = planilha().worksheet("LISTA")
    registros = aba.get_all_records(head=2)
    # mantém só quem tem código preenchido
    return [r for r in registros if str(r.get("CÓDIGO", "")).strip()]


def proximo_id_coleta():
    """Lê o maior ID da BD_COLETAS na hora de gravar (assim vários coletores,
    ou o sistema do escritório, não repetem número)."""
    aba = planilha().worksheet("BD_COLETAS")
    ids = [int(str(v).strip()) for v in aba.col_values(1)[2:] if str(v).strip().isdigit()]
    return (max(ids) if ids else 0) + 1


def configuracao_drive():
    try:
        return st.secrets.get("apps_script_url"), st.secrets.get("apps_script_token")
    except Exception:
        return None, None


def subir_para_drive(url, token, nome_arquivo, conteudo_bytes, mime_type, subpasta=None):
    """Envia o arquivo para um Apps Script da conta da empresa, que salva na
    pasta do Drive (o arquivo fica no espaço da conta, não da conta robô)."""
    if not url or not token:
        raise RuntimeError("envio para o Drive ainda não configurado nos Secrets")
    resposta = requests.post(
        url,
        json={
            "token": token,
            "nome": nome_arquivo,
            "mime": mime_type,
            "subpasta": subpasta,
            "conteudo_base64": base64.b64encode(conteudo_bytes).decode("ascii"),
        },
        timeout=90,
    )
    try:
        dados = resposta.json()
    except ValueError:
        raise RuntimeError(
            "o Apps Script não respondeu como esperado (confira se a implantação "
            "está com acesso 'Qualquer pessoa')"
        )
    if not dados.get("ok"):
        raise RuntimeError(dados.get("erro") or "o Apps Script recusou o envio")
    return dados.get("link", "")


ROTULOS_ANEXO = {"assinatura": "A assinatura", "foto": "A foto", "pdf": "O PDF do canhoto"}


def enviar_anexos(itens):
    """itens: lista de (chave, nome, bytes, mime, subpasta). Envia em paralelo e
    devolve ({chave: link}, [avisos])."""
    url, token = configuracao_drive()
    links, avisos = {}, []

    def tarefa(item):
        chave, nome, conteudo, mime, subpasta = item
        return subir_para_drive(url, token, nome, conteudo, mime, subpasta)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futuros = [(item, pool.submit(tarefa, item)) for item in itens]
        for item, futuro in futuros:
            try:
                links[item[0]] = futuro.result()
            except Exception as e:
                avisos.append(f"{ROTULOS_ANEXO.get(item[0], item[0])} não subiu para o Drive ({e}).")
    return links, avisos


def nome_arquivo_seguro(texto, limite=30):
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t[:limite] or "cliente"


def garantir_coluna_link_pdf(aba):
    """Cria a coluna L (LINK CANHOTO) na BD_COLETAS, uma única vez."""
    if st.session_state.get("coluna_pdf_ok"):
        return
    if aba.col_count < COLUNA_LINK_PDF:
        aba.add_cols(COLUNA_LINK_PDF - aba.col_count)
    if not str(aba.cell(2, COLUNA_LINK_PDF).value or "").strip():
        aba.update_cell(2, COLUNA_LINK_PDF, "LINK CANHOTO (PDF)")
    st.session_state["coluna_pdf_ok"] = True


def normalizar_codigo(valor):
    return str(valor).strip().lstrip("0") or "0"


def extrair_codigo_evento(titulo):
    """'153 - NOME', '067 - NOME', '4-NOME'... -> ('153', 'NOME')."""
    m = re.match(r"^\s*0*(\d+)\s*[-\u2013\u2014]\s*(.+)$", str(titulo or ""))
    return (m.group(1), m.group(2).strip()) if m else (None, str(titulo or "").strip())


@st.cache_data(ttl=60, show_spinner=False)
def carregar_agenda_do_dia(data_iso, agenda_id):
    """Eventos da agenda de coletas no dia (formato AAAA-MM-DD)."""
    dia_seguinte = (datetime.strptime(data_iso, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    sessao = AuthorizedSession(obter_credenciais())
    resposta = sessao.get(
        f"https://www.googleapis.com/calendar/v3/calendars/{quote(agenda_id, safe='')}/events",
        params={
            "timeMin": f"{data_iso}T00:00:00-03:00",
            "timeMax": f"{dia_seguinte}T00:00:00-03:00",
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
            "timeZone": "America/Sao_Paulo",
        },
        timeout=30,
    )
    resposta.raise_for_status()
    eventos = []
    for ev in resposta.json().get("items", []):
        if ev.get("status") == "cancelled":
            continue
        inicio = ev.get("start", {})
        hora = inicio.get("dateTime", "")[11:16] if inicio.get("dateTime") else ""
        codigo, nome_agenda = extrair_codigo_evento(ev.get("summary"))
        eventos.append({"hora": hora, "codigo": codigo, "titulo": nome_agenda})
    return eventos


def escolher_pela_agenda(codigo):
    st.session_state["cliente_agenda"] = normalizar_codigo(codigo)


def agora():
    return datetime.now(FUSO)


def formatar_data(dt):
    return dt.strftime("%d/%m/%Y")


def formatar_data_hora(dt):
    return dt.strftime("%d/%m/%Y %H:%M")


# ----------------------------------------------------------------------
# PDF do comprovante
# ----------------------------------------------------------------------
_TROCAS = str.maketrans({
    "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
})


def texto_pdf(valor):
    """A fonte padrão do PDF só aceita Latin-1: troca traços/aspas
    especiais e substitui o que não couber (ex.: emojis) por '?'."""
    texto = str(valor if valor is not None else "").translate(_TROCAS)
    return texto.encode("latin-1", "replace").decode("latin-1")


def preparar_foto(conteudo):
    """Corrige a rotação da foto (EXIF), reduz o tamanho e converte para JPEG,
    para o upload no Drive e o PDF ficarem leves."""
    img = Image.open(io.BytesIO(conteudo))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1600, 1600))
    saida = io.BytesIO()
    img.save(saida, format="JPEG", quality=82)
    return saida.getvalue()


def gerar_pdf(dados, assinatura_png_bytes, foto_bytes=None):
    pdf = FPDF(unit="mm", format="A5")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, "LRS Ambiental Ltda.", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, "Comprovante de coleta", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, f"Canhoto n. {dados['id']}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 10)
    linhas = [
        ("Gerador", dados["cliente"]),
        ("CNPJ/CPF", dados["cnpj"]),
        ("Volume", f"{dados['volume']} L"),
        ("Grupo", dados["grupo"] or "-"),
        ("Data/hora", dados["data_hora"]),
        ("Observações", dados["obs"] or "-"),
    ]
    for rotulo, valor in linhas:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(32, 6, texto_pdf(f"{rotulo}:"))
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, texto_pdf(valor), new_x="LMARGIN", new_y="NEXT")

    if foto_bytes:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Foto registrada:", new_x="LMARGIN", new_y="NEXT")
        pdf.image(io.BytesIO(foto_bytes), w=55)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Assinatura do gerador:", new_x="LMARGIN", new_y="NEXT")
    pdf.image(io.BytesIO(assinatura_png_bytes), w=55)

    return bytes(pdf.output())


# ----------------------------------------------------------------------
# Estado da sessão
# ----------------------------------------------------------------------
if "cliente_selecionado" not in st.session_state:
    st.session_state["cliente_selecionado"] = None
if "hora_abertura" not in st.session_state:
    st.session_state["hora_abertura"] = None
if "ultimo_recibo" not in st.session_state:
    st.session_state["ultimo_recibo"] = None

try:
    with st.spinner("Carregando clientes..."):
        clientes = carregar_clientes()
except Exception as e:
    st.error(f"Não foi possível carregar os clientes da planilha agora. Detalhe: {e}")
    st.stop()

# ----------------------------------------------------------------------
# Recibo da última coleta (se acabou de finalizar)
# ----------------------------------------------------------------------
if st.session_state["ultimo_recibo"]:
    r = st.session_state["ultimo_recibo"]
    st.success(f"Coleta #{r['dados']['id']} gravada na planilha.")
    st.markdown(f"""
    <div class="cartao-cliente">
      <b>Canhoto nº {r['dados']['id']}</b><br>
      {r['dados']['cliente']}<br>
      {r['dados']['volume']} L · Grupo {r['dados']['grupo'] or '-'}<br>
      {r['dados']['data_hora']}
    </div>
    """, unsafe_allow_html=True)
    if r.get("pdf_salvo"):
        st.info("O canhoto (PDF) foi salvo na pasta do Drive.")
    for aviso in r.get("avisos", []):
        st.warning(aviso)
    if r.get("pdf") and not r.get("pdf_salvo"):
        # só oferece o download quando o PDF não foi para o Drive
        st.download_button(
            "Baixar PDF do comprovante",
            data=r["pdf"],
            file_name=f"canhoto_{r['dados']['id']}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    if st.button("Nova coleta", use_container_width=True):
        st.session_state["ultimo_recibo"] = None
        st.session_state["cliente_selecionado"] = None
        st.session_state["hora_abertura"] = None
        st.session_state["foto_bytes"] = None
        st.session_state["foto_mime"] = None
        st.session_state["cliente_agenda"] = None
        st.rerun()
    st.stop()

# ----------------------------------------------------------------------
# Seleção de cliente
# ----------------------------------------------------------------------
def achar_cliente(codigo):
    if not codigo:
        return None
    return next((c for c in clientes if normalizar_codigo(c.get("CÓDIGO")) == normalizar_codigo(codigo)), None)


cliente = achar_cliente(st.session_state.get("cliente_agenda"))
if cliente is None:
    st.session_state["cliente_agenda"] = None

    # ---- Coletas previstas para hoje (Google Agenda) ----
    st.subheader("Coletas previstas para hoje")
    try:
        try:
            agenda_id = st.secrets.get("agenda_id") or AGENDA_CLIENTES_ID
        except Exception:
            agenda_id = AGENDA_CLIENTES_ID
        eventos = carregar_agenda_do_dia(agora().strftime("%Y-%m-%d"), agenda_id)
        previstos, sem_cadastro = [], []
        for ev in eventos:
            cli = achar_cliente(ev["codigo"]) if ev["codigo"] else None
            (previstos if cli else sem_cadastro).append((ev, cli))
        if not eventos:
            st.caption("Nenhuma coleta agendada para hoje na agenda.")
        for i, (ev, cli) in enumerate(previstos):
            rotulo = f'{cli["CÓDIGO"]} — {cli["NOME PJ"]}'
            if ev["hora"]:
                rotulo = f'{ev["hora"]}  ·  {rotulo}'
            st.button(
                rotulo, key=f"agenda_{i}_{cli['CÓDIGO']}", use_container_width=True,
                on_click=escolher_pela_agenda, args=(cli["CÓDIGO"],),
            )
        for ev, _ in sem_cadastro:
            st.caption(f'Na agenda, sem cadastro no sistema: {ev["titulo"]}')
    except Exception:
        st.caption(
            "Não consegui ler a agenda de hoje. Se for a primeira vez, confira se a agenda "
            "'LRS CLIENTES' foi compartilhada com a conta de serviço."
        )

    st.subheader("Cliente")
    termo = st.text_input("Buscar por nome ou código", placeholder="Nome ou código...")

    incluir_inativos = st.checkbox("Incluir clientes inativos", value=False)
    filtrados = clientes
    if not incluir_inativos:
        filtrados = [
            c for c in clientes
            if str(c.get("STATUS", "")).strip().upper() == "ATIVO"
        ]
    if termo:
        termo_up = termo.strip().upper()
        filtrados = [
            c for c in filtrados
            if termo_up in str(c.get("CÓDIGO", "")).upper()
            or termo_up in str(c.get("NOME PJ", "")).upper()
            or termo_up in str(c.get("NOME FANTASIA", "")).upper()
        ]

    opcoes = {f'{c["CÓDIGO"]} — {c["NOME PJ"]}': c for c in filtrados}
    escolha = st.selectbox(
        "Selecione o cliente",
        options=["— selecione —"] + list(opcoes.keys()),
    )
    cliente = opcoes.get(escolha)
else:
    st.subheader("Cliente")
    if st.button("Escolher outro cliente"):
        st.session_state["cliente_agenda"] = None
        st.session_state["cliente_selecionado"] = None
        st.session_state["hora_abertura"] = None
        st.rerun()

if cliente and st.session_state["cliente_selecionado"] != cliente.get("CÓDIGO"):
    st.session_state["cliente_selecionado"] = cliente.get("CÓDIGO")
    st.session_state["hora_abertura"] = agora()

if cliente:
    endereco = ", ".join(
        str(x) for x in [cliente.get("LOGRADOURO"), cliente.get("Nº"), cliente.get("BAIRRO")] if x
    )
    st.markdown(f"""
    <div class="cartao-cliente">
      <b>{cliente.get("NOME PJ","")}</b><br>
      {cliente.get("CNPJ/CPF","")}{" · "+endereco if endereco else ""}<br>
      <span style="font-size:12.5px;color:#0B6B44">
        Plano {cliente.get("FREQUÊNCIA","-")} · Limite {cliente.get("LITRAGEM MÊS","-")} L/mês · Grupo {cliente.get("GRUPO RESÍDUO","-")}
      </span>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# ----------------------------------------------------------------------
# Formulário da coleta
# ----------------------------------------------------------------------
st.subheader("Dados da coleta")
col1, col2 = st.columns(2)
with col1:
    volume = st.number_input("Volume coletado (L)", min_value=0.0, step=1.0, format="%.1f")
with col2:
    grupo = st.selectbox("Grupo de resíduo", ["", "A", "B", "E", "A-E", "A-B", "A-B-E", "Outros"])

obs = st.text_area("Observações", placeholder="Ex.: coleta normal, estabelecimento fechado, excedente, irregularidade...")

st.subheader("Foto (opcional)")
if st.session_state.get("foto_bytes"):
    st.image(st.session_state["foto_bytes"], width=220)
    if st.button("Remover foto"):
        st.session_state["foto_bytes"] = None
        st.session_state["foto_mime"] = None
        st.rerun()
else:
    modo_foto = st.radio(
        "Como registrar a foto?",
        ["Sem foto", "Tirar foto agora", "Escolher da galeria"],
        horizontal=True,
        label_visibility="collapsed",
    )
    # A câmera só é criada quando escolhida; ao tirar a foto guardamos o
    # arquivo e recarregamos, o que desliga a câmera.
    if modo_foto == "Tirar foto agora":
        foto_camera = st.camera_input("Tirar foto", label_visibility="collapsed")
        if foto_camera is not None:
            try:
                st.session_state["foto_bytes"] = preparar_foto(foto_camera.getvalue())
                st.session_state["foto_mime"] = "image/jpeg"
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível usar essa foto. Detalhe: {e}")
    elif modo_foto == "Escolher da galeria":
        foto_arquivo = st.file_uploader(
            "Escolher da galeria", type=["jpg", "jpeg", "png"], label_visibility="collapsed"
        )
        if foto_arquivo is not None:
            try:
                st.session_state["foto_bytes"] = preparar_foto(foto_arquivo.getvalue())
                st.session_state["foto_mime"] = "image/jpeg"
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível usar essa foto. Detalhe: {e}")

st.subheader("Assinatura do gerador")
st.caption("Assine com o dedo dentro do quadro.")
tela_assinatura = st_canvas(
    stroke_width=3,
    stroke_color="#000000",
    background_color="#FFFFFF",
    height=150,
    width=360,
    drawing_mode="freedraw",
    key="assinatura_canvas",
)

finalizar = st.button("Finalizar coleta", type="primary", use_container_width=True)

if finalizar:
    if not cliente:
        st.error("Selecione um cliente.")
    elif not volume:
        st.error("Informe o volume coletado.")
    elif tela_assinatura.image_data is None:
        st.error("Colete a assinatura do gerador.")
    else:
        canhoto = None
        with st.spinner("Salvando a coleta..."):
            try:
                agora_dt = agora()
                canhoto = proximo_id_coleta()

                # assinatura: canvas (RGBA numpy) -> PNG
                img_assinatura = Image.fromarray(tela_assinatura.image_data.astype("uint8"), "RGBA")
                fundo = Image.new("RGBA", img_assinatura.size, (255, 255, 255, 255))
                img_assinatura = Image.alpha_composite(fundo, img_assinatura).convert("RGB")
                buffer_assinatura = io.BytesIO()
                img_assinatura.save(buffer_assinatura, format="PNG")
                assinatura_bytes = buffer_assinatura.getvalue()
                foto_bytes = st.session_state.get("foto_bytes")

                dados_recibo = {
                    "id": canhoto,
                    "cliente": cliente["NOME PJ"],
                    "cnpj": cliente.get("CNPJ/CPF", ""),
                    "volume": volume,
                    "grupo": grupo,
                    "obs": obs,
                    "data_hora": formatar_data_hora(agora_dt),
                }
                try:
                    pdf_bytes = gerar_pdf(dados_recibo, assinatura_bytes, foto_bytes)
                except Exception:
                    pdf_bytes = None

                # assinatura, foto e PDF do canhoto vão para o Drive (em paralelo)
                prefixo = f"canhoto_{canhoto}_cod{cliente['CÓDIGO']}_"
                nome_pdf = f"canhoto_{canhoto}_{nome_arquivo_seguro(cliente.get('NOME FANTASIA') or cliente['NOME PJ'])}.pdf"
                itens = [("assinatura", prefixo + "assinatura.png", assinatura_bytes, "image/png", None)]
                if foto_bytes:
                    itens.append(("foto", prefixo + "foto.jpg", foto_bytes, "image/jpeg", None))
                if pdf_bytes:
                    itens.append(("pdf", nome_pdf, pdf_bytes, "application/pdf", "Canhotos"))
                links, avisos = enviar_anexos(itens)

                hora_abertura = st.session_state["hora_abertura"] or agora_dt
                nova_linha = [
                    canhoto,
                    formatar_data(agora_dt),
                    cliente["CÓDIGO"],
                    cliente["NOME PJ"],
                    volume,
                    grupo,
                    obs,
                    formatar_data_hora(hora_abertura),
                    formatar_data_hora(agora_dt),
                    links.get("assinatura", ""),
                    links.get("foto", ""),
                    links.get("pdf", ""),
                ]
                aba_coletas = planilha().worksheet("BD_COLETAS")
                garantir_coluna_link_pdf(aba_coletas)
                # RAW: grava o texto exatamente como veio (sem interpretar fórmulas),
                # já que o app é público e o campo de observações é livre.
                aba_coletas.append_row(nova_linha, value_input_option="RAW")

                if pdf_bytes is None:
                    avisos.append("Não foi possível gerar o PDF do canhoto.")
                st.session_state["ultimo_recibo"] = {
                    "dados": dados_recibo,
                    "pdf": pdf_bytes,
                    "pdf_salvo": bool(links.get("pdf")),
                    "avisos": avisos,
                }
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível gravar na planilha agora. Detalhe: {e}")
