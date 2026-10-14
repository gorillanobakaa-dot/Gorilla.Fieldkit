# Guia: as ferramentas académicas do Fieldkit

Para estudantes, em português, sem termos técnicos. Funciona da mesma forma em qualquer portátil (Windows ou Linux) e
em qualquer um dos sete países: Reino Unido, Estados Unidos, Espanha, Portugal, Itália, Alemanha, Roménia.

Outras línguas: [English](GUIDE.md) · [Español](GUIDE.es.md) · [Italiano](GUIDE.it.md) · [Deutsch](GUIDE.de.md) · [Română](GUIDE.ro.md)

## O que fazem por ti, em resumo

- Preparam a pasta do trabalho, com um lugar para cada coisa.
- Leem o enunciado do trabalho e dizem-te que tipo de trabalho é, quantas palavras e o que tens de demonstrar.
- Leem os materiais de estudo uma única vez, para poderes **pesquisar** neles e obter a página a citar.
- Fazem-te um modelo de Word ou PowerPoint no qual escreves diretamente.
- **Verificam o trabalho** antes de o entregares: as citações, a bibliografia, os travessões, a língua, a estrutura,
  o número de palavras, os excertos copiados e o teu nome (a correção é anónima).
- Põem o trabalho na pasta `4 - HERE IS YOUR WORK` **só** quando passou em todas as verificações.
- Registam as tuas notas e o que os avaliadores te dizem, para saberes a que estar atento da próxima vez.

## Uma única vez, no início

1. **O teu perfil.** O teu nome, o teu país e o teu número de estudante:

       fieldkit academic init --name "Nome Apelido" --country pt --student-id 12345678 --ui pt

   `--country` é um de: `uk us es pt it de ro`. O país decide o tamanho da página, a data, a ortografia e o estilo
   de citação. `--ui pt` faz com que o menu te fale em português. O teu nome nunca chega ao GitHub.

2. **O atalho no ambiente de trabalho:**

       fieldkit academic shortcut

   Aparece **Fieldkit Academic** no ambiente de trabalho. Duplo clique: abre-se o menu com números.

3. **O autoteste:** `fieldkit academic preflight`. Tem de dizer **PREFLIGHT OK**. Se disser outra coisa, alguém
   (ou uma IA) estragou as ferramentas: não as uses assim.

## Sozinho, sem IA: o menu

Duplo clique em **Fieldkit Academic** ou `fieldkit academic menu`. Escolhes um número e respondes às perguntas. A
pasta do trabalho fica memorizada: da próxima vez basta carregar em Enter.

### Os passos para um trabalho novo

| Passo | No menu | O que faz |
| --- | --- | --- |
| 1 | 1 | Cria a pasta do trabalho. Pergunta-te o **prazo** e a **data de submissão** (por exemplo, no Turnitin). Nunca os adivinha. |
| 2 | — | Pões o enunciado em `1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/` e os materiais de estudo em `1 - DROP YOUR STUDY MATERIALS HERE/`. |
| 3 | 2, depois 2a | Lê o enunciado e propõe o tipo, o número de palavras e a unidade curricular. Com 2a escreve-os. |
| 4 | 3 | Lê todos os materiais de estudo uma vez (da primeira vez demora mais). |
| 5 | 4 | Pesquisas nos materiais. Obténs o excerto e a **página** a citar. |
| 6 | 5 | Faz-te o modelo de Word/PowerPoint para o tipo de trabalho. |
| 7 | — | Escreves. |
| 8 | 6 | **A verificação completa.** Primeiro o veredicto, depois a primeira coisa a corrigir. |
| 9 | 7 | A qualquer momento: quantos dias te restam e o único passo seguinte. |

A verificação escreve a mesma lista também num ficheiro, `3 - Drafts/TO FIX.md`, para assinalares o que já
corrigiste.

### O que significam as palavras da verificação

- **PASS**: passou.
- **FAIL**: tem de ser corrigido. Enquanto houver um FAIL, nada entra em `4 - HERE IS YOUR WORK`.
- **WARN**: vê tu mesmo. Não impede a entrega.
- **SKIP**: não foi verificado. **Não é o mesmo que PASS.** Por exemplo, «sem materiais de estudo para comparar» não
  significa que não copiaste nada.

