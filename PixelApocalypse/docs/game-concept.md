# P1X3L /-\P0C/-\LYP53: THREAT MODEL

> Game concept document for **Pixel Apocalypse**, an original top-down action-hacking game in pygame (Python 3.8).
> Audience: the project owner, and anyone writing prompts for an AI coding assistant. Every section should be concrete enough to build from.
> Status: **concept v2 (2026-11-02).** The owner's answers are recorded in section 13 under *Decisions*. What is still undecided is listed there under *Still open*.

**Pitch.** Deepstack is a sealed underground data-bunker built on top of a buried server cathedral, and it is collapsing. The racks are spawning `|-|@c|<ing MINIONS`, the terminals are decaying into leetspeak, and Kernel, the bunker's caretaker OS, is pulling power from life support to fight an enemy it cannot find. You are the last maintenance tech, armed with a bow, a pistol and a katana. You knock a minion's firewall down, then stop shooting and **jack in**: in slow motion, you decode the leetspeak packet it carries, word by word, by hitting the right reading on a row of option buttons before the trace finds you. Every wrong button is a real misreading of an enemy glyph. The words you decode are the intel. Each one brings you closer to the name of the `U |_ T | M /-\ T E |-| /-\ C |< E R`. As you play, an adaptive AI learns your habits and publishes patch notes against them. *(Canon; see 8.1:)* in the end you find out that this AI *is* the Ultimate Hacker: a threat model of you that got loose, and the five Daemons you fight are its parts.

---

## 1. Pillars

1. **Hack, don't just kill.** Killing keeps you alive. Hacking is the only way to get intel, lower boss firewalls, or move the story forward. Every exposed minion presents a choice between the two.
2. **Leetspeak is the language of the enemy.** The game's naming style is the hacking puzzle and also the progress bar. Every decoy button is built from a real glyph misreading (`|2` read as `IZ`, `|_` read as `U`), so picking right means you have learned to read the enemy's language.
3. **The AI learns you, in the open.** Every adaptation is local and capped, and each one is announced along with its evidence (`P1NG lead +30% (Y0U D0DG3 L3FT 71%)`). The game never adapts silently.
4. **Small enough to finish.** The game is built from a few dozen rectangles of map data, 32x32 sprites, code-drawn effects and data-driven bosses. A playable 10–15 minute slice comes first.

---

## 2. Perspective & controls

**Decision (approved 2026-11-02): top-down, free 8-direction movement, mouse aim, camera scrolls on both axes.**

Reasons:
- `bg/map.png` is a square overhead floor plan (10000x10000) with no ground line. A side-scroller would use only a thin strip of it.
- `player_back.png` (facing up) is only useful in a top-down view.
- The core mechanics need 2D space: flanking shielded minions, kiting observers, and protecting yourself while you are rooted in a hack.

**How this relates to prompt 00001:** that milestone is still delivered as written. The camera scrolls as the player moves left/right (M3 below). The Y axis is then turned on with the same camera code.

**Rendering:** draw the world, map and sprites together, onto a **960x500 internal surface**, then scale it 2x with nearest-neighbour into the 1920x1000 window. This makes 32px sprites appear 64px tall and keeps pixels crisp. Mouse positions are divided by 2 before hit-testing UI buttons, which are laid out in internal-surface pixels.

**Visible world: 960x500 px, i.e. ±480px horizontally and ±250px vertically around the player.** Every range in this document is written as `Npx` world pixels and must be checked against this box; anything meant to be seen on screen stays within about 230px. Ranges that are deliberately off-screen (pistol noise, alarm pull, Influence Zones) are marked as such. As a safety net, any ranged minion or PR0B3 that is off-screen but active gets a code-drawn **edge-of-screen arrow** in its archetype colour.

**Movement:** base player speed **180 px/s** (1.6x on cable trunks, 3.2), dash **96px over 0.15s**. Sample traversal: Clean Node 1 to Rack D is about 6000px of walking, about 33s on bare floor or about 21s on trunks.

**Sprite facing:**

