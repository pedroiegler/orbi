# ORBI — Configuração

Toda variável do `.env`, o que ela faz, quando você precisa mexer e o que quebra
se estiver errada. Também lista o que **ainda não existe** e vai aparecer aqui
quando o produto crescer.

O arquivo `.env` fica na raiz, é lido na subida e **nunca vai para o git**
(`.gitignore`). O `.env.example` mostra a forma sem os valores.

Depois de mexer em qualquer coisa: `orbi doctor`.

Um teste garante que toda variável do código aparece no `.env.example` — variável
que existe no código e não no exemplo é variável que ninguém sabe que existe.

---

## Aplicação

### `ORBI_ENV`
`development` | `test` | `staging` | `production`

Muda o comportamento em três pontos: em `production` a aplicação **não sobe** com
configuração insegura, o provedor `rule_based` e o adapter `memory` são recusados,
e o webhook exige assinatura.

Deixe `development` na sua máquina. Só a VPS usa `production`.

### `ORBI_LOG_LEVEL`
`INFO` no dia a dia. `DEBUG` mostra muita coisa e é para investigar incidente.

### `ORBI_TIMEZONE` e `ORBI_LOCALE`
Fuso e idioma da **resposta ao usuário**. As datas ficam sempre em UTC no banco e
são convertidas na renderização — assim um cliente em outro fuso é uma linha de
configuração, não um problema de dados.

### `ORBI_SECRET_KEY` — a mais importante
Cifra as credenciais de ERP e os tokens de canal dos seus clientes.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Três coisas que você precisa saber:

- **Sem ela**, nenhuma credencial pode ser lida ou gravada. O Orbi falha alto na
  subida, não silenciosamente.
- **Trocá-la** torna ilegível tudo que já foi cifrado. Se precisar rotacionar,
  decifre com a antiga e re-cifre com a nova antes de trocar.
- **Ela fica fora do banco**, de propósito. Quem tiver um dump do seu Postgres
  não tem acesso ao ERP dos seus clientes.

Guarde uma cópia em lugar seguro. Perdê-la significa recadastrar a credencial de
todos os clientes.

---

## Banco

### `ORBI_DATABASE_URL` — o papel da aplicação
Formato: `postgresql+psycopg://usuario:senha@host:porta/banco`

Usa o papel `orbi_app`, que **não tem `BYPASSRLS`**: ele não consegue ignorar o
isolamento entre clientes nem alterar a auditoria, mesmo que o código tente.

### `ORBI_DATABASE_ADMIN_URL` — o papel administrativo
Roda migration, cria cliente e executa a anonimização da LGPD. **Nunca é usado
pelo Runtime** — há um teste que falha se alguém tentar.

⚠️ **A suíte de testes recria o schema do zero.** Se essas duas variáveis
apontarem para um banco que não seja de teste, `pytest` **aborta** — proteção
adicionada depois de eu apagar o banco de desenvolvimento exatamente assim.

### `ORBI_DATABASE_POOL_SIZE`
Conexões simultâneas. `5` atende com folga no volume do MVP. Suba só com gargalo
medido — pool grande demais desperdiça memória do Postgres.

### `ORBI_DATABASE_ECHO`
`true` imprime todo o SQL. Útil para entender uma consulta, ruidoso para
qualquer outra coisa.

---

## Orçamento de tempo

A meta do produto é **2 a 4 segundos**. Estes números são o **teto**, não o alvo:
eles definem quando o Orbi desiste, não quanto ele demora.

### `ORBI_DEADLINE_TOTAL_MS` (10000)
Orçamento do turno inteiro. Um único objeto `Deadline` desce por todas as
camadas, e cada etapa consulta o que sobrou antes de começar — é o que impede o
clássico "cada camada tem 5s e o total vira 20s".

### `ORBI_LLM_TIMEOUT_MS` (4000)
Quanto o Orbi espera pelo modelo. Se o provedor for lento (a camada gratuita do
Gemini às vezes passa de 10s), ou você sobe este número e aceita turnos longos,
ou troca de modelo. Não existe terceira opção honesta.

### `ORBI_ERP_TIMEOUT_MS` (6000)
Quanto o Orbi espera pelo ERP do cliente. Não suba isso para "parar de dar erro":
o orçamento existe para o usuário não ficar esperando. ERP lento é conversa com
o cliente, não configuração.

### `ORBI_SLOW_REPLY_THRESHOLD_MS` (2500)
Acima disso, o Orbi manda "consultando o sistema da empresa..." enquanto termina.
Percepção de velocidade vale tanto quanto o número — e a mensagem ainda mantém a
janela de conversa da Meta aberta.

---

## LLM

### `ORBI_LLM_PRIMARY` e `ORBI_LLM_FALLBACK`
`gemini` | `anthropic` | `openai` | `rule_based`

O fallback precisa ser de **outro fabricante**. Dois provedores da mesma empresa
caem juntos, e o failover vira enfeite. O Orbi **recusa subir** se os dois forem
do mesmo fabricante.

`rule_based` é o interpretador determinístico local, para desenvolvimento e CI.
Ele é recusado em produção.

