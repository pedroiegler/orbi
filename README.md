# Orbi Backend

Backend oficial da plataforma **Orbi**.

## Sobre o Projeto

O Orbi é uma plataforma SaaS de gestão inteligente com Inteligência Artificial, criada para atender múltiplos segmentos através de um Core compartilhado e extensões independentes.

O objetivo da plataforma é permitir que diferentes nichos utilizem a mesma base tecnológica, mantendo isolamento completo entre clientes (Multi-Tenant) e permitindo evolução contínua sem duplicação de código.

Inicialmente o foco será o **Orbi Dental**, mas a arquitetura foi projetada para suportar dezenas de segmentos diferentes.

Todo o processamento da plataforma acontece neste repositório.

---

# Stack

## Backend

- Python
- FastAPI

## Banco de Dados

- PostgreSQL
- pgvector

## Cache e Filas

- Redis

## Inteligência Artificial

- LangChain
- Google Gemini *(inicialmente)*
- OpenAI / Claude *(futuramente, conforme custo-benefício e necessidades do produto)*

## Persistência

- SQLAlchemy
- Alembic

## Infraestrutura

- Docker
- Docker Compose

---

# Arquitetura

A arquitetura foi projetada para crescimento de longo prazo utilizando:

- Modular Monolith
- Multi-Tenant
- Clean Architecture
- Plugin Architecture
- Event-Driven Architecture
- DDD Lite
- Hexagonal Architecture

Essa combinação permite adicionar novos módulos e novos nichos sem reescrever a plataforma.

---

# Responsabilidades

O Backend é responsável por:

- Regras de negócio
- API REST
- Autenticação
- Autorização
- Multi-Tenant
- Banco de Dados
- IA
- Agentes
- Memória dos agentes
- Embeddings
- RAG
- Integrações
- WhatsApp
- Eventos
- Workers
- Scheduler
- Relatórios
- Financeiro
- Agenda
- CRM

---

# Estrutura

```
apps/

api/
worker-ai/
worker-messages/
worker-scheduler/

packages/

core/
extensions/
shared/
database/
events/

infra/
docker/
docs/
tests/
```

---

# Workers

A plataforma utiliza Workers independentes para separar responsabilidades.

### worker_messages

Responsável pelo processamento das mensagens do WhatsApp.

### worker_ai

Executa tarefas pesadas relacionadas à Inteligência Artificial, embeddings, memória e RAG.

### worker_scheduler

Responsável por tarefas agendadas, lembretes, confirmações e rotinas automáticas.

---

# Banco de Dados

A plataforma utiliza PostgreSQL com pgvector.

Cada cliente possui isolamento através de Multi-Tenancy utilizando Schemas independentes.

Os embeddings são armazenados no próprio PostgreSQL.

Caso o volume cresça significativamente, a arquitetura permite migrar para um banco vetorial dedicado sem alterar a regra de negócio.

---

# Inteligência Artificial

A IA foi desenvolvida para ser independente do modelo utilizado.

Inicialmente será utilizado o Google Gemini devido ao excelente custo-benefício.

Como toda a comunicação é abstraída pelo LangChain, a plataforma poderá migrar futuramente para OpenAI, Claude ou qualquer outro modelo que ofereça melhor desempenho, qualidade ou custo, sem necessidade de alterar a lógica da aplicação.

---

# Desenvolvimento

Subir ambiente

```bash
docker compose up
```

Executar migrações

```bash
alembic upgrade head
```

Executar API

```bash
uvicorn app.main:app --reload
```

---

# Roadmap

- Core
- Multi-Tenant
- Orbi Dental
- Memória por Tenant
- Agentes Inteligentes
- CrewAI
- Novos Nichos
- Whitelabel

---

# Licença

Projeto privado.

Todos os direitos reservados.
