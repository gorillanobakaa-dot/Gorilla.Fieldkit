# De ce o rețea numită `:(` strică uneltele automate

## Pe scurt

Numele unei rețele WiFi este **text ales de un străin**, care ajunge direct în
mijlocul scripturilor tale. Majoritatea uneltelor îl tratează ca și cum ar fi un
cuvânt cuminte, gen `HomeWiFi`. Nu este. Este exact ce a avut chef să scrie omul
care a configurat rețeaua.

Numele `:(` are trei caractere, și fiecare atacă alt strat.

## `:` două puncte, separatorul de câmpuri de aproape peste tot

În Unix, caracterul `:` este modul implicit de a spune "aici se termină câmpul".

```
root:x:0:0:root:/root:/bin/bash        o linie din /etc/passwd
/usr/bin:/bin:/usr/local/bin           un PATH
192.168.1.1:8080                       adresă și port
fe80::1                                o adresă IPv6
```

Uneltele de rețea fac la fel. `nmcli -t` scrie o rețea pe linie, cu câmpurile
despărțite prin `:`

```
HomeWiFi:6:2437 MHz:71:WPA2
   nume  canal  frecvență  semnal  securitate
```

Un program citește asta despărțind după `:` și ia câmpul 2 ca fiind canalul.
Acum pune un `:` **în interiorul numelui**:

```
:(:13:2472 MHz:100:WPA2
```

Despărțind de la stânga la dreapta, primul câmp iese gol, apoi `(`, apoi `13`.
**Toate câmpurile s-au deplasat cu o poziție.** Programul citește acum canalul de
unde ar trebui să fie frecvența, apelează `int("2472 MHz")` și crapă. Sau, și mai
rău, nu crapă și raportează liniștit cifre greșite.

`nmcli` știe că e periculos, motiv pentru care **escapează** caracterul ca `\:`
atunci când îl afișează:

```
\:(:13:2472 MHz:100
```

Escaparea aceea este un avertisment. Un program care nu o desface va afișa rețeaua
ca `\:(` la nesfârșit, iar unul care desparte naiv este deja greșit.

**Soluția:** desparte de la DREAPTA, ca numele să absoarbă toate caracterele `:`
pe care le conține, și desfă escaparea după aceea.

## `(` și `)` parantezele, care sunt de fapt comenzi

Astea sunt mult mai periculoase, fiindcă în două limbaje diferite nu sunt text,
ci instrucțiuni.

**În shell, parantezele deschid o sub-comandă.** Asta este eroare de sintaxă:

```sh
echo :(          # bash: syntax error near unexpected token `('
```

Deci un script care construiește o comandă lipind numele într-un șir pur și
simplu moare. Ghilimelele rezolvă problema, și majoritatea scripturilor uită:

```sh
fieldkit wifi watch --ssid ':('     # corect
fieldkit wifi watch --ssid :(       # mort
```

**Într-o expresie regulată, parantezele înseamnă "grup".** Căutarea textului
literal `:(` cu o căutare după tipar nu caută rețeaua ta, ci deschide un grup pe
care nu îl închide niciodată:

```
grep -E ':('     # subtipar neterminat: eroare
grep -F ':('     # -F înseamnă "text simplu, nu tipar": funcționează
```

Așa că orice unealtă care filtrează rețelele după un tipar fie crapă, fie
potrivește altceva.

## De ce este o problemă reală de securitate, nu doar o enervare

Imaginează-ți un script care îți notează conexiunea:

```sh
echo "conectat la $SSID" >> log.txt      # fără ghilimele
```

Cineva își numește rețeaua:

```
test; rm -rf ~; echo
```

Punctul și virgula încheie prima comandă, iar următoarea rulează **cu drepturile
tale**. Asta se numește **injectare de comenzi**, iar numele rețelei este date
venite de la un atacator pe care un script neglijent le-a executat.

Caracterul `|` face parte din aceeași familie. Fără ghilimele, `|` împarte o
comandă în două și o dă pe prima ca intrare celei de-a doua. Un nume care conține
`|` transformă o linie de jurnal într-o înlănțuire de comenzi.

**Deci `:(` este un test gratuit și inofensiv pentru o vulnerabilitate reală.**
Dacă o unealtă îl tratează corect, probabil escapează cum trebuie. Dacă crapă,
dacă afișează `\:(`, sau dacă raportează canalul greșit, atunci prelucrează
numele în mod nesigur, iar aceeași slăbiciune ar fi executat ceva mult mai urât.

## Regula, pentru oricine scrie unelte

**Nu construi niciodată o comandă dintr-un nume. Nu despărți niciodată un nume
după poziție.**

1. Trimite argumentele ca **listă**, nu ca un singur șir lipit, ca shell-ul să nu
   fie implicat deloc, iar parantezele și punctul și virgula să rămână text
   inofensiv.
2. Pune ghilimele la orice ajunge totuși într-un shell.
3. Desparte ieșirea cu separatori **de la dreapta**, și desfă escaparea după.
4. Caută numele ca **text fix**, niciodată ca tipar.
5. Tratează numele ca date, întotdeauna. Îi aparțin altcuiva.

O unealtă care supraviețuiește lui `:(` va supraviețui și celor care nu au fost
alese cu gânduri bune.
