# ORBI — ERP

Credenciais, capabilities, circuito aberto e ERP fora do ar.

---

## O princípio

> **O Orbi integra apenas por interfaces oficiais do ERP.** Não acessa banco de
> dados, não instala agente no servidor do cliente e não faz scraping.

Isso não é limitação — é padrão de mercado e argumento de venda:

> "Conectamos pelo canal oficial do seu ERP, com as credenciais que você
> autoriza. Não acessamos seu banco, não instalamos nada no seu servidor e não
> guardamos cópia dos seus dados transacionais — só um índice de nomes para
> entender suas perguntas."

Ler direto do banco pula as regras de negócio do ERP: um campo `qtd_estoque` pode
não ser o estoque que o sistema mostra na tela. E `capabilities().integration_mode`
torna o princípio **verificável em teste**, não boa intenção.

---

## Conectar

```bash
orbi erp add --tenant construtora-silva --adapter odoo \
  --credentials '{"url":"https://erp.cliente.com.br","db":"producao","username":"orbi","api_key":"<chave>"}'
orbi erp test --tenant construtora-silva
orbi erp capabilities --tenant construtora-silva --refresh
```

A credencial é cifrada na aplicação com chave fora do banco. Quem tem acesso de
leitura ao Postgres **não** tem acesso ao ERP do cliente.

## Capabilities

`capabilities()` declara o que aquele ERP sabe fazer, e o Core **desliga sozinho**
as tools que ele não atende. É isso que mantém `if erp == "x"` fora do Runtime —
e há um teste de arquitetura que falha se alguém tentar.

| Campo | Efeito quando falso |
|---|---|
| `supports_reservations` | a resposta de estoque passa a declarar que o número é físico |
| `supports_multi_location` | a resposta traz só o total, sem quebra por depósito |
| `supports_customer_pricing` | o preço respondido é o de lista |
| `supports_incremental_catalog` | o sync incremental vira full |

---

## Semântica de estoque

O Orbi define um conceito canônico e **nunca mente sobre qual está entregando**:

- ERP informa reservas → responde o **disponível**;
- ERP não informa → responde o **físico** e diz isso na resposta:

> "37 un em estoque **físico** (este ERP não informa reservas)."

O erro clássico é responder "37" quando 12 estão comprometidos, o vendedor
prometer, e a confiança acabar ali. Cinco palavras de honestidade valem o produto
inteiro.

---

## ERP fora do ar

O Orbi responde a falha e alerta. **Nunca serve estoque de cache** — melhor não
responder do que responder errado sobre quantidade.

Três mecanismos:

- **Deadline** de 10 s desce por todas as camadas; cada etapa consulta o que
  sobrou antes de começar.
- **Circuit breaker por `(tenant, adapter, operação)`** — não por adapter
  inteiro: estoque lento não pode derrubar consulta de título. 5 falhas em 30 s
  abrem por 60 s.
- **Bulkhead por tenant**: um cliente não consome sozinho o rate limit do
  adapter.

Depois de resolver o incidente:

```bash
orbi erp reset-breaker
```

---

## Novo ERP

Um ERP está pronto quando **passa no Conformance Kit**:

```bash
pytest tests/conformance -q
ORBI_ODOO_URL=http://localhost:8069 pytest tests/conformance -q   # contra ERP vivo
```

Passos:

1. escrever o adapter em `src/orbi/erp/adapters/<nome>.py`, implementando o
   `ErpAdapter` (somente leitura);
2. registrar em `src/orbi/erp/registry.py`;
3. acrescentar o caso em `tests/conformance/conftest.py`;
4. rodar o kit contra uma instância real do ERP;
5. rodar o Discovery para gerar o Knowledge Model e as seed questions.

O kit é o que impede a abstração de apodrecer no segundo cliente e transforma
"integrar novo ERP" em tarefa estimável.

---

## Odoo — adapter de referência permanente

O `OdooAdapter` não é descartável. É contra ele que o Conformance Kit roda no CI,
indefinidamente. A API do Odoo é XML-RPC e a dos ERPs em nuvem brasileiros é
REST: sustentar os dois prova que a abstração aguenta quase qualquer coisa —
**antes** do primeiro cliente, quando corrigir ainda é barato.

```bash
docker compose -f docker/docker-compose.odoo.yml up -d
python scripts/odoo_bootstrap.py
```

---

## Qualificação de cliente

| Perfil | Caminho |
|---|---|
| ERP em nuvem com API REST documentada | API oficial. É o MVP. |
| ERP com API paga | Muitos "ERPs sem API" têm módulo de integração cobrado à parte, e o cliente não sabe. Verificar antes de descartar. |
| ERP com webhooks | Melhor ainda. Entra depois do MVP. |
| ERP legado no servidor do cliente | Réplica de leitura. Fora do escopo até o 10º cliente. |
| Sem interface | Recusar. Um cliente assim consome mais tempo que os outros dez somados. |
