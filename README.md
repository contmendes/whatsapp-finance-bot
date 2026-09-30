# Maria Financeira — WhatsApp + Google Sheets

Bot financeiro para registrar gastos e receitas PF/PJ recebidos pelo WhatsApp e salvar os lançamentos em uma planilha Google Sheets.

## Fluxo

```text
WhatsApp Cloud API → Flask no Render → Google Sheets
```

## Variáveis do Render

Configure estas variáveis em **Render → Environment**:

```text
PHONE_NUMBER_ID=...
ACCESS_TOKEN=...
APP_SECRET=...
VERIFY_TOKEN=...
SPREADSHEET_ID=...
SHEET_NAME=Lançamentos
GOOGLE_CREDENTIALS_JSON={...}
```

Nunca publique tokens ou o JSON da conta de serviço no GitHub. O arquivo `.env.example` contém apenas placeholders.

## Google Sheets

1. Ative a Google Sheets API no Google Cloud.
2. Crie uma Service Account e gere uma chave JSON.
3. Compartilhe a planilha com o valor `client_email` da Service Account, com permissão de Editor.
4. Crie uma aba chamada `Lançamentos` ou defina outro nome em `SHEET_NAME`.
5. Use estes cabeçalhos na primeira linha:

```text
Data | Tipo | Movimento | Categoria | Descrição | Valor | Saldo | Telefone | Nome | ID da mensagem | Mensagem original
```

## Render

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn main:app`
- **Health:** `https://SEU-SERVICO.onrender.com/health`
- **Status:** `https://SEU-SERVICO.onrender.com/api/whatsapp/status`

## Webhook da Meta

Use exatamente:

```text
Callback URL: https://SEU-SERVICO.onrender.com/webhook
Verify Token: o mesmo valor de VERIFY_TOKEN
Campo: messages
```

A rota `/api/whatsapp/webhook` não existe neste backend e retorna 404.

O POST do webhook valida `X-Hub-Signature-256` usando `APP_SECRET`.

## Formatos aceitos

```text
Receita PJ: 2000 Venda de Serviço
Despesa PF: 150 Alimentação
Investimento PF: 500 Ações
Resumo PJ
Resumo PF
Ajuda
Categorias
```

## Segurança

O Access Token anteriormente publicado no histórico/arquivo de exemplo deve ser revogado e substituído por um token novo antes de usar o bot em produção.

Não coloque credenciais em commits, issues, screenshots ou mensagens.
