# Ce este "Mesh" și ce face de fapt bifa de pe hub

## Mesh, pe bune

**O rețea mesh înseamnă mai multe puncte de acces WiFi care se poartă ca unul
singur.** Același nume de rețea, aceeași parolă, iar dispozitivele trec de la
unul la altul pe măsură ce te muți prin casă, fără să pice conexiunea.

Fără mesh, două puncte de acces sunt două rețele separate, iar laptopul se agață
de primul până când semnalul moare complet. Cu mesh, infrastructura îi spune
activ clientului *"mută-te la celălalt, e mai bun"*, prin trei standarde:

```
802.11k   hub-ul îi dă clientului lista punctelor de acces vecine
802.11v   hub-ul îi sugerează clientului să se mute la unul anume
802.11r   mutarea se face rapid, fără reautentificare completă
```

Lucrul acesta e real și util, într-o casă unde un singur punct de acces nu
acoperă tot.

## Dar bifa de obicei nu face asta

Pe hub-urile de consum, "Enable Mesh" nu e, de cele mai multe ori, **o
funcționalitate care adaugă ceva. Este o constrângere care blochează alte
setări.**

Citat direct din firmware-ul unui hub (EE Smart 5G Hub 7 Plus, citit
2026-10-13):

> *"If enabled, then only WiFi settings compatible with Mesh WiFi will be
> available to be selected on these pages."*

Nu creează o rețea mesh. Te obligă să folosești doar setări care ar funcționa cu
una, ca extenderele producătorului să se asocieze fără apeluri la suport.

Mesajele de eroare spun exact cât costă:

```
"Please set the 802.11 mode to 802.11be before enabling Mesh"
"Only WPA2/WPA3 and WPA3 modes are compatible with Mesh settings"
"Auto mode is not compatible with Mesh settings"
```

Deci: mod Wi-Fi 7 obligatoriu, securitate modernă obligatorie, fără criptare
automată, și un singur nume de rețea pentru ambele benzi.

## De ce e bifat din fabrică

Comercial, nu tehnic. **Producătorul vinde extendere mesh.** Dacă hub-ul pleacă
din fabrică deja compatibil, clientul cumpără extenderul, îl bagă în priză și
merge. Dacă ar pleca nebifat, o parte dintre clienți ar suna la suport fiindcă
extenderul nu se asociază.

Costul îl plătesc toți cei care nu cumpără niciodată unul: setări blocate pentru
o compatibilitate de care nu au nevoie.

## Ce înseamnă pentru un dispozitiv mai vechi

Un singur nume de rețea pentru ambele benzi înseamnă **band steering**: hub-ul
decide pe ce bandă pune fiecare dispozitiv. Dacă placa ta de rețea e doar de
2.4 GHz, hub-ul încearcă la fiecare conectare să te împingă pe 5 GHz și apoi
renunță. Pe unele firmware-uri asta se vede ca asociere lentă sau ca un
dispozitiv care se tot deconectează.

Multe hub-uri oferă un **"Compatibility SSID"**, care creează o rețea separată,
simplă, doar pe 2.4 GHz, fără negocierea de steering. Exact pentru asta există.
Face conexiunea unui dispozitiv vechi mai *stabilă*. Nu o face mai rapidă,
fiindcă placa rămâne plafonul.

## Dacă nu ai extendere

Debifarea opțiunii Mesh nu te costă nimic și îți dă înapoi controlul asupra
modului 802.11, al setărilor de securitate și al configurării benzilor.

Dacă ai extendere, las-o bifată. Restricțiile sunt prețul ca ele să funcționeze.
