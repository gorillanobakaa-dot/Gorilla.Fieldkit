# Guía: las herramientas académicas de Fieldkit

Para estudiantes, en español, sin términos técnicos. Funciona igual en cualquier portátil (Windows o Linux) y en
cualquiera de los siete países: Reino Unido, Estados Unidos, España, Portugal, Italia, Alemania, Rumanía.

Otros idiomas: [English](GUIDE.md) · [Português](GUIDE.pt.md) · [Italiano](GUIDE.it.md) · [Deutsch](GUIDE.de.md) · [Română](GUIDE.ro.md)

## Qué hacen por ti, en pocas palabras

- Te preparan la carpeta del trabajo, con un sitio para cada cosa.
- Leen el enunciado del trabajo y te dicen qué tipo de trabajo es, cuántas palabras y qué tienes que demostrar.
- Leen los apuntes una sola vez, para que puedas **buscar** en ellos y obtener la página que citar.
- Te hacen una plantilla de Word o PowerPoint en la que escribes directamente.
- **Revisan el trabajo** antes de entregarlo: las citas, la bibliografía, las rayas largas, el idioma, la estructura,
  el número de palabras, los fragmentos copiados y tu nombre (la corrección es anónima).
- Ponen el trabajo en la carpeta `4 - HERE IS YOUR WORK` **solo** cuando ha superado todas las comprobaciones.
- Llevan la cuenta de tus notas y de lo que te dicen los correctores, para que sepas en qué fijarte la próxima vez.

## Una sola vez, al principio

1. **Tu perfil.** Tu nombre, tu país y tu número de estudiante:

       fieldkit academic init --name "Nombre Apellido" --country es --student-id 12345678 --ui es

   `--country` es uno de: `uk us es pt it de ro`. El país decide el tamaño de página, la fecha, la ortografía y el
   estilo de citación. `--ui es` hace que el menú te hable en español. Tu nombre nunca llega a GitHub.

2. **El acceso directo del escritorio:**

       fieldkit academic shortcut

   Aparece **Fieldkit Academic** en el escritorio. Doble clic: se abre el menú con números.

3. **La autoprueba:** `fieldkit academic preflight`. Tiene que decir **PREFLIGHT OK**. Si dice otra cosa, alguien
   (o una IA) ha estropeado las herramientas: no las uses así.

## Por tu cuenta, sin IA: el menú

Doble clic en **Fieldkit Academic** o `fieldkit academic menu`. Eliges un número y respondes a las preguntas. La
carpeta del trabajo se recuerda: la próxima vez solo pulsas Intro.

### Los pasos para un trabajo nuevo

| Paso | En el menú | Qué hace |
| --- | --- | --- |
| 1 | 1 | Crea la carpeta del trabajo. Te pregunta la **fecha límite** y la **fecha de subida** (por ejemplo, en Turnitin). Nunca las adivina. |
| 2 | — | Pones el enunciado en `1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/` y los apuntes en `1 - DROP YOUR STUDY MATERIALS HERE/`. |
| 3 | 2, luego 2a | Lee el enunciado y propone el tipo, el número de palabras y la asignatura. Con 2a los escribe. |
| 4 | 3 | Lee todos los apuntes una vez (la primera vez tarda más). |
| 5 | 4 | Buscas en los apuntes. Obtienes el fragmento y la **página** que citar. |
| 6 | 5 | Te hace la plantilla de Word/PowerPoint para el tipo de trabajo. |
| 7 | — | Escribes. |
| 8 | 6 | **La revisión completa.** Primero el veredicto, luego lo primero que hay que arreglar. |
| 9 | 7 | En cualquier momento: cuántos días te quedan y el único paso siguiente. |

La revisión escribe la misma lista también como archivo, `3 - Drafts/TO FIX.md`, para que marques lo que has
arreglado.

### Qué significan las palabras de la revisión

- **PASS**: ha superado la comprobación.
- **FAIL**: hay que arreglarlo. Mientras haya un FAIL, nada entra en `4 - HERE IS YOUR WORK`.
- **WARN**: míralo tú. No impide la entrega.
- **SKIP**: no se ha comprobado. **No es lo mismo que PASS.** Por ejemplo, «no hay apuntes con los que comparar» no
  significa que no hayas copiado nada.

