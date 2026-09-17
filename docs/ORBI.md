# ORBI — Documento Essencial do Projeto

---

## 1. Ideia central

O Orbi é uma camada de linguagem natural sobre ERPs.

O usuário faz uma pergunta pelo WhatsApp. O LLM interpreta a intenção e escolhe uma operação de
uma lista fechada. O código valida se aquela operação é permitida, descobre a qual entidade o
usuário se referia, chama o ERP e monta a resposta. O ERP continua sendo a fonte de verdade dos
dados transacionais.

A regra central é:

> **O LLM interpreta. O código autoriza, executa e redige.**

Essa formulação é deliberada. O LLM não escreve a resposta final: ela é montada por templates
determinísticos a partir de dados tipados. Isso remove a possibilidade de um número ser alterado
na redação, corta cerca de um segundo do tempo de resposta, reduz o custo por consulta pela
metade e neutraliza a maior parte das tentativas de injeção de instrução.

---

## 2. Problema que o Orbi resolve

As informações já existem no ERP, mas o acesso exige navegação manual, conhecimento da estrutura
do sistema e várias etapas. Na prática, quem está fora do escritório simplesmente liga para
alguém que está dentro.

O Orbi permite perguntar diretamente:

> "Quanto temos de tubo PVC 100?"
>
> "Qual o último pedido da Construtora Silva?"
>
> "Esse cliente tem títulos em aberto?"

e receber uma resposta baseada nos dados atuais do ERP, com controle de permissão por papel e
registro de auditoria.

O desafio real do produto não é responder — é responder **de forma segura, correta e reutilizável
entre ERPs diferentes**, para uma equipe inteira, sem reescrever o sistema para cada cliente.

---

## 3. Posicionamento competitivo

### O concorrente existe e é gratuito

Os principais ERPs em nuvem para PME brasileira já lançaram assistentes nativos de WhatsApp —
gratuitos para toda a base, cobrindo consulta de estoque, saldo bancário, emissão de nota fiscal e
boleto, por texto e áudio.

Isso não é risco futuro. É concorrência ativa, e o documento a registra para que nenhuma decisão
seja tomada ignorando esse fato.

### Onde ele para

Duas limitações definem o espaço do Orbi:

- **Um usuário por aplicativo.** Cada número de celular vincula a um único perfil, e precisa ser o
  mesmo já cadastrado no sistema.
- **É o assistente do dono, não da equipe.** O posicionamento é declarado pela própria empresa:
  quanto menor o negócio, maior o uso, porque há menos segregação de função — muitas vezes o dono
  faz tudo.

O assistente nativo resolve o micro empreendedor que opera sozinho. **Ninguém resolveu a empresa
com equipe.**
Essa é exatamente a definição do ICP do Orbi.

### Os cinco diferenciais

**1. Feito para equipe, não para o dono.**
Doze vendedores, cada um com o próprio número, cada um vendo só o que pode ver.

**2. Permissão por papel e por campo.**
O vendedor consulta preço e não vê custo. O financeiro vê títulos. O dono vê tudo. A filtragem
acontece no código, na saída do Adapter — não em instrução de prompt, que se contorna.

**3. Auditoria de quem perguntou o quê.**
Registro imutável e encadeado por hash, com a versão da política que autorizou cada consulta.
Assistente nativo de ERP não foi feito para prestar essa conta.

**4. Multi-ERP.**
Empresa que cresceu por aquisição costuma ter dois sistemas. Assistente nativo enxerga só o
próprio ERP; uma camada por cima enxerga os dois.

**5. Aprende o vocabulário da empresa.**
"Cano 100", "aquele tubo grosso de esgoto". Cada correção vira alias permanente daquele cliente.
Em alguns meses o Orbi entende a gíria da casa — e isso não se copia, se acumula.

**6. Integração por ERP, não por cliente.**
Quem faz integração sob medida gasta o mesmo tempo no décimo cliente que gastou no primeiro. No
Orbi, o esforço fica no Adapter: o primeiro cliente de um ERP é caro, e do segundo em diante a
implantação leva horas. Detalhado na seção 17.

### A frase de venda

> "Seu ERP te deu um assistente. Ele funciona para você. O Orbi funciona para seus 12 vendedores —
> cada um vendo só o que pode, e você vendo tudo que foi perguntado."

### Gatilho de reavaliação

O fornecedor do ERP pode liberar multiusuário. Mas o cliente médio dele tem três pessoas, e
permissão por papel com auditoria por vendedor não é prioridade de quem atende centenas de
milhares de microempresas.

Se algum fornecedor anunciar multiusuário **com permissão por papel**, o wedge muda de "equipe"
para "multi-ERP + auditoria + vocabulário". Os changelogs dos principais devem ser acompanhados
trimestralmente.

---

## 4. MVP

O MVP é **somente leitura** e tem **um único canal: WhatsApp**.

### Interfaces do produto

**Não existe chat Web.** O WhatsApp Web já é o mesmo WhatsApp no computador, com a mesma conversa
e o mesmo histórico. Construir uma segunda interface de chat seria duplicar templates, testes e
evals para atender um usuário que já tem a tela aberta. É trabalho descartado, não adiado.

**Não existe painel para o cliente.** A administração é feita pela equipe de desenvolvimento por
linha de comando:

| Função | Comando |
|---|---|
| Cadastrar usuário | `orbi user add --tenant x --phone y --role sales_rep` |
| Trocar papel | `orbi user set-role --phone y --role finance` |
| Status do catálogo | `orbi catalog status --tenant x` |
| Forçar sync | `orbi catalog sync --tenant x` |
| Ver correções pendentes | `orbi corrections list` |
| Resolver correção | `orbi corrections resolve --id n --entity 4471` |
| Onboarding completo | `orbi onboard --tenant <slug>` |

Um **Console** — front interno da equipe, com as mesmas funções da CLI em tela — é opcional e não
entra agora. A CLI dá conta enquanto o número de clientes for pequeno, e uma tela que faz o que um
comando já faz não é prioridade. Ele entra quando a operação por comando começar a custar tempo
demais, tipicamente por volta do quarto cliente.

A distinção que importa registrar: Console interno resolve **conforto da equipe**. O que resolve
**dependência do cliente** é um painel self-service para o cliente — coisa diferente, listada no
futuro (seção 20) e não confundida com o Console.

### Domínios

- estoque
- comercial
- financeiro

### As quatro tools

Nomenclatura em inglês em todo o código.

```
check_stock(product_term, location_term?)
check_price(product_term, customer_term?, quantity?)
list_open_invoices(customer_term)
get_last_order(customer_term)
```

Note que **não existe uma tool de busca de produto**. Se existisse, o LLM precisaria de duas
rodadas (buscar → consultar), custando cerca de um segundo a mais e criando exatamente o loop de
agente que o projeto quer evitar. A resolução de entidade é uma etapa interna do Runtime.

### Fora do MVP

- painel de qualquer tipo
- escrita no ERP
- SQL gerado pelo LLM
- acesso direto do LLM ao banco
- multiagente no Runtime
- modelo próprio, fine-tuning, RAG documental
- Redis, filas, workers, infraestrutura distribuída

---

## 5. Arquitetura principal

### Runtime (caminho da requisição)

```text
CHANNEL PORT  (WhatsApp)
      ↓
IDENTITY      (tenant + user + role)
      ↓
PROMPT BUILDER
      ↓
LLM PORT      (function calling)
      ↓
POLICY LAYER  (deny by default)
      ↓
ENTITY RESOLUTION
      ↓
ERP INTERFACE
      ↓
ERP ADAPTER   (capabilities · official API only)
      ↓
ERP
      ↓
FIELD POLICY
      ↓
RESULT RENDERER  (template determinístico)
      ↓
AUDIT + TRACING
```

A **Field Policy** aparece na saída do Adapter porque filtrar custo e margem é decisão de código,
não instrução de prompt. O **Result Renderer** substitui a redação pelo LLM.

