# Orbi Backend

Backend oficial da plataforma **Orbi**.

---

## Sobre o Projeto

O Orbi é uma plataforma SaaS de gestão inteligente com
Inteligência Artificial, criada para atender múltiplos
segmentos através de um Core compartilhado e extensões
independentes por nicho.

O objetivo é permitir que diferentes segmentos utilizem
a mesma base tecnológica, mantendo isolamento completo
entre clientes via Multi-Tenant e permitindo evolução
contínua sem duplicação de código.

O foco inicial é o **Orbi Dental**, mas a arquitetura
foi projetada para suportar dezenas de segmentos sem
reescrever o core.

Todo o processamento da plataforma acontece neste
repositório.

---

## Stack

### Backend
- Python
- FastAPI

### Banco de Dados
- PostgreSQL
- pgvector

### ORM e Migrações
- SQLAlchemy
- Alembic

### Cache e Filas
- Redis

### Inteligência Artificial
- LangChain
- Google Gemini *(inicial — gratuito e eficiente)*
- Outros modelos *(futuramente, conforme custo-benefício)*

### Infraestrutura
- Docker
- Docker Compose
- Nginx + Certbot
- Cloudflare *(CDN, DNS e proteção)*

---

## Arquitetura

A arquitetura combina padrões complementares para
garantir crescimento de longo prazo sem reescrita.

### Modular Monolith
Todo o código vive em um único repositório mas dividido
em módulos independentes. Cada nicho é um módulo que
se registra no core sem que o core precise conhecê-lo.
Permite coesão no desenvolvimento sem os problemas de
coordenação de microsserviços.

### Multi-Tenant com Schema Isolation
Cada cliente possui seu próprio schema isolado no
PostgreSQL. Os dados nunca se misturam entre tenants.
O core, as extensões de nicho e os agentes sempre
operam dentro do schema do tenant ativo.

### Clean Architecture
Separação estrita entre regras de negócio e
infraestrutura. A lógica de negócio não conhece
FastAPI, PostgreSQL ou Redis — só interfaces. Isso
permite trocar qualquer peça de infraestrutura sem
alterar as regras.

```
Camadas:
├── Domain         (entidades e regras de negócio)
├── Application    (casos de uso)
├── Infrastructure (banco, cache, IA, WhatsApp)
└── Interface      (API REST, worker)
```

### Plugin Architecture
Cada extensão de nicho se registra no core na
inicialização. O core não conhece os nichos — eles
se autodeclaram. Adicionar um novo nicho é criar uma
nova extensão sem tocar no core existente.

```
Core (nunca alterado)
├── Orbi Dental  (plugin)
├── Orbi Pet     (plugin)
├── Orbi Beauty  (plugin)
└── Orbi Auto    (plugin)
```

### Event-Driven Architecture
Toda comunicação entre a API e o worker acontece via
eventos publicados no Redis. A API nunca chama o
worker diretamente — publica um evento e retorna. O
worker consome no próprio ritmo. Isso desacopla
completamente o processamento em tempo real do
processamento pesado de IA.

```
API publica evento no Redis
        ↓
Worker consome e roteia internamente
├── handler:messages  (mensagens WhatsApp)
├── handler:ai        (embeddings e memória)
└── handler:scheduler (tarefas agendadas)
```

### DDD Lite
Uso de conceitos de Domain-Driven Design sem a
complexidade total. Entidades, Value Objects e
Repositórios são aplicados onde o domínio é complexo
— como agendamentos, prontuários e agentes. Evita
over-engineering onde o domínio é simples.

### Hexagonal Architecture
Ports and Adapters para as integrações externas.
WhatsApp, modelos de IA e banco de dados são adapters
que implementam ports definidos pelo domínio. O provedor
de WhatsApp ainda está em aberto (API oficial da Meta vs.
provedor terceiro); trocar de provedor, ou trocar o modelo
de IA, é criar um novo adapter — a regra de negócio não muda.

---

## AgentManager

O AgentManager é o coração da inteligência da
plataforma. Cada tenant possui seu próprio contexto
de IA acumulado e isolado no banco.

O AgentManager é instanciado sob demanda quando um
evento de um tenant chega. Ele carrega o contexto
daquele tenant do banco, instancia o agente correto
para o nicho e age com a inteligência acumulada
daquele negócio específico.

```
AgentManager.load(tenant_id, nicho)
├── Carrega schema do tenant no PostgreSQL
├── Instancia agente do nicho
│   ├── AgenteOdonto (Orbi Dental)
│   ├── AgentePet    (Orbi Pet)
│   └── ...
└── Injeta contexto e embeddings do tenant
```

