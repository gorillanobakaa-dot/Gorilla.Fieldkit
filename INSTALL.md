# How to install Fieldkit

This guide covers four things, in order:

1. installing Fieldkit, either by asking your AI to do it or by doing it yourself;
2. where to put it and how to save the settings;
3. connecting it to **Gorilla OpenCode** and to **LM Studio**, so a model can use it from inside either one;
4. telling a small model that Fieldkit exists, so it uses the tools instead of guessing.

---

## What it does (Layman Track)

Fieldkit is a folder of tested tools. Installing it means: download the folder, let Python
set it up, and check that its tests pass. Then you tell your AI program where to find it.

After that, the AI sees a short list of new tools, such as "find a tool for this job" and
"run it safely". The tools do the hard work. The AI only picks the right one.

You need about 50 MB of space and 10 minutes. Nothing is uploaded anywhere, and you can
remove it at any time.

## Technical Definition (Developer Track)

An editable pip install of a Python 3.11+ package from Git, verified by its pytest suite,
then registered as a stdio MCP server (`fieldkit mcp`, JSON-RPC 2.0, protocol 2024-11-05)
in each client's MCP configuration. Model awareness comes from two places: the MCP
`tools/list` response, which every MCP client passes to the model, and a short
instruction block in the client's system prompt or the project's `AGENTS.md`.

---

## Step 1: choose where to put it

Pick one folder and keep Fieldkit there. The rest of this guide uses these places:

| System | Folder |
|--------|--------|
| Windows | `C:\Users\<you>\Documents\Fieldkit` |
| Linux | `~/Fieldkit` |

Any folder works, but keep the path free of spaces if you can: some programs handle
spaces in paths badly.

## Step 2, way A: ask your AI to install it

Use this if your AI program can run commands on your computer (Gorilla OpenCode, Claude
Code, Gemini CLI and similar can). Paste this prompt into it, unchanged:

```text
Install Fieldkit for me. Do each step, show me the output, and stop if a step fails.
1. Check that Python 3.11 or newer and Git are installed: python --version, git --version.
2. Clone https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit into
   Documents\Fieldkit (Windows) or ~/Fieldkit (Linux).
3. In that folder run: python -m pip install -e ".[test]"
4. Run: python -m pytest -q   and show me the last line.
5. Run: fieldkit host   and show me what it prints.
Do not change any other file or setting. Do not add Fieldkit to any program's
settings yet: I will decide that after I see the test result.
```

- **Pass:** the AI shows a test line with `0 failed` (or no `failed` at all) and
  `fieldkit host` prints your system.
- **Fail:** the AI shows an error. Paste the error back to it and ask what is missing.
  The most common cause is an old Python.

## Step 2, way B: install it yourself

Open a terminal (on Windows: press the Windows key, type `PowerShell`, press Enter).
Type each line and press Enter after each one.

```text
cd $HOME\Documents
git clone https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit Fieldkit
cd Fieldkit
python -m pip install -e ".[test]"
python -m pytest -q
fieldkit host
```

On Linux, use `cd ~` on the first line instead.

- **Pass:** the `pytest` line ends with no failures. Some tests say `skipped`; each one
  names a tool that you have not downloaded yet, which is expected.
- **Pass:** `fieldkit host` prints your system and your Python version.
- **Fail: `python` is not recognised.** Install Python 3.11 or newer from python.org and
  tick "Add python.exe to PATH" in the installer.
- **Fail: `fieldkit` is not recognised.** Use `python -m fieldkit` instead, everywhere in
  this guide. It does the same thing.

## Step 3: save your own settings

Fieldkit keeps your private settings in files that are never uploaded:

```text
copy fieldkit.local.example.json fieldkit.local.json
```

(On Linux: `cp` instead of `copy`.) Open `fieldkit.local.json` in a text editor and put
your own name, email and login name in the `privacy.terms` list. The privacy scan then
finds them in any file before you publish it. Save the file.

