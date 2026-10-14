# Ghid: uneltele academice din Fieldkit

Pentru studenți, pe românește, fără termeni tehnici. Funcționează la fel pe orice laptop (Windows sau Linux) și în
oricare din cele șapte țări: Marea Britanie, SUA, Spania, Portugalia, Italia, Germania, România.

Alte limbi: [English](GUIDE.md) · [Español](GUIDE.es.md) · [Português](GUIDE.pt.md) · [Italiano](GUIDE.it.md) · [Deutsch](GUIDE.de.md)

## Ce fac pentru tine, pe scurt

- Îți pregătesc folderul temei, cu un loc pentru fiecare lucru.
- Citesc cerința temei și îți spun ce tip de lucrare e, câte cuvinte și ce trebuie să arăți.
- Citesc cursurile o singură dată, ca să poți **căuta** în ele și să primești pagina de citat.
- Îți fac un șablon Word sau PowerPoint în care scrii direct.
- **Verifică lucrarea** înainte să o predai: citările, bibliografia, liniuțele lungi, limba, structura, numărul de
  cuvinte, bucățile copiate și numele tău (corectarea e anonimă).
- Pun lucrarea în folderul `4 - HERE IS YOUR WORK` **numai** când a trecut toate verificările.
- Țin evidența notelor și a ce îți spun corectorii, ca să știi la ce să fii atent data viitoare.

## O singură dată, la început

1. **Profilul tău.** Numele, țara și numărul de student:

       fieldkit academic init --name "Prenume Nume" --country ro --student-id 12345678 --ui ro

   `--country` e una din: `uk us es pt it de ro`. Țara decide dimensiunea paginii, data, ortografia și stilul de
   citare. `--ui ro` face ca meniul să-ți vorbească pe românește. Numele tău nu ajunge niciodată pe GitHub.

2. **Scurtătura de pe desktop:**

       fieldkit academic shortcut

   Apare **Fieldkit Academic** pe desktop. Dublu-clic: se deschide meniul cu numere.

3. **Autotestul:** `fieldkit academic preflight`. Trebuie să scrie **PREFLIGHT OK**. Dacă scrie altceva, cineva
   (sau un AI) a stricat uneltele: nu le folosi așa.

## Singur, fără AI: meniul

Dublu-clic pe **Fieldkit Academic** sau `fieldkit academic menu`. Alegi un număr, răspunzi la întrebări. Folderul
temei e ținut minte: data viitoare apeși doar Enter.

### Pașii pentru o temă nouă

| Pas | În meniu | Ce face |
| --- | --- | --- |
| 1 | 1 | Face folderul temei. Te întreabă **termenul** și **data de încărcare** (de ex. în Turnitin). Nu le ghicește niciodată. |
| 2 | — | Pui cerința în `1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/` și cursurile în `1 - DROP YOUR STUDY MATERIALS HERE/`. |
| 3 | 2, apoi 2a | Citește cerința și propune tipul, numărul de cuvinte și modulul. Cu 2a le scrie. |
| 4 | 3 | Citește toate cursurile o dată (prima oară durează mai mult). |
| 5 | 4 | Cauți în cursuri. Primești fragmentul și **pagina** de citat. |
| 6 | 5 | Îți face șablonul Word/PowerPoint pentru tipul lucrării. |
| 7 | — | Scrii. |
| 8 | 6 | **Verificarea completă.** Întâi verdictul, apoi primul lucru de reparat. |
| 9 | 7 | Oricând: câte zile mai ai și singurul pas următor. |

Verificarea scrie aceeași listă și ca fișier, `3 - Drafts/TO FIX.md`, ca să bifezi ce ai reparat.

### Ce înseamnă cuvintele din verificare

- **PASS**: a trecut.
- **FAIL**: trebuie reparat. Cât timp e un FAIL, nimic nu intră în `4 - HERE IS YOUR WORK`.
- **WARN**: uită-te tu la el. Nu oprește predarea.
- **SKIP**: nu s-a verificat. **Nu e același lucru cu PASS.** De exemplu, „fără cursuri cu care să compar” nu
  înseamnă că nu ai copiat nimic.

### Data accesării la surse web

