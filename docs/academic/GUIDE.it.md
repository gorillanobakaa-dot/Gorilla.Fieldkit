# Guida: gli strumenti accademici di Fieldkit

Per studenti, in italiano, senza termini tecnici. Funziona allo stesso modo su qualsiasi portatile (Windows o Linux)
e in uno qualsiasi dei sette paesi: Regno Unito, Stati Uniti, Spagna, Portogallo, Italia, Germania, Romania.

Altre lingue: [English](GUIDE.md) · [Español](GUIDE.es.md) · [Português](GUIDE.pt.md) · [Deutsch](GUIDE.de.md) · [Română](GUIDE.ro.md)

## Che cosa fanno per te, in breve

- Ti preparano la cartella dell'elaborato, con un posto per ogni cosa.
- Leggono la consegna e ti dicono che tipo di elaborato è, quante parole e che cosa devi dimostrare.
- Leggono i materiali del corso una sola volta, così puoi **cercare** al loro interno e avere la pagina da citare.
- Ti preparano un modello Word o PowerPoint in cui scrivi direttamente.
- **Controllano l'elaborato** prima che tu lo consegni: le citazioni, la bibliografia, le lineette lunghe, la lingua,
  la struttura, il numero di parole, le parti copiate e il tuo nome (la correzione è anonima).
- Mettono l'elaborato nella cartella `4 - HERE IS YOUR WORK` **solo** quando ha superato tutti i controlli.
- Tengono traccia dei tuoi voti e di quello che ti dicono i correttori, così sai a che cosa stare attento la prossima
  volta.

## Una volta sola, all'inizio

1. **Il tuo profilo.** Il tuo nome, il tuo paese e il tuo numero di matricola:

       fieldkit academic init --name "Nome Cognome" --country it --student-id 12345678 --ui it

   `--country` è uno tra: `uk us es pt it de ro`. Il paese decide il formato della pagina, la data, l'ortografia e lo
   stile di citazione. `--ui it` fa sì che il menu ti parli in italiano. Il tuo nome non arriva mai su GitHub.

2. **Il collegamento sul desktop:**

       fieldkit academic shortcut

   Compare **Fieldkit Academic** sul desktop. Doppio clic: si apre il menu numerato.

3. **L'autotest:** `fieldkit academic preflight`. Deve scrivere **PREFLIGHT OK**. Se scrive altro, qualcuno (o
   un'IA) ha guastato gli strumenti: non usarli così.

## Da solo, senza IA: il menu

Doppio clic su **Fieldkit Academic** oppure `fieldkit academic menu`. Scegli un numero e rispondi alle domande. La
cartella dell'elaborato viene ricordata: la volta dopo basta premere Invio.

### I passi per un nuovo elaborato

| Passo | Nel menu | Che cosa fa |
| --- | --- | --- |
| 1 | 1 | Crea la cartella dell'elaborato. Ti chiede la **scadenza** e la **data di caricamento** (per esempio su Turnitin). Non le indovina mai. |
| 2 | — | Metti la consegna in `1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/` e i materiali del corso in `1 - DROP YOUR STUDY MATERIALS HERE/`. |
| 3 | 2, poi 2a | Legge la consegna e propone il tipo, il numero di parole e l'insegnamento. Con 2a li scrive. |
| 4 | 3 | Legge tutti i materiali del corso una volta (la prima volta ci mette di più). |
| 5 | 4 | Cerchi nei materiali. Ottieni il passaggio e la **pagina** da citare. |
| 6 | 5 | Ti prepara il modello Word/PowerPoint per il tipo di elaborato. |
| 7 | — | Scrivi. |
| 8 | 6 | **Il controllo completo.** Prima il verdetto, poi la prima cosa da sistemare. |
| 9 | 7 | In qualsiasi momento: quanti giorni ti restano e l'unico passo successivo. |

Il controllo scrive la stessa lista anche come file, `3 - Drafts/TO FIX.md`, così puoi spuntare ciò che hai
sistemato.

### Che cosa significano le parole del controllo

- **PASS**: superato.
- **FAIL**: va sistemato. Finché c'è un FAIL, niente entra in `4 - HERE IS YOUR WORK`.
- **WARN**: guardalo tu. Non blocca la consegna.
- **SKIP**: non è stato controllato. **Non è la stessa cosa di PASS.** Per esempio, «nessun materiale del corso con
  cui confrontare» non significa che non hai copiato niente.

