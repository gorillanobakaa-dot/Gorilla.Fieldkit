---
name: fieldkit-icons
description: Sharp desktop icons on any computer - find out why desktop icons look blurry or soft, rebuild the icon cache (Windows Explorer, GTK, KDE) with a backup, install a one-click "Fix blurry icons" button, read and check the frames inside .ico/.exe/.dll files, and build crisp recoloured .ico files from one big picture. Use when icons look blurry, soft or pixelated, after installs or display changes, or when making icons.
---

# Sharp icons (fieldkit icons)

Every command ends with `NEXT:` or says `not-this-platform`. Nothing names anyone's folders: Python and the Fieldkit
folder are found on the machine it runs on; backups and the button's launcher live in `<Fieldkit>/local/icons`.

| The person says | Run |
|---|---|
| "My desktop icons are blurry" | `fieldkit icons cache` - shows whether the icon files are sharp (then it is the cache) or not (then the file is the problem) |
| "Fix it" | `fieldkit icons cache --apply` - countdown, then the cache is moved to a backup and rebuilt. Windows: Explorer restarts (taskbar and File Explorer windows close and come back; programs and unsaved work are not touched). Tell the person before running it. |
| "I keep having to do this" (Windows) | `fieldkit icons install-fix-button` - a "Fix blurry icons" desktop shortcut and right-click entry; undo: `fieldkit icons uninstall-fix-button` |
| "Is this icon file any good?" | `fieldkit icons frames FILE` (sizes and sharpness), `fieldkit icons check-ico FILE.ico [--master BIG.png]` (exit 3 = a soft or fake frame) |
| "Make me icons from this picture" | copy `fieldkit/icons/example-profile.json` beside the picture, fill it in, `fieldkit icons build PROFILE.json` |
| "Put this icon on that shortcut" (Windows) | `fieldkit icons set-icon "SHORTCUT.lnk" --ico FILE.ico` (backs up first; undo with `restore`) |

Rules: never change a shortcut nobody asked about (`fieldkit icons shortcuts` shows what is there and when it changed);
after taking screenshots on Windows, `fieldkit icons tih-check --end` stops a stuck TextInputHost.