### Discovery (offline, fora do caminho da requisição)

```text
DISCOVERY RUN
      ↓
ANALYST     → surface map + deep model
      ↓
VALIDATOR   → confiança campo a campo
      ↓
KNOWLEDGE MODEL v.N
      ↓
DIFF REVIEW HUMANO
      ↓
ACTIVATE
```

---

## 6. Runtime — fluxo completo

### 6.1 Usuário pergunta

> "Quanto tem de tubo PVC 100?"

### 6.2 Canal recebe

O `ChannelPort` abstrai o canal com três operações: `send_text`, `send_options` e `on_inbound`.

`send_options` é desenhado desde já **prevendo botões nativos**, mesmo que o WhatsApp use lista
numerada por enquanto. Isso importa porque Slack, Telegram e Discord — os próximos canais —
suportam botão, e a desambiguação vira um toque em vez de digitar "2".

Cada tenant tem **um número próprio**: a identificação fica determinística e o risco de banimento
fica isolado — um cliente com problema não derruba os outros.

A janela de 24 horas do WhatsApp não é obstáculo no fluxo normal: a pergunta do usuário abre a
janela e a resposta cabe dentro dela. Templates aprovados pela Meta só são necessários para
mensagem proativa, que aparece com o Plantão — mas convém cadastrar um genérico cedo, porque a
aprovação demora.

### 6.3 Identificação

```text
tenant  ← número de destino
user    ← número de origem (via user_identities)
role    ← vínculo do usuário
```

**Cadastro prévio é obrigatório.** Número desconhecido recebe recusa genérica, entra em rate limit
agressivo e gera alerta — nunca revela se o número existe no sistema. Reciclagem de número é
tratada por re-verificação com código; inatividade de 90 dias exige re-verificação.

Esses dados ficam em PostgreSQL, não em memória de processo. Cache em memória quebra a cada
deploy e prende a aplicação a um único worker — e continua sem exigir Redis.

### 6.4 Montagem do prompt

Duas camadas:

- **Prefixo estático**, idêntico entre requisições do mesmo tenant: regras, schemas das tools,
  glossário. Vai primeiro, para aproveitar prompt caching.
- **Sufixo dinâmico**: data/hora, papel, slots de contexto, últimos 5 turnos, pergunta atual.

Texto do usuário **nunca** entra no system prompt. As tools são filtradas por papel **na
montagem** — o modelo nunca vê uma tool que não pode chamar.

Antes de qualquer envio, o `PIIRedactor` mascara CPF, CNPJ, telefone e e-mail. Como o LLM não
precisa de identificador para nada (ver 6.6), mascarar não custa capacidade alguma. O que sai é
"quanto tem de tubo pvc 100".

O `prompt_version` é gravado na auditoria. Sem isso, comparar resultados de eval entre semanas
vira adivinhação.

### 6.5 Function Calling

`tool_choice=auto`, **uma tool por turno**, parallel tool calls desligado. A resposta é
normalizada em um `ToolCallEnvelope` próprio, o que desacopla o Runtime do formato de cada
provedor e torna o failover viável.

Se o modelo responder texto em vez de tool: uma única re-tentativa pedindo esclarecimento; se
falhar de novo, mensagem determinística de fora de escopo. Nunca um segundo loop.

### 6.6 Contrato de argumentos

Regra estrutural, aplicada por tipo e não por convenção:

> **O LLM nunca emite identificadores.** Só escreve o que a pessoa falou, em palavras.

```python
class EntityTerm(str):
    # rejeita dígitos puros com 4+ caracteres, CPF/CNPJ,
    # UUID e padrões de código interno
```

Modelo alucina ID com naturalidade, e um ID alucinado passa por qualquer validação de tipo comum.
Com `EntityTerm`, `product_term="4471"` é rejeitado pelo Pydantic antes de qualquer execução.
Todos os schemas usam `strict=True` e `extra="forbid"`.

### 6.7 Tool Registry

Cada tool é declarada uma única vez, em um `ToolSpec`, que gera cinco artefatos:

1. JSON Schema enviado ao LLM
2. modelo Pydantic de validação
3. registro na tabela `tools`
4. fixture do eval set
5. template de resposta

Um teste de CI falha se algum artefato estiver dessincronizado. A causa mais comum de bug nesse
tipo de sistema é o schema do LLM divergir da validação do backend depois de meses de mudanças.

### 6.8 Policy Layer

Determinística, ordenada, *deny by default*:

```
TenantActive → UserActive → ToolEnabledForTenant
→ ToolAllowedForRole → ArgsValid → RateLimit
```

Permissão efetiva = `tenant_tools ∩ role_tools`. Retorno tipado: `ALLOW` ou `DENY(reason_code)`.

**Field Policy** é a camada que quase todo projeto esquece. Vendedor consulta preço mas não vê
custo nem margem — e essa filtragem acontece na **saída do Adapter**, por whitelist de campos por
`(permissão, tool)` — indexada pela permissão (`price:read_cost`), não pelo nome do papel, para que
cada cliente possa ter os próprios papéis sem tocar nesta camada (D-040). Esconder campo via
instrução de prompt funciona até o dia em que não funciona.

Cada decisão grava o `policy_version_hash`. Meses depois, é possível provar qual configuração
autorizou aquela operação.

Teste obrigatório: matriz combinatória `role × tool × tenant` como golden test.

### 6.9 Entity Resolution

Cascata de quatro estágios, do mais barato ao mais caro:

1. **Alias aprendido** do tenant (`entity_aliases`)
2. **Match exato** de código ou EAN, detectado por regex no termo
3. **`pg_trgm`** sobre o nome, para erro de digitação
4. **Embedding + pgvector**

Resultado: `FOUND`, `AMBIGUOUS` ou `NOT_FOUND`.

**Limiares são calibrados por tenant, não fixos no código.** No onboarding, o sistema gera um
conjunto de teste sintético a partir do catálogo real do cliente: para cada produto amostrado,
cria variações realistas (nome truncado, abreviado, com erro de digitação, sem a medida). Como a
resposta correta é conhecida por construção, é possível varrer os pares de limiar sem depender de
dado rotulado à mão.

O critério de otimização é assimétrico, e essa é a decisão central:

> Responder a entidade errada com confiança é um erro grave. Perguntar "qual desses?" é um custo
> pequeno.

Maximiza-se o acerto sujeito a **erro silencioso ≤ 1%**, empurrando a incerteza para `AMBIGUOUS`.
Ponto de partida da varredura: top1 ≥ 0.82 e gap para o top2 ≥ 0.05. Os valores finais ficam em
`tenant_settings`, com recalibração automática quando o catálogo muda mais de 20%.

### 6.10 Desambiguação e aprendizado

`AMBIGUOUS` devolve três opções numeradas e grava uma pendência com TTL de 10 minutos. O usuário
responde "2" e o Runtime resolve sem nova chamada ao LLM.

`NOT_FOUND` sugere o mais próximo e pede o código.

O alias **não** é gravado na primeira escolha: entra com `confidence=low` e só é promovido a
`confirmed` após dois usos bem-sucedidos sem correção — e a cascata **lê** essa confiança: alias
`low` que discorda do catálogo vira pergunta, não resposta (D-044). Isso evita que um toque errado envenene a
resolução daquele tenant permanentemente.

Esse vocabulário acumulado é dado proprietário e é a defesa competitiva mais durável do produto —
ver seção 3.

### 6.11 ERP Adapter

```python
class ErpAdapter(Protocol):
    def capabilities(self) -> Capabilities: ...
    def get_stock(self, product_id, location_id=None) -> StockResult: ...
    def get_price(self, product_id, customer_id=None, qty=None) -> PriceResult: ...
    def list_open_invoices(self, customer_id) -> list[Invoice]: ...
    def get_last_order(self, customer_id) -> Order | None: ...
    def iter_catalog(self, since=None) -> Iterator[CatalogItem]: ...
```