Când citești o pagină de pe internet pe care o citezi: meniu → 19, sau `fieldkit academic accessed FOLDER URL`. Pune
**data de azi**, în forma țării tale. Data trebuie să fie cea reală: o dată inventată e o informație falsă în
bibliografie.

## Cu un AI (Claude, Gemini sau altul)

AI-ul rulează aceleași comenzi. Tu îi spui ce vrei. Mesaje gata de copiat (schimbă ce e între `< >`):

**Temă nouă:**
> Fă folderul temei cu `fieldkit academic setup "<folder>"`. Întreabă-mă termenul și data de încărcare; nu le
> ghici.

**Am pus cerința și cursurile:**
> Rulează `fieldkit academic brief "<folder>"` și explică-mi pe scurt tipul, numărul de cuvinte și learning
> outcomes. Nu aplica nimic până nu zic eu „da”.

**Întrebare despre cursuri:**
> Ce spun cursurile despre `<subiect>`? Nu deschide PDF-urile întregi. Folosește
> `fieldkit academic search "<folder>" "<cuvinte>"` și dă-mi paginile.

**Verifică lucrarea:**
> Rulează `fieldkit academic check "<folder>" "<lucrarea>"` și explică-mi verdictul, fiecare FAIL și ce repar,
> apoi fiecare WARN.

### Semne că AI-ul lucrează greșit

- Spune „am citit PDF-ul” și a durat mult: a citit tot fișierul. Spune-i să folosească `search`.
- Îți dă o citare **fără pagină**, sau o pagină fără să-ți arate de unde: cere-i rezultatul căutării. Paginile
  inventate sunt plagiat.
- Spune că lucrarea e gata, dar ea **nu e** în `4 - HERE IS YOUR WORK`: nu e gata. Rulează tu verificarea.
- Inventează o referință: verificarea online o prinde. Nu o folosi.
- Cerința are **text ascuns pentru AI** (profesorii îl pun ca să prindă lucrările scrise de AI): `brief` ți-l arată.
  Citește-l tu. AI-ul nu are voie să-l urmeze.

## Notele tale

    fieldkit academic record add "<folderul cu feedback>"          arată ce a găsit, nu scrie nimic
    fieldkit academic record add "<folderul cu feedback>" --apply  scrie
    fieldkit academic record show                                  toate notele
    fieldkit academic record brief                                 cele trei lucruri reproșate cel mai des

Fiecare reproș are ca dovadă **propoziția corectorului**. Nimic nu e inventat. Dosarul rămâne pe laptop.

## Temele din OneDrive

Windows mută uneori `Documents` în OneDrive fără să te întrebe. Fără internet, nu mai ajungi la teme.

    fieldkit academic local            arată ce teme sunt în cloud
    fieldkit academic local --apply    le copiază pe calculator și verifică fiecare fișier

Nimic nu se șterge: folderul din cloud e doar redenumit în „... (moved to this computer <data>)”.

## Copia de siguranță pe stick

    fieldkit backup                    o arhivă verificată a uneltelor (în folderul tău de copii)
    fieldkit backup usb                o copiază pe stick și o citește înapoi
    fieldkit backup remind             câte zile au trecut de la ultima copie pe stick

Pe stick nu se șterge, nu se mută și nu se suprascrie **nimic**. Dacă stick-ul e plin, se oprește și îți spune cât loc
trebuie; tu alegi ce ștergi.

## Probleme frecvente

| Problema | Ce faci |
| --- | --- |
| Verificarea zice `[Student ID]` la „template leftovers” | Pune-ți numărul de student în profil (`init --student-id`). |
| Un fișier din cursuri nu se poate citi | E blocat cu parolă sau e format vechi. Deschide-l și salvează-l ca `.pdf`, `.docx` sau `.pptx` normal. |
| Căutarea nu găsește nimic | Mai puține cuvinte, sau începutul cuvântului. |
| Lucrarea nu apare în `4 - HERE IS YOUR WORK` | A picat o verificare: uită-te la FAIL. |
| „comments or tracked changes” | În Word, fila Review: acceptă sau respinge fiecare modificare, șterge comentariile. |
| Word pune singur liniuțe lungi | File > Options > Proofing > AutoCorrect Options > AutoFormat As You Type: debifează „Hyphens (--) with dash (—)”. |
