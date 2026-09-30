# Categorias padrão do assistente financeiro
# O usuário pode adicionar/modificar conforme precisar

CATEGORIES = {
    "PJ": {
        "RECEITA": [
            "Venda de Serviço",
            "Consultoria",
            "Freelancer",
            "Produto Digital",
            "Royalties",
            "Outro"
        ],
        "DESPESA": [
            "Fornecedores",
            "Software/Ferramentas",
            "Marketing",
            "Hospedagem",
            "Impostos",
            "Contador",
            "Aluguel Comercial",
            "Energia/Internet",
            "Outro"
        ]
    },
    "PF": {
        "RECEITA": [
            "Salário",
            "Freelancer",
            "Bônus",
            "Presente",
            "Rendimentos",
            "Outro"
        ],
        "DESPESA": [
            "Alimentação",
            "Transporte",
            "Saúde",
            "Educação",
            "Lazer",
            "Moradia",
            "Assinaturas",
            "Vestuário",
            "Outro"
        ]
    },
    "GERAL": {
        "INVESTIMENTO": [
            "Ações",
            "Criptomoedas",
            "Poupança",
            "Renda Fixa",
            "Imóvel",
            "Outro"
        ]
    }
}

def get_categories_text(categories=None):
    """Retorna categorias legíveis e instruções de edição."""
    categories = categories or CATEGORIES
    text = "📋 *Categorias disponíveis:*\n"
    for tipo in ("PF", "PJ"):
        text += f"\n*{tipo}*\n"
        for movimento in ("RECEITA", "DESPESA"):
            values = categories.get(tipo, {}).get(movimento, [])
            text += f"\n{movimento.title()}: " + ", ".join(values) + "\n"
    text += "\n*Investimento*\n" + ", ".join(categories.get("GERAL", {}).get("INVESTIMENTO", []))
    text += "\n\nEditar: *categoria adicionar PF despesa Mercado* ou *categoria remover PF despesa Mercado*."
    return text