```python
class Capabilities:
    integration_mode: Literal["official_api"]
    supports_reservations: bool
    supports_multi_location: bool
    supported_tools: set[str]
    ...
```

`integration_mode` hoje aceita um único valor. Ele existe justamente para tornar o princípio da
seção 10 verificável em teste, em vez de boa intenção.

Exceções normalizadas: `ErpTimeout`, `ErpUnavailable`, `ErpAuthError`, `ErpRateLimited`,
`ErpNotFound`. Payload nativo do ERP nunca cruza a fronteira.

`capabilities()` faz o Core desabilitar automaticamente tools que aquele ERP não atende — o que
mata a tentação de espalhar `if erp == "omie"` pelo Runtime.

**Adapter Conformance Kit:** uma suíte pytest compartilhada, com cassettes gravados, que todo
adapter novo precisa passar. Um ERP está pronto quando passa no kit. É isso que impede a
abstração de apodrecer no segundo cliente e transforma "integrar novo ERP" em tarefa estimável.

### 6.12 Semântica de estoque

O Orbi define um conceito canônico e obriga o Adapter a traduzir — e **nunca mente sobre qual
conceito está entregando**:

```python
StockResult(
    physical: Decimal,
    reserved: Decimal | None,
    available: Decimal | None,        # physical - reserved
    basis: Literal["available", "physical"],
    location: str | None,
)
```

Se o ERP informa reservas, responde-se o disponível. Se não informa, `capabilities()` declara isso
e a resposta muda de texto:

> "37 un em estoque **físico** (este ERP não informa reservas)."

O erro clássico desse tipo de produto é responder "37" quando 12 estão comprometidos, o vendedor
prometer, e a confiança acabar ali. A honestidade sobre a base do número custa cinco palavras e
vale o produto inteiro.

### 6.13 Resiliência

Um objeto `Deadline(total_ms=10_000)` desce por todas as camadas; cada estágio consulta
`remaining_ms()` antes de começar. Isso impede o clássico "cada camada tem 5s de timeout e o total
vira 20s".

- **ERP:** timeout 6s, 1 retry apenas em timeout/5xx (leitura é idempotente), backoff com jitter
  limitado ao que sobrou do orçamento.
- **Circuit breaker** por `(tenant, adapter, operation)` — não por adapter inteiro, senão uma
  operação lenta derruba as outras três. 5 falhas em 30s abre por 60s, half-open com uma sonda.
- **Bulkhead:** semáforo por tenant limitando chamadas concorrentes, para que um cliente não
  consuma o rate limit e derrube os demais no mesmo adapter.

Circuito aberto responde honestamente e dispara alerta no canal de ops. **Nunca sirva estoque em
cache** — melhor não responder do que responder errado sobre quantidade.

### 6.14 Resposta

`ResultRenderer` escolhe o template Jinja por `(tool, channel, locale)` e monta a resposta a
partir do DTO tipado, já filtrado pela Field Policy.

Toda resposta mostra **qual entidade foi usada** — o *resolution receipt*:

```
Tubo PVC Esgoto 100mm (4471) — 37 un disponíveis
  Matriz 25 · Filial Cambé 12
```

Isso transforma erro silencioso em erro visível e corrigível: o usuário responde "não é esse" e o
sistema ganha um dado de treino em vez de perder confiança.

Multi-depósito é progressivo:

| Situação | Resposta |
|---|---|
| 1 local com saldo | só o total |
| 2 ou 3 locais | total + quebra inline |
| 4 ou mais | total + os 2 maiores + "e outros N locais" |
| `location_term` informado | só aquele local |

`users.default_location_id` é opcional: quando preenchido, a resposta lidera pelo depósito do
próprio vendedor.

O rendering por LLM fica atrás de feature flag (`llm_rendering_enabled=false`), para o dia em que
algum resultado complexo justifique.

---

## 7. Catálogo e pgvector

O Orbi não copia o ERP. Mantém um índice de resolução.

```text
catalog(
  tenant_id, erp_entity_id, entity_type,
  name, code, canonical_name,
  embedding vector(768), model_version,
  search_text tsvector, name_hash,
  active, updated_at
)
```

### Nome canônico

**Não se vetoriza o nome cru.** O `CanonicalNameBuilder` traduz antes:

```
"TB PVC ESG 100MM BR"
        ↓
"tubo pvc esgoto 100 mm branco"
```

Expansão de abreviações (dicionário por tenant, alimentado pelo Discovery), normalização de
unidades, remoção de código interno e ruído. O ganho de qualidade vem daqui, não de dobrar a
dimensão do vetor — catálogo de PME brasileira é abreviado e inconsistente, e é exatamente isso
que quebra a busca semântica.

Embeddings de 768 dimensões: metade do armazenamento e da latência, qualidade equivalente para
nomes curtos. Índice HNSW no vetor, GIN no tsvector, ambos com `tenant_id` no filtro.

### Sincronização

Full noturno + incremental por `updated_at` a cada 4 horas, via cron. O job é idempotente e
retomável, com cursor em `catalog_sync_runs`.

- **Re-embedding só quando `name_hash` muda.** Sem isso, paga-se embedding do catálogo inteiro
  toda noite sem motivo.
- Produto ausente no ERP vira `active=false`, nunca DELETE — a auditoria referencia esses IDs.
- Um sync que alteraria mais de 30% do catálogo **para e alerta** em vez de aplicar: quase sempre
  significa credencial errada ou API devolvendo página vazia.

---

## 8. Core, Adapter e ACL

O **Orbi Core** contém: Runtime, Tool Registry, Policy Layer, Field Policy, Entity Resolution,
multi-tenancy, RLS, auditoria, contexto, observabilidade e rendering.

O **ERP Adapter** contém as particularidades de cada ERP.

A **Anti-Corruption Layer** é o princípio que mantém essas particularidades fora do Core. Na
prática, ela é aplicada por três mecanismos: DTOs tipados na fronteira, hierarquia de exceções
normalizada e o Conformance Kit.

Se um segundo ERP exigir mudanças no Core, a abstração está errada — e o Conformance Kit é o que
detecta isso cedo.

---

## 9. Discovery

Discovery não é Runtime. Roda **offline**, uma vez por ERP, e sua saída é **dado versionado, não
código**. O Runtime consome o Knowledge Model ativo e não sabe que o CrewAI existe.

```text
Analyst   → surface map + deep model
Validator → confiança campo a campo
          → Knowledge Model v.N
          → diff review humano
          → activate
```

### Duas passadas

1. **Surface map** — varre toda a API oficial do ERP e cataloga entidades, endpoints e campos, sem
   profundidade semântica. Execução barata.
2. **Deep model** — só estoque, comercial e financeiro: semântica dos campos, unidades,
   relacionamentos, confiança por campo.

O mapa raso justifica seu custo por três razões: vira o roadmap do que dá para lançar em seguida
com esforço conhecido, vira argumento concreto de venda, e evita que a segunda rodada de Discovery
comece do zero.

### O que torna o Discovery valioso

- **Structured output com confiança campo a campo.** O Validator não dá nota geral. A revisão
  humana olha apenas o que está abaixo do limiar — minutos, não horas.
- **Gera as seed questions do eval set.** Ao mapear os domínios, o Analyst produz 30 a 50
  perguntas realistas com tool e argumentos esperados. As camadas L1 e L2 do eval nascem junto com
  o modelo.
- **Alimenta o `capabilities()` do Adapter** e o dicionário de abreviações do
  `CanonicalNameBuilder`.

### Versionamento

Linhas imutáveis + ponteiro `active_version_id` por tenant. Rollback é um UPDATE no ponteiro. O
diff da versão candidata contra a ativa é revisado antes de ativar: o humano aprova a mudança, não
o documento inteiro.

Orçamento fixo de tokens e tempo por execução, logado no Langfuse como qualquer outra chamada.

---

## 10. Integração com ERP

### Princípio

