# A voice worth listening to

## What this is

Your computer can talk to you. It tells you when a long job has finished, when
it needs you to look at something, and when it has lost the internet and cannot
get back on by itself.

On Windows this has always sounded fine. On Linux it sounded like a robot from
1995, and this is the story of why, and how it was fixed.

## The problem, in one sentence

**Linux does not play a recording of a person. It calculates one.**

The speech program every Linux computer comes with, called espeak, has a
mathematical model of a mouth, a throat and vocal cords. It works out what sound
those would make, and plays that.

It sounds like a robot because it is one. That is the design, not a fault. The
technique is from the 1980s.

There is a second one, called mbrola, which takes real recordings of a person,
chops them into tiny fragments, and glues the fragments back together in a new
order. Closer to human, but the joins never quite line up, so you get a metallic
edge. That technique is from about 1996.

**No setting fixes either of them.** We tried slowing them down and lowering the
pitch. Still a robot, just a calmer robot.

## Why Windows sounds fine and Linux does not

Windows does not use either one. Windows ships modern voices that were made by
training a computer on recordings of real speech. Microsoft pays for them and
includes them.

Linux cannot do that. The reasons are dull and they are not neglect:

**One: size.** A modern voice is about 61 megabytes, for one voice, in one
language. Linux supports around a hundred languages. Nobody is going to put
several gigabytes of voices into a basic installation.

**Two, and this is the real one: Linux speech was built for blind people, not
for notifications.** Someone using a screen reader listens at three hundred to
four hundred and fifty words a minute, all day. What they need is instant
response and perfectly predictable pronunciation. espeak is superb at that.
Modern voices are actually worse for it: they take a moment to start, and their
lovely natural rhythm turns to mush at that speed.

So the people who use Linux speech most genuinely do not want the thing that was
wanted here. Nobody dropped the ball. They built it for somebody else.

## Why nobody noticed for months

The harness checked whether a speech program existed, and reported that
everything was fine.

Which was true. espeak **was** working.

It just never asked the only question that matters: **would a person want to
listen to this?**

That is the single most useful thing learned here, and it is not about speech at
all. A check that confirms a thing exists is not the same as a check that
confirms the thing is any good.

## What was done

A modern voice called piper was installed. It is about 61 megabytes, it runs on
your own computer, and once it is downloaded it never contacts anybody again.

Then, instead of teaching one program to use it, it was plugged in **underneath
everything**. Linux has a layer that all programs talk to when they want to say
something. Fix that layer, and every program gets the better voice without being
told about it: the build announcements, the alerts, the desktop notifications,
the screen reader, and even the AI assistant's own notifications.

One change, everything improves.

## The surprise

The laptop this was done on is from 2012. Its processor is missing an
instruction that modern AI tools normally expect, which is why it cannot run
local AI chat models at all.

The expectation was that a modern voice would not work either.

**It worked, and it is more than twice as fast as it needs to be.** Three
seconds of work produces seven seconds of speech.

The reason is simple once you see it: a voice is a small, specialised thing,
about 61 megabytes. An AI chat model is a large, general thing, often several
gigabytes. One has to know how to say words. The other has to know everything.

The machine was never too weak for this. Nobody had checked.

## What it costs

```
one download, about 115 megabytes, kept forever
nothing afterwards: no subscription, no account, nothing sent anywhere
```

For comparison, that is less than two minutes of high-definition video.

## The embarrassing part, which is being written down on purpose

Getting to that 115 megabytes took about **one gigabyte of downloads**.

Voices were downloaded to listen to them, deleted, then downloaded again when
the requirements changed. The speech program was installed twice because nobody
checked whether half of it was already on the machine. An online service was
tried that refused every request and produced one corrupt file.

That is roughly **ninety per cent waste**.

It is written into the tool itself, as instructions, so that the next person or
the next AI does not repeat it. The rule that came out of it is simple: **keep
everything you download until the job is finished, and only then tidy up.**
Tidying up early is how you download the same thing twice.

## What you will hear now

An American woman's voice, slightly lower and slightly quicker than it comes out
of the box, and loud enough to hear on old laptop speakers.

If the modern voice is ever missing, the old robot is still there as a backup,
so the machine is never silent.
