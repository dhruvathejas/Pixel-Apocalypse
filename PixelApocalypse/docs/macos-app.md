# Pixel Apocalypse as a macOS app

The game also ships as a normal Mac app, `Pixel Apocalypse.app`, inside a disk image
`PixelApocalypse-<version>-macOS.dmg`. The zip + `./play.sh` release keeps working unchanged.

## Building

From anywhere, with Apple's Python (no pip, no venv, nothing downloaded):

    /usr/bin/python3 PixelApocalypse/tools/make_app.py            # .app + .dmg
    /usr/bin/python3 PixelApocalypse/tools/make_app.py --no-dmg   # just the .app

Output goes to `dist/` at the repo root (scratch files to `build/macapp/`; both are git-ignored):

    dist/Pixel Apocalypse.app
    dist/PixelApocalypse-0.2.0-alpha-macOS.dmg

The version comes from `VERSION` in `menu.py`. The build needs the Command Line Tools
(`clang`, `iconutil`, `codesign`, `hdiutil`), and takes about 20 seconds. It:

1. compiles `tools/macapp/launcher.c` into `Contents/MacOS/Pixel Apocalypse` (x86_64 only),
2. copies the game into `Contents/Resources/game/` (without `tools/`, `docs/`, `prompts/`,
   `chat.md`, `save/`, `__pycache__`, and pygame's docs / examples / tests),
3. draws `AppIcon.icns` from `player_assets/player_right_asset.png` (stdlib PNG code in
   `tools/pixelart.py`, packed with `iconutil`),
4. writes `Info.plist`, ad-hoc signs the bundle and checks it with
   `codesign --verify --deep --strict`,
5. makes a compressed (UDZO) disk image with the app and an `Applications` link.

## What players need

- **macOS 12.5 (Monterey) or newer** (`LSMinimumSystemVersion`; older macOS refuses to open it).
- **Apple's Command Line Tools 14 or newer** (or Xcode 14 or newer). The app does not carry its
  own Python: it runs on the Python 3.9 that comes with them (older tools have Python 3.7 / 3.8,
  which the bundled pygame cannot use; tools 14 and newer need macOS 12.5, hence the minimum).
  - Tools missing: the app says so and offers an **Install** button (the same as running
    `xcode-select --install`); open the game again once the install finishes.
  - Tools too old: the app says so and offers to open **Software Update** to update them.
- Install: open the .dmg and drag Pixel Apocalypse onto Applications (see the first launch
  steps below).

### Apple Silicon (M1 and later)

The vendored pygame is an Intel (x86_64) build, so the app is Intel-only on purpose. On Apple
Silicon macOS runs the whole app under Rosetta 2, and offers to install Rosetta the first time
if it is not there yet. Apple's Python framework is universal, so its Intel half loads.

### First launch: macOS blocks the app

The app is signed ad hoc, not with a paid Apple Developer ID, and it is not notarized. A copy
that was downloaded (the .dmg from a browser, a zip from chat or mail) carries macOS's
quarantine mark, so the first plain double-click is refused ("can't be opened because Apple
cannot check it for malicious software" or similar; the wording varies by macOS version).
This is needed **once**; after that the app opens normally.

Do these steps on **the app itself** (in Applications), not on an alias, a Dock icon or a
Launchpad shortcut. macOS only grants the one-time exception to the app you Control-click;
opening it through an alias counts as a plain double-click, so you get the same warning
again with no **Open** button. Once the app has been allowed, aliases and the Dock work
normally.

1. Open the .dmg and drag **Pixel Apocalypse** onto **Applications**. Start it from there, not
   from the disk image or Downloads (that works too, but macOS then runs it from a hidden
   temporary read-only copy).
2. **macOS 13 Ventura and 14 Sonoma:** in Applications, **Control-click (or right-click) Pixel
   Apocalypse > Open**, then click **Open** in the dialog.
3. **macOS 15 Sequoia and later** (right-click > Open no longer gets past the block there; this
   also works on 13 and 14):
   1. Double-click the app once. In the "Not Opened" / "can't be opened" dialog click **Done**
      (or **OK**). **Not "Move to Trash"**: that deletes the app.
   2. Open **System Settings > Privacy & Security**, scroll down to **Security**, and click
      **Open Anyway** next to "Pixel Apocalypse was blocked...". The button only shows up after
      a blocked attempt, and only for about an hour; if it is gone, double-click the app again.
   3. Enter your password, then click **Open Anyway** in the dialog that follows.
4. **macOS 12 Monterey:** the same idea in **System Preferences > Security & Privacy >
   General > Open Anyway** (or right-click > Open as in step 2).
5. **Terminal alternative, any version:** remove the quarantine mark, then open the app normally:

       xattr -dr com.apple.quarantine "/Applications/Pixel Apocalypse.app"

(`spctl --assess` keeps saying "rejected" for this app even after it has been approved; that is
expected for an ad-hoc signature and does not stop it from opening.)

## Where saves live

The app never writes inside itself (that would break its signature). The launcher sets
`PIXEL_APOCALYPSE_DATA_DIR` and the game keeps the run save and settings there:

    ~/Library/Application Support/Pixel Apocalypse/save/run.json
    ~/Library/Application Support/Pixel Apocalypse/save/settings.json

Delete that folder to start completely fresh. (`play.sh` / the zip still saves into the
game folder's `save/`.)

## How the launcher works

`launcher.c` finds Apple's Python 3.9 framework (`xcode-select -p`, then
`/var/db/xcode_select_link`, then the standard Command Line Tools and Xcode locations),
loads it into its own process and calls `Py_BytesMain` on `Resources/game/init.py`, forwarding
any command-line arguments. macOS therefore sees one real app (name, icon, Dock) rather than a
`python3` process. Python runs as `python3 -E -s -B`: it ignores every `PYTHON*` variable of
the shell it was started from, ignores `~/Library/Python/3.9` packages and never writes
`__pycache__` into the bundle. On macOS a window icon also replaces the Dock icon (and with
none set pygame shows its own logo), so inside the app the game passes `Resources/AppIcon.png`
(the same artwork as `AppIcon.icns`) to `pygame.display.set_icon`.

Running the app from Terminal (handy for logs and headless checks):

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
        "dist/Pixel Apocalypse.app/Contents/MacOS/Pixel Apocalypse" --dev --frames 300

A **downloaded** copy has to be approved once first (see the first launch steps above, or
`xattr -dr com.apple.quarantine` on it): otherwise macOS kills the executable before it starts,
and the shell only shows `Killed: 9` (exit code 137) with no other output.

Add `--save-check` to also write the settings and a fresh run save at start and print where they
went (it replaces the current save, so point `PIXEL_APOCALYPSE_DATA_DIR` at a scratch folder
unless you mean to test the real one).

Test-only environment variables:

| Variable | Effect |
| --- | --- |
| `PIXEL_APOCALYPSE_DATA_DIR` | use this save folder instead of Application Support (made absolute, `~/` expanded) |
| `PIXEL_APOCALYPSE_PYTHON_LIB` | use exactly this Python dylib (a missing file = "not found") |
| `PIXEL_APOCALYPSE_DEVELOPER_DIR` | look for Python 3.9 only in this developer folder (a folder with `usr/bin` but no 3.9 = "too old") |
| `PIXEL_APOCALYPSE_NO_ALERT=1` | skip the "Command Line Tools needed / too old" dialogs (still exits with code 3) |

Exit codes: the game's own code, 3 = Python 3.9 not found (tools missing or too old),
4 = the app bundle is damaged.
