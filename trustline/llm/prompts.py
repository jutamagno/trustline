"""
Prompt templates for all LLM features.
Versioned so regressions can be detected by the eval framework.
"""

PROMPT_VERSION = "1.0.0"


def inconsistency_detection_prompt(
    raw_fields: dict,
    customer_age: int,
    declared_income: float,
    product_type: str,
    channel: str,
    loan_amount: float,
    region: str,
) -> str:
    return f"""Você é um analista de risco de crédito consignado em um banco brasileiro.
Analise os dados abaixo de uma proposta de crédito e identifique INCONSISTÊNCIAS INTERNAS
que possam indicar fraude, dados fabricados ou erro de preenchimento.

DADOS DA PROPOSTA:
- Produto: {product_type}
- Canal de originação: {channel}
- Valor do empréstimo: R$ {loan_amount:,.2f}
- Idade do cliente: {customer_age} anos
- Renda declarada: R$ {declared_income:,.2f}/mês
- UF: {region}
- Campos adicionais: {raw_fields}

ANALISE:
1. A renda declarada é compatível com a idade e o valor solicitado?
2. O produto e canal são elegíveis para este perfil de cliente?
3. Há campos com valores implausíveis ou internamente contraditórios?
4. O valor do empréstimo está dentro de limites razoáveis para a renda declarada?
   (Regra geral: parcela máxima = 35% da renda mensal, prazo típico 12-96 meses)

Responda APENAS com JSON no formato:
{{
  "inconsistencies": ["lista de inconsistências encontradas, vazia se nenhuma"],
  "confidence": 0.0,
  "reasoning": "explicação resumida em 2-3 frases"
}}

Se não houver inconsistências, retorne lista vazia. Confidence: 0.0 (baixa) a 1.0 (alta certeza).
"""


def consent_borderline_prompt(
    consent_method: str,
    channel: str,
    product_type: str,
    customer_age: int,
) -> str:
    return f"""Você é especialista em compliance de crédito consignado (Resolução BCB 538/2025, LGPD).
Avalie se o método de consentimento é adequado para esta operação.

OPERAÇÃO:
- Método de consentimento registrado: {consent_method}
- Canal de originação: {channel}
- Produto: {product_type}
- Idade do cliente: {customer_age} anos

CONTEXTO: O Banco BMG, por determinação do INSS e BACEN, deve usar videochamada para
contratações presenciais de consignado por correspondentes. Clientes acima de 70 anos
requerem proteção adicional (Estatuto do Idoso).

Avalie: o consentimento registrado é adequado, borderline ou inadequado para este canal/produto?

Responda APENAS com JSON:
{{
  "adequate": true,
  "issues": ["lista de problemas, vazia se adequado"],
  "reasoning": "explicação em 1-2 frases"
}}
"""


def risk_scoring_prompt(
    correspondent_id: str,
    signals: dict,
    operations_30d: int,
    flagged_30d: int,
) -> str:
    flag_rate = round(flagged_30d / operations_30d, 3) if operations_30d else 0
    return f"""Você é analista de risco de correspondentes bancários em um banco brasileiro.
Avalie o risco do correspondente abaixo com base em padrões de comportamento operacional.

CORRESPONDENTE: {correspondent_id}
PERÍODO: últimos 30 dias

SINAIS DETECTADOS:
- Total de operações: {operations_30d}
- Operações flagradas: {flagged_30d} ({flag_rate:.1%})
- Sinais adicionais: {signals}

CONTEXTO: O escândalo do INSS (2025) mostrou que correspondentes fraudulentos tipicamente:
- Originam muitas operações em horários incomuns (madrugada/finais de semana)
- Têm alta concentração geográfica em regiões específicas
- Apresentam taxa de cancelamento pós-originação acima de 20%
- Têm operações com prazo muito curto (< 30 dias) para clientes vulneráveis

Avalie o risco APENAS com JSON:
{{
  "risk_score": 0.0,
  "risk_level": "low|medium|high|critical",
  "reasoning": "explicação dos principais fatores de risco em 2-3 frases"
}}

risk_score: 0.0 (sem risco) a 1.0 (risco máximo).
"""


def bcb538_narrative_prompt(stats: dict, period: str) -> str:
    return f"""Gere um relatório executivo de conformidade com a Resolução BCB 538/2025
(Cibersegurança e Proteção de Dados) para o período {period}.

DADOS DO PERÍODO:
{stats}

O relatório deve:
1. Apresentar os números de forma clara e objetiva
2. Destacar melhorias ou deteriorações em relação ao período anterior
3. Mencionar ações tomadas para mitigar riscos identificados
4. Usar linguagem formal adequada para comunicação regulatória

Gere apenas o texto narrativo em Markdown (sem JSON).
Máximo 400 palavras.
"""


def nl_to_es_query_prompt(nl_query: str) -> str:
    return f"""Converta a consulta em linguagem natural abaixo para uma query ElasticSearch.
O índice contém documentos de eventos de originação de crédito com os campos:
- correspondent_id (keyword)
- channel (keyword): correspondent, app, api, branch
- product_type (keyword): consignado_inss, consignado_privado, cartao_consignado
- risk_level (keyword): low, medium, high, critical
- risk_score (float)
- occurred_at (date)
- region (keyword): UF code
- loan_amount (float)
- inconsistencies (text)
- llm_reasoning (text)

CONSULTA: {nl_query}

Responda APENAS com JSON de query ElasticSearch válida:
{{
  "query": {{ ... }}
}}
"""
