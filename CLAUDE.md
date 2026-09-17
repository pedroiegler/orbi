## Escopo

- ORBI é a fonte da verdade — código, decisões (`docs/ORBI-DECISOES.md`) e documentação sempre coerentes; `docs/ORBI.md` é a especificação e muda junto
- Não implementar nada fora do escopo atual: WhatsApp, somente leitura, as quatro tools
- Não existe frontend — a administração é pela CLI (`orbi ...`)
- Não adicionar tecnologia sem um problema real que justifique
- Não antecipar roadmap, exceto os pontos de extensão já previstos na arquitetura
- Foco comercial: distribuidor e atacado até o 3º cliente pagante; depois rastreamento (D-045). Ramo novo só se cumprir as seis condições de `ORBI-COMERCIAL.md`

## LLM

- O LLM interpreta; o código autoriza, executa e monta a resposta
- O LLM nunca emite IDs ou códigos, nunca acessa o banco, nunca gera SQL
- Respostas por template do código — não existe caminho de código em que o LLM redija
- Uma tool por turno — sem loops ou agentes no caminho da pergunta (testado contando chamadas)
- Enviar só as tools permitidas às permissões do usuário
- Mascarar CPF, CNPJ, telefone e e-mail antes de qualquer envio ao LLM
- Toda integração com provedor passa pelo LLMPort
- Provedores: com cliente pagante, OpenAI primário e Anthropic fallback; Gemini só em desenvolvimento e PoC; `rule_based` só em dev e CI (D-047). Um modelo para todos os clientes (D-036). Modelo em uso tem que estar em `llm/pricing.py`
- Chave de LLM pode ser por cliente, cifrada; falha da chave própria cai para a global **com alerta** (D-041)

## Tools, papéis e resolução

- Não existe tool de busca de produto — a resolução é interna ao Runtime
- Tool declarada uma única vez no Tool Registry, com os cinco artefatos (registry, adapter, field policy, template, evals)
- Permissão é por capability, nunca por nome de papel; cada cliente define os próprios papéis com `orbi role` (D-040). Papel vazio ou desconhecido = nenhum acesso
- Na dúvida ou ambiguidade, perguntar — nunca chutar. Alias `low` que discorda do catálogo pergunta (D-044)
- Toda resposta mostra qual entidade foi usada; nunca mentir sobre a base do número (físico vs disponível)
- Nomes de tools, argumentos, classes e arquivos em inglês

## Segurança

- Policy Layer deny-by-default, antes do acesso ao ERP
- Field Policy controla campos visíveis — nunca depender do prompt
- Isolamento total entre tenants: RLS forçado em toda tabela com dado de cliente; sessão sempre pela dependency que define o tenant; tabela global só se declarada e testada
- Canary tenant no CI — vazamento entre tenants falha o build
- Sem condicional por tenant ou por ERP no Core — diferença entre clientes vive em linha de tabela, nunca em ramo (testado)
- Credenciais de ERP e chaves de LLM cifradas; segredos nunca no repositório; a CLI nunca imprime chave inteira
- Número de WhatsApp: nosso por padrão, em Business Portfolio separado por cliente; o do cliente só se insistir (D-048). Conferir o teto de portfólios antes do 2º cliente

## ERP

- Somente APIs ou interfaces oficiais — nunca banco direto ou scraping
- Cada ERP em seu próprio Adapter; adapter novo só entra depois do Conformance Kit
- Odoo é o ERP de referência permanente — validar tudo nele antes de cliente real
- Nunca responder estoque de cache; ERP fora do ar, informar a falha

## Qualidade

- `pip install -e ".[dev]"` seguido de `pytest` tem que passar — o extra `dev` inclui os SDKs que a suíte exercita (D-046)
- Rodar `ruff check`, `ruff format --check src tests scripts`, `mypy src` e a suíte inteira ao final; corrigir antes de concluir. Migrations aplicadas não se editam nem se reformatam
- Ler o exit code de cada portão — nunca inferir sucesso de saída truncada por `tail`/`grep`
- CI roda em merge para `main`, PR para `main` e sob demanda. Depois de todo push para `main`, conferir o run pela API pública do GitHub antes de afirmar que está verde
- Mudança de comportamento passa pelos evals correspondentes; trace_id compartilhado entre observabilidade e auditoria
- Reutilizar em vez de duplicar; sem código morto, redundante ou fallback desnecessário; seguir o padrão do código existente

## Git e docs

- `main` recebe merges de `development`; commits em inglês, sem citar ferramentas de IA
- Atualizar a documentação ao mudar contratos, arquitetura ou decisões — em todos os documentos que repetem o fato, não só num
- Número ou afirmação substituída entra na lista de aposentados em `tests/unit/test_architecture.py`; a suíte aponta quem ainda repete
- Toda proibição aponta para o teste que a garante; ponteiro quebrado falha a suíte
- Sem analogias — literal e técnico
