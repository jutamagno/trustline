# Trustline

**Intelligent audit platform for credit origination data in banking.**

> *"Não basta gerar metadados. É preciso saber se o dado de entrada pode ser confiado."*

---

## Por que este projeto existe

Em maio de 2025, a Operação Sem Desconto revelou um esquema sistemático de fraude em crédito consignado: correspondentes bancários editavam áudios de aposentados para simular consentimento e originavam contratos de forma fraudulenta. O Banco BMG foi investigado pelo TCU, Senacon e CPMI do INSS, pagou R$ 7 milhões em ressarcimentos e foi multado em R$ 5,1 milhões pelo Ministério da Justiça por uso indevido de dados de idosos.

Paralelamente, a **Resolução BCB 538/2025** (publicada em dezembro de 2025) estabeleceu novas exigências de cibersegurança e proteção de dados para instituições financeiras, com deadline de conformidade em **dezembro de 2026**.

O Trustline resolve o problema de dados que está na origem desse escândalo: **como garantir, antes de originar o crédito, que os dados de entrada são confiáveis?**

---

## O que o Trustline faz

```
EVENTO DE ORIGINAÇÃO                     RESULTADO
(correspondente, app, API)
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│                        TRUSTLINE                            │
│                                                             │
│  ┌──────────────┐  Kafka  ┌─────────────────────────────┐   │
│  │ Ingestion API│ ──────► │     Stream Processor        │   │
│  │  FastAPI     │         │  InconsistencyDetector      │   │
│  │  MongoDB     │         │  ConsentVerifier            │   │
│  │  (events)    │         │  → AnalysisResult + Audit   │   │
│  └──────────────┘         └──────────────┬──────────────┘   │
│                                          │                  │
│         ┌────────────────────────────────┼──────────┐       │
│  ┌──────▼──────┐  ┌──────────────┐  ┌───▼────────┐  │       │
│  │  MongoDB    │  │  RDS (PG)    │  │  S3/Glue   │  │       │
│  │  Event store│  │  Audit trail │  │  Reports   │  │       │
│  │  Risk scores│  │  Eval runs   │  │  LocalStack│  │       │
│  └─────────────┘  └──────────────┘  └────────────┘  │       │
│                                                      │       │
│  ┌──────────────────────────────────────────────────┐│       │
│  │  Airflow DAGs (4)                                ││       │
│  │  ├ dag_risk_scoring   — recalcula score diário   ││       │
│  │  ├ dag_bcb538_report  — relatório BACEN 06:00    ││       │
│  │  ├ dag_lgpd_audit     — inventário LGPD semanal  ││       │
│  │  └ dag_llm_eval       — testa o próprio LLM 02:00││       │
│  └──────────────────────────────────────────────────┘│       │
│                                                      │       │
│  ┌──────────────────┐  ┌──────────────────────────┐  │       │
│  │  ElasticSearch   │  │  Grafana + Prometheus    │  │       │
│  │  NL query sobre  │  │  • Risk distribution     │  │       │
│  │  audit trail     │  │  • Detection rate        │  │       │
│  └──────────────────┘  │  • LLM eval score/tempo  │  │       │
│                        │  • Bedrock cost/feature  │  │       │
│                        └──────────────────────────┘  │       │
└─────────────────────────────────────────────────────────────┘
```

---

## Stack

| Camada | Tecnologia |
|---|---|
| API | FastAPI + uvicorn |
| Event store | MongoDB 7 |
| Audit trail | PostgreSQL 16 (RDS) |
| Streaming | Apache Kafka |
| LLM | AWS Bedrock (Claude Haiku) |
| Orquestração | Apache Airflow 2.9 |
| Search | ElasticSearch 8 |
| Storage | AWS S3 (LocalStack em dev) |
| Observabilidade | Prometheus + Grafana |
| Deploy | Docker Compose + Helm (EKS) |
| Testes | pytest + mongomock |

---

## Componentes principais

### LLM Analyzers

**InconsistencyDetector** — detecta dados implausíveis em propostas de crédito:
- Hard rules determinísticas (sem custo LLM): idade, renda mínima, loan-to-income ratio
- LLM via Bedrock para análise nuançada de campos da proposta
- Fallback gracioso quando Bedrock está indisponível (circuit breaker)

**ConsentVerifier** — valida a cadeia de consentimento conforme BCB 538 + Acordo BMG-INSS:
- Regra: correspondente + INSS/cartão exige videochamada ou biometria
- Regra: canal digital (app/api) é incompatível com assinatura física
- Regra: cliente 70+ anos exige proteção adicional (Estatuto do Idoso)
- LLM apenas para casos borderline

**CorrespondentRiskScorer** — score de risco baseado em padrões temporais:
- Sinais: taxa de flagramento, operações em madrugada, velocidade, concentração geográfica
- Blend 60% heurística + 40% LLM (raciocínio auditável)
- Expande via `@task.expand()` no Airflow para paralelismo por correspondente

### LLM Eval Framework

