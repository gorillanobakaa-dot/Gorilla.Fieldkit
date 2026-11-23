# v0.10.10, in plain language

This is the complete explanation, not a summary of one. Nothing below is behind a link.

### What changed for you

- **A Gorilla Trash menu in Windows 11 Explorer:** Right-click a folder and the first menu now offers "Recycle with Gorilla Trash". It sends the folder to the Recycle Bin and keeps the hidden folder settings files (desktop.ini and Thumbs.db) in place. In the Recycle Bin itself, "Empty with Gorilla Trash" appears only in the older list under "Show more options", because Windows refuses to put that entry in the new menu. The new menu stays as it is: you chose to keep it.
- **It is installed in a safe way:** The menu comes in a package that Fieldkit signs itself and that your computer is told to trust. The signing key is never shared or published.
- **Tests run less and faster:** When you change a file, the automatic checks on GitHub now run only the tests that touch that file, and a test that already passed is not run again. Fewer machines are started when there is little to check.
- **No more flashing windows:** Fieldkit used to open a brief black console window for every small job it started in the background on Windows. Now they stay hidden.
- **The pre-commit check can no longer fail silently:** A check that looks at PowerShell scripts and generated files crashed on a file with an unusual byte and the commit went through anyway. It now reads that output safely, and if it ever crashes it stops the commit.
- **Smaller fixes:** the Board shows open refusal defects as rows, a memory-hog report no longer cries wolf on normal file-cache use, and agent permission audits cover more assistants.

### What did not change

No tool was removed. Existing commands keep their behavior.

### What was not checked

The trash menu was verified on one Windows 11 computer by the owner. Other Windows builds were not tried by hand.