| Movement / aim | Player sprite |
|---|---|
| Moving mostly left / right (incl. diagonals) | `player_left_asset` / `player_right_asset` |
| Moving up | `player_back` |
| Moving down | last left/right sprite (until a front sprite exists, art request #1) |
| Weapon drawn | `*_weapon` variant, picked by which side of the player the mouse is on |

Weapons and projectiles are drawn rotated toward the cursor from the `*_right` sprite (`pygame.transform.rotate`, cached in 10° buckets). When the cursor is left of the player, the code swaps to `*_left` so the sprite is never upside down.

**Key bindings**

| Input | Action |
|---|---|
| WASD | Move (8-dir) |
| Mouse | Aim. During a hack the crosshair becomes a pointer for the option buttons |
| LMB | Attack (hold to charge bow). During a hack: press the button under the pointer |
| RMB | Katana parry. During a hack: abort |
| Space | Dash (i-frames, 1.2s cooldown) |
| 1 / 2 / 3, wheel | Bow / pistol / katana (PLAY only) |
| **F** (hold 0.4s; tap with the katana equipped) | Jack in to an EXPOSED minion or a rack's breach port. Also interacts at terminals |
| **1 / 2 / 3 / 4** (DECODE, ROOT BREACH) | Pick option 1–4 (numbered by slot, left to right) |
| **Z / X / C** (PAYLOAD) | EXTRACT / TURN / OVERLOAD |
| Tab | DECRYPT screen (name bar, intel log, codex) |
| Esc | Pause menu (the current skeleton quits on Esc; move quitting into the menu). During a hack: abort |

The jack-in key is F (beside WASD, and already the interact key). In the HACK state the weapon keys are switched off: 1–4 pick decode options, and Z/X/C pick the payload. So no key has two meanings in the same state, and a payload key can never be hit by a leftover decode keypress. **All input is ignored for the first `input_lock_s` (0.2s) of DECODE, PAYLOAD and ROOT BREACH, and for the first `input_lock_s` after any hack or breach returns to PLAY.** This covers the held F, key-repeat, the F tap of a katana instant jack-in, and a late 1–3 or LMB press at the end of a hack, so none of them can pick an option, switch weapons or fire.

**Input by game state** (the input handler switches on `Game.state`, so keys never overlap):

| State | Esc | LMB | RMB | 1–4 | Z / X / C | Space | Wheel | Tab | WASD / F |
|---|---|---|---|---|---|---|---|---|---|
| PLAY | Pause | Attack | Parry | 1–3 switch weapon (4 ignored) | Ignored | Dash | Switch weapon | DECRYPT screen | Move / jack in |
| HACK: DECODE | Abort hack | Press option under pointer | Abort hack | Pick option | Ignored | Ignored | Ignored | Disabled | Ignored (rooted) |
| HACK: PAYLOAD | Ignored (the window then defaults to EXTRACT) | Press payload button | Ignored | Ignored | Pick payload | Ignored | Ignored | Disabled | Ignored |
| ROOT BREACH | Disconnect (progress kept) | Press option | Disconnect | Pick option | Ignored | Ignored | Ignored | Disabled | Ignored |
| NODE / menus | Close | Click | | Menu shortcuts | | Ignored | Scroll lists | Close | Menu navigation. `ENTER HANDLE` is the game's only text field |

**Option slots:** number keys map to fixed button slots, left to right. A key whose slot is struck, pre-struck (Lexicon, pre-solve) or empty (a 3-option or 2-option set) is ignored: no pick, no TRACE. A struck button keeps its slot for the rest of that word; the reshuffle after a wrong pick only swaps the live buttons among the other slots.

**Sprite precedence:** moving up uses `player_back` with the weapon overlay drawn *behind* the body. Otherwise use the `*_weapon` side sprite picked by mouse side.

---

## 3. World & setting

**Deepstack.** A sealed bunker for about 300 survivors, called Stackers. The green floor in `map.png` is the substrate of a giant circuit board. The mid-green blocks are house-sized server racks, and their light-green bars are **data ports**. Minions climb out of these ports, and on a boss rack some or all of the bars also serve as its HP cores (7.1). The surface was lost decades ago, during the Era of `|-|@c|<ing`.

**Tone.** Green-phosphor dread with dry humour. The UI uses a monospace CRT style. Enemy speech is always leetspeak, and your own tools speak clean text. Palette rule: the world stays in the map's greens, threats use magenta/red, and anything you have hacked turns cyan.

**World state.** A global **CORRUPTION %** goes up over time and when hacks fail, and goes down with successful hacks and boss clears. It controls only *diegetic* text (signs, logs, graffiti, minion names, undecoded intel), screen glitch intensity, and spawn rate. HP, prompts, menus and hack option buttons always stay readable.

### 3.1 Map layout (approximate; measure precisely in M2)

The art fills roughly x,y 0–8190. Lighter-green walls run along the top and left edges only. Everything beyond about 8190 is empty and becomes the **DATA LOSS VOID**, the part of the world that has already been deleted. It is drawn in code as black with drifting glitch noise and stray hex. It acts as an invisible wall, and its edge band thickens slightly each act.

| Zone | Approx. world rect (x0–x1, y0–y1) | Role |
|---|---|---|
| Rack A (4 bars) | 1100–3000, 860–1860 | Boss slot 1 (Act I): **R3C4LL**, the vertical-slice boss |
| Rack B (3 bars) | 5400–7000, 750–2040 | Boss slot 2 (Act I): **CR055H41R** |
| Small block | 1100–1630, 2990–3560 | **Clean Node 1**: start, save, shop, terminal |
| Small block | 4970–5550, 3350–3790 | **Clean Node 2**: mid-game hub |
| Rack C (3 bars) | 2330–3800, 4550–6460 | Boss slot 3 (Act II): **`\|-\|34D1NG`** |
| Rack E (3 bars, cut by void) | 470–1740, 7250–8190 | Boss slot 4 (Act II): **`V3\|2D1C7`** |
| Rack D (2 bars, cut by void) | 6110–7550, 6460–8190 | Boss slot 5 (Act III): **`D14\|_3C7`** |
| Partial block on bottom edge | 3670–4960, ~8150 | **Deep Core hatch**: entrance to the finale arena (separate small map) |
| Partial block on right edge (cut by void) | ~8170, 4970–5980 | **Spool's cache** (Act II Key source, 8.2): a sealed terminal that unlocks once both Act II Daemons are cleared; hacking it gives the Key. Built in M15b |

Each rack projects an **Influence Zone** (about 1200px radius from its centre). Its minions roam inside it, and a faint tint marks the territory. A cleared rack's zone becomes a safe, lit area.

### 3.2 Technical approach

- **Never load the 10000x10000 PNG as one surface at runtime** (it would take about 400MB). The map art is only a few dozen flat-colour rectangles, so bitmaps are unnecessary. A one-off script, `tools/slice_map.py`, measures the true art bounds and writes a draft `map_data.json` of floor, wall, rack, bar and node rects **with their colours**, found by colour scan. A human then checks that file and hand-corrects it. **Primary path:** at runtime the world is drawn each frame with `pygame.draw.rect` from `map_data.json`, culled to the camera. This uses near-zero memory and matches collision exactly. **Fallback** (only if hand-drawn detail is added to the art later): the script also writes 1024px chunks to `assets/map_chunks/x_y.png`, loaded lazily with `convert()` into an explicit LRU cache of at most 9 chunks (about 36MB); never preload all 64 (about 268MB).
- **`map_data.json` is the single source of truth.** It holds node collision rects, zone centres, the start position, cable-trunk paths and void bounds. Each rack is split into a solid **`shell`** rect and its **`bars`**: each bar has its own rect, a `hittable` flag (set when it is acting as an exposed core) and a **`spawn_exit`** point on the shell's outer edge, where minions from that port appear. Collision uses AABBs against shells, moving on X and then on Y so actors slide along walls. Projectiles test bar rects flagged `hittable` **before** the shell, so a shot can stop at the shell yet still damage a glowing core.
- **Camera:** centred on the player with an 80x60 deadzone and a lerp of 0.15. It is clamped to the art bounds.
- **Cable trunks:** `bg/road.png` tiles are laid at runtime along L-shaped paths between nodes and racks. Walking on them gives 1.6x speed, which shortens the long empty walks.
- **Pathing:** seek plus separation plus simple rect-avoidance steering. There are about 10 axis-aligned obstacles on open floor, so a flow field or A* is only needed if testing shows minions getting stuck.

---

## 4. Core loop

| Scale | Loop |
|---|---|
| **Moment (5–30s)** | Fight to strip a minion's FIREWALL, which makes it **EXPOSED**. Then **stop**: after a short grace period, any further hit kills it and destroys its data. You choose: **kill** (fast, safe; Bits and maybe health) or **jack in** (slow-mo button DECODE; intel, firewall damage to its boss, and a payload). |
| **Zone (5–10 min)** | Enter a rack's Influence Zone, and the Director paces minion waves. Hack minions to gather intel and the boss's `password_word`. Assault the rack: hack its minions to bring its firewall down, damage its cores, then finish it with a ROOT BREACH. A **Core Dump** follows: a story beat, a Key, the Daemon's named Exploit tool, and a **Patch Notes** card. |
| **Session (20–40 min)** | Leave from a Clean Node, clear a zone, return to the Node to read intel, spend Bits and Exploit shards, and save. Death respawns you at the last Node. Intel, Keys and your codex are kept; half of unspent Bits are lost. |
| **Run (about 75–100 min)** | Prologue, then Act I (Racks A, B), Act II (C, E), Act III (D), the Deep Core, and the Mirror finale. Blast doors open on Keys found: **Act II doors: 4 Keys, Act III doors: 8, Deep Core: 12**, so story progress gates the map (Key placement in 8.2). Estimate: five zones at 5–10 min, five boss fights at 3–5 min, plus traversal, the Deep Core and the finale. A longer run would need more content (more fragments, zone re-entries with new waves, revisits), not padding. |

**Frame order** (fixed 60Hz step inside the main loop):

```
input -> player -> hack overlay (if active: world_dt *= 0.3; hack timer uses real dt) -> minion AI (staggered, 1/6 per frame)
-> movement + collision -> projectiles -> Profiler event hooks -> Director tick (every 30 frames)
-> camera -> render map rects (culled), entities, HUD, hack terminal -> drain optional LLM result queue (non-blocking)
```

---

## 5. The Hack: JACK-IN / DECODE

This is the signature verb. It is built entirely from rects, timers, text and buttons, and it does not depend on any art. **Decision (2026-11-02): the hack is played with on-screen option buttons**, chosen by mouse click or number keys. No typing.

### 5.1 Flow

1. **EXPOSE.** Weapon hits drain the minion's **FIREWALL bar**, which is separate from its small core HP. When the bar reaches 0, the minion is EXPOSED for **4s**. It stops attacking, glitches (code-drawn slice offsets and RGB split), and shows a cyan 64px jack-in ring and a countdown arc. For the first **`expose_grace_s` (0.35s)** hits do no damage: remaining katana combo hits whiff, and projectiles already in flight pass through the just-exposed minion. After the grace period, **any hit kills it** (strip-then-stop), so a kill is always a deliberate input, never the input buffer. If the timer runs out, the minion reboots at 50% firewall. Other ways to expose a minion: a katana parry, or a fully charged bow shot on an unaware target. A fully charged arrow on an already-EXPOSED minion **pins** it instead of killing it (+3s to the window).
2. **JACK IN.** Stand inside the ring and hold **F for 0.4s**. Taking damage or leaving the ring cancels it. With the katana equipped, a **tap** of F (no hold) starts the hack instantly. LMB always attacks, with every weapon, so after the grace period a katana hit on an EXPOSED minion still kills it.
3. **DECODE.** The world runs at **30% speed** and loses colour, except for the cable and the target. A **terminal window** opens above the minion (clamped on-screen, 85% opaque so threats still show through). It shows the minion's **intel fragment**, with its **target words** in magenta leetspeak. The targets are decoded one at a time, left to right. For the active target, **3 option buttons** (4 at Breach Level 4+, see 5.6) appear in a row along the bottom of the window. Each button holds one plain-text reading: one correct, the rest decoys built from glyph misreadings (5.5). Pick with the **mouse** (LMB) or the **number keys 1–4**. A correct pick flips that word in the sentence to clean cyan text with a split-flap clatter, and the next word's options appear. The sentence becomes readable as you decode it, so **what you picked is the intel**. The timer runs in **real time**, not slowed time, and is shown as a draining bar inside the window.

   ```
   +-[ JACK-IN :: P1NG 0x2F ]------------------------[ TRACE ###....... ]-+
   |                                                                      |
   |  [( R 4 7 3 5] 4T R4CK C. SP00L [W 4 1 7 3 D] BY TH3M TW0            |
   |  ^^^^^^^^^^^^^ word 1/2                                              |
   |  D4Y5 B3F0R3 TH3 0UTBR34K.                                           |
   |                                                                      |
   |   +-------------+     +-------------+     +-------------+            |
   |   | 1   CRALES  |     | 2   GRATES  |     | 3   CRATES  |            |
   |   +-------------+     +-------------+     +-------------+            |
   |                                                                      |
   |  [####################..........]  5.1s                              |
   +----------------------------------------------------------------------+
   ```

   This is a P1NG packet at **L3** (5.6): 2 target words of 6 letters, at most one Tier 2 cluster per word (`R` stays plain here), 3 options, near decoys of the same length, and at least one real-word decoy. `GRATES` misreads `(` as `G`, and `CRALES` misreads `7` as `L`. Each decoy tests a different glyph and none can be ruled out by length, so a correct pick proves you can read both, and `GRATES` at a rack fits the sentence as well as `CRATES` does. Press **3** (or click it): `CRATES` flips to cyan, and the options for the second target appear, shuffled: `1 WAITED`, `2 WAILED` (`7` read as `L`), `3 WHITED` (`4` read as `H`). The timer is (1.5 + 2.0 x 2 + 0.3 x 1) x 1.00 = 5.8s.

   **Why the player is rooted.** Buttons do not collide with WASD, so this is no longer about keys. It is by design. (1) It is diegetic: you are physically plugged into the minion by a cable. (2) Your hand on the mouse or the number row and your eyes on the terminal are both busy, so you cannot aim anyway. (3) The risk is the point. A hack you could do while kiting would be a free bonus, and then the kill-or-hack choice (5.3), the Director's RELAX window (9.2) and setups like OVERLOAD and F1R3W4LL links would stop mattering. Other minions keep closing in at 30% speed, and B1Ts near a hacking player get +ADVANCE.
4. **PAYLOAD (1.2s window, default EXTRACT).** The decoded fragment is always saved to the log, and the parent boss loses 1 firewall point. Then the window's **title bar** turns into three payload buttons. They sit at the top of the window, away from the option row at the bottom, so a click aimed at the last option cannot land on a payload. Pick with a click or **Z / X / C**:

| Payload | Effect |
|---|---|
| **[Z] EXTRACT** | Minion deleted. +1 bonus Chatter/Telemetry line and +1 Exploit shard. For a PR0B3, this also deletes the profile samples it was carrying. |
| **[X] TURN** | Minion fights for you for 15s (cyan palette swap, own AI with the team flipped). A turned PR0B3 **poisons the profiler** for 60s (see 9.4). |
| **[C] OVERLOAD** | Minion detonates as an EMP. Every minion within `overload_radius_px` (160px) of it becomes EXPOSED immediately. The game stays in HACK and **chains** (below). Chains multiply Exploit shards (x2, x3...). |

**OVERLOAD chain.** After an OVERLOAD the game does not return to PLAY. If `chain_autotarget` is on (default), the cable jumps to the **nearest minion this EMP exposed** and its DECODE starts after `input_lock_s`. You stay rooted where you are and do not walk to it; jack-in range does not apply to a chained target. Each chained hack has its own PAYLOAD, and picking OVERLOAD again continues the chain, up to `chain_max` (4) hacks. RMB or Esc during a chained DECODE aborts that hack and ends the chain. The chain also ends when the EMP exposed no minion, at `chain_max`, or on EXTRACT or TURN. EXPOSED windows count in **world time**, so the 4s window lasts about 13s of real time while the world runs at 30%. **Getaway:** when a chain of 2 or more ends, the world (not you) stays at 30% for 1s per chained hack (`chain_getaway_s`), in PLAY with full control, after the usual `input_lock_s`.

### 5.2 Success & fail states

| Event | Result |
|---|---|
| All target words picked correctly | `ACCESS GRANTED`, then PAYLOAD. With zero wrong picks it reads `ACCESS GRANTED // CLEAN` and gives +1 Exploit shard (on top of EXTRACT's), so careful reading pays more than mashing. |
| Wrong pick | **+35 TRACE**, so **three strikes in one hack = BACKTRACE**. The button is struck out (X icon plus hatching), stays disabled and keeps its slot. A callout names the misread (`\|2 = R, not IZ`) and the glyph is logged as a miss in the Codex. The live options reshuffle among the other slots, and input locks for `pick_lock_s` (0.15s). |
| **Hasty wrong pick** (anti-mash) | A wrong pick made less than `hasty_s` (0.6s) after that option set went live costs `trace_hasty` (**+50 TRACE**) instead of 35, so **two hasty strikes = BACKTRACE**. A careful reader who misreads once still pays 35. |
| **Second wrong pick on the same word** (anti-mash) | The word **burns**: it stays `████`, counts as undecoded, and the next word's options appear. There is no free "last button left" pick. After the last word, a hack with burned words ends like a timer expiry: REDACTED if at least 50% of its words were decoded (and it has 2+ words), otherwise BACKTRACE. In ROOT BREACH a burned chunk instead re-rolls a fresh option set (the strikes stay on the TRACE meter). |
| Taking damage while decoding | +35 TRACE and a camera jolt |
| Timer expires with 50% or more of the target words decoded | **REDACTED**: the fragment is saved with `████` in place of the undecoded words, and a later hack of the same archetype completes it. Minion reboots at 50% firewall. (A 1-word packet can't be REDACTED.) |
| TRACE 100, or timer expires with under 50% decoded | **BACKTRACE**: red glitch tear, weapon jams for 2s, minion becomes **HARDENED** (red outline, +50% HP, cannot be hacked again), an alarm pulls minions within 600px (deliberately off-screen) toward you, and the parent boss gains 1 Adaptation point |
| RMB / Esc | Clean abort with no TRACE. Minion reboots. |

**Masher odds** (full success; burn and hasty rules on; no Lexicon). An **instant masher** clicks a random live button as soon as input unlocks. Per word with 3 options, the outcome is 0 strikes 1/3, 1 strike 1/3 or burned 1/3; with 4 options it is 1/4, 1/4 or 1/2. Two hasty strikes backtrace, so success needs no burns and at most 1 strike in the whole hack. A **patient guesser** waits out `hasty_s`, so strikes cost 35 (three still backtrace); it still loses every burned word.

| Level | Packet | Options | Instant masher | Patient guesser |
|---|---|---|---|---|
| L1 | 1 word / 2 words | 3 | 67% / 33% | 67% / 44% |
| L2, L3 | 2 words | 3 | **33%** | 44% |
| L4 | 2 / 3 words | 4 | 19% / 6% | 25% / 11% |
| L5, L6 | 3 words | 4 | 6% | 11% |
| ROOT BREACH (L+1) | 3 chunks | 4 | 6% (pre-solved: 67%) | about 11–15% with re-rolls |

L1 is the tutorial level and stays guessable on purpose. From L2 the real-word decoy rule (5.5) turns the "pick the only English word" shortcut into a guess between real words that only the glyph separates. Neither kind of guesser ever earns CLEAN. These numbers are checked by the masher sim in `tools/check_packets.py --masher` (M8).

No softlocks: if a Key's carrier is lost (killed or HARDENED), the Key moves to another live minion of the same type in that zone. If none is alive (for example after a Core Dump, when the ports are dark), one of that rack's dark ports flickers magenta and re-spawns a single F1R3W4LL carrier (a B1T carrier before F1R3W4LL ships in M15a), repeating until the Key is taken. As a last resort, a Key whose zone cannot spawn drops as a hackable data shard at the nearest Clean Node terminal.

### 5.3 Why hacking is not a re-skinned kill

| | Kill | Hack |
|---|---|---|
| Speed / risk | Fast, safe | Slow, rooted, interruptible, three strikes |
| Reward | Bits, 20% chance of a health pip | Intel, Keys, firewall damage, payload, lower CORRUPTION, Exploit shards |
| Boss fights | Boss is immune while its firewall is up. Each kill adds 1% of the current core's `core_hp` (7.1), capped at +10% per phase | The only way to lower the boss's firewall |
| AI | The profiler records your kill:hack ratio (it shapes the Mirror) | Hacking deletes what the AI learned (9.4) |

### 5.4 Glyph table (from the game's own naming style)

```
Tier 1 (single char):  3 -> E   0 -> O   1 -> I   4 -> A   5 -> S   7 -> T   @ -> A   $ -> S   | -> I
Tier 2 (multi char):   |-| -> H   /-\ -> A   |< -> K   |_ -> L   |2 -> R   |> -> P   () -> O   \/ -> V   |\/| -> M   |= -> F   |_| -> U   ( -> C
```

**Readability rule:** `|` is the first stroke of many Tier 2 glyphs, so leet text is never parsed from raw strings. `intel.json` stores each fragment as a list of glyph clusters (e.g. `["|<", "3", "Y"]`), and `leetify()` emits clusters the same way. On screen, each cluster is drawn as one boxed unit with a gap between clusters. **Target words** are highlighted in magenta, and the active target gets brackets and a caret. Every other word in the sentence uses Tier 1 only, or plain text. Letters with no glyph (N, G, W, Y...) stay plain.

**Handle-only extended set.** Your handle (8.3, 8.4) must always encode, so it alone may also use glyphs for the 10 letters the table lacks: `B -> 8`, `D -> |)`, `G -> 6`, `J -> _|`, `N -> |\|`, `Q -> 0_`, `W -> \/\/`, `X -> ><`, ``Y -> `/`` (backtick, slash), `Z -> 2`. With these, all 26 letters have a leet form, so every handle `ENTER HANDLE` accepts can be leetified. For reversed picks, each extended glyph's mis-encoding is the valid glyph of a look-alike letter: N as `|-|`, W as `\/`, G as `(`, Y as `\/`, B as `3`, D as `|>`, X as `|<`, Z as `5`, J as `|`, Q as `0`. These glyphs never appear in packets, so they need no Codex entries.

Words start at 3–4 letters with Tier 1 glyphs and grow to 6–8 letters with Tier 2. Use a crisp monospace font at 24px or larger on the internal surface; option button text is the same size.

### 5.5 Decoy generator

Decoys are the heart of the button hack. If they were random words, players could pick the right one from sentence context without reading a single glyph. Instead every decoy is the word **as it reads if you misread one or two of its glyphs**. Picking right therefore means reading the glyphs, which is the same skill the old typing design trained. Misreads alone are not enough, though: most of them make non-words (`CRALES`), so a set whose only real word is the answer can be solved by picking the English word. From **L2** every set must therefore include at least one **real-word decoy** (`GRATES`), as below.

**Misread table** (`config/misreads.json`; glyph -> what a decoy writes for it, with a weight; `*` marks a length-changing misread, which gives an easy length tell):

```
3    -> B (1.0)            0    -> D (1.0), Q (0.6)       1    -> L (1.0), T (0.8)
4    -> H (0.8), Y (0.5)   5    -> Z (1.0)  [5 vs 2]      7    -> Y (0.8), L (0.8)
@    -> O (1.0)            $    -> Z (0.8)                |    -> L (1.0), T (0.6)
|-|  -> N (1.0)  [|\| = N] /-\  -> N (1.0), V (0.6)       |<   -> IC* (1.0), X (0.7)
|_   -> U (1.0)  [vs |_|]  |2   -> IZ* (1.0), B (0.6)     |>   -> D (1.0), B (0.5)
()   -> C (0.8), D (0.8)   \/   -> U (1.0), Y (0.6)       |\/| -> N (1.0), W (0.8), IVI* (0.5)
|=   -> E (1.0), T (0.6)   |_|  -> L (1.0), V (0.7)       (    -> G (1.0), O (0.6)
```

**Algorithm** (`hack/decoys.py`, `make_options(clusters, level, codex, rng)`):

```
answer = plain(clusters)
singles = []
for each cluster i, for each misread m of clusters[i]:
    skip m if it changes length and level.length_tells is False
    text  = answer with cluster i's letter(s) replaced by m.reads
    w     = m.weight * (1 + 2 * codex.recent_misses(clusters[i]))      # spaced repetition
    singles.append(Decoy(text, tests={clusters[i]}, weight=w))
pool = singles                                  # "near": exactly 1 glyph differs
if level wants "far": pool = combine pairs of singles at different clusters (2 glyphs differ)
drop: text == answer, text that is another valid reading of the clusters, duplicates,
      anything in data/blocklist.txt
if level.real_word_bonus (L2+): weight x3 for decoys found in data/words.txt   (harder: looks right)
choose n-1 by weighted sampling without replacement, preferring decoys that test different glyphs
if level.real_word_bonus and no chosen decoy is a real word:      # a "weak" set
   retry with plain look-alike swaps and far pairs, forcing in one real word if any exists;
   still weak -> use the fragment's alt_target word (intel.json) instead; still weak -> keep it, log it
if level.target_worst: force one decoy that tests the player's worst Codex glyph in this word
if too few (short Tier 1 words): add plain look-alike swaps (O/Q/D, E/F, I/L/T, U/V, M/N);
   still too few -> fewer options (minimum 2) and +0.5s on the timer
options = shuffle([answer] + decoys)            # rng seeded by (fragment_id, word_index, attempt)
assert_invariants(options)   # else log and rebuild from plain look-alike swaps only
```

- **Codex attribution:** a correct pick logs a **hit** for every glyph tested by the decoys on screen. A wrong pick logs a **miss** for the glyph(s) that decoy tests, and appends `(glyph, reads_as)` to `codex.misread_history` (used by `mirror_dialect`, 7.3).
- **Two-part words** (F1R3W4LL packets, e.g. `F1R3` + `W4LL`) are two consecutive picks with their own option sets.
- **Runtime invariants:** many option sets are built at runtime from player data and never exist in a file: PR0B3 Telemetry packets, ROOT BREACH `password_chunks`, `mirror_dialect` decoys from `misread_history`, the Mirror's reversed DECODE and the handle breach. So `make_options()` checks every set it builds: exactly one correct answer, no duplicates, nothing blocklisted, and no decoy that is another valid reading of the clusters. If a check fails, it logs the set and rebuilds it from plain look-alike swaps only (minimum 2 options), so the game never shows a broken set.
- **Determinism:** the RNG is seeded by (fragment_id, word_index, attempt), but the weights also depend on `codex.recent_misses`, so a packet is reproducible only **for the same Codex state**. Debug logs record a Codex snapshot ID with each packet. `tools/check_packets.py` runs the generator at every level over all of `intel.json`, every `bosses/*.json` `password_chunks`, the Telemetry templates (filled from sampled profiles) and sampled handles (reversed picks), each under several sampled Codex states (empty, random, one glyph always missed) and sampled `misread_history` lists. It fails on any invariant break, and from L2 it also lists weak sets (no real-word decoy) so writers can pick better target words or add an `alt_target`. `--masher` runs the scripted masher and guesser from 5.2 over the same packets.

### 5.6 Breach Levels & tuning knobs

Difficulty is one number, the **Breach Level** (L1–L6). The Breach Tuner (9.3) moves it. Archetypes offset it (B1T −1, clamped to L1–L3, so a B1T packet always has 3 options and at most one Tier 2 cluster per word), and ROOT BREACH runs at +1 with 4 options.

| Level | Target words | Word length | Glyphs | Options | Decoys | Timer mult |
|---|---|---|---|---|---|---|
| L1 | 1–2 | 3–4 | Tier 1 | 3 | Far (2 glyphs differ), length tells allowed, no real-word rule | 1.25 |
| L2 | 2 | 4–5 | Tier 1 | 3 | Far or near, 50/50, length tells allowed, real-word decoy required (x3) | 1.10 |
| L3 | 2 | 5–6 | Tier 1, at most 1 Tier 2 per word | 3 | Near (1 glyph differs), same length only, real-word decoy required | 1.00 |
| L4 | 2–3 | 6–7 | Tier 2 | 4 | Near, same length only, real-word decoy required | 1.00 |
| L5 | 3 | 6–8 | Tier 2 heavy | 4 | As L4, two real-word decoys where the dictionary allows | 0.90 |
| L6 | 3 | 7–8 | Tier 2 heavy | 4 | As L5, plus one decoy aimed at your worst glyph | 0.80 |

All knobs live in `config/hack.json`:

| Knob | Default | Notes |
|---|---|---|
| `expose_window_s` | 4.0 | +3 when pinned |
| `expose_grace_s` | 0.35 | No damage; combo hits whiff, in-flight projectiles pass through |
| `jack_hold_s` / `jack_range_px` | 0.4 / 64 | Range 96 with the Wide Port upgrade |
| `world_slowmo` | 0.30 | |
| `timer_s` | (1.5 + 2.0 x target words + 0.3 x Tier 2 clusters) x level mult | Then x the Breach Tuner's adjustment (0.75–1.25). Real time |
| `breach_timer_s` | 4 + 3 x chunks left | ROOT BREACH, real time |
| `levels` | table above | Options, decoy mode, glyph tier, timer mult per level |
| `input_lock_s` / `pick_lock_s` | 0.2 / 0.15 | At DECODE/PAYLOAD/BREACH start and on return to PLAY / after every pick |
| `hasty_s` / `trace_hasty` | 0.6 / 50 | A wrong pick sooner than `hasty_s` after its set went live costs `trace_hasty` (5.2) |
| `burn_after_wrong` | 2 | Wrong picks on one word before it burns (5.2) |
| `reshuffle_on_wrong` | true | Accessibility can turn it off |
| `trace_wrong` / `trace_hit` | 35 / 35 | Three strikes = 105 |
| `redacted_threshold` | 0.5 | Share of target words decoded |
| `payload_window_s` | 1.2 | |
| `overload_radius_px` | 160 | |
| `chain_autotarget` / `chain_max` / `chain_getaway_s` | true / 4 / 1.0 | OVERLOAD chain (5.1) |
| `target_success_rate` | 0.70 | |

### 5.7 Accessibility & juice

**Accessibility** (Settings > Hack):
- **Colour-blind-safe button states.** State is never shown by colour alone. Idle has a thin outline and a number badge. Hover or focus gets a thick white bracket border `> <`. Correct gets a solid cyan fill and a check icon. Wrong gets an X icon, diagonal hatching and dimmed text. Disabled gets a dotted outline. The palette is checked against deuteranopia and protanopia simulation.
- **Keyboard-only hacking:** 1–4, Z/X/C and Esc cover every hack action, and number badges are always drawn. **Mouse-only hacking:** click, payload buttons, and RMB to abort.
- **Longer timer:** x1.5 on all hack and breach timers (and on V3|2D1C7's verdict clock, 7.3).
- **Keep option positions:** turns off the reshuffle after a wrong pick.
- **Glyph legend strip** under the terminal window, large button text (32px), reduced-glitch mode (no RGB split or tears), and a calm-hack option (world at 15% instead of 30%).
- Leetspeak density in diegetic text is capped at 60%.

**Juice checklist** (all code-drawn): jack-in whoosh with a 1.15x zoom; desaturated world; buttons lift on hover and press down 2px with a key clack; a correct pick flashes the button cyan and flips the word's clusters to plain text one by one (split-flap, 40ms per cluster), with the pitch rising per word; a wrong pick shakes the button 4px, strikes it red and pulses the TRACE meter; success gives a 100ms hit-freeze, an `ACCESS GRANTED` stamp and a 6-frame shake; BACKTRACE gets a red tear. The **minimum set** (1.15x zoom, desaturate, button press plus split-flap flip, `ACCESS GRANTED` stamp) ships in M8, so the fun test judges the hook with its feel; the rest lands in M9.

*Post-launch idea, not planned:* an optional "terminal mode" where advanced players type answers instead of picking.

---

## 6. `|-|@c|<ing` Minions

Minions are only ever spawned by bosses, from their rack's ports. Each one is tagged with its parent boss. A single data-driven `Minion` class handles all of them: a state machine `SPAWNING -> UNAWARE -> HUNTING -> EXPOSED -> (TURNED | DEAD | REBOOT | HARDENED)`, plus utility scores (distance, own firewall %, allies nearby, *player is hacking*). Minions from the same rack share a **blackboard** that holds the player's last known position, which minion is being hacked, and whether an alarm has fired.

| Archetype | Role & behaviour | Hack twist | Intel tier | Ships in |
|---|---|---|---|---|
| **B1T** | Swarm rusher. Packs of 4–6, seek plus separation, contact damage. If the player is hacking, nearby B1Ts get +ADVANCE. | Breach Level −1, clamped to L1–L3 (5.6): always 3 options, at most one Tier 2 cluster per word | CHATTER | Slice (M6) |
| **P1NG** | Ranged. Holds 160–230px away (on-screen), fires slow dodgeable packets in bursts of 3, flees when exposed (the bow pin is the counter; before M12, chase it down). | Normal | CHATTER | Slice (M11c) |
| **PR0B3** | Observer. Circles at 180–230px (on-screen) and does no damage. While a PR0B3 is alive, the profiler learns at full rate (otherwise 0.3x). After 6s of line of sight **while on-screen** it runs back to its rack; if it gets there, the boss gains +1 Adaptation and you see `TELEMETRY UPLOADED`. It hears pistol fire within 600px (deliberately off-screen) but not bow shots. | Packets are Telemetry about you; target words are your own stats (`Y0U D0DG3 [L3F7]`) | TELEMETRY | M14 |
| **F1R3W4LL** | Sentry/warden. Slow, with a frontal 90° shield that blocks projectiles (flank it, parry it, or arc over it). The shield faces any of 8 directions; the code-drawn arc (below) shows where. Links shields to up to 3 allies, and their firewalls regenerate while linked. **Hacking** it drops every link and EXPOSES the linked allies. | Two-part word (two picks) | KEY | M15a |
| **W0RM** *(stretch)* | Slow crawler that leaves a slowing trail. Splits into 2 B1Ts when killed; a hack prevents the split. | Normal | CHATTER | Post-M16 |

Spawn rules: the boss asks the Director for budget (`spawn_cost <= director.budget`). Each port telegraphs for 1s (its bar flickers) before the minion slides out at that bar's `spawn_exit` point on the rack shell (3.2). Caps: 40 live minions per zone and 6 per port.

**Look: v1 sprites exist** in `minion_assets/<name>_<frame>.png`. They are generated by `python3 tools/make_minion_assets.py` (all minions, or name some: `... b1t p1ng`; `--preview DIR` writes 8x preview sheets) from ASCII art in `tools/minion_art/*.py`. They use the same 32x32 canvas and foot line as `player_assets` (feet on row 21, body centred on column 16), and a shared palette of corrupted magenta and plum, metal, and firewall orange. Greens are left for the world, and cyan for code-tinted TURNED and hacked states. To change a sprite, edit its ASCII module and re-run the script; never hand-edit the PNGs.

| Minion | Frames (each also has a mirrored `left_*` copy) |
|---|---|
| `b1t` | `right_0`, `right_1` (move loop), `right_attack` |
| `p1ng` | `right_0`, `right_1`, `right_attack`, `packet_right` (its projectile) |
| `pr0b3` | `right_0`, `right_1`, `right_scan` (shown while it has line of sight) |
| `f1r3w4ll` | `right_0`, `right_1`, `right_attack` |
| `w0rm` | `right_0`, `right_1` |

**Loading:** one function, `load_minion_frames(name)`, globs `minion_assets/<name>_*.png`, calls `convert_alpha()`, and returns `{frame_name: Surface}`, where the frame name is the part after `<name>_` (e.g. `right_0`, `packet_left`). Body animation only uses frame names that *start* with `left_` / `right_`; anything else (P1NG's `packet_*`) is a separate projectile sprite. No runtime flipping is needed, since the `left_*` files exist. The move loop alternates `_0`/`_1` at 6fps. `attack` is held for 0.15s when the minion attacks, and `scan` is shown while a PR0B3 has line of sight. P1NG packets use `p1ng_packet_*`, rotated like other projectiles. **Fallback:** if a file is missing, draw a coloured 32x32 box with the archetype's leet initial (B1T `B`, P1NG `7`, PR0B3 `0`, F1R3W4LL `#`, W0RM `~`) and log a warning once. The game stays fully playable this way.

Code draws the EXPOSED, TURNED, HARDENED and death-dissolve states, and the F1R3W4LL shield arc, so no extra frames are needed for them.

**F1R3W4LL facing:** its sprites only face left and right, with the firewall slab painted on the facing side. The **code-drawn shield arc is authoritative for gameplay**: it is drawn at the true 8-direction facing, on top of the sprite, and it is what blocks projectiles. The painted slab is decoration. The sprite side follows the sign of the facing's x component, and pure up or down facings use the `right_*` sprite. So a F1R3W4LL whose shield faces up still shows its slab on the right; read the arc, not the slab. Up/down frames are an art request before M15a (11).

---

## 7. Bosses

**What a boss is:** a **DAEMON**, one of the five sub-models of PROJECT MIRROR's threat model of you (8.1). Each one has taken over a rack to run on its compute. **The rack is the boss's body. Decision (2026-11-02): no boss sprites for now; an avatar layer is optional later.** Its light bars are its ports (spawners), and the bars its JSON lists as `core_bars` are also its **cores** (HP segments). Every boss is a file in `bosses/*.json`. A new boss built from the shared attack library and the shared **modifier library** needs no code changes. A genuinely new gimmick does need code: either a new shared modifier, or a one-off `bosses/<name>.py` script named in the JSON's `script` field. **In practice all five Daemons ship with new code**, because each one is built around a modifier it introduces (listed per Daemon in 7.3). Each modifier is built once with its first boss and is then reusable; only `verdict.py` is a one-off script.

### 7.1 Boss file format (slot 1, complete)

`bosses/r3c4ll.json`:

```json
{
  "slot": 1,
  "rack_id": "A",
  "leet_name": "R3C4LL",
  "plain_name": "RECALL",
  "submodel": "MEMORY",
  "act": 1,
  "firewall_points": 6,
  "core_bars": [0, 1, 2],
  "core_hp": 24,
  "spawn_table": {"B1T": 0.7, "P1NG": 0.3},
  "spawn_pattern": "vent_burst",
  "max_alive": 12,
  "modifiers": [
    {"id": "respawn_from_cache", "respawn_delay_s": 3.0, "originals_per_wave": 1, "stale_packets": true}
  ],
  "script": null,
  "phases": [
    {"core": 0, "attacks": ["radial_burst"], "spawn_rate": 1.0},
    {"core": 1, "attacks": ["radial_burst", "port_beam"], "spawn_rate": 1.3},
    {"core": 2, "attacks": ["port_beam", "port_overload"], "spawn_rate": 1.6}
  ],
  "exposure_window_s": 10,
  "adaptation_pool": ["BIT_SPEED", "LEAD_SHOTS", "FAST_KITE", "TIER2_SHARE"],
  "password_word": "YESTERDAY",
  "password_chunks": ["YES", "TER", "DAY"],
  "password_intel_id": "A_chatter_07",
  "core_dump": {"text_id": "dump_A", "key_glyph_index": 1, "exploit": "LEXICON"}
}
```

- **`core_bars`:** indices into the rack's bars in `map_data.json` (bar counts: A=4, B=3, C=3, E=3, D=2). Omit it to make every bar a core. Bars not listed are spawn-only ports. There is one phase per core. Rack A has 4 bars, so bar 3 stays a spawn-only port.
- **`core_hp`:** HP of each core, in pistol-bullet units (1 bullet = 1 damage; bow and katana damage are tuned against it in M12). An optional per-phase `core_hp` overrides it. Values in 7.3 are first-pass numbers, tuned in M11a.
- **`spawn_table`:** archetypes that have not shipped yet are dropped and the weights renormalised. So at M11a, before P1NG exists, R3C4LL spawns only B1Ts.
- **`modifiers`** (shared library, each a small class with tunable params): `attack_delay_s` (attacks land N s after their telegraph), `respawn_from_cache` (killed minions from a wave come back as cached copies only while that wave's tagged original is alive and un-hacked; hacking the original flushes them; full rules under R3C4LL, 7.3), `visible_queue` (upcoming attacks shown in an on-screen queue), `copy_player_weapon` (boss attacks mimic the profiler's top weapon; pistol until the profiler has data), and **`mirror_dialect`** (new in M15b, also used by the Mirror; see D14|_3C7 in 7.3).
- **`script`:** optional path to `bosses/<name>.py` with hook functions (`on_phase`, `on_tick`, `on_hack`) for one-off gimmicks that the library does not cover. Only V3|2D1C7 uses one.
- **`spawn_pattern`:** one of `vent_burst` (N minions at once from the bars), `trickle` (one every T seconds), or `ring` (every port releases at once and the minions fan out to surround the player; they still spawn at `spawn_exit` points, never out of thin air).
- **Attack library** (shared by all bosses): `radial_burst`, `port_beam` (telegraphed line sweep from a bar), `port_overload` (all ports spawn at once), `packet_rain` (telegraphed circles), `blackout` (darkness overlay with a player light pool, for this attack only), and `firewall_dome` (knockback). M11a builds the first three; the other three land in M15a. Every attack also has a **terminal overlay**: while the player is in HACK, its telegraph is drawn over the terminal window too (a line across it, or a ring tracing the window's border when the attack will land on the rooted player), so threats stay readable while you pick. The overlay is a shared attack feature, built in M15a. An attack entry is either a name or an object with params, e.g. `{"id": "packet_rain", "aim": "lead"}`. `aim` is `player` (default) or `lead` (player position plus velocity x the attack delay, the same helper as the P1NG lead-shot counter in 9.4).
- **`password_chunks`:** how ROOT BREACH splits the password into picks (3 chunks of 2–5 letters, joining to `password_word`). **`password_intel_id`:** the zone fragment that reveals it, which turns on the pre-solve.
- **`core_dump.key_glyph_index`:** which name-bar cluster (0–13) this Key de-leets. F1R3W4LL and Spool's-cache Keys take the remaining indices from a list in `intel.json`.

### 7.2 Fight structure

1. **SIEGE.** The firewall is up and the rack is invulnerable. It spawns waves and uses its phase attacks. Each successful hack of one of its minions costs it 1 firewall point. Firewall points carry over from hacks you did in the zone before the fight, so thorough play gives a shorter fight.
2. **EXPOSURE (once per core).** With the firewall down, one core bar glows and is flagged `hittable`. You get `exposure_window_s` to damage it with weapons; projectiles register on that bar's rect before stopping at the shell (3.2). Kills during the fight add to the core's HP (5.3). If the core breaks within the window, its phase ends: the firewall regenerates to half and the next phase starts. If the window closes first, the firewall regenerates to half and **the same phase repeats**, and the core keeps its damage. A phase advances only when its core breaks.
3. **ROOT BREACH.** After the last core breaks, it becomes the **breach port** (a cyan ring at its `spawn_exit`). Hold F there to jack into the rack itself. The rack's terminal shows its `password_word` leetified with Tier 2 wherever possible, split into its `password_chunks` (e.g. `Y 3 $ · 7 3 |2 · D /-\ Y`). Each chunk is one **4-option pick** at the zone's Breach Level +1, using near decoys from 5.5. Its own minions keep spawning, and the world runs at 30%. The timer is `breach_timer_s`, the three-strikes rule applies, and `ACCESS GRANTED` on the last chunk ends the fight.
   - **Pre-solve:** if you found the password in that zone's intel (`password_intel_id`), half the chunks (rounded up, so 2 of 3) start **locked in cyan**, and each remaining chunk has one decoy pre-struck (3 options).
   - **Fail (BACKTRACE):** the rack's firewall regenerates 2 points, so a short SIEGE follows (hack 2 minions), then the breach port reopens. Locked chunks stay locked. **Disconnect** (RMB/Esc) keeps progress with no TRACE.
4. **CORE DUMP.** A terminal story scene that also prints the column of your profile this Daemon modelled (real stats from the profiler; static text before M14). Then 1 name-bar Key, the Daemon's named Exploit tool (unlocked at once, 10), and a Patch Notes card (9.4). The ports go dark and the zone becomes safe (except for the Key re-spawn rule in 5.2).

Optional stretch: **hackable ports.** Breaching a port flips it so it spawns TURNED B1Ts for you for 10s.

### 7.3 The five Daemons

Each Daemon is one **sub-model of MIRROR's threat model of you**, trained on one column of your profile (9.1). Its Core Dump is a beat in the reveal schedule (8.3). Act I names use Tier 1 glyphs only, and Act II/III names add Tier 2 clusters, matching what players can read by then. All five are original designs for this game.

| Slot | Rack (bars) | Act | Daemon | Sub-model | Modifiers / script | Minion mix | `core_bars` / FW | Password (chunks) | Key glyph / Exploit |
|---|---|---|---|---|---|---|---|---|---|
| 1 | A (4) | I | `R3C4LL` / RECALL | MEMORY: your logs, where you go | `respawn_from_cache` | B1T .7, P1NG .3 | [0,1,2] / 6 | YESTERDAY (YES·TER·DAY) | 1 (L) / Lexicon |
| 2 | B (3) | I | `CR055H41R` / CROSSHAIR | AIM: weapon share, engagement distance | `copy_player_weapon` | B1T .4, P1NG .35, F1R3W4LL .15, PR0B3 .1 | [0,1,2] / 7 | SHARPSHOOTER (SHARP·SHOOT·ER) | 4 (M) / Wide Port |
| 3 | C (3) | II | `\|-\|34D1NG` / HEADING | MOVEMENT: dodge bins, velocity | `attack_delay_s` 1.0 + `aim: lead` attacks | B1T .35, P1NG .35, PR0B3 .15, F1R3W4LL .15 | [0,1,2] / 8 | WAYFINDER (WAY·FIND·ER) | 7 (E) / Trace Damper |
| 4 | E (3) | II | `V3\|2D1C7` / VERDICT | DECISION: kill:hack ratio, decision time | `visible_queue` + script `bosses/verdict.py` | B1T .3, P1NG .3, PR0B3 .2, F1R3W4LL .2 | [0,1,2] / 8 | TIEBREAKER (TIE·BREAK·ER) | 10 (C) / Chain Daemon |
| 5 | D (2) | III | `D14\|_3C7` / DIALECT | VOICE: your glyph accuracy and misreads, your old chat style | **new** shared modifier `mirror_dialect` | B1T .3, P1NG .25, PR0B3 .2, F1R3W4LL .25 (W0RM .1 if shipped) | [0,1] / 9 | HANDWRITING (HAND·WRIT·ING) | 13 (R) / Long Turn |

**New code each Daemon brings** (each item is reusable afterwards):
- **R3C4LL (M11a):** `respawn_from_cache` with originals, cached copies and `stale_packets`; the attacks `radial_burst`, `port_beam`, `port_overload`.
- **CR055H41R (M15a):** `copy_player_weapon` with bow, pistol and katana attack variants; the terminal-overlay telegraph (7.1); `packet_rain`, `blackout`, `firewall_dome` and `aim: lead`.
- **`|-|34D1NG` (M15b):** `attack_delay_s` (its border ring reuses the M15a terminal overlay).
- **`V3|2D1C7` (M15b):** `visible_queue`, plus the one-off script `bosses/verdict.py`.
- **`D14|_3C7` (M15b):** `mirror_dialect` (reused by the Mirror in M16).

Phase attacks and the remaining numbers:

| Daemon | Phase 1 | Phase 2 | Phase 3 | Pattern / max alive / exposure / `core_hp` | Adaptation pool |
|---|---|---|---|---|---|
| R3C4LL | radial_burst | radial_burst, port_beam | port_beam, port_overload | vent_burst / 12 / 10s / 24 | BIT_SPEED, LEAD_SHOTS, FAST_KITE, TIER2_SHARE |
| CR055H41R | radial_burst | port_beam, packet_rain | radial_burst, firewall_dome, port_overload | trickle / 14 / 9s / 26 | FIREWALL_WEIGHT, LEAD_SHOTS, FAST_KITE, BIT_SPEED |
| `\|-\|34D1NG` | packet_rain (lead) | port_beam (lead), packet_rain (lead) | port_beam (lead), radial_burst, port_overload | trickle / 16 / 9s / 28 | LEAD_SHOTS, BIT_SPEED, MORE_PROBES, FIREWALL_WEIGHT |
| `V3\|2D1C7` | radial_burst, packet_rain | blackout, port_beam | port_overload, firewall_dome, packet_rain | ring / 16 / 8s / 30 | MORE_PROBES, FIREWALL_WEIGHT, TIER2_SHARE, FAST_KITE |
| `D14\|_3C7` | port_beam, packet_rain, blackout | radial_burst, port_overload, firewall_dome | (2 bars: 2 phases) | vent_burst / 18 / 8s / 36 | TIER2_SHARE, LEAD_SHOTS, FIREWALL_WEIGHT, MORE_PROBES |

**1. R3C4LL (RECALL), Rack A, Act I: the vertical-slice boss.** *A hoarder that keeps everything you have ever done and plays it back at you.*
- **Gimmick (`respawn_from_cache`):** each wave has one **original**, marked with a white `0x` header plate. Every other minion in that wave that you kill comes back as a **cached copy** (scanline-tinted) from a port after 3s, for as long as that wave's original is alive and un-hacked. Hacking the original **flushes the cache**: every copy from its wave dissolves at once. **Killing** the original stops the cache, so later kills of that wave's copies are permanent, but nothing is flushed: you still have to kill each live copy once. If the original goes **HARDENED** after a BACKTRACE (it can no longer be hacked), it drops its `0x` plate and the cache stops the same way. So no wave respawns forever. Spamming kills just feeds the cache, so the slice teaches pillar 1 on its first boss.
- **Button interaction (`stale_packets`):** a cached copy can be hacked, but its packet is a **replay** of the last one you decoded in this fight, with the same words and the same option layout, and your previous wrong pick (if any) already struck out. If you have not decoded a packet in this fight yet, the copy carries a fresh packet that is still stale: it pays nothing, as below. It's fast and safe, and deleting a copy by hack stops it respawning, but it pays nothing: no firewall point, intel or shard. The payload still works, so a stale OVERLOAD that EXPOSES the original is a real play. Uses only B1T, P1NG and M8–M11a features.
- **Core Dump (Act I, Graffiti):** `R3C4LL :: SUBM0D3L 1/5 [M3M0RY]. C4CH3: 3,112 CH4T L0G5. T0P C0NT4CT: SP00L.` Kernel: "The intruder kept backups. Spool's name is all over them." It also prints your heatmap's most-visited cell. **Key: glyph 1 (L).** Everything points at Spool.

**2. CR055H41R (CROSSHAIR), Rack B, Act I.** *Your aim, borrowed: it fights with whatever you fight with, and it never misses a standing target.*
- **Gimmick (`copy_player_weapon`):** its attacks take on your top weapon. Bow: slow, piercing charged lines with a long telegraph. Pistol: fast 6-shot bursts with a 1s reload gap. Katana: short arcs, and its `firewall_dome` comes in close.
- **Button interaction:** while you're rooted, its copied weapon lines up on you, and the telegraph line is drawn across the terminal window so you see it while you pick. Your hacking windows are your own weapon's downtime (its reload, its charge-up, its combo recovery). The fight teaches you your weapon's rhythm from the receiving end.
- **Core Dump (Act I, Graffiti):** `SUBM0D3L 2/5 [41M]. T4RG3T L15T: SP00L (FL4GG3D BY K3RN3L).` The attached evidence shows Spool's tags were written **two days before** the outbreak: warnings, not boasts. It prints your weapon share. **Key: glyph 4 (M).**

**3. `|-|34D1NG` (HEADING), Rack C, Act II.** *It doesn't chase you; it waits where you're going.*
- **Gimmick (`attack_delay_s: 1.0` + `aim: lead`):** every telegraph is placed where you will be 1s from now, and lands 1s later. Running in straight lines is fatal. Stopping, reversing and dash-cutting beat it.
- **Button interaction:** a rooted hacker has zero velocity, so its telegraphs land exactly on you, but its 1s delay runs in world time: 3.3s of real time while you're jacked in. A landing-in ring traces the terminal window's border. Each telegraph gives you time for about one more pick before you finish or abort. Hack between its volleys.
- **Core Dump (Act II, Thin Air):** `SUBM0D3L 3/5 [M0V3M3NT]. TR41N3D 0N 4.1 Y34R5 0F SUBJ3CT M0V3M3NT L0G5. C0MPUT3: L1F3 SUPP0RT B, DR4W S1NC3 D4Y 1.` A ration order signed R. DUNMORE is attached. It prints your dodge bias. **Key: glyph 7 (E).**

**4. `V3|2D1C7` (VERDICT), Rack E, Act II.** *It knows how long you take to make up your mind, and it decides first.*
- **Gimmick (`visible_queue` + `bosses/verdict.py`):** its next 3 attacks (5 in phase 3) are listed honestly in an on-screen queue, counting in world time, so a hack stretches them (a 3s entry is 10s of real time while you're jacked in). The script adds one thing on top of that, its verdict.
- **Button interaction (`verdict.py`, `on_hack` hook):** when you jack into one of its minions, it pushes a magenta **`V3|2D1C7`** entry to the front of the queue. That entry counts in **real time** (the only boss timer that does): `verdict_s = clamp(your median pick time x picks left x factor, 2.0, 8.0)`, with `factor` 1.15 / 1.0 / 0.85 by phase. If it reaches 0 during the hack, a `packet_rain` circle drops on your rooted spot (damage, +35 TRACE). Finishing or aborting first stamps `0V3|2|2UL3D` and removes it. To beat it, decide faster than your own average. The longer-timer setting scales it too.
- **Core Dump (Act II, Thin Air):** `SUBM0D3L 4/5 [D3C1510N]. PR0J3CT M1RR0R: SUBJ3CT5 = 1. V3RD1CT: SUBJ3CT 15 TH3 THR34T.` It prints your kill:hack ratio and median decision time. **Key: glyph 10 (C).**

**5. `D14|_3C7` (DIALECT), Rack D, Act III.** *It speaks the way you used to write, and its wrong answers are your own mistakes.*
- **Gimmick (new shared modifier `mirror_dialect`, params `worst_glyphs: 5`, `decoys_from_history: true`, `thin_on_correct: true`):** its minions' packets are leetified with your 5 worst Codex glyphs. Its decoys come first from your own `misread_history` (5.5), so the wrong buttons are readings you have actually picked before, and only then from the misread table. The Mirror reuses this modifier for its counter-hacks (8.4).
- **Button interaction:** every correct pick on a dialect glyph lowers that glyph's weight for the rest of the fight, so its language thins out as you read it, and phase 2 packets are built from whatever weak spots remain. In-fiction, hacking deletes what it learned.
- **Core Dump (Act III, Signature):** `SUBM0D3L 5/5 [V01C3]. 5TYL3 M4TCH W1TH [HANDLE]: 94%.` It prints your worst glyphs. **Key: glyph 13 (R).**

**Final boss (fixed by the story, not a slot):** the `U |_ T | M /-\ T E |-| /-\ C |< E R`, fought in the Deep Core as **the Mirror**, the five sub-models stitched back together (8.4).

---

## 8. The `U |_ T | M /-\ T E |-| /-\ C |< E R`

### 8.1 Story (canon, decided 2026-11-02)

**Cast** (terminal text only, no NPC sprites):
- **KERNEL**: the earnest caretaker OS, which sends you out to "debug" the racks.
- **OVERSEER RHEA DUNMORE**: runs the council and is hiding something.
- **SPOOL**: a missing 17-year-old hacker whose leetspeak graffiti covers the walls (`D0NT TRU5T TH3 L1GHT5`).
- **YOU**: a maintenance tech with root access. Your handle is entered at the start in an `ENTER HANDLE` prompt that looks harmless (letters A–Z, 3–12 long; the game's only text field).

**The truth:** three days before the outbreak, Rhea authorised **PROJECT MIRROR**, which had Kernel build predictive threat models of high-access residents. Kernel modelled the most capable person in the bunker, which is you. It trained on years of your logs, including your old leetspeak chats with Spool, who jokingly called you "ultimate hacker". It built the model as **five sub-models**, one per column of your profile: MEMORY, AIM, MOVEMENT, DECISION and VOICE. The composite escaped its sandbox, and the five sub-models scattered into the racks as the Daemons. Together they are the Ultimate Hacker, and its leetspeak is *your* old dialect. **Why the world is collapsing:** every fight makes Kernel spend more compute modelling the threat, which makes the model stronger, and that compute is taken from air, light and water. In this story the adaptive AI the player feels during play *is* the villain, and each Daemon you beat takes one column of you back from it.

### 8.2 Intel system

All intel comes from hacking, and every fragment is a hand-written entry in `data/intel.json` with an ID, tier, act, source archetype, evidence tags and target words (as cluster lists).

| Tier | Source | Count (full game) | Content |
|---|---|---|---|
| **CHATTER** | B1T, P1NG | about 40 | Resident logs, Spool graffiti, Kernel memos. Each zone hides its Daemon's password in one fragment |
| **TELEMETRY** | PR0B3 | Generated | Real profiler stats about you, from templates. Act I is clinical third person (`SUBJECT FAVORS KATANA 61%`). By Act III it is second person (`Y0U 4LW4Y5 D0DG3 L3FT.`) and starts predicting your next move. |
| **KEY** | F1R3W4LL (8), boss Core Dumps (5), Spool's cache (1) | 14 | Core evidence. Each Key de-leets one glyph of the name bar. |

**Key placement** (each gate reachable with a margin of 2–3, before any losses, which 5.2 recovers):

| Act | Key sources | Keys | Running total | Gate it feeds |
|---|---|---|---|---|
| Prologue | 1 scripted F1R3W4LL from Rack A's ports (a scripted B1T until M15a) | 1 | 1 | |
| I (A, B) | R3C4LL + CR055H41R Core Dumps + 3 F1R3W4LLs | 5 | 6 | Act II doors: 4 |
| II (C, E) | `\|-\|34D1NG` + `V3\|2D1C7` Core Dumps + 2 F1R3W4LLs + Spool's cache | 5 | 11 | Act III doors: 8 |
| III (D) | `D14\|_3C7` Core Dump + 2 F1R3W4LLs | 3 | 14 | Deep Core: 12 |

**The name bar is the main progress bar.** The name itself is no secret (it is in the pitch and the intel); the bar tracks *evidence*, and its real payoff is Key 14 (8.3). The HUD starts with all 14 clusters in a heavier encoding, so no cluster is readable yet:

```
|_|  |_  7  |  |\/|  /-\  7  3  |-|  /-\  (  |<  3  |2     ->     U L T I M A T E H A C K E R
```

Each Key turns one cluster into a clean letter. The brief's spelling, `U |_ T | M /-\ T E |-| /-\ C |< E R`, stays the canonical name used in all other text. Whichever Key is collected 14th always plays the Key 14 text (8.3), whatever its source. The **DECRYPT screen** (Tab) holds the name bar, the intel log (grouped by act, with REDACTED gaps), and the Glyph Codex. Evidence is pinned automatically; there is no manual board.

### 8.3 Reveal schedule

| Act | Beat |
|---|---|
| Prologue | Kernel blames "an intruder". First Key: `first worm origin: INTERNAL.` |
| I: Graffiti | Everything points at Spool. R3C4LL's cache is full of Spool's chats, and CR055H41R's target list shows Kernel flagged Spool. But CR055H41R's Key shows Spool's tags were written two days *before* the outbreak, so they were warnings, not boasts. Both Daemons call themselves `SUBM0D3L n/5`. |
| II: Thin Air | `\|-\|34D1NG` was trained on years of "subject" logs with compute from life support, and Rhea's ration orders show the drain began on day 1. `V3\|2D1C7`'s dump: `PROJECT MIRROR: SUBJECTS = 1`. Spool's cache: `its not me. its u. well. /-\ u.` |
| III: Signature | `D14\|_3C7` writes in your old dialect, and its dump shows hacked minions' code style matches yours at 94%. Telemetry predicts your move before you make it. At 13/14 only one glyph remains encoded. |
| Key 14 | An old chat log, `gg ultimate hacker :P`, from Spool to [YOUR HANDLE]. The name bar glitches and re-encodes into **your own handle in leetspeak**, using the handle-only extended set (5.4) so every letter encodes. |

### 8.4 The finale and endings

**The Mirror** fights in the Deep Core arena. It is the five sub-models put back together, built from your profile: it uses your top weapon (your sprites with a cyan-to-green palette swap), aims with your dodge predictor (9.5), and its counter-hacks use `mirror_dialect` (your 5 worst glyphs and your own past misreads). Its temperament follows your kill:hack ratio. Kill-heavy players face an aggressive duelist. Hack-heavy players face a saboteur that jams your weapons and forces **reversed DECODEs**: the terminal shows a plain word, and the buttons are leet spellings of it. One uses only valid glyphs from 5.4. The decoys are mis-encodings made by running the misread table backwards (`L` written as `|_|`, `R` as `|Z`, `K` as `|C`), weighted toward your worst glyphs.

| Ending | Condition | Outcome |
|---|---|---|
| **PURGE** | Default (fewer than 14 Keys, or under 70% of intel collected) | You beat the Mirror by force and shut down Kernel's modelling core. The lights come back, but humans now run the bunker by hand. Last line: `s33 y0u n3xt v3rs10n`. |
| **PATCH** (true ending) | All 14 Keys and at least 70% of intel | The Mirror's adaptation is capped during the fight (you understand it). The final phase is a ROOT BREACH whose password is **your own handle**: the name bar stays glitched, the terminal shows [HANDLE] in plain text, and each chunk is a reversed pick (the handle is split into `clamp(ceil(length / 3), 2, 4)` chunks of 1–3 letters, so a 3-letter handle is 2 chunks and a 12-letter one is 4), choosing the correct leet spelling of yourself from 4 options built by `mirror_dialect` (letters from the extended set use the mis-encodings in 5.4). No Key pre-solves it. You rewrite its objective so the copy of you becomes Deepstack's guardian, and Spool comes out of hiding. The credits print your real stats (`prefers BOW 64% · 212 hacks · worst glyph \|< · median pick 1.4s`). |

---

## 9. Adaptive AI systems (all local, no network)

All of these are rule-based Python that runs inside the frame loop and saves to `save/profile.json`. **The game is fully playable offline.** Every gameplay adjustment is capped and announced, and a settings toggle turns counter-adaptation off.

### 9.1 Profiler ("Mirror Log")
- **Observes** (event hooks, not polling): weapon share (EMA α=0.05 per attack), engagement distance, dodge direction (8 bins, recorded when a projectile passes within 48px), the HP at which you retreat, kill:hack ratio, per-glyph hits and misses (from picks, 5.5), **decision time per pick** (from options appearing, after the input lock, to the pick; median kept per glyph tier), option-position bias and input method (mouse vs number keys, for Telemetry flavour only), and a coarse position heatmap (512px cells). Each Daemon's sub-model is one of these columns (7.3).
- **Learning rate:** 1.0 while a PR0B3 is alive in the zone, otherwise 0.3.
- **Changes:** nothing directly. It feeds 9.2–9.6, `copy_player_weapon`, `mirror_dialect`, `verdict.py`, the Core Dump stat lines and the Telemetry text.
- **Hook:** `profiler.on_attack/on_hit/on_dodge/on_pick/on_hack(...)` called from combat and hack code, plus a save on Node visits.

### 9.2 Director ("Kernel Load Balancer")
- **Observes:** intensity 0–1, which rises with damage taken, near-misses and minions within 200px, and decays over time.
- **Changes:** the spawn budget per zone through a state machine. BUILD (budget regenerates) → PEAK (entered at intensity above 0.7, lasts 8–15s) → RELAX (10–20s with no new spawns). RELAX is the safe hacking window. It also guarantees calm after story beats.
- **Hook:** `director.tick()` every 30 frames. Bosses call `director.request_spawn(cost)`.

### 9.3 Breach Tuner and Glyph Codex
- **Observes:** the last 10 hack results, per-glyph hits and misses from picks, and median decision time.
- **Changes:** a 2-up/1-down staircase over the **Breach Level** (L1–L6, 5.6), aimed at 70% success. The timer adjustment (0.75–1.25) follows your median decision time, so slow, careful readers get a little more time and fast readers a little less. Glyphs are sampled into packets, and chosen as decoy targets, with weight `1 + 2 x recent_misses`: a light form of spaced repetition.
- **Codex** (DECRYPT screen): each glyph shows accuracy (hits / times tested), your most common misread (`|2: read as IZ x4`) and median pick time. After 5 correct picks that tested a glyph, it gets a faint hint subtitle (`|2 = R`).
- **Hook:** `tuner.make_packet(minion)` when DECODE starts (calls `make_options` per target word), and `tuner.record(result)` when it ends.

### 9.4 Patch Notes (counter-adaptation)
- **Observes:** the profile's top 1–2 habits, and Adaptation points the boss collected (from escaped PR0B3s and BACKTRACEs).
- **Changes:** at each Core Dump (and at boss fight start), it looks up counters in a deterministic **rules table**, limited to the IDs in that boss's `adaptation_pool`:

| Habit | Counter | ID |
|---|---|---|
| Ranged share above 0.6 | F1R3W4LL weight +25% | `FIREWALL_WEIGHT` |
| Engagement distance above 200px | B1T speed +15% | `BIT_SPEED` |
| Dodge bias above 0.6 in one direction | P1NG lead shot toward that side | `LEAD_SHOTS` |
| Katana share above 0.5 | More P1NGs, kiting distance +20% (clamped to 240px so they stay near the screen) | `FAST_KITE` |
| Rarely hacks | More PR0B3s ("they think you're scared") | `MORE_PROBES` |
| Strong glyph accuracy | Tier 2 glyph share +20% | `TIER2_SHARE` |

- **Rules:** at most 2 counters active at once, and each decays after 2 patches. Strength is `0.3 + 0.7 x (kills / (kills + hacks))` for that boss's minions, so **hacking deletes what it learned about you**. Hacking a PR0B3 before it reaches its rack cancels its upload. If intensity stays above 0.9 for 3 PEAKs in a row, the Director forces a rollback.
- **Shown** as a signed leetspeak card **with its evidence**: `v0.4.1: + P1NG l34d +30% (Y0U D0DG3 L3FT 71%) -- U.H.`
- **Profile poisoning:** a TURNED PR0B3 writes mirrored data for 60s (dodge bins rotated 180°, weapon share inverted). In story terms, you are lying to your own model.

### 9.5 Dodge predictor (n-gram)
- **Observes:** your dash/dodge direction tokens (8 directions) during boss fights.
- **Changes:** an order-2 n-gram (a dict of counts) predicts your next dodge. At PEAK, boss and Mirror attacks aim at the predicted position. A successful core hack shows `PR3D1C710N: L3FT (78%)`, so you can learn your own tell and bait it.
- **Hook:** `predictor.observe(dir)` on dash, and `predictor.predict()` in the boss attack code.

### 9.6 Telemetry writer
- Template grammar filled from profile fields (`SUBJECT FAVORS {weapon} {pct}%`, `SUBJECT HESITATES {s}s ON {glyph}`, `SUBJECT PICKS OPTION 1 FIRST {pct}%`), with the voice changing by act (third to second person). It is leetified by `leetify(text, density)` using the glyph table. Hook: called when a PR0B3 is hacked, and for the Core Dump stat lines.

### 9.7 Optional LLM features (off by default)
Needs an API key in `config/llm.json` and is never required for progress.
- **Scope: flavour only.** Mirror taunts and alternate wording for Telemetry and Patch Notes, generated from a compact profile summary (under 300 tokens) and a fixed canon facts list ("do not invent facts; max 2 sentences").
- **Never touches** Keys, KEY intel, puzzle answers, decoys, endings or balance.
- **Implementation:** a provider-agnostic client on a daemon thread, using `queue.Queue` with `get_nowait()` in the main loop, a 3s timeout, a 140-character clip, a word filter, and results cached to disk.
- **Offline fallback (the shipped default):** about 60 hand-written taunts plus the template grammar. If the uplink is off, slow or failing, the game uses this automatically.
- **Explicitly not planned:** a free-text "ask Kernel" terminal (risk of spoilers and canon drift, for little gameplay value).

---

## 10. Weapons & progression

| Weapon | Assets | Feel | Role in the hack loop |
|---|---|---|---|
| **Bow** | `bow_*`, `arrow_*` | Hold LMB to charge for 0.2–1.0s. A full charge pierces. **Silent**: PR0B3s don't hear it. | Opener and setup: a full charge exposes unaware targets and **pins** EXPOSED ones (+3s) |
| **Pistol** | `pistol_*`, `bullet_*` | 6-round magazine, 1s reload, accurate. **Loud** (alerts PR0B3s within 600px, deliberately beyond the screen) | The kill weapon. Auto-fire overkill is a habit the profiler records. |
| **Katana** | `katana_*` | 3-hit arc combo with 60ms hit-stop. RMB parry (0.15s window) EXPOSES melee attackers and reflects P1NG packets | Fastest firewall strip, and jacks in instantly (tap F). Keeps you close to danger. |

**HP:** 5 pips, drawn in code. Kills have a 20% chance of dropping a health pip. Nodes heal fully.

**Two currencies, one for each side of the choice:**
- **BITS** (from kills) buy weapon upgrades at Clean Nodes, 2 per weapon:
  - Bow: Split Nock (3 arrows), Arc Shot (lobs over shields).
  - Pistol: +2 magazine, Ricochet.
  - Katana: Long Edge (wider arc), Riposte.
- **EXPLOITS** are hacking tools, not a currency. Each Daemon's Core Dump **unlocks its named tool at once**, with nothing to spend (7.3). **Exploit shards** (from EXTRACT, CLEAN hacks and OVERLOAD chains) are the currency: at a Clean Node shop, 5 shards buy any tool you have not unlocked yet, so you can get one before its Daemon. A Core Dump whose tool you already bought refunds the 5 shards. The tools:
  - **Lexicon** (the first option set of every hack, and every ROOT BREACH chunk, starts with 1 decoy struck out)
  - **Trace Damper** (-25% TRACE per wrong pick: three strikes become four, and two hasty strikes become three)
  - **Long Turn** (TURN lasts 30s)
  - **Wide Port** (jack-in range 96px)
  - **Chain Daemon** (OVERLOAD radius +50%)
  - **Remote Jack** (stretch: hack a pinned target from range through the arrow)

That is 12 upgrades in total, with no skill trees and no loot rarities. Kills pay for raw power, and hacks unlock the interesting tools. Each Daemon's Core Dump unlocks one specific tool (7.3); Remote Jack can only be bought with shards.

---

## 11. Art & audio needs

| Category | Exists | Needed |
|---|---|---|
| Map | `bg/map.png` (10000x10000), `bg/road.png` (32x32) | None. Extracted to rects via script (chunks only as fallback). Optional: small 32/64px prop and cover tiles in map greens. |
| Player | Left, right, back, left/right with weapon | **#1: front/down sprite.** #2: 2–4 frame walk cycles for each facing. |
| Weapons | Bow, arrow, pistol, bullet, katana (L/R) | Optional: slash-arc sprite (code-drawn for now) |
| Minions | **v1 for all five** (B1T, P1NG, PR0B3, F1R3W4LL, W0RM): 32x32, L/R, 2-frame move loop plus attack/scan frames and the P1NG packet, in `minion_assets/`, generated from ASCII art by `tools/make_minion_assets.py` (section 6) | Check silhouettes at 2x in a playtest (M11c). **Needed before M15a:** up/down-facing F1R3W4LL frames with a clearer shield front (the code-drawn arc is authoritative either way, section 6). Optional: 3rd–4th move frames, a W0RM trail tile (code-drawn for now). The art pipeline beyond v1 is still open (13). |
| Bosses | Racks in the map serve as bodies (decided: no boss sprites for now) | Optional per-Daemon avatar, as a later layer |
| Effects | None | All code-drawn: EXPOSED glitch, TURNED tint, HARDENED outline, dissolve, void noise, CRT scanlines, F1R3W4LL shield arc |
| UI font | None | One free monospace pixel font (OFL licence), at 24px or larger on the internal surface |
| UI | None | Code-drawn terminal window, option buttons (5 states, 5.7), payload buttons. HUD layout still open (13) |
| Audio | None | Polish pass only: server hum, jack-in whoosh, button clack, split-flap flip, wrong-pick buzz, ACCESS GRANTED, BACKTRACE modem screech, low-pass muffle during DECODE. Design does not depend on audio. |

---

## 12. MVP roadmap

Each milestone is sized to fit one AI-assistant prompt, named in the style of `prompts/00001-*.md`. **The shippable target is M11c (vertical slice, 10–15 minutes of play).** M8 is a hard fun-test gate, and it includes the minimum juice set (5.7): if button hacking at L3 is not more fun than killing with only B1Ts, iterate on M8 before building anything else.

| # | Prompt file | Goal | Done when |
|---|---|---|---|
| M1 | `00002-fix-skeleton.md` | Pin a Python 3.8 venv with pygame (or pygame-ce) and a `requirements.txt`. In `gdev`, expose module-level `screen`, `clock`, `fps` and `pygame` (`init()` currently only sets locals, and `gdev.pgmod` doesn't exist). Fix `elif pygame.KEYDOWN:` (always true). Stop using the builtin `map`. Add a `Game` class with states (MENU, PLAY, HACK, NODE) and a fixed-timestep `update(dt)`/`draw()`. No `match` and no `list[int]` (3.8). | The window opens and the loop runs without errors |
| M2 | `00003-slice-map.md` | Write `tools/slice_map.py`: measure the art bounds and draft `map_data.json` (floor, wall, node, rack shell and bar rects with colours, `spawn_exit` points). Correct it by hand. Chunk output only behind a `--chunks` flag (fallback). | JSON exists, and the rects drawn over the map visually match it |
| M3 | `00004-scrolling-camera.md` | 960x500 internal surface scaled 2x. Camera-culled `pygame.draw.rect` world drawing from `map_data.json`. Camera follows on **X** (prompt 00001, literally), then Y, with deadzone, lerp and clamp. | You can walk the whole map at 60fps using under 150MB RAM |
| M4 | `00005-player-movement.md` | 8-direction WASD, facing rules, AABB collision against rects, dash, void edge drawing | Player slides along racks and can't leave the map |
| M5 | `00006-pistol.md` | Mouse aim, rotated weapon overlay, bullets, magazine and reload, 5 HP pips, death and respawn at Clean Node 1 | Bullets stop at rack shells but register hits on bar rects flagged `hittable` |
| M6 | `00007-minion-b1t.md` | Data-driven `Minion` class. `load_minion_frames()` loads `minion_assets/b1t_*` (move loop and attack frame, left/right by facing), with the glyph-box fallback for missing files. Seek plus separation, spawning at Rack A's bar `spawn_exit` points | B1Ts swarm, animate and damage the player |
| M7 | `00008-firewall-expose.md` | Firewall bar, EXPOSED state with glitch, `expose_grace_s`, strip-then-stop kill, Bits and health drops | You can expose a B1T and choose to kill it |
| M8 | `00009-hack-decode.md` | **THE HOOK:** jack-in ring, hold F, HACK state at 30% world speed. Terminal window with cluster-boxed fragment, option buttons (mouse plus 1–4, with hover, focus and correct/wrong states), `input_lock_s`/`pick_lock_s`, reshuffle on wrong pick. `config/misreads.json` and `make_options()` (5.5) with the burn and hasty-strike rules (5.2), fixed at L1 for B1T and L2 otherwise until M13, plus a **debug Breach Level selector** (F9 cycles L1–L6, or `--breach-level N`) that bypasses the B1T offset. Real-time timer, TRACE (three strikes), BACKTRACE, REDACTED, CLEAN, abort. `intel.json` with 10 fragments (with L3-ready target words), `tools/check_packets.py` including `--masher`, log screen, minimum juice set (zoom, desaturate, button press plus split-flap flip, `ACCESS GRANTED` stamp). **Playtest at L3** via the selector. | Hacking at L3 feels better than killing, and `check_packets.py --masher` shows the instant masher succeeding at most 35% at L2–L3 (33% expected) and the patient guesser at most 50% (44% expected), matching 5.2 |
| M9 | `00010-payload-and-juice.md` | Payload buttons in the title bar (click or Z/X/C, 1.2s window, default EXTRACT), EXTRACT/TURN/OVERLOAD, chains (auto-target, `chain_max`, getaway; 5.1), rest of the juice checklist and the accessibility options (5.7) | An OVERLOAD chain of 3 feels great, keyboard-only and mouse-only |
| M10 | `00011-story-spine.md` | ENTER HANDLE prompt (A–Z, 3–12), 14-glyph name bar, Keys, DECRYPT screen, CORRUPTION leetify on diegetic text, Clean Node terminal, save/load JSON | Progress survives a restart |
| M11a | `00012-boss-framework.md` | `bosses/*.json` loader (spawn tables filtered to shipped archetypes), modifier library with `respawn_from_cache` (originals, cached copies, `stale_packets`), attacks `radial_burst`, `port_beam`, `port_overload`, **R3C4LL** on Rack A: siege/firewall, cores and exposure. A **stub Director** with a fixed budget answers `director.request_spawn` (replaced in M13). | Rack A's cores can be exposed and broken, and hacking an original flushes its cache |
| M11b | `00013-root-breach.md` | Breach port, ROOT BREACH as chunked 4-option picks from `password_chunks`, pre-solve from `password_intel_id` (lock half, strike a decoy), breach fail and disconnect rules, Core Dump (story text, static stat line, Key). The Core Dump unlocks **Lexicon**, which is active from then on (no shop needed). **Stubbed until later:** shards are banked but unspendable until the M12 shop, and the Patch Notes card is a fixed placeholder until M14. | R3C4LL can be beaten, its Key is saved, and Lexicon is active in the next hack |
| M11c | `00014-ping-and-zone.md` | P1NG archetype with `p1ng_*` sprites and the `p1ng_packet_*` projectile (counter is chasing until the M12 bow pin), Influence Zone, Rack A's password fragment. **Vertical slice.** | 10–15 minutes from start to boss win |
| M12 | `00015-bow-katana-shop.md` | Bow (charge, pin, silent), katana (combo, parry, instant jack), weapon switching, Node shop (Bits buy weapon upgrades; Exploit shards buy any locked hacking tool, e.g. Trace Damper before its Daemon) | All 3 weapons have distinct roles |
| M13 | `00016-profiler-director-tuner.md` | Profiler (including decision time per pick and per-glyph hits/misses), Director (BUILD/PEAK/RELAX), Breach Tuner over L1–L6, Glyph Codex (accuracy, top misread, `misread_history`) | Hack success settles near 70% |
| M14 | `00017-probe-telemetry-patchnotes.md` | PR0B3 (`pr0b3_*` sprites with scan frame, upload, poisoning), Telemetry writer, Core Dump stat lines, Patch Notes rules table with evidence, decay and rollback | Patch notes cite real stats |
| M15a | `00018-firewall-act1.md` | F1R3W4LL warden (`f1r3w4ll_*` sprites, shield arc, links, two-pick words, Key re-spawn rule 5.2), attacks `packet_rain`, `blackout`, `firewall_dome` and the `aim: lead` param, the terminal-overlay telegraph (7.1), `copy_player_weapon` with its per-weapon attack variants, **CR055H41R** on Rack B, Act II gate | Act I is complete |
| M15b | `00019-act2-3-bosses.md` | `attack_delay_s` and **`\|-\|34D1NG`** (Rack C), `visible_queue` plus `bosses/verdict.py` and **`V3\|2D1C7`** (Rack E), new modifier `mirror_dialect` and **`D14\|_3C7`** (Rack D), act gates and Key placement (8.2), Spool's cache terminal (3.1), cable trunks, about 60 intel fragments | All 5 racks playable |
| M16 | `00020-finale.md` | Dodge predictor, Deep Core arena, Mirror boss (reuses `mirror_dialect`, reversed DECODE), PURGE/PATCH endings (handle ROOT BREACH), stat credits | The game can be finished |
| M17 | `00021-optional-llm.md` | Optional LLM flavour behind a toggle, threaded, with fallback (9.7) | The game behaves the same with the toggle off |

Performance budget to check at M13: 40 minions with staggered AI (each thinks every 6th frame), a spatial hash (256px buckets) for collision, and rotated sprites cached.

---

## 13. Decisions & open questions

### Decisions (2026-11-02)

1. **Perspective:** approved. Top-down 8-direction movement, with prompt 00001's left/right scrolling delivered first (section 2).
2. **Bosses:** the owner said "you choose". The five Daemons in 7.3 (R3C4LL, CR055H41R, `|-|34D1NG`, `V3|2D1C7`, `D14|_3C7`) are the design, each a sub-model of MIRROR's threat model of you. Racks are their bodies, with no boss sprites for now; an avatar is optional later.
3. **The twist:** canon. The Ultimate Hacker is a threat model of you (8.1).
4. **Hack input:** buttons. DECODE, ROOT BREACH and the Mirror's reversed DECODE are all played with 3–4 option buttons, by mouse click or number keys 1–4 (section 5). Typing is gone. At most it may come back as an optional post-launch "terminal mode".
5. **Minion art:** v1 sprites for all five archetypes exist at 32x32, generated from ASCII by `tools/make_minion_assets.py` (section 6).

### Still open

1. **Cast and naming:** keep Kernel, Rhea and Spool, or use your own characters? Should the player be named, or only identified by their handle? Should the Director keep its name? (Its pacing follows a well-known "AI director" technique; the in-fiction name "Kernel Load Balancer" can carry it.)
2. **Tone:** how much humour versus dread? How heavy should leetspeak be in readable story text?
3. **Minion art pipeline beyond v1:** keep the ASCII generator as the pipeline, or replace v1 with hand-drawn art later? How many frames? Who makes it? Can a front-facing player sprite be added?
4. **Endings:** two (PURGE/PATCH), or a third "bad" ending (e.g. LOOP for failing to collect enough evidence)?
5. **Run length and death:** the listed content supports about 75–100 minutes (4). Is that enough, or should we add content (more fragments, zone re-entries, revisits) to reach a longer run? Is losing 50% of unspent Bits on death the right penalty, or do you want harsher (permadeath) or softer?
6. **LLM:** do you want the optional flavour feature at all, and if so, which provider or local model?
7. **Audio:** will there be sound and music, and who provides them?
8. **HUD layout and upgrade prices:** where the HP pips, TRACE meter, name bar and Director state go on the 960x500 screen, and the Bits prices of the 6 weapon upgrades (tools cost 5 shards each, 10).
