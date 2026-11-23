# v0.10.5, in plain language

This update adds everyday desktop tools and makes installing and checking Fieldkit safer on both Windows and Linux.

## What changed for you

- **Check before changing anything:** Before an install or update touches files, Fieldkit now runs a read-only check. It first tests whether Gorilla Size and Gorilla Trash work from a fresh terminal outside the install folder, and then checks whether background services can see them too. If a tool already works in your terminal and only a background service cannot find it, the installer stops and tells you how to fix the service search path instead of reinstalling the program.
- **Gorilla Size and Gorilla Trash everywhere:** One setup command installs both programs for your account on Windows and Linux so they work from any folder, and adds a Recycle Bin menu entry on Windows while keeping the confirmation prompt before emptying anything.
- **One clean-slate check across your computers:** Instead of running dozens of manual checks, one read-only audit reports what is sitting in agent scratch folders on Windows and Linux without changing any files. For remote checks, it asks whether to use the cable, a remote login, or both, and never silently switches connection types.
- **Windows Take Ownership in File Explorer:** Right-clicking a file, folder, or local drive now offers a Fieldkit Take Ownership menu item that asks for administrator approval once, changes ownership without quietly altering permissions, and keeps permission recovery as a separate explicit drive action.
- **Faster search startup:** Setup now prepares the search helper ahead of time so normal searches start directly without recompiling on every run.
- **Linux background service path fix:** The Linux cable listener service now includes your user command folder so it can find tools installed for your account.

## What did not change

Nothing is deleted or emptied automatically. The read-only audit and preflight checks never modify your files, and Gorilla Trash still asks before emptying the Recycle Bin.