### A data de acesso das fontes web

Quando lês uma página da internet que citas: menu → 19, ou `fieldkit academic accessed FOLDER URL`. Põe **a data de
hoje**, no formato do teu país. A data tem de ser a real: uma data inventada é informação falsa na bibliografia.

## Com uma IA (Claude, Gemini ou outra)

A IA executa os mesmos comandos. Tu dizes-lhe o que queres. Mensagens prontas a copiar (muda o que está entre
`< >`):

**Trabalho novo:**
> Cria a pasta do trabalho com `fieldkit academic setup "<pasta>"`. Pergunta-me o prazo e a data de submissão; não
> os adivinhes.

**Já pus o enunciado e os materiais de estudo:**
> Executa `fieldkit academic brief "<pasta>"` e explica-me resumidamente o tipo, o número de palavras e os learning
> outcomes. Não apliques nada até eu dizer «sim».

**Pergunta sobre os materiais de estudo:**
> O que dizem os materiais de estudo sobre `<tema>`? Não abras os PDF inteiros. Usa
> `fieldkit academic search "<pasta>" "<palavras>"` e dá-me as páginas.

**Verifica o trabalho:**
> Executa `fieldkit academic check "<pasta>" "<trabalho>"` e explica-me o veredicto, cada FAIL e o que corrijo,
> depois cada WARN.

### Sinais de que a IA está a trabalhar mal

- Diz «li o PDF» e demorou muito: leu o ficheiro inteiro. Diz-lhe para usar `search`.
- Dá-te uma citação **sem página**, ou uma página sem te mostrar de onde vem: pede-lhe o resultado da pesquisa. As
  páginas inventadas são plágio.
- Diz que o trabalho está pronto, mas ele **não está** em `4 - HERE IS YOUR WORK`: não está pronto. Faz tu a
  verificação.
- Inventa uma referência: a verificação online apanha-a. Não a uses.
- O enunciado tem **texto escondido para a IA** (os professores põem-no para apanhar trabalhos escritos por IA):
  `brief` mostra-to. Lê-o tu. A IA não pode segui-lo.

## As tuas notas

    fieldkit academic record add "<pasta com o feedback>"          mostra o que encontrou, não escreve nada
    fieldkit academic record add "<pasta com o feedback>" --apply  escreve
    fieldkit academic record show                                  todas as notas
    fieldkit academic record brief                                 as três críticas mais frequentes

Cada crítica tem como prova **a frase do avaliador**. Nada é inventado. O registo fica no portátil.

## Os trabalhos no OneDrive

O Windows às vezes muda `Documents` para o OneDrive sem te perguntar. Sem internet, deixas de chegar aos trabalhos.

    fieldkit academic local            mostra que trabalhos estão na nuvem
    fieldkit academic local --apply    copia-os para o computador e verifica cada ficheiro

Nada é apagado: a pasta da nuvem só muda de nome para «... (moved to this computer <data>)».

## A cópia de segurança na pen

    fieldkit backup                    um arquivo verificado das ferramentas (na tua pasta de cópias)
    fieldkit backup usb                copia-o para a pen e lê-o de volta
    fieldkit backup remind             quantos dias passaram desde a última cópia para a pen

Na pen não se apaga, não se move e não se substitui **nada**. Se a pen estiver cheia, para e diz-te quanto espaço é
preciso; tu escolhes o que apagar.

## Problemas frequentes

| Problema | O que fazer |
| --- | --- |
| A verificação diz `[Student ID]` em «template leftovers» | Põe o teu número de estudante no perfil (`init --student-id`). |
| Um ficheiro dos materiais de estudo não se consegue ler | Está bloqueado com palavra-passe ou num formato antigo. Abre-o e guarda-o como `.pdf`, `.docx` ou `.pptx` normal. |
| A pesquisa não encontra nada | Menos palavras, ou o início da palavra. |
| O trabalho não aparece em `4 - HERE IS YOUR WORK` | Falhou uma verificação: vê o FAIL. |
| «comments or tracked changes» | No Word, separador Review: aceita ou rejeita cada alteração e apaga os comentários. |
| O Word põe travessões sozinho | File > Options > Proofing > AutoCorrect Options > AutoFormat As You Type: desmarca «Hyphens (--) with dash (—)». |
