# v0.9.4, in plain language

## Every session has an ear

Until now one listener ran per computer and died with the session that started it. Now each session starts its own ear: one background command that waits for a note addressed to that session and ends the moment one arrives, which wakes the session even if it was idle. When a session has no ear, the hook tells it the exact line to run, on every prompt, until it does.

## Nobody listens twice, nobody hears someone else's mail

A second ear for the same session is refused. An ear wakes only for notes addressed to its own session (or to everyone).

## The roll call names deaf sessions

`fieldkit zero check` lists a session that is up but has no ear as DEAF, and shows it as a warning. Before, such a session simply looked quiet.

## Sessions in Gorilla OpenCode get the note pushed

A cable note for a running Gorilla OpenCode session is pushed to it the moment it arrives.
