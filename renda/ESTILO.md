# Estilo do canal @rendaextraparatodos

Guia para os próximos lotes de `renda/posts.json`. Saiu da leitura dos posts do canal em 10/11/2026.

## O que mais teve visualização no canal
- Texto que começa com uma história curta ("Nem toda renda extra começa com uma ideia genial…"): 305 views.
- Aplicativo que paga por algo simples: 263.
- Cursos grátis do MEC, Etec e Instituto Federal: 140, 96 e 48.
- Listas do tipo "3 sites que pagam…": 90.

## Foco do canal (desde 11/11/2026)
Coisas que **aumentam as habilidades da pessoa para ganhar dinheiro**:
- onde deixar o currículo e como fazer ele passar na triagem;
- cursos profissionalizantes e cursos grátis (IF, Senac, Senai, Sebrae, Prefeitura, Sine);
- IA que vale a pena aprender e como usar no trabalho;
- vagas de emprego e home office;
- serviços e renda extra que a pessoa consegue começar com o que já sabe.
**Sem quiz de golpe.** O Manoel tirou esses quizzes em 11/11/2026. Os quizzes que ficam são de decisão (quanto cobrar, trabalho de teste).

## Regras
- **Dicas honestas.** Nada de prometer valor por dia, nada de aposta, cassino, pirâmide ou cripto.
- **Sem link de afiliado** por enquanto (decisão do Manoel em 10/11/2026). O foco é crescer o canal.
- **Arte v2, ilustrada** (o `renda/arte.py` usa sozinho quando existe `renda/ilustra/NNN.png`):
  - fundo creme com pattern fino de círculos;
  - ilustração em moldura verde com sombra dourada;
  - título em DM Serif Display, com o número em terracota;
  - lista limpa com números em círculos dourados, sem emoji na arte;
  - rodapé verde com @rendaextraparatodos e "Salve e compartilhe".
  A arte antiga (só ícones) ficou com cara de "IA genérica" e foi aposentada.
- **Cada post traz:**
  - título com emoji;
  - uma abertura que conversa com a pessoa;
  - uma lista de 4 a 7 itens;
  - um bloco "👀 Toque para ver a dica de ouro";
  - uma chamada para salvar ou compartilhar;
  - de 3 a 5 hashtags, sempre com `#rendaextra`.
- **Mistura do lote:** cerca de 85% listas e 15% quizzes de decisão.
- **Notícias:** o `renda.py` garimpa o Google Notícias sozinho, com 1 por dia às 15h, e sai sempre com capa e imagem. Temas buscados:
  - cursos grátis e profissionalizantes;
  - cursos de IA;
  - vagas home office e vagas com inscrição aberta.
  Notícias locais de uma cidade só ficam de fora.

## Como gerar um lote
1. Acrescente os itens no `renda/posts.json`, com os campos `id`, `tipo`, `titulo`, `imagens`, `arte`, `corpo` e `tags`.
2. Gere 1 ilustração por dica em `renda/ilustra/NNN.png` (3:2), com este estilo no pedido:
   "Ilustração editorial plana (flat vector) moderna, com textura granulada sutil de risografia, estilo de revista de negócios.
   Paleta: verde-escuro #0E3B2E, dourado #F2C14E, creme #F7F3E8, terracota #C8643B. Fundo creme liso.
   SEM nenhum texto, letras, números ou logotipos." e a cena da dica.
3. Rode `python renda/arte.py 029 030 ...` para desenhar só essas (ou `tudo`).
4. Rode `python renda.py previa` para ver tudo no privado antes de subir.
