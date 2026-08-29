# ORBI — Registro de Decisões

Cada decisão em três linhas: **contexto**, **escolha**, **motivo**. Data no formato `AAAA-MM-DD`.
Quando a mesma proposta voltar, aponte a linha em vez de rediscutir.

---

## D-001 · Rendering determinístico (2026-08-24)
- **Contexto:** o LLM poderia redigir a resposta final a partir do resultado do ERP.
- **Escolha:** resposta montada por template Jinja a partir de DTO tipado; `llm_rendering_enabled=false`.
- **Motivo:** remove alteração de número na redação, corta ~1s, reduz custo e neutraliza injeção.

## D-002 · O LLM nunca emite identificador (2026-08-24)
- **Contexto:** modelos alucinam IDs, e ID alucinado passa por validação de tipo comum.
- **Escolha:** tipo `EntityTerm` rejeita dígitos puros com 4+ caracteres, CPF/CNPJ, UUID e códigos.
- **Motivo:** transforma uma convenção em erro de validação antes de qualquer execução.

## D-003 · Sem tool de busca de produto (2026-08-24)
- **Contexto:** seria natural expor `search_product` ao LLM.
- **Escolha:** resolução de entidade é etapa interna do Runtime, nunca uma tool.
- **Motivo:** evita duas rodadas de LLM (~1s a mais) e o loop de agente no caminho da pergunta.

## D-004 · SQLAlchemy síncrono (2026-08-24)
- **Contexto:** FastAPI favorece async, mas XML-RPC do Odoo, CLI e jobs de sync são síncronos.
- **Escolha:** um único caminho síncrono (psycopg 3 + SQLAlchemy 2.0), endpoints `def`.
- **Motivo:** evita duplicar cada repositório em duas versões; o gargalo do turno é o ERP, não I/O local.

## D-005 · Sessão só pela dependency com `SET LOCAL app.tenant_id` (2026-08-24)
- **Contexto:** RLS só funciona se toda transação declarar o tenant.
- **Escolha:** `tenant_session()` é a única porta; lint de import proíbe usar o engine direto.
- **Motivo:** isolamento não pode depender de o desenvolvedor lembrar.

## D-006 · Canary tenant como fixture global (2026-08-24)
- **Contexto:** teste de vazamento só existe quando alguém lembra de escrevê-lo.
- **Escolha:** fixture global inspeciona todo resultado de query; linha do canário falha o build.
- **Motivo:** todo teste vira teste de vazamento sem esforço adicional.

## D-007 · Embedding: porta com provedor local determinístico (2026-08-24)
- **Contexto:** o MVP precisa rodar em CI e em dev sem chave de API nem download de modelo.
- **Escolha:** `EmbeddingPort` com dois provedores: `hashing` (n-gramas de caractere, 768d, local,
  determinístico) e `openai`/`voyage` por API para produção.
- **Motivo:** o estágio 4 da cascata é o mais barato de trocar; a qualidade real vem do nome
  canônico. O provedor local é funcional (hashing trick), não um stub.

## D-008 · Circuit breaker por `(tenant, adapter, operation)` (2026-08-24)
- **Contexto:** breaker por adapter inteiro derruba as quatro tools quando só uma está lenta.
- **Escolha:** chave composta; 5 falhas em 30s abre por 60s, half-open com uma sonda.
- **Motivo:** falha de estoque não pode impedir consulta de título em aberto.

## D-009 · Nunca servir estoque de cache (2026-08-24)
- **Contexto:** cache de estoque melhoraria latência e resistiria a ERP fora do ar.
- **Escolha:** proibido. ERP indisponível responde a falha, com honestidade.
- **Motivo:** número errado de estoque vira promessa quebrada com o cliente final do tenant.

## D-010 · Auditoria com hash encadeado, sem payload bruto (2026-08-24)
- **Contexto:** guardar o payload do ERP facilitaria depuração.
- **Escolha:** `prev_hash` + `row_hash` por tenant; do payload guarda-se só hash e campos-chave.
- **Motivo:** adulteração detectável por scan, e payload completo é passivo de LGPD sem contrapartida.

## D-011 · Dois provedores de LLM de fabricantes diferentes (2026-08-24)
- **Contexto:** failover dentro do mesmo fabricante cai junto.
- **Escolha:** `LLMPort` com primário e fallback obrigatoriamente de fabricantes distintos; ambos no eval.
- **Motivo:** incidente de provedor é correlacionado dentro da mesma empresa.

## D-012 · Slots resolvidos no contexto, não só mensagens (2026-08-24)
- **Contexto:** "e o preço dele?" precisa saber qual é "dele".
- **Escolha:** `conversation_contexts` guarda `last_product_id`/`last_customer_id` + 5 turnos, TTL 15 min.
- **Motivo:** o Runtime preenche o termo pelo slot em vez de o LLM adivinhar.