O componente mais diferenciador: um sistema de avaliação contínua dos próprios LLMs.

```python
metrics = evaluator.run_suite(golden_cases, consistency_runs=3)
# false_negative_rate  — fraude não detectada / total fraudes (crítico)
# false_positive_rate  — legítimo flagrado / total legítimos
# pii_leakage_rate     — % respostas com CPF/dados pessoais (LGPD)
# consistency_score    — mesmo caso → mesma decisão em 3 runs
# total_cost_usd       — custo Bedrock por suite completa
```

Golden dataset: 12 casos sintéticos baseados nos padrões públicos do escândalo INSS/2025
(áudio editado, duplicata de CPF, renda implausível, operação em madrugada, idoso sem vídeo).

Threshold de CI: `false_negative_rate > 10%` ou `pii_leakage_rate > 0%` → pipeline falha.

### Compliance automático

- **BCB 538 report** (diário): agrega métricas operacionais + gera narrativa Markdown via LLM
- **LGPD audit** (semanal): inventaria titulares, verifica base legal por produto, detecta violações de retenção

---

## Como rodar

### Pré-requisitos
- Docker + Docker Compose
- Python 3.11+

### Subir a stack

```bash
git clone https://github.com/jutamagno/trustline
cd trustline
make up          # sobe todos os serviços (MongoDB, Kafka, ES, LocalStack, Airflow, Grafana)
make seed        # gera 500 correspondentes + 10k operações sintéticas
make demo        # fluxo end-to-end: ingesta → detecção → relatório
```

### Testes

```bash
make test        # pytest com cobertura
make eval        # LLM eval suite (sem infra — usa mocks)
make lint        # ruff
```

### API

```
POST /events                  — ingere evento de originação
GET  /correspondents          — lista com risk score atual
GET  /correspondents/{id}/risk — score detalhado + histórico 30d
GET  /audit/trail             — audit trail com filtros
POST /search                  — NL query: "correspondentes com score > 0.7 em SP"
GET  /compliance/bcb538       — último relatório BCB 538
GET  /compliance/lgpd         — último inventário LGPD
GET  /health                  — status dos componentes
GET  /metrics                 — Prometheus text format
```

Docs interativos: http://localhost:8000/docs

---

## Arquitetura: decisões relevantes

**Por que MongoDB para eventos e não só PostgreSQL?**
Eventos de originação têm `raw_fields` com schema variável por canal e produto. MongoDB
permite evolução do schema sem migrations. PG é reservado para o audit trail imutável
(onde ACID e queries analíticas importam).

**Por que hard rules antes do LLM?**
Custo e latência. 70% dos eventos passam pelas hard rules sem custo de LLM. O LLM
só é chamado quando há algo para analisar. Isso reduz custo Bedrock em ~60%.

**Por que blend 60/40 no risk scorer?**
A heurística é determinística e auditável (necessário para BACEN). O LLM adiciona
nuance que as regras não capturam. O blend mantém previsibilidade com raciocínio.

**Circuit breaker no Bedrock client?**
Bedrock em produção tem latência variável. Um circuit breaker CLOSED→OPEN→HALF
evita cascata de timeouts. Threshold: 5 falhas → OPEN por 60s. Config via env vars.

---

## Deploy em EKS

```bash
# Build e push para ECR
docker build -t trustline-api:latest .
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr-url>
docker tag trustline-api:latest <ecr-url>/trustline-api:latest
docker push <ecr-url>/trustline-api:latest

# Deploy com Helm
helm upgrade --install trustline ./helm/trustline \
  --set image.repository=<ecr-url>/trustline-api \
  --set image.tag=latest \
  --namespace trustline --create-namespace
```

O Helm chart inclui HPA (CPU 70%), liveness/readiness probes e configmap por ambiente.
Para Airflow em EKS, usar o chart oficial `apache-airflow/airflow` com executor KubernetesExecutor.

---

## Roadmap

- [ ] OpenMetadata integration: publicar lineage de eventos → MongoDB → ElasticSearch
- [ ] Databricks Workflow: batch scoring de risco para carteiras históricas
- [ ] Spark Streaming: substituir Kafka consumer Python por job Spark para maior throughput
- [ ] Trino: queries analíticas cross-source (MongoDB + PG + S3) para relatórios ad-hoc
- [ ] MDM com LLM: resolução de entidades de clientes (deduplicação de CPF_hash equivalentes)

---

## Contexto regulatório

| Regulação | Como o Trustline endereça |
|---|---|
| BCB 538/2025 | Relatório diário automático + monitoramento contínuo de sistemas de decisão automatizada |
| LGPD Art. 7 | Inventário de titulares, base legal por produto, verificação de retenção |
| Acordo BMG-INSS (2025) | Regras de consentimento hard-coded: vídeo obrigatório para correspondente + INSS |
| Estatuto do Idoso | Proteção extra para clientes 70+ em consentimento por áudio |

---

*Trustline — porque crédito responsável começa nos dados de entrada.*
