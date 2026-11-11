// Recebe a assinatura, a foto e o PDF do canhoto enviados pelo app e salva
// no Drive. O arquivo fica no espaço da conta que publicou este script (a da
// empresa). O PDF do canhoto vai para a subpasta "Canhotos" (criada sozinha).

const TOKEN = 'Amb13ntal2026#';
const PASTA_ID = '1jTpq6aZQmizpqCLBGzDpTtjcWs5uvAh2'; // "LRS Canhoto Digital - Anexos"

function doPost(e) {
  try {
    const dados = JSON.parse(e.postData.contents);
    if (dados.token !== TOKEN) {
      return resposta({ ok: false, erro: 'não autorizado' });
    }
    const bytes = Utilities.base64Decode(dados.conteudo_base64);
    const blob = Utilities.newBlob(bytes, dados.mime, dados.nome);
    const arquivo = pastaDestino(dados.subpasta).createFile(blob);
    return resposta({ ok: true, link: arquivo.getUrl() });
  } catch (erro) {
    return resposta({ ok: false, erro: String(erro) });
  }
}

function pastaDestino(nomeSubpasta) {
  const raiz = DriveApp.getFolderById(PASTA_ID);
  if (!nomeSubpasta) return raiz;
  const existentes = raiz.getFoldersByName(nomeSubpasta);
  return existentes.hasNext() ? existentes.next() : raiz.createFolder(nomeSubpasta);
}

function resposta(objeto) {
  return ContentService.createTextOutput(JSON.stringify(objeto))
    .setMimeType(ContentService.MimeType.JSON);
}