> **O Orbi integra apenas por interfaces oficiais do ERP.** Não acessa banco de dados, não instala
> agente no servidor do cliente e não faz scraping.

Isso não é limitação — é o padrão do mercado e um argumento de venda:

> "Conectamos pelo canal oficial do seu ERP, com as credenciais que você autoriza. Não acessamos
> seu banco, não instalamos nada no seu servidor e não guardamos cópia dos seus dados
> transacionais — só um índice de nomes para entender suas perguntas."

O motivo técnico é que ler direto do banco pula as regras de negócio do ERP: um campo
`qtd_estoque` pode não ser o estoque que o sistema mostra na tela. O motivo contratual é que
muitos fornecedores anulam suporte nesse cenário. E o motivo de dados é o excesso: pela API você
recebe o que pediu; pelo banco, recebe folha de pagamento junto.

### Aplicação prática

- `capabilities().integration_mode` declara o modo e hoje só aceita `"official_api"`
- O Conformance Kit valida isso em teste
- **"ERP com interface oficial documentada" é critério de qualificação de cliente**, ao lado de
  porte e segmento

### Quando o cliente for diferente

| Perfil | Caminho |
|---|---|
| ERP em nuvem com API REST documentada | API oficial. É o MVP. |
| ERP com API paga | Muitos "ERPs sem API" têm módulo de integração cobrado à parte, e o cliente não sabe. Verificar antes de descartar. |
| ERP com webhooks | Melhor ainda: o ERP avisa quando muda. Entra depois do MVP. |
| ERP instalado no servidor do cliente (legado) | Réplica de leitura com usuário restrito e semântica mapeada pelo Discovery. Fora do escopo até o 10º cliente. |
| Sem interface | Recusar. Um cliente assim consome mais tempo que os outros dez somados. |

---

## 11. Segurança

A regra mais importante:

> **O LLM nunca é a autoridade de segurança.**

Cada conceito tem um mecanismo concreto, não apenas uma intenção:

| Conceito | Mecanismo |
|---|---|
| Authentication | `user_identities` por canal, cadastro prévio obrigatório, re-verificação por código |
| Authorization | Policy Layer deny-by-default, decisão tipada com `reason_code` |
| RBAC | capabilities internas; 3 papéis padrão (`sales_rep`, `finance`, `admin`) e papéis próprios por cliente (D-040) |
| Field-level | Field Policy na saída do Adapter, whitelist por `(permissão, tool)` |
| Tenant isolation | `tenant_id` em tudo + RLS + canary tenant no CI |
| RLS | Role sem `BYPASSRLS`, `FORCE ROW LEVEL SECURITY`, `SET LOCAL app.tenant_id` |
| Secrets | Credenciais de ERP cifradas na aplicação, chave fora do banco |
| Least privilege | Tools filtradas por papel antes de chegarem ao modelo |
| Audit logging | Append-only com encadeamento de hash |
| Prompt injection | Saída restrita a conjunto fechado + rendering determinístico + content firewall |
| Data minimization | `PIIRedactor` antes do prompt; payload do ERP guardado como hash |
| LGPD | Papel de operador, DPA, retenção zero no provedor, `erase_user_data` |

### Papéis

```
sales_rep  → check_stock, check_price, get_last_order
finance    → list_open_invoices, get_last_order, check_price (com custo)
admin      → tudo
```

O cliente enxerga três nomes simples. Internamente, cada papel é um preset sobre capabilities
(`stock:read`, `price:read`, `price:read_cost`, `invoice:read`, `customer:read`). Quando um tenant
pedir "meu gerente vê tudo menos custo", isso é uma linha de configuração — não um deploy.

O que separa `check_price` de `check_price com custo` é a Field Policy, não uma tool diferente.

### RLS na prática

Uma **única porta de entrada**: uma dependency do FastAPI é o único caminho para obter sessão, e
ela emite `SET LOCAL app.tenant_id` na transação. Um lint de import proíbe usar o engine
diretamente.

O banco de testes tem um **canary tenant** com linhas envenenadas. Uma fixture global inspeciona
todo resultado de query em todos os testes e falha o build se qualquer linha do canário aparecer.
Não é preciso lembrar de escrever o teste de vazamento: qualquer teste vira teste de vazamento.

### Prompt injection

Três barreiras, sendo a primeira a que realmente resolve:

1. **Estrutural.** A saída do LLM só pode ser uma tool de um conjunto fechado, com argumentos
   validados, sem identificadores. O pior caso de uma injeção é uma tool errada — que a Policy
   Layer ainda precisa autorizar. Não existe caminho de "o modelo foi convencido a vazar dados",
   porque o modelo nunca teve acesso a eles.
2. **Rendering determinístico.** O resultado do ERP não volta ao LLM. Nome de produto malicioso é
   texto renderizado, não instrução.
3. **Content firewall no sync.** Nomes com padrão de instrução ("ignore", "system:",
   delimitadores) entram sinalizados e ficam fora do índice até revisão.

### Auditoria

`audit_logs` particionada por mês, append-only (UPDATE e DELETE revogados no banco). Cada linha
carrega o `prev_hash` da anterior daquele tenant e seu próprio `row_hash` — adulteração vira
detectável com um scan.

Registra: `trace_id`, tenant, usuário, texto original, tool, argumentos, entidade resolvida,
decisão da policy, `policy_version_hash`, `prompt_version`, latências por etapa, status, **hash**
do payload do ERP e apenas os campos-chave. Payload completo é passivo de LGPD sem contrapartida.

### LGPD

O Orbi é **operador**; o cliente é controlador. Isso exige DPA no contrato, lista pública de
subprocessadores e retenção zero contratada com o provedor de LLM — este último é requisito
eliminatório na escolha do provedor.

`erase_user_data(user_id)` anonimiza o texto original preservando as métricas: atende à LGPD sem
destruir a base de evals.

---

## 12. Stack

### Backend
- Python · FastAPI · Pydantic (`strict`)
- CLI de operação (`orbi ...`) — é a interface de administração

### Banco
- PostgreSQL + pgvector (HNSW) + pg_trgm
- SQLAlchemy · Alembic

### IA
- Dois provedores de LLM, **de fabricantes diferentes**, atrás de um `LLMPort` próprio
- Function Calling nativo · Structured Output no Discovery
- Embedding multilíngue de 768 dimensões

O primário é escolhido por **custo por resposta correta**, não por preço de token:

```
custo_por_acerto = custo_médio_do_turno / taxa_de_acerto (L1+L2)
```

Um modelo 40% mais barato que erra a tool 8% mais vezes é mais caro na prática, porque cada erro
vira desambiguação, retrabalho e ticket de suporte. O segundo colocado — obrigatoriamente de outro
fabricante, para não haver correlação de queda — vira o fallback, com failover automático
respeitando o `Deadline`.

**Ambos rodam no eval do CI.** Failover que nunca foi avaliado é failover que degrada
silenciosamente a qualidade exatamente no dia do incidente.

### Discovery
- CrewAI · Analyst · Validator

### Observabilidade
- Langfuse
- Canal de ops no WhatsApp (alertas + resumo diário)

### Canal
- Meta WhatsApp Cloud API direta, um número por tenant

BSP resolve problemas que o MVP não tem (caixa compartilhada, transbordo humano, múltiplos
atendentes) e cobra por isso desde o primeiro dia. O gatilho de migração fica definido desde já:
migra-se quando **qualquer** um ocorrer:

- mais de 10 tenants ativos
- necessidade real de transbordo para atendente humano
- segundo incidente de qualidade ou banimento de número

### Infraestrutura
- Docker · Docker Compose · VPS em **São Paulo** · cron

Hospedagem no Brasil por três razões, em ordem: latência até o ERP e até a Meta; a conversa de
LGPD fica trivial em vez de exigir explicação sobre transferência internacional; e a diferença de
custo em escala de MVP é irrelevante perto do tempo gasto justificando o contrário.