- **Pass:** `fieldkit privacy scan fieldkit.local.json` reports your own words as findings.
  That proves the scan knows them. (That file is ignored by Git, so it never leaves your
  computer.)

## Step 4: connect it to Gorilla OpenCode

1. Close Gorilla OpenCode.
2. Make a copy of your settings file as a backup:
   `C:\Users\<you>\.config\gorilla-opencode\config.json` on Windows,
   `~/.config/gorilla-opencode/config.json` on Linux.
3. Open `config.json` in a text editor. Just after the first `{`, add:

   ```json
   "mcpServers": {
     "fieldkit": { "type": "stdio", "command": "fieldkit", "args": ["mcp"] }
   },
   ```

   If `fieldkit` is not recognised on your computer, put the full path to `fieldkit.exe`
   in `command` instead, for example
   `C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python312\\Scripts\\fieldkit.exe`
   (in JSON, every `\` is written twice).
4. Save the file and start Gorilla OpenCode.

- **Pass:** ask it "What Fieldkit tools can you use?". It lists `discover`, `describe`,
  `run`, `undo`, `next` and `readiness`, and asks your permission the first time it uses one.
- **To undo:** put the backup copy back.

## Step 5: connect it to LM Studio

LM Studio can use MCP tools in its own chat window, with a model that supports tool use.

1. Find LM Studio's `mcp.json`. It lives in LM Studio's home folder, which is usually
   `C:\Users\<you>\.lmstudio\`. If the file `C:\Users\<you>\.lmstudio-home-pointer`
   exists, it names the real home folder instead (on the author's laptop:
   `C:\Users\<you>\.cache\lm-studio\`). LM Studio can also open this file for you from
   its app, in the section about MCP or program settings.
2. Make a backup copy of `mcp.json`.
3. Put the Fieldkit entry inside `mcpServers`:

   ```json
   {
     "mcpServers": {
       "fieldkit": { "command": "fieldkit", "args": ["mcp"] }
     }
   }
   ```

4. Save the file. Restart LM Studio if the tools do not appear.

- **Pass:** in a chat with a tool-capable model, ask "What Fieldkit tools can you use?".
  LM Studio shows the Fieldkit tools and asks you before each call.
- **To undo:** put the backup copy back.

## Step 6: tell the model that Fieldkit exists

Connecting Fieldkit gives the model the tools. A small model still tends to guess instead
of using them, so tell it plainly. Paste this block, unchanged, into the right place:

```text
You have Fieldkit, a set of tested tools. Use them instead of guessing.
- Not sure which tool fits? Call discover with the job in plain words.
- Before using a tool, call describe to see its inputs and what it changes.
- Tools that change files: run with mode "preview" first, show the owner, then apply.
- Never claim a result the tool did not report. Quote its answer.
- In a pipeline, call next and do exactly the one step it gives.
```

Where to paste it:

| Program | Where |
|---------|-------|
| Gorilla OpenCode | A file named `AGENTS.md` in the root folder of the project you work on (Gorilla OpenCode reads it for your own projects). If the file exists, add the block at the end. |
| LM Studio | The **System Prompt** box of the chat, or of the model's preset so it is there in every chat. |
| Claude Code | `CLAUDE.md` in the project root. |
| Gemini CLI | `GEMINI.md` in the project root. |

The block is about 90 words, so it costs very little of a small context window.

You can also install Fieldkit's short skill files, which Claude Code, Codex and Gemini CLI
read on their own:

```text
python install_skills.py
```

- **Pass:** start a new chat and ask "Find me a tool that checks a Word file is safe to
  send". The model calls `discover` and finds `office-deliver`.

## Removing Fieldkit

1. Remove the `fieldkit` entry from each program's MCP settings (or put the backups back).
2. Remove the instruction block from `AGENTS.md` or the system prompt.
3. Run `python -m pip uninstall fieldkit`.
4. Delete the Fieldkit folder.

Fieldkit changes nothing else on your computer.