### La data di consultazione delle fonti web

Quando leggi una pagina internet che citi: menu → 19, oppure `fieldkit academic accessed FOLDER URL`. Mette **la
data di oggi**, nel formato del tuo paese. La data deve essere quella vera: una data inventata è un'informazione
falsa nella bibliografia.

## Con un'IA (Claude, Gemini o un'altra)

L'IA esegue gli stessi comandi. Tu le dici che cosa vuoi. Messaggi pronti da copiare (cambia ciò che sta tra
`< >`):

**Nuovo elaborato:**
> Crea la cartella dell'elaborato con `fieldkit academic setup "<cartella>"`. Chiedimi la scadenza e la data di
> caricamento; non indovinarle.

**Ho messo la consegna e i materiali del corso:**
> Esegui `fieldkit academic brief "<cartella>"` e spiegami in breve il tipo, il numero di parole e i learning
> outcomes. Non applicare niente finché non dico «sì».

**Domanda sui materiali del corso:**
> Che cosa dicono i materiali del corso su `<argomento>`? Non aprire i PDF interi. Usa
> `fieldkit academic search "<cartella>" "<parole>"` e dammi le pagine.

**Controlla l'elaborato:**
> Esegui `fieldkit academic check "<cartella>" "<elaborato>"` e spiegami il verdetto, ogni FAIL e che cosa devo
> sistemare, poi ogni WARN.

### Segnali che l'IA lavora male

- Dice «ho letto il PDF» e ci ha messo molto: ha letto tutto il file. Dille di usare `search`.
- Ti dà una citazione **senza pagina**, o una pagina senza mostrarti da dove viene: chiedile il risultato della
  ricerca. Le pagine inventate sono plagio.
- Dice che l'elaborato è pronto, ma **non è** in `4 - HERE IS YOUR WORK`: non è pronto. Esegui tu il controllo.
- Inventa un riferimento: il controllo online lo scopre. Non usarlo.
- La consegna contiene **testo nascosto per l'IA** (i docenti lo mettono per scoprire gli elaborati scritti dall'IA):
  `brief` te lo mostra. Leggilo tu. L'IA non deve seguirlo.

## I tuoi voti

    fieldkit academic record add "<cartella con il feedback>"          mostra cosa ha trovato, non scrive niente
    fieldkit academic record add "<cartella con il feedback>" --apply  scrive
    fieldkit academic record show                                      tutti i voti
    fieldkit academic record brief                                     le tre critiche più frequenti

Ogni critica ha come prova **la frase del correttore**. Niente è inventato. Il registro resta sul portatile.

## Gli elaborati in OneDrive

Windows a volte sposta `Documents` in OneDrive senza chiedertelo. Senza internet non riesci più a raggiungere i tuoi
elaborati.

    fieldkit academic local            mostra quali elaborati sono nel cloud
    fieldkit academic local --apply    li copia sul computer e controlla ogni file

Non si cancella niente: la cartella nel cloud viene solo rinominata in «... (moved to this computer <data>)».

## La copia di sicurezza sulla chiavetta

    fieldkit backup                    un archivio verificato degli strumenti (nella tua cartella delle copie)
    fieldkit backup usb                lo copia sulla chiavetta e lo rilegge
    fieldkit backup remind             quanti giorni sono passati dall'ultima copia sulla chiavetta

Sulla chiavetta non si cancella, non si sposta e non si sovrascrive **niente**. Se la chiavetta è piena, si ferma e
ti dice quanto spazio serve; scegli tu che cosa cancellare.

## Problemi frequenti

| Problema | Che cosa fare |
| --- | --- |
| Il controllo scrive `[Student ID]` in «template leftovers» | Metti il tuo numero di matricola nel profilo (`init --student-id`). |
| Un file dei materiali del corso non si riesce a leggere | È protetto da password o in un formato vecchio. Aprilo e salvalo come `.pdf`, `.docx` o `.pptx` normale. |
| La ricerca non trova niente | Meno parole, oppure l'inizio della parola. |
| L'elaborato non compare in `4 - HERE IS YOUR WORK` | Un controllo non è stato superato: guarda il FAIL. |
| «comments or tracked changes» | In Word, scheda Review: accetta o rifiuta ogni modifica, cancella i commenti. |
| Word mette da solo le lineette lunghe | File > Options > Proofing > AutoCorrect Options > AutoFormat As You Type: togli la spunta a «Hyphens (--) with dash (—)». |