### Backup e DR
- `pg_dump` diário + WAL archiving contínuo, **em provedor diferente do da aplicação**
- RPO 15 min · RTO 4h, declarados e não implícitos
- Job mensal automatizado que sobe container limpo, restaura, roda smoke tests e falha
  ruidosamente

Backup no mesmo fornecedor da aplicação não protege contra conta suspensa, incidente de
faturamento ou falha regional. Backup nunca restaurado não é backup.

---

## 13. Documentação operacional

O projeto será desenvolvido em grande parte por vibe coding. Isso torna a documentação parte da
arquitetura, não um acessório: sem ela, o modelo esquece a decisão da semana passada e reinventa.

Todos os arquivos usam o prefixo `ORBI-`.

```
/docs
  ORBI.md                    este documento — o que o projeto é
  ORBI-RESUMO.md             versão curta, para abrir sessão de desenvolvimento
  ORBI-CONVENCOES.md         nomenclatura, estrutura de pastas, proibições explícitas
  ORBI-DECISOES.md           registro de decisões: contexto, escolha, motivo, data
  ORBI-TENANT.md             criar, ativar, desativar cliente
  ORBI-USUARIOS.md           cadastro, vínculo de número, papel, re-verificação
  ORBI-CATALOGO.md           sync, re-embedding, alias, catálogo travado
  ORBI-ERP.md                credenciais, capabilities, circuito aberto, ERP fora do ar
  ORBI-INCIDENTES.md         LLM caiu, ERP lento, suspeita de vazamento
  ORBI-IMPLANTACAO.md        roteiro de onboarding cronometrado
  ORBI-OBSERVABILIDADE.md    onde olhar, o que cada alerta significa
```

Dois merecem atenção especial:

- **`ORBI-CONVENCOES.md`** precisa de uma seção de **proibições escritas como regras**, não como
  preferências. Sem isso, o modelo criará tool de busca de produto, deixará o LLM redigir a
  resposta final e cacheará estoque.
- **`ORBI-DECISOES.md`** é o que mais economiza tempo. Cada decisão em três linhas. Quando surgir a
  proposta de cachear estoque pela quarta vez, aponta-se a linha em vez de argumentar de novo.

---

## 14. Observabilidade

Um `trace_id` por turno, propagado via `contextvars`, compartilhado entre Langfuse e `audit_logs`.
Um problema relatado no WhatsApp vira investigação em segundos.

Spans: `channel → llm → policy → resolution → erp → render`.

Métricas: latência por etapa (não só total), tokens, custo, erros, taxa de `AMBIGUOUS`, taxa de
correção.

### Canal de ops

Como não há painel, a observabilidade é a interface de operação. Um número interno de WhatsApp
recebe:

**Alertas imediatos:** ERP fora do ar, circuito aberto, sync falhou ou variou mais de 30%, 👎 de
usuário, tenant sem atividade há 24h, número desconhecido tentando acesso.

**Resumo diário:** volume de perguntas por tenant, taxa de ambiguidade, p50 e p95, custo do dia,
cinco termos mais buscados sem resultado.

Os termos sem resultado são a lista de trabalho: cada um vira um alias ou uma correção de nome
canônico.

Para tenants em piloto, um modo debug adiciona o código curto do trace no rodapé da mensagem. O
usuário reclama citando o código e a investigação já começa pronta.

### Evals em camadas

| Camada | O que mede |
|---|---|
| L1 | seleção de tool |
| L2 | argumentos |
| L3 | resolução de entidade |
| L4 | end-to-end com ERP mockado e fixtures golden |
| L5 | adversarial: injeção, escalada de privilégio, fora de escopo |

Roda no CI e bloqueia merge em caso de regressão. Cada execução grava `prompt_version`, modelo e
scores, formando uma tabela de regressão histórica.

**Shadow evals:** 5% das perguntas reais, anonimizadas, são reexecutadas à noite contra a versão
candidata do prompt. A regressão aparece antes do usuário, e o eval set cresce sozinho.

### Loop de feedback

Reação 👍/👎 no WhatsApp chega por webhook e é gravada contra o `trace_id`. O negativo entra na
fila de correções (`orbi corrections list`) junto com o trace completo, e é classificado como:
erro de tool, erro de resolução, erro de dado do ERP, ou expectativa incorreta.

Erro de resolução vira alias. Erro de tool vira caso no eval set. Erro de dado vira conversa com o
cliente.

---

## 15. Performance

Meta: **2 a 4 segundos**, acompanhando p50 e p95. É uma meta de engenharia a ser medida, não uma
promessa antes dos testes.

Orçamento explícito por etapa:

```
channel      200 ms
llm          800 ms
policy         5 ms
resolution    60 ms
erp        500–2000 ms
render        50 ms
```

O caminho é curto e single-shot. Nada de loops de agente no Runtime.

Três decisões que compram tempo diretamente:

- **Rendering determinístico** economiza a segunda chamada ao LLM (~1s)
- **Prompt caching** no prefixo estático reduz latência e custo
- **Hospedagem no Brasil** encurta a ida ao ERP e à Meta

Acima de 2,5s, dispara mensagem intermediária ("consultando o ERP..."). Percepção de velocidade
vale tanto quanto o número — e a mensagem ainda mantém a janela de conversa ativa.

---

## 16. Banco e multi-tenancy

```text
um PostgreSQL + tenant_id + RLS
```

### Tabelas

```text
tenants
tenant_settings          -- limiares de resolução calibrados
users
user_identities          -- multi-canal, verificação
roles / capabilities
tools
tenant_tools
role_tools
erp_connections          -- credenciais cifradas, chave fora do banco
knowledge_models
catalog
entity_aliases           -- vocabulário aprendido
catalog_sync_runs
pending_resolutions      -- desambiguação com TTL
conversation_contexts
audit_logs               -- particionada, append-only
```

### Contexto

```text
conversation_contexts(
  tenant_id, user_id, channel,
  last_product_id, last_customer_id, last_tool,
  recent_turns jsonb, expires_at
)
```

Guardar **slots resolvidos**, e não apenas mensagens, é o que faz "e o preço dele?" funcionar de
forma determinística: o Runtime preenche `product_term` a partir do slot em vez de deixar o LLM
adivinhar. TTL deslizante de 15 minutos.

### Migrations

Alembic desde o primeiro commit. `alembic check` no CI. Migration aplicada nunca é editada.
Políticas RLS versionadas como migration explícita — política de segurança precisa de histórico.

### Retenção

| Dado | Prazo |
|---|---|
| `audit_logs` | 12 meses |
| traces | 90 dias |
| contexto | 30 dias |

Particionamento mensal: expurgo é `DROP PARTITION`.

---

## 17. Produto e operação

### ICP — definido por condição, não por segmento

Um cliente serve para o Orbi quando as três condições valem ao mesmo tempo:

1. **Usa um ERP com interface oficial documentada.** Sem isso não há integração possível dentro do
   princípio da seção 10.
2. **Mais de uma pessoa precisa consultar o ERP.** Se só o dono consulta, o assistente nativo do
   próprio ERP já resolve de graça.
3. **Alguém pergunta de fora do escritório.** Vendedor externo, técnico em campo, comprador em
   feira, gestor viajando. É essa distância que cria a dor.

Segmento é consequência, não critério. Distribuidor e atacadista costumam preencher as três
condições com folga — catálogo grande, equipe na rua, perguntas de estoque e crédito o dia inteiro
— e por isso são o ponto de partida natural. Mas comércio de materiais, autopeças, food service,
representação comercial e prestadores com equipe em campo preenchem igual.

Fica de fora: empresa de três pessoas (condição 2 falha), ERP legado sem API (condição 1 falha), e
operação 100% interna sentada na frente do sistema (condição 3 falha).

### Odoo — ERP de testes e adapter de referência