Deixe `ORBI_LLM_FALLBACK` vazio enquanto houver só um provedor. O `orbi doctor`
avisa que falta — o que é verdade, e você decide quando resolver.

### `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_THINKING_BUDGET`

A chave sai de [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

`GEMINI_THINKING_BUDGET`: `0` desliga o raciocínio (escolher entre quatro
operações não precisa disso, e pensar custa segundos); `-1` **não envia o campo**,
que é o necessário nos modelos `lite` — eles recusam com 400.

Sobre a escolha de modelo, veja a seção de cotas mais abaixo.

### `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` e seus modelos
Implementados e testados estruturalmente, sem chave. Quando você quiser o
failover de verdade, preencha um dos dois — de fabricante diferente do primário.

### A chave global e a chave de cada cliente

As chaves do `.env` são as **globais**: valem para todo cliente que não tenha a
própria. É o caso da maioria, e o normal no começo.

Um cliente pode ganhar chave própria quando tiver projeto próprio no provedor
(D-041) — o que vale a pena na OpenAI e na Anthropic, onde projeto/workspace com
teto de gasto é nativo, e não vale no Gemini, onde a cota é por projeto do Google
Cloud e a conta de faturamento começa limitada a cinco:

```bash
orbi tenant set-llm --tenant construtora-silva \
  --provider openai --api-key "sk-proj-..." --model gpt-5-mini

orbi tenant show --tenant construtora-silva   # mostra só os 4 últimos dígitos
orbi tenant clear-llm --tenant construtora-silva
```

A chave é cifrada com a mesma `ORBI_SECRET_KEY` das credenciais de ERP e nunca
aparece inteira na CLI — chave impressa num terminal vai para o histórico do
shell e para o scrollback.

**Se a chave do cliente falhar**, o turno cai para a global e é atendido: teto de
gasto estourado não pode deixar um vendedor sem resposta no meio do expediente.
Mas a queda **emite alerta**, e a auditoria grava qual provedor de fato atendeu.
A consequência precisa estar clara: naquele turno o gasto volta a ser seu, então
o teto do provedor é um corte no gasto *daquele cliente*, não um corte absoluto.

### `ORBI_LLM_RENDERING_ENABLED`
**Mantenha `false`.** É o feature flag que permitiria o LLM redigir a resposta.
A resposta é template a partir de dado tipado, e é isso que impede um número de
ser alterado na redação. O `orbi doctor` reclama se estiver ligado em produção.

---

## Embeddings

### `ORBI_EMBEDDING_PROVIDER`
`hashing` roda local, sem chave e sem custo, e captura erro de digitação e nome
truncado. `openai` entende sinônimo ("cano" ≈ "tubo") e custa por uso.

Trocar exige re-indexar:

```bash
orbi catalog sync --tenant <slug> --force
orbi maintenance recalibrate --tenant <slug> --force
```

O gatilho para trocar: quando o resumo diário mostrar termos sem resultado que
são claramente sinônimos e o alias aprendido não estiver dando conta.

---

## Canal WhatsApp

### `ORBI_WHATSAPP_VERIFY_TOKEN`
**Você inventa** esse valor. Ele é cadastrado na Meta e devolvido por ela no
handshake de verificação do webhook — serve para provar que a URL é sua.

### `ORBI_WHATSAPP_APP_SECRET`
Sai de *Meta → seu app → Configurações → Básico*. A Meta assina cada webhook com
ele, e o Orbi confere antes de processar qualquer coisa.

**Sem ele, em produção, todo webhook é recusado.** É o que impede alguém que
descubra sua URL de falar pelo canal do seu cliente.

### `ORBI_WHATSAPP_API_VERSION`
Versão da Graph API. A Meta descontinua versões antigas com aviso; subir é trocar
esse número e rodar os testes.

### O token de cada cliente **não fica aqui**
Ele é gravado cifrado no banco, por cliente:

```bash
orbi tenant set-token --tenant construtora-silva --token "<token>"
```

Cada cliente tem número e token próprios.

---

## Canal de operação

`ORBI_OPS_PHONE`, `ORBI_OPS_PHONE_NUMBER_ID`, `ORBI_OPS_ACCESS_TOKEN`

O número **interno da sua equipe**, que recebe alertas imediatos (ERP fora do ar,
circuito aberto, 👎 de usuário, número desconhecido) e o resumo diário.

Sem isso configurado, os alertas ainda acontecem — só vão para o log em vez do
WhatsApp. Como não há painel, configurar isso é o que transforma a
observabilidade em interface de operação.

---

## Observabilidade

`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`

Sem chave, o Orbi funciona igual e o trace vive só em `audit_logs`. Com chave,
cada turno aparece no Langfuse com o **mesmo `trace_id` da auditoria** e a
latência de cada etapa.

A camada gratuita do Langfuse é generosa para o volume do MVP.

---

# A cota gratuita do Gemini, medida

Não é teoria: os números abaixo saíram de chamadas reais com a chave do projeto.

## Como funciona

A camada gratuita do Google AI Studio limita por três dimensões — requisições por
minuto (RPM), tokens por minuto (TPM) e **requisições por dia (RPD)** — e a
documentação não publica mais os números: eles ficam no painel
[ai.dev/rate-limit](https://ai.dev/rate-limit), porque variam por conta.

Estourar qualquer uma devolve `429 RESOURCE_EXHAUSTED`. E a mensagem de erro diz
exatamente qual cota caiu:

```
Quota exceeded for metric: generate_content_free_tier_requests,
limit: 20, model: gemini-3.7-flash
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
```

## O achado que muda a estratégia

Leia o nome da cota: **`PerProjectPerModel`**. A cota é **por modelo**.

Medido na prática: com `gemini-3.7-flash` esgotado em 20 requisições no dia,
`gemini-3.5-flash-lite` e `gemini-flash-lite-latest` **continuavam respondendo**.

## Latência medida, por modelo

| Modelo | Latência do LLM | Cota/dia | Observação |
|---|---|---|---|
| `gemini-3.7-flash` | 8,5 a 17,9 s | **20** | modelo novo, sob alta demanda |
| `gemini-3.5-flash-lite` | **0,9 a 3,1 s** | maior | recusa `thinking_config` |
| `gemini-flash-lite-latest` | **0,9 a 2,1 s** | maior | idem |

Turno completo com o `lite`, contra o Odoo real: **1,2 a 1,6 segundo** — dentro
da meta de 2 a 4 segundos do produto.

## A configuração recomendada hoje

Depende de quem está do outro lado:

**Desenvolvimento e PoC** — Gemini na camada gratuita (D-035):

```bash
ORBI_LLM_PRIMARY=gemini
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_THINKING_BUDGET=-1
```

Mesmo o `lite` teve variação grande na camada gratuita — uma chamada passou de 20
segundos — e a cota é de 20 perguntas/dia por modelo. Para PoC serve; para cliente
pagante, não.

**Cliente pagante** — OpenAI primário, Anthropic fallback (D-047):

```bash
ORBI_LLM_PRIMARY=openai
OPENAI_MODEL=gpt-5-mini
ORBI_LLM_FALLBACK=anthropic
ANTHROPIC_MODEL=claude-sonnet-5
```

Os defaults do código já são estes modelos; o que muda no `.env` é o primário, o
fallback e as chaves. A chave pode ser por cliente (`orbi tenant set-llm`, D-041).

## Quando o 429 aparecer

Três saídas, em ordem de esforço:

1. **Trocar de modelo** — cota nova, imediata, uma linha no `.env`.
2. **Configurar o fallback** de outro fabricante — o Orbi troca sozinho quando o
   primário falha.
3. **Ativar billing** no AI Studio. A camada paga tem cota de produção e latência
   melhor; passa a custar centavos por consulta.

---

# O que ainda não está no `.env`

Estas variáveis não existem hoje. Elas aparecem quando o problema aparecer — a
regra do projeto é não adicionar tecnologia por antecipação.

| Variável futura | Quando entra | O que dispara |
|---|---|---|
| `REDIS_URL` | contexto e rate limit no Postgres virarem gargalo **medido** | p95 de banco subindo com carga |
| `ORBI_BACKUP_REMOTE` | primeiro cliente pagante | precisa de backup em provedor diferente do da aplicação |
| `SENTRY_DSN` | erros que a auditoria não explica sozinha | hoje o alerta de ops e o `trace_id` bastam |
| `ORBI_TELEGRAM_TOKEN` | segundo canal | cliente pedir, ou custo por mensagem da Meta pesar |
| `ORBI_CONSOLE_*` | operar por CLI passar de 4h/mês | tipicamente por volta do quarto cliente |
| `ORBI_SHADOW_SAMPLE_RATE` | os 5% fixos deixarem de servir | volume alto demais ou baixo demais para a amostra |
| chave de embedding próprio | sinônimo virar problema recorrente | termos sem resultado no resumo diário |

Cada linha dessa tabela tem um gatilho **medido**, não uma data. É o que impede a
configuração de crescer sozinha.

---

# Receitas prontas

## Desenvolvimento na sua máquina
```bash
ORBI_ENV=development
ORBI_LLM_PRIMARY=rule_based       # sem gastar cota nem depender de rede
ORBI_EMBEDDING_PROVIDER=hashing
```

## Desenvolvimento com LLM real
```bash
ORBI_ENV=development
ORBI_LLM_PRIMARY=gemini
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_THINKING_BUDGET=-1
ORBI_LLM_TIMEOUT_MS=20000         # a camada gratuita varia muito
ORBI_DEADLINE_TOTAL_MS=30000
```

## PoC com cliente
```bash
ORBI_ENV=production
ORBI_LLM_PRIMARY=gemini
ORBI_LLM_FALLBACK=anthropic       # ou openai — fabricante diferente
ORBI_WHATSAPP_APP_SECRET=<da Meta>
ORBI_WHATSAPP_VERIFY_TOKEN=<voce inventa>
ORBI_OPS_PHONE=<numero da sua equipe>
LANGFUSE_PUBLIC_KEY=<opcional, mas ajuda muito no piloto>
```

Rode `orbi doctor` antes do go-live. Ele lista o que falta para produção e
**recusa** subir com configuração insegura.