### La fecha de consulta de las fuentes web

Cuando lees una página de internet que citas: menú → 19, o `fieldkit academic accessed FOLDER URL`. Pone **la fecha
de hoy**, en el formato de tu país. La fecha tiene que ser la real: una fecha inventada es información falsa en la
bibliografía.

## Con una IA (Claude, Gemini u otra)

La IA ejecuta los mismos comandos. Tú le dices lo que quieres. Mensajes listos para copiar (cambia lo que está entre
`< >`):

**Trabajo nuevo:**
> Crea la carpeta del trabajo con `fieldkit academic setup "<carpeta>"`. Pregúntame la fecha límite y la fecha de
> subida; no las adivines.

**He puesto el enunciado y los apuntes:**
> Ejecuta `fieldkit academic brief "<carpeta>"` y explícame brevemente el tipo, el número de palabras y los learning
> outcomes. No apliques nada hasta que yo diga «sí».

**Pregunta sobre los apuntes:**
> ¿Qué dicen los apuntes sobre `<tema>`? No abras los PDF enteros. Usa
> `fieldkit academic search "<carpeta>" "<palabras>"` y dame las páginas.

**Revisa el trabajo:**
> Ejecuta `fieldkit academic check "<carpeta>" "<trabajo>"` y explícame el veredicto, cada FAIL y qué arreglo,
> luego cada WARN.

### Señales de que la IA trabaja mal

- Dice «he leído el PDF» y ha tardado mucho: ha leído el archivo entero. Dile que use `search`.
- Te da una cita **sin página**, o una página sin enseñarte de dónde sale: pídele el resultado de la búsqueda. Las
  páginas inventadas son plagio.
- Dice que el trabajo está listo, pero **no está** en `4 - HERE IS YOUR WORK`: no está listo. Haz tú la revisión.
- Se inventa una referencia: la comprobación en línea la detecta. No la uses.
- El enunciado tiene **texto oculto para la IA** (los profesores lo ponen para pillar los trabajos escritos por IA):
  `brief` te lo muestra. Léelo tú. La IA no puede seguirlo.

## Tus notas

    fieldkit academic record add "<carpeta con comentarios>"          muestra lo que ha encontrado, no escribe nada
    fieldkit academic record add "<carpeta con comentarios>" --apply  lo escribe
    fieldkit academic record show                                     todas las notas
    fieldkit academic record brief                                    las tres cosas que más te reprochan

Cada reproche lleva como prueba **la frase del corrector**. Nada es inventado. El registro se queda en el portátil.

## Los trabajos en OneDrive

Windows a veces mueve `Documents` a OneDrive sin preguntarte. Sin internet, ya no llegas a tus trabajos.

    fieldkit academic local            muestra qué trabajos están en la nube
    fieldkit academic local --apply    los copia al ordenador y comprueba cada archivo

No se borra nada: la carpeta de la nube solo se cambia de nombre a «... (moved to this computer <fecha>)».

## La copia de seguridad en un USB

    fieldkit backup                    un archivo comprobado de las herramientas (en tu carpeta de copias)
    fieldkit backup usb                lo copia al USB y lo vuelve a leer
    fieldkit backup remind             cuántos días han pasado desde la última copia al USB

En el USB no se borra, no se mueve y no se sobrescribe **nada**. Si el USB está lleno, se para y te dice cuánto
espacio hace falta; tú eliges qué borrar.

## Problemas frecuentes

| Problema | Qué hacer |
| --- | --- |
| La revisión dice `[Student ID]` en «template leftovers» | Pon tu número de estudiante en el perfil (`init --student-id`). |
| Un archivo de los apuntes no se puede leer | Está bloqueado con contraseña o tiene un formato antiguo. Ábrelo y guárdalo como `.pdf`, `.docx` o `.pptx` normal. |
| La búsqueda no encuentra nada | Menos palabras, o el principio de la palabra. |
| El trabajo no aparece en `4 - HERE IS YOUR WORK` | Ha fallado una comprobación: mira el FAIL. |
| «comments or tracked changes» | En Word, la pestaña Review: acepta o rechaza cada cambio y borra los comentarios. |
| Word pone rayas largas por su cuenta | File > Options > Proofing > AutoCorrect Options > AutoFormat As You Type: desmarca «Hyphens (--) with dash (—)». |