Antes do primeiro cliente, o desenvolvimento roda contra uma instância própria de **Odoo
Community** em Docker, com os dados de demonstração que já vêm com ele. Não se constrói um ERP:
sobe-se o Odoo, carrega-se a demo e ajustam-se alguns nomes de produto para ficarem abreviados
como catálogo brasileiro real (`TB PVC ESG 100MM BR`). **Máximo 2 a 3 dias.** Se passar disso, o
foco saiu do produto.

Isso resolve três problemas de uma vez: desenvolver sem depender de credencial de cliente, não
consumir rate limit de API de terceiros, e testar cenários que nenhum cliente vai fornecer —
produto zerado, nome duplicado, catálogo de 20 mil itens, sync interrompido no meio.

O `OdooAdapter` **não é descartável**. Ele vira o adapter de referência permanente:

- É contra ele que o Conformance Kit roda no CI, indefinidamente
- É ele que permite testar o Runtime inteiro sem tocar em API externa
- E, principalmente: construí-lo **antes** do ERP de produção testa a abstração antes do primeiro
  cliente

O princípio 19 diz que o segundo ERP é o teste da qualidade da abstração. Com o Odoo, esse teste
acontece na semana 3 em vez de no quinto cliente — quando corrigir ainda é barato.

A API do Odoo é XML-RPC, bem diferente das REST usadas pelos ERPs em nuvem brasileiros. Isso é
vantagem: se o `ErpAdapter` acomoda os dois estilos, acomoda quase qualquer coisa. Se não
acomodar, o problema aparece agora.

### Primeiro ERP

A escolha é por critério, não por marca. O primeiro ERP de produção deve ter, nesta ordem:

1. **API REST oficial e documentada**, com autenticação por token gerado pelo próprio cliente
2. **Base grande de PME brasileira** — quanto mais clientes rodam o mesmo ERP, mais o Adapter se
   paga
3. **Módulo financeiro relevante**, porque duas das quatro tools dependem dele
4. **Limites de requisição compatíveis** com a meta de latência — verificar antes de decidir

O segundo ERP não é opcional no médio prazo: é o teste real da qualidade da abstração, e quanto
mais diferente do primeiro, melhor o teste.

### A economia da integração

O custo de integração do Orbi é **por ERP, não por cliente**. Essa é a diferença estrutural em
relação a quem faz integração sob medida.

```
Sob medida:  cliente 1 = semanas · cliente 2 = semanas · cliente 10 = semanas
Orbi:        ERP 1 = semanas · cliente 2 = horas · cliente 10 = horas
```

| O que | Custo | Automatizado? |
|---|---|---|
| Cliente novo em ERP já suportado | 2–4 horas | Sim — `orbi onboard` faz tudo |
| ERP novo | dias a semanas | Não. O Discovery ajuda muito, mas o Adapter é código escrito |
| Domínio novo em ERP já suportado | horas a dias | Parcial |

Três peças do projeto existem para sustentar isso: o comando `orbi onboard`, o par
Adapter + `capabilities()` (o Core não sabe qual ERP está falando) e o Conformance Kit (ERP novo
só entra quando passa na suíte, o que transforma integração em tarefa estimável).

**O que dizer na venda, e o que não dizer.** Nunca prometer "integração automática" — é falso e
quebra na primeira semana. A formulação correta é:

> "A integração é feita uma vez por ERP, não uma vez por cliente. Do segundo cliente em diante,
> colocar no ar leva horas."

Ressalva honesta: isso vale para ERP com interface oficial. Sistema legado continua caro — para o
Orbi e para todo mundo.

### Implantação

Onboarding é um comando, não um documento:

```
orbi onboard --tenant <slug>
```

Executa em sequência: valida credenciais → consulta `capabilities()` → sync inicial do catálogo
com relatório → importa usuários e papéis de CSV → **calibra os limiares de resolução** → roda um
acceptance eval de 20 perguntas escritas pelo próprio cliente contra os dados reais dele → emite
relatório de aprovação.

O relatório final é o artefato que se mostra ao cliente para dizer "está pronto". Implantação
artesanal de três dias mata a margem antes do primeiro problema.

Warm-up do número de WhatsApp é etapa cronometrada do onboarding, não surpresa da semana de
go-live.

O roteiro completo vive em `ORBI-IMPLANTACAO.md`.

### Suporte

Horário comercial, SLA de 1 dia útil, sem desenvolvimento sob demanda incluso. No contrato.

Como não há painel, **toda mudança de configuração passa pela equipe**. Isso é aceitável enquanto
o número de clientes for pequeno, e é o que o painel self-service (seção 20) resolve quando o
volume justificar.

### Critério de validação

- 3 tenants pagantes
- ≥ 60% dos usuários cadastrados ativos por semana
- ≥ 85% das perguntas resolvidas sem intervenção humana
- p95 < 6s
- zero vazamento entre tenants, no CI e em produção
- nenhum churn em 3 meses

---

## 18. Plano de execução e economia

### As cinco etapas

| Etapa | Duração | Custo/mês | Resultado |
|---|---|---|---|
| 1. Odoo + fundação | 2–3 semanas | R$ 200–400 | Runtime completo contra ERP de teste |
| 2. Adapter do 1º ERP de produção | 1–2 semanas | R$ 200–400 | Passa no Conformance Kit; abstração validada |
| 3. PoC com cliente | 3 semanas | R$ 200–370 | 20 casos escritos por ele, passando |
| 4. Primeiro contrato | — | R$ 250–470 | R$ 350/mês, preço de fundador, 12 meses |
| 5. Do 2º cliente em diante | 4h cada | +R$ 120–250 | R$ 890/mês, preço cheio |

**Reserva necessária:** R$ 2.500–4.000 cobre os primeiros oito meses de infraestrutura com folga.

### PoC gratuita — as quatro regras

A PoC é gratuita porque ninguém assina contrato para um sistema que fala com o próprio ERP sem ver
funcionando. Mas PoC sem regra vira piloto eterno, e piloto eterno vira usuário que nunca paga.

1. **Prazo fixo de 3 semanas**, escrito antes de começar. Sem "vamos ver como vai".
2. **Escopo fechado:** três usuários, quatro tools, um ERP. Pedido fora disso entra na lista do
   plano pago.
3. **Critério de sucesso definido pelo cliente, antes de começar.** Ele escreve 20 perguntas reais
   que quer ver funcionando, e elas viram o acceptance eval do `orbi onboard`. Se passarem, ele não
   tem argumento para dizer "não me convenceu" — o critério foi dele.
4. **Carta de intenção assinada.** Uma página: se os 20 casos passarem, contrato de 12 meses a
   R$ X. Sem isso, entrega-se valor por três semanas e recebe-se "vou pensar".

O primeiro cliente paga com **desconto de fundador**, não de graça: R$ 350/mês por 12 meses em
troca de ser referência e dar depoimento. Cliente que paga pouco continua sendo cliente; cliente
que paga zero é usuário, e usuário não cancela — ele só some.

### Custo de infraestrutura por fase

| Fase | Situação | Custo/mês |
|---|---|---|
| 0 | Odoo local, sem cliente | R$ 200–400 |
| 1 | PoC com 1 cliente | R$ 200–370 |
| 2 | 1 cliente pagante | R$ 250–470 |
| 3 | 10 clientes | R$ 1.200–2.500 |

Composição: VPS em São Paulo (Docker Compose, Postgres e Langfuse na mesma máquina), backup em
provedor diferente, LLM e embeddings. **Custo por cliente em escala: R$ 120–250/mês.**

**O número de WhatsApp é do cliente**, não do Orbi — custo zero para a operação e melhor posição
na LGPD.

Sobre a tarifa da Meta, um ponto que exige atenção imediata: até setembro de 2026, respostas em
texto livre dentro da janela de 24 horas aberta pelo usuário são gratuitas — exatamente o fluxo do
Orbi. **A partir de outubro de 2026 a Meta volta a cobrar essas mensagens**, à mesma tarifa por
mensagem dos templates de utilidade em cada país. A página oficial da Meta confirma a data e que
o preço é o de utilidade; para o Brasil o rate card BRL indica R$ 0,0350/mensagem, com 1.000 mensagens
de serviço grátis por número/mês segundo fontes secundárias (16/09/2026) — reconferir antes de proposta.