## D-013 · Alias só é promovido após dois usos limpos (2026-08-24)
- **Contexto:** gravar alias na primeira escolha aprende rápido — e erra permanentemente.
- **Escolha:** entra com `confidence=low`, vira `confirmed` após 2 usos sem correção.
- **Motivo:** um toque errado não pode envenenar a resolução do tenant.

## D-014 · CLI antes de qualquer tela (2026-08-24)
- **Contexto:** painel é o pedido natural.
- **Escolha:** `orbi ...` (Typer) é a única interface de administração; Console só acima de 4h/mês de CLI.
- **Motivo:** tela que faz o que um comando já faz não é prioridade no MVP.

## D-015 · Odoo como adapter de referência permanente (2026-08-24)
- **Contexto:** o Odoo seria só um ambiente de teste descartável.
- **Escolha:** `OdooAdapter` é permanente e é contra ele que o Conformance Kit roda no CI.
- **Motivo:** XML-RPC vs REST testa a abstração antes do primeiro cliente, quando corrigir é barato.

## D-016 · Discovery com CrewAI opcional em runtime (2026-08-24)
- **Contexto:** CrewAI é dependência pesada e o Runtime não pode depender dela.
- **Escolha:** `discovery` é um extra de instalação (`pip install .[discovery]`); a saída é dado versionado.
- **Motivo:** o Runtime consome o Knowledge Model ativo e não sabe que o CrewAI existe.

## D-017 · Rate limit e pendências no Postgres, sem Redis (2026-08-24)
- **Contexto:** rate limit e TTL de desambiguação são o caso clássico de Redis.
- **Escolha:** tabelas no Postgres com janela e `expires_at`; Redis só com gargalo medido.
- **Motivo:** o volume do MVP cabe com folga e cada serviço novo custa operação.

## D-018 · Papéis são presets sobre capabilities (2026-08-24)
- **Contexto:** "meu gerente vê tudo menos custo" chega no primeiro mês.
- **Escolha:** 3 papéis visíveis sobre capabilities internas; Field Policy separa `check_price` com e sem custo.
- **Motivo:** vira linha de configuração, não deploy nem tool nova.

## D-019 · Python 3.13 e Pydantic v2 strict (2026-08-24)
- **Contexto:** escolher versão base do projeto.
- **Escolha:** Python 3.13, Pydantic v2 (`strict`), SQLAlchemy 2.0, FastAPI atual, Typer.
- **Motivo:** versões estáveis atuais, com suporte longo e tipagem forte de ponta a ponta.

## D-020 · Provedor de LLM `rule_based` para dev e teste (2026-08-24)
- **Contexto:** E2E e demonstração local não podem depender de chave de API nem de rede.
- **Escolha:** provedor `rule_based` implementa o mesmo `LLMPort` com regras determinísticas,
  habilitado só por configuração explícita e nunca padrão em produção.
- **Motivo:** permite E2E real do turno inteiro no CI; produção exige provedor real declarado.

---

## Decisões tomadas durante a construção

As anteriores vieram do projeto. Estas vieram de bater no problema.

## D-021 · Lookup de tenant por função `SECURITY DEFINER` (2026-08-25)
- **Contexto:** o Runtime precisa achar o tenant pelo número de destino antes de poder emitir `SET LOCAL app.tenant_id` — e a RLS, corretamente, esconde `tenants` de quem ainda não declarou o tenant.
- **Escolha:** uma única função `orbi_tenant_by_channel`, de escopo mínimo, com `GRANT EXECUTE` para o papel da aplicação.
- **Motivo:** a alternativa era dar credencial administrativa ao Runtime, o que anularia a RLS inteira para ganhar uma consulta. A exceção fica escrita, versionada e revisável.

## D-022 · Append-only com duas exceções precisas (2026-08-25)
- **Contexto:** o gatilho original recusava qualquer `UPDATE`, o que também bloqueava a anonimização da LGPD e a gravação do 👍/👎.
- **Escolha:** o gatilho recusa `DELETE` sempre e recusa `UPDATE` em todo campo coberto pela corrente de hash; só `message_text` e `feedback` podem mudar.
- **Motivo:** as duas exceções não alteram o `row_hash`, então a corrente continua verificável — e o resto continua imutável até para o superusuário.

