# Why a wifi network called `:(` breaks things

## The short version

**Computers tell things apart using punctuation. If your name contains that same punctuation, the computer loses track of where your name ends.**

That is it. That is the whole idea. Everything below is detail.

---

## The shopping list

Imagine you write a shopping list, and you separate the items with commas.

```
eggs, milk, bread
```

Three items. Obvious.

Now add one more item: **salt, and pepper**.

```
eggs, milk, bread, salt, and pepper
```

**You now have five items, not four.** And two of them are "salt" and "and pepper", which is not what you wanted.

Nothing went wrong with the computer. It did exactly what it was told. The problem is that the thing separating your items also appeared *inside* one of them.

That is what a wifi network called `:(` does.

---

## What actually happened here

A Linux tool lists your wifi networks like this, using a colon to separate each piece of information:

```
yes:MyNetwork:11:2462 MHz:540 Mbit/s:82
```

Read it as: connected? **yes**. Name? **MyNetwork**. Channel? **11**. And so on.

Now name your network `:(` instead:

```
yes:\:(:11:2462 MHz:540 Mbit/s:82
```

Count the colons. The computer counting from the left now thinks the signal strength is **"2462 MHz"**, which is not a number, and it falls over.

**This is not a clever hack.** It is the shopping list problem. It just looks dramatic when it happens.

---

## Three different problems, not one

This is the part worth understanding, because people lump them together and they are genuinely different.

### Problem 1: the name gets lost

**Character:** `:` as in `:(`

The computer uses it as a separator, your name contains one, everything after it shifts along by one place.

**What you see:** the program crashes, or shows nonsense.

**How bad:** annoying. It is loud, so somebody notices and fixes it.

---

### Problem 2: the name becomes an instruction

**Character:** `|` as in `_|_`

Some punctuation does not just separate things. It **means something**. The `|` character tells a computer "take the result of that, and feed it into this".

So if a careless program glues your network name into a command, your name stops being a name and starts being an order.

**The analogy:** you fill in a form with your name. You write:

> *John Smith. Also, please empty the safe.*

A careful clerk files that as a very odd name. **A careless clerk empties the safe.**

**What you see:** usually nothing. It works, quietly, and does something nobody asked for.

**How bad:** this is the serious one. Anybody within range can broadcast a wifi network with any name they like. If a program on your laptop reads that name carelessly, a stranger in the street has just handed your computer an instruction.

---

### Problem 3: the fix for problem 1 creates a new problem

**Character:** `\`, the backslash

Because problem 1 is well known, careful tools mark the awkward character so it is not mistaken for a separator. They put a backslash in front of it. Your `:(` becomes `\:(` on the way out.

That works, as long as **the next program remembers to take the backslash off again**.

If it forgets, it goes looking for a network genuinely called `\:(`, which does not exist. It finds nothing.

**What you see:** nothing at all. No error. It simply does not work.

**How bad:** the worst of the three to find, because there is nothing to find. That is exactly what happened to the watchdog on this machine: it would have tried to put you back online and quietly asked for the wrong network, at the only moment the tool exists for.

---

## Why anybody would do this on purpose

The owner of this machine named his networks `:(`, `:)` and `_|_` deliberately. Those symbols are from the IRC era, before emoji, when people built faces out of punctuation. The last one is rude.

But the reason to keep them is not nostalgia. **It is a free test.**

Any tool that handles his wifi has to cope with a name containing a separator, a shell character, and an escape sequence, all at once, before it can claim to work. It cost about five minutes to name the network and it found two real bugs in an evening, one of which was invisible.

**A test that runs every single day, costs nothing, and never gets switched off because somebody was in a hurry.**

---

## What this means for you

If you are not writing software, there is one practical thing worth knowing:

**If something stops working after you rename a network, a folder, a printer or a phone, try a plain name made only of letters, numbers and hyphens.** If the problem goes away, you have found a program that handles names carelessly. That is the program's fault, not yours, but knowing it saves you an evening.

And if you are feeling devious, name something `:(` and see what falls over.

---

## For the record: what to avoid in a name, and why

| Character | What it does | Risk |
|---|---|---|
| `:` `;` `,` `\|` `=` | separates things in many tools | the name gets split in half |
| `\|` `&` `;` `$` `` ` `` `(` `)` | means "do something" to a terminal | the name becomes an instruction |
| `\` | marks the next character as ordinary | the mark gets left behind, or doubled |
| `"` `'` | wraps text | the wrapping ends early |
| a leading `-` | looks like an option | the name is read as a setting |
| spaces | separates words | one name becomes several |
| nothing at all | a hidden network | tools read it as missing |

None of these should break anything. All of them sometimes do.