Três implicações: o custo por cliente precisa ser recalculado assim que a tabela sair; o teto de
consultas por plano deixa de ser só proteção contra abuso e passa a ser controle de margem; e os
canais alternativos (Telegram, Slack) ganham peso, porque não cobram por mensagem.

### Gatilhos de gasto

Nada sobe por antecipação. Cada aumento tem uma condição medida:

| Gasto | Só quando |
|---|---|
| VPS maior | p95 passar de 6s por CPU, não por lentidão do ERP |
| Postgres gerenciado | backup manual falhar ou o banco passar de 50 GB |
| Redis | contexto no Postgres virar gargalo medido |
| Segunda máquina | um deploy derrubar cliente em horário comercial |
| Console interno | operar por CLI custar mais de 4h/mês |

### Preço

| Plano | Usuários | Mensal |
|---|---|---|
| Essencial | até 5 | R$ 490 |
| Time | até 15 | R$ 890 |
| Operação | até 30 | R$ 1.490 |

Setup de implantação: R$ 1.500–2.500, dispensável nos primeiros clientes em troca de depoimento.

**Cobra-se por usuário, não por consulta** — o custo variável real é suporte, não token. Mas cada
plano tem teto de consultas, só para conter abuso: um cliente entusiasmado sozinho pode dobrar a
conta de LLM.

A justificativa de valor é direta: um distribuidor com 10 vendedores que economizam 20 minutos por
dia deixando de ligar para o escritório recupera mais de 60 horas por mês. Cobrar R$ 890 por isso
é conversa fácil.

### Margem

| Situação | Receita | Custo | Margem |
|---|---|---|---|
| 1 cliente fundador (R$ 350) | R$ 350 | R$ 250–470 | negativa a zero |
| 1 cliente pagante (R$ 890) | R$ 890 | R$ 250–470 | 47–72% |
| 3 clientes | ~R$ 2.200 | R$ 500–800 | 64–77% |
| 10 clientes | ~R$ 7.500 | R$ 1.200–2.500 | 67–84% |
| 20 clientes | ~R$ 15.000 | R$ 2.000–4.000 | 73–87% |

O primeiro cliente não fecha a conta, e não precisa: ele existe para provar. **O segundo cliente a
preço cheio já deixa a operação no azul.** É a economia de integração da seção 17 aparecendo no
resultado — o custo marginal do décimo cliente é praticamente o mesmo do segundo.

### Aquisição de clientes

O Google Maps entrega a lista bruta, mas não mostra **qual ERP a empresa usa** — que é a primeira
condição do ICP. Serve para montar a lista; a qualificação vem de outro lugar.

Cinco fontes, em ordem de eficácia:

1. **Contador local.** Um escritório contábil enxerga o ERP de dezenas de empresas ao mesmo tempo
   e sabe exatamente quem usa o quê. Comissão de indicação de 10–15% do primeiro ano resolve o
   problema de qualificação de uma vez.
2. **Parceiros e integradores dos ERPs em nuvem.** Já vendem, já implantam, já conhecem a base.
   Muitos travam exatamente onde o Orbi resolve. É canal, não concorrente.
3. **Associação comercial e sindicatos do atacado.** Acesso a listas e a eventos onde o dono está
   presente e receptivo.
4. **LinkedIn por cargo, não por empresa.** Gerente e supervisor comercial: é quem sente a dor e
   quem convence o dono.
5. **Prospecção pelo próprio WhatsApp.** Vender um produto de WhatsApp prospectando por WhatsApp é
   demonstração, não coincidência.

**A oferta que abre porta:**

> "Me dá acesso de leitura ao seu ERP por uma semana. Em 4 horas seus 3 vendedores estão
> perguntando estoque e preço pelo WhatsApp. Se não servir, desligo e não custou nada."

Funciona porque é verdade — `orbi onboard` roda em horas. É a economia de integração virando
argumento de venda.

---

## 19. Trajetória do produto

```text
FASE 1  →  pergunta e resposta · WhatsApp · síncrono · somente leitura
           ↳ é aqui que o produto vive, cresce e amadurece

Futuro  →  Orbi Plantão · Modo Analista · novos canais
```

### A Fase 1 é o produto, não um degrau

O erro mais provável deste projeto não é técnico: é correr para a fase seguinte antes de esgotar a
atual. Enquanto houver cliente a conquistar, tool a adicionar e resolução a melhorar, o retorno de
aprofundar a Fase 1 é maior que o de abrir uma frente nova.

Aprofundar a Fase 1 significa, em ordem de prioridade:

1. **Mais clientes no mesmo ERP.** Custo marginal quase zero, receita cheia. É o movimento de maior
   retorno que existe no projeto.
2. **Mais tools**, puxadas pelo uso real. O relatório diário de termos sem resultado e a fila de
   correções dizem exatamente quais faltam — não é preciso adivinhar. Candidatas naturais: posição
   de pedido, limite de crédito, histórico de compra do cliente, prazo de entrega.
3. **Segundo ERP.** Multiplica o mercado endereçável e valida a abstração de verdade.
4. **Melhor resolução de entidade.** Cada ponto percentual de acerto reduz desambiguação, suporte e
   atrito — e o vocabulário aprendido é o ativo que mais compõe com o tempo.
5. **Novos domínios** dentro do mesmo ERP: compras, logística, fiscal.

Nada disso exige arquitetura nova. Tudo cabe no Runtime que já existe.

### Critérios para abrir a próxima fase

O Plantão só entra quando **todos** valerem:

- Fase 1 estável, com pelo menos 8 a 10 tools cobrindo o dia a dia
- 10 ou mais clientes pagando, com churn próximo de zero
- Clientes pedindo espontaneamente por aviso proativo
- Custo de mensagem proativa do WhatsApp conhecido e coberto pelo preço do plano superior

O Modo Analista entra depois disso, e o gatilho é o próprio uso: quando um mesmo tipo de pergunta
complexa aparecer com frequência e não couber em nenhuma tool.

O princípio que amarra as fases futuras:

> **Agentes ganham tempo, nunca privilégio.**

Todo agente executa com a identidade de uma pessoa real e atravessa a mesma Policy Layer, a mesma
Field Policy e a mesma auditoria de uma pergunta feita no WhatsApp.

### Orbi Plantão

Agentes CrewAI que trabalham enquanto a empresa dorme. De madrugada, sem usuário esperando e sem
orçamento de latência, um time varre o ERP de cada tenant:

- **Estoque** — o que deve furar na semana considerando o giro recente
- **Comercial** — clientes recorrentes que pararam de comprar; pedidos parados em aprovação
- **Financeiro** — títulos vencendo, clientes estourando limite de crédito

Às 7h, cada pessoa recebe **uma mensagem curta, só com o que é dela**:

> **Bom dia, Carlos.**
> 3 clientes seus que compram todo mês ainda não compraram em agosto: Construtora Silva, Maratex,
> JB Materiais.
> Tubo PVC 100 deve furar em ~6 dias no ritmo atual.
> Pedido 8842 (Maratex) está parado em aprovação há 4 dias.

Por que isso importa:

- **Inverte a relação com o produto.** Antes, o Orbi só existe quando alguém lembra de perguntar.
  Um produto que aparece sozinho todo dia às 7h não é cancelado.
- **É onde o assistente nativo não vai.** Briefing personalizado por papel exige exatamente a
  segregação que o concorrente não tem.
- **Não custa latência.** Se um agente levar 40 segundos analisando, ninguém percebe.
- **Reaproveita tudo.** Os agentes chamam as mesmas tools, pelo mesmo Adapter, sob a mesma Policy
  Layer.

