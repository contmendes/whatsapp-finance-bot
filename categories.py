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

def get_categories_text():
    """Retorna as categorias em formato legível"""
    text = "📋 *Categorias disponíveis:*\n\n"

    for tipo_pj_pf in ["PJ", "PF"]:
        text += f"\n*{tipo_pj_pf}:*\n"
        for tipo_movimento in ["RECEITA", "DESPESA"]:
            text += f"\n  {tipo_movimento}:\n"
            for cat in CATEGORIES[tipo_pj_pf][tipo_movimento]:
                text += f"    • {cat}\n"

    text += f"\n*INVESTIMENTO (qualquer tipo):*\n"
    for cat in CATEGORIES["GERAL"]["INVESTIMENTO"]:
        text += f"  • {cat}\n"

    return text
