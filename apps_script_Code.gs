// Recebe a assinatura/foto enviada pelo app e salva na pasta do Drive.
// O arquivo fica no espaço da conta que publicou este script (a da empresa).

const TOKEN = 'TROQUE_POR_UMA_SENHA_LONGA_SUA';   // a mesma vai nos Secrets do Streamlit
const PASTA_ID = '1jTpq6aZQmizpqCLBGzDpTtjcWs5uvAh2'; // "LRS Canhoto Digital - Anexos"

function doPost(e) {
  try {
    const dados = JSON.parse(e.postData.contents);
    if (dados.token !== TOKEN) {
      return resposta({ ok: false, erro: 'não autorizado' });
    }
    const bytes = Utilities.base64Decode(dados.conteudo_base64);
    const blob = Utilities.newBlob(bytes, dados.mime, dados.nome);
    const arquivo = DriveApp.getFolderById(PASTA_ID).createFile(blob);
    return resposta({ ok: true, link: arquivo.getUrl() });
  } catch (erro) {
    return resposta({ ok: false, erro: String(erro) });
  }
}

function resposta(objeto) {
  return ContentService.createTextOutput(JSON.stringify(objeto))
    .setMimeType(ContentService.MimeType.JSON);
}
