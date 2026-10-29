# LRS Canhoto Digital (Streamlit)

App público (sem login) para o coletor registrar coletas no celular.
Diferente da versão anterior (dentro da Claude), aqui as credenciais do
Google ficam guardadas **no servidor** — quem usa o link não precisa ter
conta Google nem conta Claude.

## O que você precisa fazer (uma vez só)

### 1. Criar a Conta de Serviço do Google

Isso cria uma "conta robô" que o app usa para acessar a planilha e o
Drive, sem envolver a sua conta pessoal.

1. Acesse https://console.cloud.google.com/ e use o mesmo projeto que
   você já tem (`lrs-sistema`), ou crie um novo.
2. No menu, vá em **"APIs e Serviços" → "Biblioteca"** e ative:
   - **Google Sheets API**
   - **Google Drive API**
3. Vá em **"APIs e Serviços" → "Credenciais" → "Criar Credenciais" →
   "Conta de serviço"**.
   - Dê um nome, ex.: `lrs-canhoto-app`.
   - Não precisa dar nenhum papel/permissão especial no projeto.
4. Depois de criada, clique na conta de serviço → aba **"Chaves"** →
   **"Adicionar chave" → "Criar nova chave" → JSON**. Isso baixa um
   arquivo `.json` — **guarde-o com cuidado, é uma senha**.
5. Copie o e-mail da conta de serviço (algo como
   `lrs-canhoto-app@lrs-sistema.iam.gserviceaccount.com`) — você vai
   precisar dele nos próximos dois passos.

### 2. Dar acesso à planilha e à pasta do Drive

A conta de serviço só enxerga o que for compartilhado com ela
explicitamente:

1. Abra a planilha **"Controle total"** no Google Sheets → botão
   **Compartilhar** → cole o e-mail da conta de serviço → permissão
   **Editor**.
2. Abra a pasta do Drive
   **"LRS Canhoto Digital - Anexos"**
   (https://drive.google.com/drive/folders/1jTpq6aZQmizpqCLBGzDpTtjcWs5uvAh2)
   → **Compartilhar** → mesmo e-mail → permissão **Editor**.

### 3. Subir o código no GitHub

Como você já tem conta no GitHub:

1. Crie um repositório novo (pode ser privado), ex.: `lrs-canhoto-app`.
2. Suba os arquivos desta pasta para o repositório — pelo site do
   GitHub (arrastar os arquivos) ou por linha de comando:
   ```
   git init
   git add .
   git commit -m "App canhoto digital"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/lrs-canhoto-app.git
   git push -u origin main
   ```
   **Importante**: o arquivo `.streamlit/secrets.toml` (se você criar um
   local pra testar) nunca deve ir para o GitHub — o `.gitignore` já
   cuida disso automaticamente.

### 4. Publicar no Streamlit Community Cloud

1. Acesse https://share.streamlit.io/ e entre com sua conta GitHub.
2. **"New app"** → selecione o repositório `lrs-canhoto-app`, branch
   `main`, arquivo principal `app.py`.
3. Antes de clicar em "Deploy", vá em **"Advanced settings" → "Secrets"**
   e cole o conteúdo do arquivo `.streamlit/secrets.toml.example`
   **substituindo pelos valores reais do seu arquivo `.json` baixado no
   passo 1** (abra o `.json` num editor de texto e copie cada campo
   para o formato `chave = "valor"` mostrado no exemplo — a
   `private_key` é a mais longa, copie ela inteira entre aspas, mantendo
   os `\n`).
4. Clique em **Deploy**. Em 1–2 minutos o app estará no ar, com uma URL
   fixa tipo `https://lrs-canhoto-app.streamlit.app` — esse é o link que
   o coletor usa, sem precisar logar em nada.

### 5. Testar

Abra o link no celular do coletor, selecione um cliente, preencha,
assine, tire uma foto e finalize — confira se a linha apareceu certinha
na aba BD_COLETAS e se os arquivos (assinatura/foto) chegaram na pasta
do Drive.

## Rodando localmente (opcional, pra testar antes de publicar)

```
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edite .streamlit/secrets.toml com os valores reais
./venv/bin/streamlit run app.py
```

## Arquivos deste projeto

- `app.py` — o app em si.
- `requirements.txt` — dependências com versões travadas (testadas e
  compatíveis entre si — não atualize sem testar, principalmente o
  `streamlit-drawable-canvas`, que quebra em versões mais novas do
  Streamlit).
- `.streamlit/secrets.toml.example` — modelo de onde colar as
  credenciais (nunca o arquivo real).
- `.gitignore` — mantém o `secrets.toml` de verdade fora do GitHub.

## O que esse app ainda não faz (comparado à versão anterior)

- Não mostra as "coletas previstas para hoje" vindas da Google Agenda
  — dá pra adicionar depois, usando a mesma conta de serviço (basta
  compartilhar a agenda "LRS CLIENTES" com o e-mail da conta de
  serviço, do mesmo jeito que a planilha).
- Não envia e-mail automático pro cliente.