A interface do AgentManager é estável e não muda
conforme a IA evolui. Hoje retorna um agente único.
No futuro retorna uma crew completa do CrewAI. O
worker não sabe a diferença.

---

## Worker

A plataforma utiliza um worker único que centraliza
todo o processamento assíncrono. Internamente ele
é organizado por handlers com responsabilidades
separadas, facilitando a divisão em workers
independentes quando o volume exigir.

```
worker/
├── handler_messages   Mensagens WhatsApp + AgentManager
├── handler_ai         Embeddings, memória e pgvector
└── handler_scheduler  Lembretes, confirmações e rotinas
```

### handler_messages
Processa mensagens do WhatsApp em tempo real.
Instancia o AgentManager do tenant, gera resposta
via LangChain e Gemini e retorna ao paciente.

### handler_ai
Executa tarefas pesadas sem urgência de latência.
Gera e armazena embeddings de atendimentos no
pgvector e atualiza memória por tenant.

### handler_scheduler
Executa tarefas agendadas e rotinas automáticas.
Confirmações de consulta, lembretes, reativação
de pacientes sumidos e alertas operacionais.

### Quando escalar
Quando o volume crescer e o worker único virar
gargalo, cada handler é movido para seu próprio
container independente sem reescrever lógica —
só separando o que já existe.

```
worker único
        ↓ (quando escalar)
├── worker_messages
├── worker_ai
└── worker_scheduler
```

---

## Banco de Dados

PostgreSQL com pgvector. Cada tenant possui schema
isolado no mesmo banco.

```
banco: orbi
├── schema: public           (tenants, planos, config)
└── schema: tenant_<slug>    (dados do cliente)
    ├── pessoas
    ├── agenda
    ├── financeiro
    ├── historico
    ├── embeddings            (pgvector)
    └── [tabelas do nicho]
```

Os embeddings dos agentes ficam no próprio schema
do tenant via pgvector. Caso o volume cresça
significativamente, a arquitetura Hexagonal permite
migrar para um banco vetorial dedicado como Pinecone
sem alterar nenhuma regra de negócio.

---

## Inteligência Artificial

A IA foi projetada para ser completamente independente
do modelo utilizado. LangChain abstrai toda a
comunicação com o modelo — trocar Gemini por OpenAI
ou Claude é alterar uma variável de ambiente.

Inicialmente será utilizado o Google Gemini Flash
pelo excelente custo-benefício e plano gratuito.
Conforme a receita crescer, o modelo será avaliado
com base em desempenho, qualidade e custo.

---

## Estrutura de Pastas

```
apps/
├── api/                   FastAPI — API REST
└── worker/                Worker único
    ├── handler_messages/  Mensagens e AgentManager
    ├── handler_ai/        Embeddings e memória
    └── handler_scheduler/ Tarefas agendadas

packages/
├── core/                  Regras de negócio compartilhadas
├── extensions/            Extensões por nicho
│   ├── dental/
│   ├── pet/
│   └── ...
├── shared/                Utilitários compartilhados
├── database/              Models, repositórios, migrações
└── events/                Definição de eventos e filas

infra/
├── docker/                Dockerfiles e compose
└── nginx/                 Configuração do proxy

docs/                      Documentação técnica
tests/                     Testes automatizados
```

---

## Desenvolvimento

### Subir ambiente completo
```bash
docker compose up
```

### Executar migrações
```bash
alembic upgrade head
```

### Executar API em modo desenvolvimento
```bash
uvicorn app.main:app --reload
```

---

## Princípios que guiam o projeto

- **Core nunca é alterado** — só configurado via
  toggles e flags por tenant.
- **Nicho nunca mexe no core** — só adiciona
  extensão por cima.
- **Tenant completamente isolado** — schema próprio,
  contexto próprio, agente próprio.
- **Tecnologia não muda — só onde roda** — o código
  permanece o mesmo, só a infraestrutura ao redor evolui.
- **IA independente do modelo** — LangChain abstrai,
  troca é variável de ambiente.
- **Worker único no início** — handlers internos
  separados por responsabilidade, prontos para virar
  workers independentes quando o volume exigir.
- **AgentManager com interface estável** — hoje
  retorna agente único, no futuro retorna uma crew
  do CrewAI sem o worker saber.
  
---

## Licença

Projeto privado.  
Todos os direitos reservados
