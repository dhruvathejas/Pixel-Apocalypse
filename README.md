# Pixel-Apocalypse
A game of violence and shooting with fun and enjoyment, embarrassment and well what else!

## Download for macOS

1. Open `PixelApocalypse-<version>-macOS.dmg` and drag **Pixel Apocalypse** onto **Applications**.
2. Needs macOS 12.5 or newer and Apple's Command Line Tools 14 or newer (the app offers to
   install them if they are missing).
3. The app is not signed with an Apple Developer ID, so macOS blocks the first launch once:
   - macOS 13-14: Control-click (right-click) the app > **Open** > **Open**.
   - macOS 15 and later (works on 13-14 too): double-click once and click **Done** (not
     "Move to Trash"), then **System Settings > Privacy & Security > Open Anyway**.
   - Or in Terminal: `xattr -dr com.apple.quarantine "/Applications/Pixel Apocalypse.app"`

Details (Apple Silicon, where saves live, building the app):
[PixelApocalypse/docs/macos-app.md](PixelApocalypse/docs/macos-app.md).
The zip release runs with `./play.sh` instead.
