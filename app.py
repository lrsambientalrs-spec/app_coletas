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
from collections.abc import Mapping
from datetime import datetime, timezone, timedelta

import streamlit as st
import streamlit.components.v1 as components
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from streamlit_drawable_canvas import st_canvas
from fpdf import FPDF
from PIL import Image, ImageOps

# ----------------------------------------------------------------------
# Configuração fixa
# ----------------------------------------------------------------------
SHEET_ID = "1skzK1B-OkNG7uyTku2904dthtn9Wc0RllMSHJTYgCQY"
DRIVE_PASTA_ANEXOS = "1jTpq6aZQmizpqCLBGzDpTtjcWs5uvAh2"  # "LRS Canhoto Digital - Anexos"
FUSO = timezone(timedelta(hours=-3))  # América/Sao_Paulo (sem horário de verão)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

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
def obter_clientes_google():
    info = extrair_credenciais(st.secrets)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    drive = build("drive", "v3", credentials=creds)
    return gc, drive


def planilha():
    gc, _ = obter_clientes_google()
    return gc.open_by_key(SHEET_ID)


@st.cache_data(ttl=120, show_spinner=False)
def carregar_clientes():
    """Lê a aba LISTA (cabeçalho na linha 2) e devolve lista de dicts."""
    aba = planilha().worksheet("LISTA")
    registros = aba.get_all_records(head=2)
    # mantém só quem tem código preenchido
    return [r for r in registros if str(r.get("CÓDIGO", "")).strip()]


def proximo_id_coleta():
    """Calcula o próximo ID da BD_COLETAS, cacheando em sessão pra não
    reler milhares de linhas a cada coleta."""
    if "proximo_id" not in st.session_state:
        aba = planilha().worksheet("BD_COLETAS")
        coluna = aba.col_values(1)[2:]  # pula as 2 linhas de cabeçalho
        maior = 0
        for v in coluna:
            try:
                n = int(str(v).strip())
                if n > maior:
                    maior = n
            except ValueError:
                pass
        st.session_state["proximo_id"] = maior + 1
    valor = st.session_state["proximo_id"]
    st.session_state["proximo_id"] += 1
    return valor


def subir_para_drive(nome_arquivo, conteudo_bytes, mime_type):
    _, drive = obter_clientes_google()
    media = MediaIoBaseUpload(io.BytesIO(conteudo_bytes), mimetype=mime_type, resumable=False)
    arquivo = drive.files().create(
        body={"name": nome_arquivo, "parents": [DRIVE_PASTA_ANEXOS]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return arquivo.get("webViewLink", "")


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
    if r["pdf"]:
        st.download_button(
            "Baixar PDF do comprovante",
            data=r["pdf"],
            file_name=f"canhoto_{r['dados']['id']}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.warning("A coleta foi gravada, mas não foi possível gerar o PDF do comprovante.")
    if st.button("Nova coleta", use_container_width=True):
        st.session_state["ultimo_recibo"] = None
        st.session_state["cliente_selecionado"] = None
        st.session_state["hora_abertura"] = None
        st.session_state["foto_bytes"] = None
        st.session_state["foto_mime"] = None
        st.rerun()
    st.stop()

# ----------------------------------------------------------------------
# Seleção de cliente
# ----------------------------------------------------------------------
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
        gravou = False
        with st.spinner("Salvando na planilha..."):
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

                prefixo = f"canhoto_{canhoto}_cod{cliente['CÓDIGO']}_"
                link_assinatura = subir_para_drive(prefixo + "assinatura.png", assinatura_bytes, "image/png")

                foto_bytes = st.session_state.get("foto_bytes")
                link_foto = ""
                if foto_bytes:
                    mime_foto = st.session_state.get("foto_mime") or "image/jpeg"
                    extensao = "png" if mime_foto == "image/png" else "jpg"
                    link_foto = subir_para_drive(prefixo + "foto." + extensao, foto_bytes, mime_foto)

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
                    link_assinatura,
                    link_foto,
                ]
                # RAW: grava o texto exatamente como veio (sem interpretar fórmulas),
                # já que o app é público e o campo de observações é livre.
                planilha().worksheet("BD_COLETAS").append_row(nova_linha, value_input_option="RAW")
                gravou = True

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
                    pdf_bytes = None  # a coleta já está gravada; só o PDF não saiu

                st.session_state["ultimo_recibo"] = {"dados": dados_recibo, "pdf": pdf_bytes}
                st.rerun()
            except Exception as e:
                if not gravou and canhoto is not None:
                    st.session_state["proximo_id"] = canhoto  # devolve o número, não houve gravação
                st.error(f"Não foi possível gravar na planilha agora. Detalhe: {e}")