## D-023 · A pergunta passa pelo mesmo `CanonicalNameBuilder` do catálogo (2026-08-25)
- **Contexto:** o catálogo era canonicalizado e a pergunta apenas normalizada.
- **Escolha:** as duas pontas passam pela mesma tradução antes de qualquer comparação.
- **Motivo:** comparar "cim cp-ii 50kg" com "cimento cp-ii 50 kg" é comparar coisas diferentes; era um erro silencioso de resolução esperando para acontecer.

## D-024 · Slot resolvido não volta pela cascata (2026-08-25)
- **Contexto:** "e o preço dele?" preenchia o termo pelo slot e mandava resolver de novo.
- **Escolha:** o slot guarda o `erp_entity_id` já resolvido; o Runtime usa direto.
- **Motivo:** re-resolver um id conhecido gasta tempo e reintroduz a chance de errar a entidade que já estava certa.

## D-025 · O abort de sync tem override explícito (2026-08-25)
- **Contexto:** renomeação em massa legítima batia no limite de 30% e travava a operação.
- **Escolha:** `orbi catalog sync --force`, com a mensagem de abort dizendo quando usar.
- **Motivo:** proteção sem escape vira contorno improvisado — e o contorno improvisado não fica registrado em lugar nenhum.

## D-026 · Métricas do processo no `/health` (2026-08-25)
- **Contexto:** o `TurnMetrics` acumulava latência por etapa e ninguém lia.
- **Escolha:** `/health` devolve o snapshot junto com a saúde do banco.
- **Motivo:** sem painel, esse é o lugar onde a operação lê latência e taxa de ambiguidade sem abrir o banco.

## D-027 · Shadow eval não toca no ERP (2026-08-25)
- **Contexto:** reexecutar a pergunta inteira daria uma comparação "mais completa".
- **Escolha:** o shadow compara escolha de tool e argumentos; para no LLM.
- **Motivo:** é o que muda quando o prompt muda. Repetir a consulta gastaria rate limit do cliente de madrugada sem responder nada de novo.

## D-028 · Latência por etapa vai como metadata, não como span (2026-08-25)
- **Contexto:** o turno mede durações depois do fato, não marcos de relógio.
- **Escolha:** um observation por turno, com a latência de cada etapa em metadata.
- **Motivo:** span com início e fim inventados parece preciso e engana; número em metadata é filtrável e honesto.

## D-029 · As proibições são testes, não parágrafos (2026-08-25)
- **Contexto:** `ORBI-CONVENCOES.md` lista proibições que ninguém relê no meio de uma sessão.
- **Escolha:** `tests/unit/test_architecture.py` verifica cada uma no código.
- **Motivo:** documento não segura arquitetura. Escrever os testes já encontrou uma violação real.

## D-030 · Índice especializado declarado no modelo (2026-08-25)
- **Contexto:** HNSW e GIN trigram existiam só na migration; `alembic check` queria removê-los.
- **Escolha:** declarados também no modelo, com `postgresql_using` e `postgresql_ops`.
- **Motivo:** `alembic check` no CI só vale se modelo e banco descreverem a mesma coisa.

## D-031 · A suíte recusa banco que não seja de teste (2026-08-25)
- **Contexto:** o `conftest` recria o schema do zero; apontar `ORBI_DATABASE_ADMIN_URL` para o banco de desenvolvimento e rodar `pytest` apaga tudo. Aconteceu uma vez durante a construção.
- **Escolha:** a suíte aborta com `UsageError` quando o nome do banco não está na lista de bancos de teste.
- **Motivo:** o custo de errar é destruir dados de produção; o custo da guarda é uma linha de verificação.

## D-032 · Gemini como primário até o primeiro cliente (2026-08-25)
- **Contexto:** o bake-off dos modelos continua pendente e o produto ainda não tem receita; a camada gratuita do Google AI Studio cobre desenvolvimento e PoC.
- **Escolha:** `gemini-2.5-flash` como primário, sem fallback declarado até existir o segundo fabricante; `mode=AUTO` com a lista de tools do papel e execução automática de função desligada no SDK.
- **Motivo:** o Orbi pede uma coisa só do LLM — escolher uma tool de uma lista fechada — e isso o Gemini faz. Trocar de fabricante é uma linha no `.env`, porque tudo passa pelo `LLMPort`.

## D-033 · O orçamento do turno é cobrado por nós, não pela API (2026-08-25)
- **Contexto:** a API do Gemini recusa deadline abaixo de 10 s, e o orçamento do Orbi para o LLM é menor que isso — pedir 4 s devolve 400.
- **Escolha:** enviar à API o menor prazo que ela aceita e cobrar o orçamento real com um vigia próprio, que para de esperar no tempo do turno.
- **Motivo:** sem isso o `Deadline` teria um buraco neste provedor, e buraco no `Deadline` é usuário esperando sem saber até quando.