O agente que prepara o briefing do Carlos **executa com a identidade do Carlos**. Ele não consegue
ver custo — não por instrução, mas porque a Field Policy bloqueia. Cada consulta gera `audit_log`
com `channel="plantao"`.

Comercialmente, o Plantão é o plano superior: o básico responde perguntas; o Plantão avisa antes
de você perguntar. Requer um template aprovado pela Meta ("resumo diário"), aprovado uma vez.

### Modo Analista

Perguntas complexas não cabem em 2 segundos nem em uma tool. A saída é assumir isso:

> Usuário: "quais clientes do Norte compraram menos que no ano passado?"
> Orbi: "Isso exige cruzar várias consultas. Te respondo em uns 2 minutos, pode ser?"
> *(2 min depois)* "Analisei 34 clientes. 7 caíram mais de 30%: ..."

Por trás, um agente monta um plano, chama tools em sequência e sintetiza. Como o usuário
**consentiu em esperar**, sai-se da corrida de latência.

Só três coisas mudam para o agente: **mais tempo, mais chamadas, mesma permissão.**

Efeito colateral valioso: as perguntas que caem no Modo Analista são o **mapa das próximas tools**.
Se "clientes que caíram de faturamento" aparecer 50 vezes num mês, vira tool síncrona. O produto
passa a dizer o que construir.

---

## 20. O que fica aberto para o futuro

**Canais.** Slack, Telegram e Discord depois do WhatsApp. O `ChannelPort` já existe e o
`send_options` já prevê botões nativos — nesses canais a desambiguação vira um toque em vez de
digitar "2". Telegram é o mais simples (sem janela de 24h, sem custo por conversa) e serve bem para
pilotos; Slack é o canal natural para a equipe de escritório do cliente.

**Console interno.** Front da equipe com as mesmas funções da CLI em tela. Entra quando operar por
comando começar a custar tempo demais, tipicamente por volta do quarto cliente. É conforto da
equipe, não funcionalidade de produto.

**Painel self-service do cliente.** Coisa diferente do Console: é o que tira a equipe do meio de
"entrou um vendedor novo" e "ele não achou o produto X". Escopo mínimo — usuários e papéis, status
do catálogo, e uma fila de correções onde o cliente aponta o produto certo e o Orbi grava o alias.
Entra quando o volume de pedidos de configuração justificar.

**MCP e reposicionamento como camada de acesso governado.** Expor as tools do Orbi como servidor
MCP permitiria que qualquer IA que o cliente já use consulte o ERP dele atravessando a Policy
Layer, a resolução de entidades e a auditoria do Orbi. O Tool Registry já é fonte única, então o
trabalho é de fronteira, não de arquitetura. Fica registrado como destino possível, não como
compromisso.

**Demais itens.** Webhooks do ERP · Router de tools (quando o número de tools crescer) · agentes
especializados por domínio no Discovery · RAG documental · hybrid search · reranking · Redis, filas
e workers · cache distribuído · banco dedicado · BSP de WhatsApp · adapter de réplica de leitura
para ERP legado.

**Fora do horizonte por ora.** Escrita no ERP. Ela muda a natureza do produto e do risco: leitura
errada gera uma pergunta a mais, escrita errada gera um pedido errado no sistema do cliente.
Enquanto houver crescimento disponível dentro da Fase 1, não há motivo para assumir esse risco.

> **Não adicionar tecnologia apenas porque ela existe. Adicionar quando houver um problema real
> que justifique a complexidade.**

---

## 21. O que ainda está pendente

Nenhuma decisão de projeto está em aberto. O que resta são verificações contra a realidade:

| Pendência | Por quê | Quando |
|---|---|---|
| Limites de requisição da API do 1º ERP | Pode invalidar a meta de latência ou exigir cache | Semana 1 |
| **Tarifa de mensagem de serviço do WhatsApp** | A Meta volta a cobrar respostas dentro da janela de 24h a partir de **1º/out/2026**; as tarifas do Brasil saem até **1º/set/2026** — dias. Confirmado em ago/2026. É a maior linha de custo variável por cliente depois da mudança, maior que o LLM | **esta semana** |
| Retenção zero no provedor de LLM | Requisito eliminatório da escolha | Semana 1 |
| Bake-off dos modelos | Os números decidem, não a opinião | Semana 2–3 |
| Limiares calibrados | Precisa de catálogo real | Onboarding do 1º cliente |
| p95 medido de ponta a ponta | Meta de engenharia, não promessa | Após a 1ª integração |
| DPA revisado por advogado | Operador de dados de terceiros | Antes do 1º contrato |

Adiados por decisão de negócio: **preço** e **primeiro cliente**.

---

## 22. Princípios fundamentais

1. **O LLM interpreta; o código autoriza, executa e redige.**
2. **O LLM nunca emite identificadores** — só termos em linguagem natural.
3. **A resposta é determinística**, montada por template a partir de dados tipados.
4. **Na dúvida, pergunta.** Nunca chuta a entidade.
5. **A resposta sempre mostra qual entidade foi usada.**
6. **Nunca mentir sobre a base do número** — disponível é disponível; físico é declarado como
   físico.
7. **O ERP é a fonte de verdade dos dados transacionais.**
8. **O Orbi integra apenas por interfaces oficiais do ERP.** Sem banco, sem agente no servidor,
   sem scraping.
9. O LLM não acessa o banco e não gera SQL.
10. **Segurança não depende do LLM** e não depende de instrução em prompt.
11. **Agentes ganham tempo, nunca privilégio** — sempre com identidade de pessoa real e sob a
    mesma Policy Layer.
12. **Adapters isolam as particularidades dos ERPs**, e o Conformance Kit garante isso.
13. `erp_entity_id` é o identificador usado pelo Orbi; pgvector serve à resolução, nunca a estoque
    ou preço.
14. **Single-shot no Runtime** enquanto for suficiente. Agentes ficam fora do caminho da pergunta.
15. **Observabilidade e evals desde o primeiro dia** — no começo, a observabilidade *é* a interface
    de operação.
16. **Todo erro do usuário é um dado de treino** — alias, eval ou correção.
17. **A decisão fica escrita** em `ORBI-DECISOES.md`, ou será reinventada.
18. Redis, MCP e demais tecnologias entram quando houver necessidade real.
19. **O segundo ERP é o teste da qualidade da abstração.**
20. **O MVP prova o produto antes de aumentar a complexidade.**

---

## 23. Resumo

O Orbi é uma camada de acesso a ERPs por linguagem natural, com segurança e auditoria no código,
feita para **equipes** — não para o dono sozinho, que o assistente nativo do próprio ERP já
atende.

```text
WhatsApp
→ tenant + usuário + papel
→ prompt (PII mascarada, tools filtradas por papel)
→ LLM escolhe uma tool de um conjunto fechado
→ Policy Layer autoriza
→ Entity Resolution descobre a entidade (alias → código → trigram → vetor)
→ ERP Adapter consulta o ERP pela API oficial
→ Field Policy filtra os campos
→ template monta a resposta
→ audit + tracing
```

E, separadamente, offline:

```text
Discovery → Analyst → Validator → Knowledge Model → aprovação humana
```

O LLM entende a linguagem — e só isso. O código controla autorização, execução e redação. O
pgvector identifica entidades a partir de nomes canônicos, não de nomes crus. O Adapter traduz a
operação para cada ERP, pela porta da frente, e declara o que sabe fazer. O ERP fornece o dado
atual. O PostgreSQL guarda configuração, isolamento, auditoria, contexto, vocabulário aprendido e
o índice de resolução.

O projeto começa pequeno e deliberadamente estreito: um canal, um ERP, quatro operações, três
papéis, nenhuma tela. O crescimento acontece **dentro da Fase 1** — mais clientes, mais tools,
mais ERPs — e só depois em frentes novas, sempre puxado por problema real e nunca por tecnologia
disponível.

**A primeira meta continua simples: fazer uma pergunta real, consultar o ERP correto e devolver
uma resposta correta, segura, rápida e rastreável.**
