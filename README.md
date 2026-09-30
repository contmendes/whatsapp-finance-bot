# 💰 Assistente Financeiro WhatsApp + Dashboard

Um bot de WhatsApp que gerencia finanças (PJ/PF) e armazena dados em Google Sheets, com dashboard interativo para visualização.

## 🚀 Arquitetura

```
WhatsApp Business API → Backend Python (Flask) → Google Sheets
                                    ↓
                        Dashboard Interativo (HTML/JS)
```

## 📋 Pré-requisitos

- ✅ Conta WhatsApp Business (com Phone Number ID e Access Token)
- ✅ Google Sheets criada
- ✅ Conta Render.com (para hospedagem grátis)

## 🔧 Setup Local

### 1. Clonar e instalar dependências
```bash
git clone <seu-repo>
cd whatsapp_finance_bot
pip install -r requirements.txt
```

### 2. Configurar variáveis de ambiente
```bash
cp .env.example .env
# Editar .env com seus valores
```

### 3. Configurar Google Sheets

#### Opção A: Usando arquivo credentials.json
1. Ir em https://console.cloud.google.com
2. Criar novo projeto
3. Ativar Google Sheets API
4. Criar "Service Account"
5. Gerar chave JSON
6. Salvar como `credentials.json` na raiz do projeto

#### Opção B: Usando variável de ambiente
1. Mesmo processo acima
2. Converter conteúdo do JSON para uma linha
3. Adicionar em `.env`:
```
GOOGLE_CREDENTIALS_JSON='{"type": "service_account", ...}'
```

### 4. Compartilhar planilha com Service Account
1. Abrir arquivo credentials.json
2. Copiar email do campo `client_email`
3. Compartilhar a planilha com esse email (Editor)

### 5. Testar localmente
```bash
python main.py
# Servidor rodará em http://localhost:5000
```

## 🚀 Deploy no Render

### 1. Conectar repositório
1. Ir em https://render.com
2. Fazer login / criar conta
3. Criar novo "Web Service"
4. Conectar seu repositório GitHub

### 2. Configurar variáveis de ambiente
No Render, ir em "Environment":
- `PHONE_NUMBER_ID`: seu ID
- `ACCESS_TOKEN`: seu token
- `VERIFY_TOKEN`: um token seguro (ex: `sua_senha_123`)
- `GOOGLE_CREDENTIALS_JSON`: conteúdo completo do credentials.json em uma linha

### 3. Deploy automático
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn main:app`

### 4. Copiar URL do Render
Após deploy, você terá uma URL como:
```
https://seu-app-xyz.onrender.com
```

## 🔌 Configurar Webhook do WhatsApp

1. Ir em https://developers.facebook.com
2. App → Configuração → Básico
3. Em "Webhooks", configurar:
   - **Callback URL**: `https://seu-app-xyz.onrender.com/webhook`
   - **Verify Token**: O mesmo token que você colocou em `.env`
   - **Eventos**: selecionar `messages`

4. Clicar em "Verificar e salvar"

## 📱 Como Usar

### Via WhatsApp
Enviar mensagens como:
```
Receita PJ: 2000 Venda de Serviço
Despesa PF: 150 Alimentação
Investimento: 500 Ações
Resumo PJ
Categorias
```

### Dashboard
1. Abrir `dashboard.html` no navegador
2. Carregar CSV/XLSX da planilha
3. Visualizar gráficos PJ e PF

## 📊 Estrutura da Planilha

Cada aba mensal deve ter:
```
Data | Tipo | Movimento | Categoria | Descrição | Valor | Saldo | Obs
```

## 🔐 Segurança

- Nunca commitar `.env` com valores reais
- Usar variáveis de ambiente no servidor
- Regenerar Access Token se vazado

## 🆘 Troubleshooting

### Webhook não funciona
- Verificar se a URL está correta em Developers
- Verificar VERIFY_TOKEN
- Ver logs no Render

### Google Sheets não salva
- Verificar se planilha está compartilhada
- Verificar credenciais
- Ver logs: `python main.py`

### Dashboard não carrega dados
- Exportar Google Sheets como CSV/XLSX
- Carregar no dashboard
- Verificar formato das colunas

## 📝 Licença

Aberto para uso pessoal e comercial.
