"""Headless smoke test: drives whole sessions through Game.step() with fake events.

Run from the code folder:
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy /usr/bin/python3 tools/smoke_test.py
    SMOKE_ONLY=test_full_run,test_minion_types ...   only those steps (after the menus)

What it does (and asserts along the way):
  menus by keyboard and mouse -> SETTINGS change -> HOW TO PLAY -> DECRYPT (empty)
  -> NEW GAME -> ENTER HANDLE (typed + clicked letters, too-short rejected) -> PLAY
  -> move, shoot, reload -> PAUSE / resume -> Rack A: real B1Ts spawn from R3C4LL's zone waves,
  pistol strips one, jack in with F, decode (keys + mouse), payload
  -> wrong pick, abort, TURN, OVERLOAD chain, weapon kill, death + respawn
  -> FULL RUN on the same run: Prologue F1R3W4LL carrier (two-part Key) -> Act I: R3C4LL
     (respawn_from_cache: original, cached stale copy, flush) and CR055H41R (copied weapon),
     carriers fw:A1 (real 20 s wait), fw:B1 (Key re-spawn rule: port re-spawn, heir, HARDENED),
     fw:B2 -> the Act II door blocks at 3 Keys, opens at 4 -> Act II: |-|34D1NG (attack delay;
     its ROOT BREACH fails first), V3|2D1C7 (queue + verdict clock), fw:C1, fw:E1, Spool's cache
     -> the Act III door blocks at 7, opens at 8 -> save at CLEAN NODE 2, QUIT, CONTINUE, state
     compared -> Act III: D14|_3C7 (dialect; a death mid-fight disengages it), fw:D1, fw:D2 ->
     Key 14 scene -> the hatch -> the Deep Core -> the Mirror: real weapon hits, real
     counter-hacks for all 5 cores, a death in the arena, the reversed handle ROOT BREACH
     (fails once, re-seals, then wins) -> PATCH ending -> credits -> MAIN MENU.
     Every Daemon: the real fight trigger (SIEGE), firewall points from real hacks, EXPOSURE with
     real pistol shots on the core lane, a ROOT BREACH (plain / pre-solved / failing), Core Dump.
  -> a second run: 12 Keys, the Mirror, pause + settings + QUIT / CONTINUE inside the Deep
     Core, broken by force -> PURGE -> credits -> DECRYPT log
  -> minion archetypes: PR0B3 upload / hacked mid-upload (cancel + poisoning), F1R3W4LL shield
     block from the front / strip from behind, links + EXPOSE on hack, W0RM split / no split /
     slime, the Key shard fallback at a Clean Node
  -> review regressions (last pick at the buzzer, hasty window, getaway speed, window
     placement, focus loss mid-hack, intel log BACK, L1 word counts, callout)
  -> data dir (the macOS app): with PIXEL_APOCALYPSE_DATA_DIR set, a child process saves a
     setting and a run there, init.py --dev --frames runs from another folder, and nothing in
     the code folder is created or changed -> chaos
  -> perf: an empty-frame baseline, then PLAY / HACK / a Daemon fight with ~40 minions.
Dev helpers (grant_keys, dev_kill_phase, firewall / core skips) are used only where a real
step was already exercised once; each such shortcut is commented where it happens.

Screenshots go to $SMOKE_OUT (default: the session scratchpad folder below).
Settings and the run save are written to temporary files, never to the real save/ folder.
Exit code 0 = everything passed.
"""
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import traceback

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)      # the code folder (and the vendored pygame) first

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import gdev  # noqa: E402  (first: relaunches with the Python the vendored pygame needs)
import pygame  # noqa: E402
import settings as settings_mod  # noqa: E402

# Keep the player's real settings file untouched.
_tmp = tempfile.mkdtemp(prefix="pa_smoke_")
settings_mod.SETTINGS_FILE = os.path.join(_tmp, "settings.json")

import game  # noqa: E402
import hack  # noqa: E402
import minions as minions_mod  # noqa: E402
import finale as finale_mod  # noqa: E402
import bosses as bosses_mod  # noqa: E402

game.SAVE_FILE = os.path.join(_tmp, "run.json")     # never touch the real save/run.json

OUT = os.environ.get("SMOKE_OUT", "/private/tmp/claude-503/-Users-dhruva-Documents-workspace-"
                     "Pixel-Apocalypse/658bf41c-43b0-433f-8da0-da93713e023e/scratchpad/full")
K = pygame
g = None
log_lines = []
shots = []


# ------------------------------------------------------------------ helpers

class SmokeFail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise SmokeFail(msg + "   [state=%s frame=%d]" % (g.state if g else "?", g.frame if g else -1))


def say(msg):
    log_lines.append(msg)
    print("  " + msg)


GUARD = [False]      # True: the player keeps i-frames between steps (the long scripted runs)


def _guard():
    if GUARD[0] and g.run is not None and not g.run.player.dead:
        g.run.player.invuln = max(g.run.player.invuln, 1.0)


def frames(n=1, events=None):
    _guard()
    g.step(list(events or []))
    for _ in range(n - 1):
        _guard()
        g.step([])


def shot(name):
    p = os.path.join(OUT, "smoke_%s.png" % name)
    g.save_frame(p)
    shots.append(p)


def wait_until(pred, max_frames, msg):
    for _ in range(max_frames):
        if pred():
            return
        frames(1)
    check(pred(), "timed out: " + msg)


def teleport(pos):
    p = g.run.player
    p.pos = pygame.math.Vector2(pos)
    p._sync_rect()
    g.run.camera.snap(p.pos)


def free_spot_near(pos, radius=40):
    w = g.run.world
    for r in range(0, 200, 8):
        for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, -r), (r, -r), (-r, r)):
            rect = pygame.Rect(0, 0, radius, radius)
            rect.center = (int(pos[0] + dx), int(pos[1] + dy))
            if w.rect_free(rect):
                return rect.center
    raise SmokeFail("no free floor near %r" % (pos,))


def aim_at(world_pos):
    """Mouse-move event that puts the cursor on a world position."""
    sx, sy = g.run.camera.world_to_internal(world_pos)
    sx = max(1, min(gdev.INTERNAL_SIZE[0] - 2, sx))
    sy = max(1, min(gdev.INTERNAL_SIZE[1] - 2, sy))
    return gdev.mouse_move((sx, sy))


def exposed_minion_near(offset=(28, 0)):
    """Force-spawn a B1T next to the player and strip it (public API), grace skipped."""
    p = g.run.player
    m = g.run.minions.spawn("b1t", free_spot_near((p.pos.x + offset[0], p.pos.y + offset[1]), 12))
    m.expose()
    m.grace_t = 0.0
    return m


def jack_in():
    """Hold F until the HACK state starts (0.4 s hold)."""
    frames(1, [gdev.key_event(K.K_f, True)])
    wait_until(lambda: g.state == game.HACK, 60, "jack-in after holding F")
    frames(1, [gdev.key_event(K.K_f, False)])
    return g.run.hack


def answer_hack(h, use_mouse=False, payload="EXTRACT", payload_by_click=False, max_frames=900):
    """Decode every word with the correct option, then pick a payload.
    Returns the session result. Mixes keys and clicks when use_mouse == "mix"."""
    picks = 0
    for _ in range(max_frames):
        if h.finished or g.run is None or g.run.hack is not h:
            break
        if h.input_ready():
            if h.phase == hack.DECODE and h.correct_slot() is not None:
                slot = h.correct_slot()
                mouse = use_mouse is True or (use_mouse == "mix" and picks % 2 == 1)
                if mouse:
                    frames(1, gdev.click(h.slot_center(slot)))
                else:
                    frames(1, gdev.tap(K.K_1 + slot))
                picks += 1
                continue
            if h.phase == hack.PAYLOAD:
                if payload_by_click:
                    frames(1, gdev.click(h.payload_center(payload)))
                else:
                    key = {"EXTRACT": K.K_z, "TURN": K.K_x, "OVERLOAD": K.K_c}[payload]
                    frames(1, gdev.tap(key))
                # let the session close itself
                for _ in range(120):
                    if h.finished or g.state != game.HACK or g.run.hack is not h:
                        break
                    frames(1)
                break
        frames(1)
    check(h.finished, "hack session never finished")
    return h.result


def protect_player():
    p = g.run.player
    p.invuln = max(p.invuln, 5.0)


# ------------------------------------------------------------------ the scripted session

def test_menus():
    say("MAIN MENU")
    frames(20)
    check(g.state == game.MENU, "should boot into MENU")
    shot("01_main")
    mm = g.main_menu
    # keyboard: down twice -> SETTINGS
    frames(1, gdev.tap(K.K_s))
    frames(1, gdev.tap(K.K_DOWN))
    check(mm.selected == 2, "W/S navigation: expected SETTINGS selected, got %d" % mm.selected)
    frames(1, gdev.tap(K.K_RETURN))
    check(g.state == game.SETTINGS, "Enter on SETTINGS")
    sm = g.settings_menu
    # keyboard: hack_difficulty NORMAL -> HARD with Right
    check(sm.rows[0] == "hack_difficulty", "first settings row")
    before = g.settings["hack_difficulty"]
    frames(1, gdev.tap(K.K_RIGHT))
    check(g.settings["hack_difficulty"] != before, "Right cycles hack difficulty")
    frames(1, gdev.tap(K.K_LEFT))
    check(g.settings["hack_difficulty"] == before, "Left cycles it back")
    # mouse: click the SHOW FPS row
    i = sm.rows.index("show_fps")
    r = sm.row_rects()[i]
    old = g.settings["show_fps"]
    frames(1, gdev.click((r.right - 20, r.centery)))
    check(g.settings["show_fps"] != old, "click toggles SHOW FPS")
    check(os.path.isfile(settings_mod.SETTINGS_FILE), "settings saved on change")
    check(settings_mod.load()["show_fps"] == g.settings["show_fps"], "saved settings reload")
    # screen shake off then on again (applied live, read at draw time)
    j = sm.rows.index("screen_shake")
    sm.selected = j
    frames(1, gdev.tap(K.K_RETURN))
    check(g.settings["screen_shake"] is False, "Enter toggles screen shake off")
    frames(1, gdev.tap(K.K_RETURN))
    check(g.settings["screen_shake"] is True, "and back on")
    # fullscreen: the settings row and F11 both switch it (dummy driver: no real window)
    sm.selected = sm.rows.index("fullscreen")
    frames(1, gdev.tap(K.K_RIGHT))
    check(g.settings["fullscreen"] is True and gdev.fullscreen, "FULLSCREEN setting applies live")
    frames(1, gdev.tap(K.K_F11))
    check(g.settings["fullscreen"] is False and not gdev.fullscreen, "F11 toggles fullscreen back")
    frames(10)
    shot("02_settings")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.MENU, "Esc from SETTINGS back to MENU")
    say("SETTINGS changed: show_fps=%s, saved to temp file" % g.settings["show_fps"])

    # HOW TO PLAY by mouse, page through tabs, back by Esc
    rects = mm.item_rects()
    frames(1, gdev.click(rects[1].center))
    check(g.state == game.HOWTO, "click HOW TO PLAY")
    for k in range(3):
        frames(1, gdev.tap(K.K_RIGHT))
        frames(8)
    shot("03_howto")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.MENU, "Esc from HOW TO PLAY")

    # INTEL LOG (empty, no run yet)
    frames(1, gdev.click(rects[3].center))
    check(g.state == game.INTEL, "click INTEL LOG")
    frames(10)
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.MENU, "Esc from INTEL LOG")

    # Esc on the main menu only points at QUIT, never exits
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.running and g.state == game.MENU and mm.selected == 4, "Esc on main menu selects QUIT")

    # NEW GAME by mouse click -> ENTER HANDLE
    check(mm.items[0][1] == "new_game" and not g.has_save(), "no save: first item is NEW GAME")
    frames(1, gdev.click(rects[0].center))
    check(g.state == game.HANDLE, "NEW GAME opens ENTER HANDLE")
    hs = g.handle_screen
    frames(20)
    for k in (K.K_d, K.K_h):
        frames(1, gdev.tap(k))
    frames(1, gdev.tap(K.K_RETURN))
    check(g.state == game.HANDLE and hs.value == "DH", "a 2-letter handle is rejected")
    for k in (K.K_r, K.K_u, K.K_v, K.K_x):
        frames(1, gdev.tap(k))
    frames(1, gdev.tap(K.K_BACKSPACE))
    frames(1, gdev.tap(K.K_1))                   # not a letter: ignored
    frames(1, gdev.click(hs.cell_rects()[0].center))     # the on-screen "A"
    check(hs.value == "DHRUVA", "typed + clicked handle: %r" % hs.value)
    frames(10)
    shot("02b_handle")
    frames(1, gdev.tap(K.K_RETURN))
    check(g.state == game.PLAY and g.run is not None and g.run.handle == "DHRUVA", "ENTER starts the run")
    import menu
    check(menu.game_start, "menu.game_start is True while a run is active")
    check(g.run.progression.act == 0 and g.run.progression.key_count == 0, "a new run starts in the PROLOGUE")
    say("NEW GAME -> handle DHRUVA -> PLAY (prologue)")


def test_move_shoot_pause():
    run = g.run
    p = run.player
    frames(20)
    x0, y0 = p.pos.x, p.pos.y
    frames(1, [gdev.key_event(K.K_d)])
    frames(59)
    frames(1, [gdev.key_event(K.K_d, False)])
    dx = p.pos.x - x0
    check(140 <= dx <= 200, "D for 1 s should move ~180 px, moved %.1f" % dx)
    frames(1, [gdev.key_event(K.K_w)])
    frames(29)
    frames(1, [gdev.key_event(K.K_w, False)])
    check(p.pos.y < y0 - 40, "W moves up")
    say("moved %.0f px right in 1 s, then up %.0f px" % (dx, y0 - p.pos.y))

    # dash
    xd = p.pos.x
    frames(1, [gdev.key_event(K.K_a)])
    frames(1, gdev.tap(K.K_SPACE))
    frames(18)
    frames(1, [gdev.key_event(K.K_a, False)])
    check(xd - p.pos.x > 100, "Space dash should carry well past walking speed (%.0f px)" % (xd - p.pos.x))

    # shoot the pistol at the mouse
    hudw = p.weapon_hud()
    check(hudw["name"] == "PISTOL", "start with the pistol")
    ammo0 = hudw["ammo"]
    target = (p.pos.x + 120, p.pos.y - 40)
    frames(1, [aim_at(target)])
    frames(1, [gdev.mouse_button(g.mouse_pos, 1, True)])
    check(len(p.projectiles) >= 1, "LMB fires a bullet")
    frames(1, [gdev.mouse_button(g.mouse_pos, 1, False)])
    frames(4)
    shot("04_shoot")
    check(p.weapon_hud()["ammo"] == ammo0 - 1, "a shot uses one round")
    frames(1, gdev.tap(K.K_r))
    frames(70)
    check(p.weapon_hud()["ammo"] == p.weapon_hud()["mag"], "R reloads within ~1 s")

    # weapon switching 1/2/3
    frames(1, gdev.tap(K.K_1))
    frames(2)
    check(p.weapon_name() == "BOW", "1 selects the bow")
    frames(1, gdev.tap(K.K_3))
    frames(2)
    check(p.weapon_name() == "KATANA", "3 selects the katana")
    frames(1, gdev.tap(K.K_2))
    frames(15)
    check(p.weapon_name() == "PISTOL", "2 selects the pistol")

    # pause / resume / settings from pause
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PAUSE, "Esc pauses")
    t = run.time
    frames(30)
    check(run.time == t, "world frozen while paused")
    shot("05_pause")
    frames(1, gdev.click(g.pause_menu.item_rects()[1].center))
    check(g.state == game.INTEL and g.return_state == game.PAUSE, "DECRYPT LOG from pause")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PAUSE, "back to PAUSE")
    frames(1, gdev.click(g.pause_menu.item_rects()[2].center))
    check(g.state == game.SETTINGS and g.return_state == game.PAUSE, "SETTINGS from pause")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PAUSE, "back to PAUSE")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PLAY, "Esc resumes")
    frames(1, gdev.tap(K.K_TAB))
    check(g.state == game.INTEL, "Tab opens INTEL over the game")
    frames(1, gdev.tap(K.K_TAB))
    check(g.state == game.PLAY, "Tab closes it")
    say("move / dash / shoot / reload / weapon switch / pause / tab OK")


def test_rack_a_real_fight():
    """Go to Rack A, let the real spawner send B1Ts, strip one with the pistol, jack in."""
    run = g.run
    w = run.world
    rack = w.rack("A")
    check(rack is not None, "map has Rack A")
    bar = rack.bars[len(rack.bars) // 2]
    spot = free_spot_near((bar.spawn_exit[0], bar.spawn_exit[1] + 150))
    teleport(spot)
    check(rack.in_zone(run.player.pos), "teleported into Rack A's zone")
    wait_until(lambda: any(b.flicker > 0 for b in rack.bars) or run.minions.live_count() > 0,
               300, "spawn telegraph at Rack A")
    shot("06_telegraph")
    wait_until(lambda: run.minions.live_count() >= 2, 400, "B1Ts spawned from Rack A ports")
    say("Rack A spawner released %d minions" % run.minions.live_count())
    frames(40)
    shot("07_fight")

    # fight: aim at the nearest hunting minion and click until something is EXPOSED
    target = None
    for i in range(900):
        protect_player()
        p = run.player
        target = run.minions.nearest_exposed(p.pos, 400)   # slow 1-firewall B1Ts can expose far out
        if target is not None:
            break
        live = [m for m in run.minions.minions if m.alive and m.team == "enemy" and
                m.state in (minions_mod.HUNTING, minions_mod.HARDENED)]
        if not live:
            frames(1)
            continue
        # shoot unshielded minions first: the prologue's F1R3W4LL carrier blocks frontal shots
        live.sort(key=lambda m: (m.kind == "f1r3w4ll", (m.pos - p.pos).length_squared()))
        m = live[0]
        ev = [aim_at(m.pos)]
        if i % 8 == 0:
            ev.append(gdev.mouse_button(g.mouse_pos, 1, True))
        elif i % 8 == 1:
            ev.append(gdev.mouse_button(g.mouse_pos, 1, False))
        frames(1, ev)
    check(target is not None, "pistol stripped a firewall -> EXPOSED minion")
    say("pistol exposed %s (firewall %d)" % (target.label, target.firewall_max))
    frames(1, [gdev.mouse_button(g.mouse_pos, 1, False)])

    # walk to it while holding F (it is frozen while exposed); the pack can body-block,
    # so keep steering until the hold completes
    frames(2)
    shot("08_exposed")
    frames(1, [gdev.key_event(K.K_f, True)])
    held = set()
    for _ in range(300):
        protect_player()
        if g.state == game.HACK or not target.hackable:
            break
        d = target.pos - run.player.pos
        want = set()
        if abs(d.x) > 6:
            want.add(K.K_d if d.x > 0 else K.K_a)
        if abs(d.y) > 6:
            want.add(K.K_s if d.y > 0 else K.K_w)
        ev = [gdev.key_event(k, False) for k in held - want] + [gdev.key_event(k) for k in want - held]
        held = want
        frames(1, ev)
    frames(1, [gdev.key_event(k, False) for k in held | set([K.K_f])])
    if g.state != game.HACK:
        say("(exposed minion got away; using a force-exposed one for the first jack-in)")
        exposed_minion_near()
        protect_player()
        jack_in()
    h = run.hack
    check(run.time_scale == game.WORLD_SLOWMO, "world in 30% slow-mo while hacking")
    frames(14)
    shot("09_hack_decode")
    res = answer_hack(h, use_mouse="mix")
    say("hack #1 (real fight): %s clean=%s fragment=%s words=%s" % (
        res["outcome"], res["clean"], res["fragment_id"], h.target_words()))
    frames(5)
    check(g.state == game.PLAY, "back to PLAY after the hack")
    # leave the zone and clear the pack so the scripted hacks below are calm
    teleport(w.player_start)
    run.minions.clear()
    frames(10)


def test_wrong_pick_and_aborts():
    run = g.run
    m = exposed_minion_near()
    h = jack_in()
    wait_until(lambda: h.input_ready(), 60, "decode input ready")
    t0 = h.trace
    frames(1, gdev.tap(K.K_1 + h.wrong_slot()))
    frames(2)
    check(h.trace > t0, "a wrong pick adds TRACE")
    shot("10_wrong_pick")
    frames(1, gdev.tap(K.K_ESCAPE))
    wait_until(lambda: g.state == game.PLAY, 60, "Esc aborts the hack")
    check(run.stats["aborts"] == 1, "abort counted")
    check(m.alive and not m.jacked, "aborted minion rebooted, not jacked")
    say("wrong pick -> TRACE %.0f, Esc abort OK" % h.trace)
    frames(20)

    m2 = exposed_minion_near((-28, 0))
    h = jack_in()
    wait_until(lambda: h.input_ready(), 60, "decode input ready")
    frames(1, gdev.click((5, 5), 3))         # RMB anywhere aborts
    wait_until(lambda: g.state == game.PLAY, 60, "RMB aborts the hack")
    check(run.stats["aborts"] == 2, "RMB abort counted")
    run.minions.clear()
    frames(20)


def test_payloads():
    run = g.run
    # EXTRACT by keyboard answers, Z
    m = exposed_minion_near()
    h = jack_in()
    res = answer_hack(h, use_mouse=False, payload="EXTRACT")
    check(res["outcome"] == "success" and res["payload"] == "EXTRACT", "EXTRACT hack: %r" % res)
    frames(30)
    check(not m.alive, "EXTRACT deletes the minion")
    say("hack (keys, Z) EXTRACT OK, %s" % res["fragment_id"])

    # TURN by mouse answers and a mouse click on TURN
    m = exposed_minion_near((0, 28))
    h = jack_in()
    res = answer_hack(h, use_mouse=True, payload="TURN", payload_by_click=True)
    check(res["outcome"] == "success" and res["payload"] == "TURN", "TURN hack: %r" % res)
    frames(3)
    check(m.state == minions_mod.TURNED, "TURN makes an ally")
    shot("11_turned")
    say("hack (mouse, click) TURN OK, %s" % res["fragment_id"])
    frames(20)

    # OVERLOAD with a second exposed minion close by -> chain hack
    a = exposed_minion_near((30, 0))
    b = run.minions.spawn("b1t", free_spot_near((a.pos.x + 60, a.pos.y), 12))
    hacks0 = run.stats["hacks"]
    h = jack_in()
    res = answer_hack(h, use_mouse="mix", payload="OVERLOAD")
    check(res["payload"] == "OVERLOAD", "OVERLOAD picked")
    frames(1)
    check(g.state == game.HACK and run.hack is not None and run.hack_target is b,
          "OVERLOAD exposes the neighbour and chains straight into it")
    shot("12_overload_chain")
    res2 = answer_hack(run.hack, use_mouse=False, payload="EXTRACT")
    frames(2)
    check(run.stats["hacks"] == hacks0 + 2, "chain counted two hacks")
    check(run.getaway > 0, "chain getaway slow-mo")
    say("OVERLOAD chain OK (%s -> %s)" % (res["fragment_id"], res2["fragment_id"]))
    frames(80)
    run.minions.clear()


def test_kill_and_death():
    run = g.run
    p = run.player
    # weapon kill: strip and shoot a B1T with real clicks
    m = run.minions.spawn("b1t", free_spot_near((p.pos.x + 140, p.pos.y), 12))
    kills0, bits0 = run.stats["kills"], run.bits
    for i in range(400):
        protect_player()
        if not m.alive:
            break
        ev = [aim_at(m.pos)]
        if i % 10 == 0:
            ev.append(gdev.mouse_button(g.mouse_pos, 1, True))
        elif i % 10 == 1:
            ev.append(gdev.mouse_button(g.mouse_pos, 1, False))
        frames(1, ev)
    frames(1, [gdev.mouse_button(g.mouse_pos, 1, False)])
    frames(40)
    check(run.stats["kills"] == kills0 + 1, "strip-then-stop: weapon kill counted once")
    check(run.bits > bits0, "kill gives Bits")
    say("weapon kill OK: kills=%d bits=%d" % (run.stats["kills"], run.bits))

    # death -> SIGNAL LOST -> respawn at Clean Node 1, a quarter of the Bits lost, intel kept
    bits = run.bits
    intel = run.intel.count()
    for _ in range(p.max_hp + 2):
        p.invuln = 0.0
        p.take_damage(1, (p.pos.x + 10, p.pos.y))
        frames(1)
    check(p.dead, "%d hits kill the player" % p.max_hp)
    frames(30)
    shot("13_death")
    wait_until(lambda: not p.dead, int(game.DEATH_S * 60) + 30, "respawn after death")
    check(run.stats["deaths"] == 1, "death counted")
    check(run.bits == bits - bits // 4, "a quarter of the Bits lost (%d -> %d)" % (bits, run.bits))
    check(run.intel.count() == intel, "intel kept through death")
    check(p.hp == p.max_hp, "full HP after respawn")
    n1 = run.world.node("N1")
    check((p.pos - pygame.math.Vector2(n1.rect.center)).length() < 600, "respawned at Clean Node 1")
    say("death + respawn OK (bits %d -> %d)" % (bits, run.bits))
    frames(30)


def answer_breach(h, max_frames=900, wrong=False):
    """Pick the correct option (or, with wrong=True, a wrong one) for every chunk of a
    BreachSession until it closes (no payload)."""
    for _ in range(max_frames):
        if h.finished or g.run is None or g.run.hack is not h:
            break
        if h.input_ready() and h.phase == hack.DECODE and h.correct_slot() is not None:
            slot = h.wrong_slot() if wrong else None
            if slot is None:                 # no decoy left on this chunk: solve it, miss the next
                slot = h.correct_slot()
            frames(1, gdev.tap(K.K_1 + slot))
            continue
        frames(1)
    check(h.finished, "breach session never finished")
    return h.result


def close_scene(max_pages=12):
    """Page through a TerminalScene with Enter until it closes."""
    for _ in range(max_pages * 3):
        if g.state != game.SCENE:
            return
        frames(25)
        frames(1, gdev.tap(K.K_RETURN))
    check(g.state != game.SCENE, "scene never closed")


# ------------------------------------------------------------------ full-run helpers

V = pygame.math.Vector2
EVENTS = []          # (name, data) of every Run event since the last listen()


class _Recorder(object):
    def on_event(self, name, data):
        EVENTS.append((name, data))


def listen():
    """Record the current run's events into EVENTS (call again after a load: a new Run)."""
    if g.run is not None and not any(isinstance(x, _Recorder) for x in g.run.listeners):
        g.run.listeners.append(_Recorder())


def events(name, since=0):
    return [d for n, d in EVENTS[since:] if n == name]


def release_all():
    ev = [gdev.key_event(k, False) for k in list(g.keys_down)]
    ev += [gdev.mouse_button(g.mouse_pos, b, False) for b in list(g.mouse_buttons)]
    frames(1, ev)


def walk_to(pos, max_frames=900, tol=12, protect=True):
    """Steer with WASD toward a world point (real movement and collision). -> distance left."""
    run = g.run
    held = set()
    target = V(pos)
    for _ in range(max_frames):
        if protect:
            protect_player()
        d = target - run.player.pos
        if d.length() <= tol or g.state != game.PLAY:
            break
        want = set()
        if abs(d.x) > 4:
            want.add(K.K_d if d.x > 0 else K.K_a)
        if abs(d.y) > 4:
            want.add(K.K_s if d.y > 0 else K.K_w)
        ev = [gdev.key_event(k, False) for k in held - want] + [gdev.key_event(k) for k in want - held]
        held = want
        frames(1, ev)
    frames(1, [gdev.key_event(k, False) for k in held])
    return (target - run.player.pos).length()


def fire_at(target, n_frames, stop=None):
    """Hold LMB aimed at a world point (or a callable giving one) for up to n_frames.
    stop() -> True ends it early. Returns the number of frames held."""
    get = target if callable(target) else (lambda: target)
    frames(1, [aim_at(get()), gdev.mouse_button(g.mouse_pos, 1, True)])
    n = 1
    while n < n_frames and g.state == game.PLAY and not (stop and stop()):
        protect_player()
        frames(1, [aim_at(get())])
        n += 1
    frames(1, [gdev.mouse_button(g.mouse_pos, 1, False)])
    return n


def zone_spot(rack, keep_px=400):
    """A free floor point inside a rack's Influence Zone, in the rack's own act region, but
    further than the boss fight trigger from its shell (carriers walk here, the fight stays off)."""
    w = g.run.world
    reg = w.region_of(rack.bars[0].spawn_exit) if rack.bars else None
    far = rack.shell.inflate(2 * keep_px, 2 * keep_px)
    cx, cy = rack.zone_center
    for k in (0.75, 0.6, 0.9, 0.97, 0.45):
        for a in range(0, 360, 20):
            p = V(cx, cy) + V(rack.zone_radius * k, 0).rotate(a)
            r = pygame.Rect(0, 0, 24, 24)
            r.center = (int(p.x), int(p.y))
            if rack.in_zone(p) and not far.collidepoint(p) and w.art_bounds.contains(r) and w.rect_free(r) \
                    and (reg is None or w.region_of((p.x, p.y)) == reg):
                return (int(p.x), int(p.y))
    raise SmokeFail("no calm spot in zone %s" % rack.id)


def spawn_exposed(kind="b1t", rack_id=None, tags=None, offset=(26, 0)):
    """A minion next to the player, stripped (public API), grace skipped."""
    p = g.run.player
    m = g.run.minions.spawn(kind, free_spot_near((p.pos.x + offset[0], p.pos.y + offset[1]), 12), rack_id)
    for k, v in (tags or {}).items():
        setattr(m, k, v)
    m.expose()
    m.grace_t = 0.0
    return m


def hack_minion(m, payload="EXTRACT"):
    """Jack into an EXPOSED minion next to the player and decode it for real."""
    protect_player()
    frames(1)
    h = jack_in()
    check(g.run.hack_target is m, "jacked into the wrong target (%r)" % getattr(g.run.hack_target, "label", None))
    res = answer_hack(h, use_mouse="mix", payload=payload)
    frames(2)
    return h, res


def zoom_shot(name, zoom=1):
    """A screenshot with the camera pulled out (a rack is ~1400 px; at 3X the view is ~320)."""
    cam = g.run.camera
    z0 = cam.zoom
    cam.set_zoom(zoom)
    cam.snap(g.run.player.pos)
    frames(2)
    shot(name)
    cam.set_zoom(z0)
    cam.snap(g.run.player.pos)


def at_jack_target(pos):
    """Stand on free floor next to a jack target and let the input lock run out."""
    teleport(free_spot_near(pos, 12))
    frames(int(game.INPUT_LOCK_S * 60) + 3)


def quiet_zone():
    """Drop live minions, queued spawns and attacks (the scripted parts that follow need calm)."""
    g.run.minions.clear()
    g.run.attacks.clear()


# ------------------------------------------------------------------ full run: the pieces

FULL = {"breach_presolved": 0, "breach_plain": 0, "breach_failed": 0, "core_hits": {}, "fights": []}


def take_carrier(sid, real_wait=False, kill_test=False):
    """A zone's Key carrier: walk into the zone (outside its Daemon's fight range), wait for the
    carrier to come out of a port (real 20 s wait, or the timer shortened), strip it, hack it."""
    run = g.run
    prog = run.progression
    src = prog.source(sid)
    rack = run.world.rack(src["zone"])
    quiet_zone()
    if real_wait:
        prog.carriers.pop(sid, None)            # the countdown starts when you walk in
    teleport(zone_spot(rack))
    keys0 = prog.key_count
    t0 = run.time
    if not real_wait:
        frames(2)
        st = prog.carriers.get(sid)
        if st is not None:
            st["t"] = min(st["t"], 0.5)
    carrier = None
    for _ in range(int((prog_mod().CARRIER_DELAY_S + 8) * 60)):
        protect_player()
        frames(1)
        st = prog.carriers.get(sid)
        if not real_wait and st is not None and st["t"] > 0.5:
            st["t"] = 0.5
        cs = [m for m in run.minions.live() if getattr(m, "key_source", None) == sid]
        if cs:
            carrier = cs[0]
            break
    check(carrier is not None, "Key carrier %s spawns at RACK %s" % (sid, rack.id))
    if real_wait:
        waited = run.time - t0
        check(prog_mod().CARRIER_DELAY_S - 1 <= waited <= prog_mod().CARRIER_DELAY_S + 3,
              "carrier %s came out after %.1fs (want ~%.0fs)" % (sid, waited, prog_mod().CARRIER_DELAY_S))
    check(carrier.kind == "f1r3w4ll" and carrier.rack_id == rack.id, "carrier is a F1R3W4LL of RACK %s" % rack.id)
    wait_until(lambda: carrier.state != minions_mod.SPAWNING, 60, "carrier out of its port")
    if kill_test:
        key_respawn_rule(carrier, sid, rack)
        carrier = [m for m in run.minions.live() if getattr(m, "key_source", None) == sid][0]
    p = run.player
    carrier.pos = V(free_spot_near((p.pos.x + 26, p.pos.y), 12))
    carrier.state = minions_mod.HUNTING
    carrier.expose()
    carrier.grace_t = 0.0
    h, res = hack_minion(carrier)
    check(res.get("key_source") == sid and res["outcome"] == "success", "carrier %s packet: %r" % (sid, res.get("outcome")))
    check(any(pk.get("parts") == 2 for pk in h.picks) or all(len(h.shown[w]) < 4 for w in h.targets),
              "F1R3W4LL Key packet has a two-part word")
    check(prog.key_count == keys0 + 1 and sid in prog.collected, "carrier %s -> Key %d" % (sid, prog.key_count))
    frames(int(game.KEY_BANNER_S * 60) + 5)
    return carrier


def key_respawn_rule(carrier, sid, rack):
    """doc 5.2: a carrier killed by a weapon -> its Key moves to a same-kind minion of the zone;
    with none, a port re-spawns a carrier after 3 s; a HARDENED carrier passes it on too."""
    run = g.run
    prog = run.progression
    others = [m for m in run.minions.live(rack.id, kind="f1r3w4ll") if m is not carrier]
    for m in others:
        m.kill("despawn")
    frames(2)
    # 1) no heir: a weapon kill -> the port re-spawns one carrier ~3 s later
    carrier.expose()
    carrier.grace_t = 0.0
    carrier.kill("weapon")
    t0 = run.time
    frames(2)
    check(not [m for m in run.minions.live() if m.key_source == sid], "dead carrier lost its Key")
    wait_until(lambda: [m for m in run.minions.live() if m.key_source == sid], 400, "port re-spawns the carrier")
    dt = run.time - t0
    check(2.2 <= dt <= 4.2, "re-spawned carrier after %.1fs (want ~3s)" % dt)
    c2 = [m for m in run.minions.live() if m.key_source == sid][0]
    wait_until(lambda: c2.state != minions_mod.SPAWNING, 60, "re-spawned carrier out of its port")
    # 2) an heir in the zone: the Key moves to it
    heir = run.minions.spawn("f1r3w4ll", free_spot_near((c2.pos.x + 60, c2.pos.y), 14), rack.id)
    c2.kill("weapon")
    frames(2)
    check(heir.key_source == sid and c2.key_source is None, "the Key moved to the other F1R3W4LL")
    # 3) a HARDENED (backtraced) carrier passes it on as well
    c3 = run.minions.spawn("f1r3w4ll", free_spot_near((heir.pos.x + 60, heir.pos.y), 14), rack.id)
    heir.harden()
    run.emit("minion_hardened", minion=heir)
    frames(2)
    check(c3.key_source == sid and heir.key_source is None, "a HARDENED carrier hands the Key on")
    say("Key re-spawn rule (%s): port re-spawn after %.1fs, moved to an heir, HARDENED hand-off" % (sid, dt))


def prog_mod():
    import progression
    return progression


def boss_fight(bid, breach="plain", die=False, extra=None):
    """One Daemon, through its real lifecycle: the fight trigger (SIEGE), firewall points from
    real hacks of its rack's minions, EXPOSURE with real pistol shots on the core lane, the
    other cores skipped (dev), ROOT BREACH (plain / presolved / failing first), Core Dump."""
    run = g.run
    prog = run.progression
    b = run.bosses.bosses[bid]
    rack = b.rack
    check(not b.cleared and b.fight_allowed(), "%s can be fought (act %d)" % (bid, prog.act))
    quiet_zone()
    keys0 = prog.key_count
    # the real trigger: walk the last bit into fight range
    teleport(zone_spot(rack))
    frames(5)
    check(b.state == "ZONE", "%s: in its zone the Daemon is ZONE (got %s)" % (bid, b.state))
    g.teleport_to_zone(rack.id)
    wait_until(lambda: b.state == "SIEGE", 30, "%s fight starts inside fight_trigger_px" % bid)
    check(run.bosses.active is b, "%s is the active boss" % bid)
    frames(2)
    check([d for d in events("boss_fight_started") if d["boss"] is b], "%s: boss_fight_started" % bid)
    bar = b.nearest_bar()                       # the screenshot: close enough to see its ports
    out = V(bar.spawn_exit) - V(rack.shell.center)
    out = V(0, 1) if out.length_squared() == 0 else out.normalize()
    teleport(free_spot_near(V(bar.spawn_exit) + out * 50, 14))
    frames(60)
    zoom_shot("4%d_%s_siege" % (b.slot, bid))
    if extra:
        extra(b, "siege")
    if die:
        boss_death(b)
    # firewall: two real hacks of its minions, then (dev) down to the last point, one more real hack
    for i in range(2):
        if b.firewall <= 1:
            break
        fw = b.firewall
        m = spawn_exposed("b1t", rack.id, {"boss_id": b.id})
        _h, res = hack_minion(m)
        check(res["outcome"] == "success" and b.firewall == fw - 1,
              "%s: a hack of its minion costs 1 firewall point (%d -> %d)" % (bid, fw, b.firewall))
    b.firewall = min(b.firewall, 1)
    m = spawn_exposed("b1t", rack.id, {"boss_id": b.id})
    hack_minion(m)
    wait_until(lambda: b.state == "EXPOSURE", 30, "%s EXPOSURE at firewall 0" % bid)
    check(any(x.hittable for x in rack.bars), "%s: the core bar is hittable" % bid)
    shoot_core(b)
    # the rest of the cores (dev), down to the breach port
    for _ in range(8):
        if b.state == "BREACH_READY":
            break
        if b.state == "SIEGE":
            b.firewall = 0
            frames(1)
        g.dev_kill_phase()
        frames(1)
    check(b.state == "BREACH_READY" and b.port is not None, "%s: breach port open (got %s)" % (bid, b.state))
    if extra:
        extra(b, "breach_ready")
    quiet_zone()
    if breach == "fail":
        h = open_breach(b)
        h.trace_mult = 5.0                   # easy tuning: wrong picks alone rarely BACKTRACE now
        res = answer_breach(h, wrong=True)
        check(res["outcome"] == "backtrace", "%s: wrong picks -> BACKTRACE (got %r)" % (bid, res["outcome"]))
        frames(2)
        check(b.state == "SIEGE" and b.firewall == bosses_mod.BREACH_FAIL_FIREWALL and b.port is None,
              "%s: failed breach -> SIEGE with firewall %d (got %s, %d)"
              % (bid, bosses_mod.BREACH_FAIL_FIREWALL, b.state, b.firewall))
        FULL["breach_failed"] += 1
        for _ in range(bosses_mod.BREACH_FAIL_FIREWALL):
            m = spawn_exposed("b1t", rack.id, {"boss_id": b.id})
            hack_minion(m)
        wait_until(lambda: b.state == "BREACH_READY", 30, "%s: the hacks reopen the breach port" % bid)
        say("%s: breach failed -> BACKTRACE -> SIEGE (firewall %d) -> hacks -> port reopened"
            % (b.leet_name, bosses_mod.BREACH_FAIL_FIREWALL))
    hidden = None
    pid = b.d.get("password_intel_id")
    if breach == "plain" and run.intel.has(pid) and not run.intel.entry(pid).get("redacted"):
        hidden = run.intel.entry(pid)           # a zone hack found the password: hide it for this case
        hidden["redacted"] = True
    spec = b.breach_spec()
    h = open_breach(b)
    pre = len(spec["presolved"])
    if breach == "presolved":
        check(pre >= 1 and spec["prestrike"], "%s: found password pre-solves chunks %r" % (bid, spec["presolved"]))
        check(len(h.targets) == len(spec["chunks"]) - pre, "%s: pre-solved chunks are locked" % bid)
    elif breach == "plain":
        check(pre == 0, "%s: no password found -> nothing pre-solved (%r)" % (bid, spec["presolved"]))
    frames(14)
    shot("4%d_%s_breach" % (b.slot, bid))
    res = answer_breach(h)
    check(res["outcome"] == "success" and res.get("kind") == "breach", "%s breach: %r" % (bid, res.get("outcome")))
    FULL["breach_presolved" if pre else "breach_plain"] += 1
    if hidden is not None:
        hidden["redacted"] = False
    wait_until(lambda: g.state == game.SCENE, 90, "%s Core Dump scene" % bid)
    check(g.scene_spec.get("kind") == "core_dump" and g.scene_spec.get("boss_id") == bid, "core dump scene")
    kinds = [pg.get("kind", "text") for pg in g.scene_spec["pages"]]
    check(set(["stats", "key", "exploit", "patch"]).issubset(kinds), "core dump pages %r" % kinds)
    frames(30)
    frames(1, gdev.tap(K.K_RETURN))             # type the first page out
    frames(5)
    shot("4%d_%s_core_dump" % (b.slot, bid))
    close_scene()
    check(g.state == game.PLAY and b.cleared and run.bosses.active is None, "%s cleared, back to PLAY" % bid)
    check(prog.key_count == keys0 + 1 and prog.keys.get(b.key_glyph_index) == "boss:" + bid,
          "%s Key de-leets glyph %d" % (bid, b.key_glyph_index))
    check(prog.has_exploit(b.exploit), "%s unlocks %s" % (bid, b.exploit))
    check(not run.minions.live(rack.id), "RACK %s's minions flushed" % rack.id)
    FULL["fights"].append(bid)
    say("%s: SIEGE -> EXPOSURE (%s) -> breach (%s%s) -> Core Dump -> Key %d/14, %s" % (
        b.leet_name, FULL["core_hits"].get(bid), "pre-solved %d" % pre if pre else "plain",
        ", failed once first" if breach == "fail" else "", prog.key_count, b.exploit))


def open_breach(b):
    at_jack_target(b.port.pos)
    check(g.run.jack_target is b.port, "%s: the breach port is the jack target" % b.id)
    protect_player()
    h = jack_in()
    check(isinstance(h, hack.BreachSession), "holding F at the port opens a ROOT BREACH")
    return h


def shoot_core(b):
    """EXPOSURE: stand in front of the exposed core's face and hold the pistol on it."""
    import bosses as bosses_mod
    run = g.run
    bar = b.core_bar()
    _lane, _conduit, face = bosses_mod.face_lane(b.rack, bar)
    out = V(bar.spawn_exit) - V(face.center)
    out = out.normalize() if out.length_squared() else V(0, 1)
    quiet_zone()
    teleport(free_spot_near(V(face.center) + out * 70, 14))
    frames(1, gdev.tap(K.K_2))
    frames(3)
    c = b.core()
    hp0, hits0 = c["hp"], bar.hits
    phase0 = b.phase

    def done():
        if run.minions.live_count():
            run.minions.clear()              # the zone waves come out of these very ports
        return b.state != "EXPOSURE" or b.phase != phase0

    n = fire_at(lambda: face.center, int(b.d.get("exposure_window_s", 10) * 60) + 30, stop=done)
    frames(1, [gdev.mouse_button(g.mouse_pos, 1, False)])
    dmg = hp0 - (0.0 if c["broken"] else c["hp"])
    check(dmg > 0 and bar.hits > hits0, "%s: real pistol shots damage the exposed core (%.0f, %d hits)" % (
        b.id, dmg, bar.hits - hits0))
    if c["broken"]:
        out_s = "core broken by %d shots in %.1fs" % (bar.hits - hits0, n / 60.0)
        check(b.state in ("SIEGE", "BREACH_READY"), "%s: a broken core moves the fight on" % b.id)
    else:
        out_s = "core %d/%d after the window" % (c["hp"], c["max"])
        check(b.state == "SIEGE" and b.firewall == int((b.firewall_max + 1) // 2) and c["hp"] == hp0 - dmg,
              "%s: window over -> firewall half, core keeps its damage" % b.id)
    FULL["core_hits"][b.id] = out_s
    if not FULL.get("exposure_shot"):
        FULL["exposure_shot"] = True
    return dmg


def boss_death(b):
    """Die mid-fight: the Daemon disengages (DORMANT, firewall and cores kept as they were); back
    at the node, return to the zone and the fight starts again."""
    run = g.run
    p = run.player
    c0 = b.cores[0]
    c0["broken"] = True                                  # a core broken earlier in the fight
    c0["hp"] = 0.0
    b._sync_phase()
    b.firewall = 1
    deaths = run.stats["deaths"]
    for _ in range(p.max_hp + 2):
        p.invuln = 0.0
        p.take_damage(1, (p.pos.x + 10, p.pos.y))
        frames(1)
    check(p.dead, "killed in the %s fight" % b.id)
    shot("4%d_%s_death" % (b.slot, b.id))
    wait_until(lambda: not p.dead, int(game.DEATH_S * 60) + 40, "respawn after dying in a boss fight")
    check(run.stats["deaths"] == deaths + 1, "death counted")
    check(b.state == "DORMANT" and run.bosses.active is None, "%s disengaged on death (%s)" % (b.id, b.state))
    check(b.firewall == 1 and c0["broken"], "%s keeps broken cores and its firewall as it was (got %d)"
          % (b.id, b.firewall))
    check(not b.rack.in_zone(p.pos), "respawned at a Clean Node, away from the rack")
    frames(30)
    g.teleport_to_zone(b.rack.id)
    wait_until(lambda: b.state == "SIEGE", 30, "%s fight restarts on return" % b.id)
    check(b.phase == 1, "%s resumes at the second core" % b.id)
    say("%s: died mid-fight -> disengaged (firewall %d, core 1 kept broken) -> respawn -> fight resumed"
        % (b.leet_name, b.firewall))


def door_test(door_id, pass_to):
    """A closed blast door stops the player (and shots); once the gate opens, walk through."""
    run = g.run
    w = run.world
    d = w.door(door_id)
    check(d is not None, "map has door %s" % door_id)
    quiet_zone()
    above = (d.rect.centerx, d.rect.top - 40)
    below = (d.rect.centerx, d.rect.bottom + 40)
    start, goal = (above, below) if pass_to == "south" else (below, above)
    if not d.open:
        teleport(free_spot_near(start, 14))
        check(d.rect in w.solids, "closed door %s is solid" % door_id)
        walk_to(goal, max_frames=150)
        here = w.region_of((run.player.pos.x, run.player.pos.y))
        check(here == w.region_of(start), "closed door %s blocks the player (now in %s)" % (door_id, here))
        frames(1, gdev.tap(K.K_2))
        flashed = []
        fire_at(goal, 40, stop=lambda: flashed.append(1) if d.flash > 0 else False)
        check(flashed, "a closed door stops (and flashes on) shots")
        shot("5x_door_%s_closed" % door_id)
        say("door %s closed at %d Keys: walked into it, stayed in %s" % (door_id, run.progression.key_count, here))
        return False
    teleport(free_spot_near(start, 14))
    frames(10)
    shot("5x_door_%s_open" % door_id)
    left = walk_to(goal, max_frames=300)
    region = w.region_of((run.player.pos.x, run.player.pos.y))
    check(left < 30 and region == w.region_of(goal), "walked through the open %s door into %s" % (door_id, region))
    say("door %s open at %d Keys: walked through into %s" % (door_id, run.progression.key_count, region))
    return True


def decode_intel(pct):
    """Fill the DECRYPT log up to pct% of the CHATTER (dev: records decodes directly)."""
    run = g.run
    prog = run.progression
    ids = sorted(prog._chatter_ids())
    for fid in ids:
        if prog.intel_percent() >= pct:
            break
        e = run.intel.entry(fid)
        if e is None or e.get("redacted"):
            run.intel.record({"outcome": "success", "fragment_id": fid, "source": "B1T", "gaps": []})
    return prog.intel_percent()


# ------------------------------------------------------------------ full run: the sequence

def test_full_run():
    """NEW GAME (from test_menus) -> Prologue Key -> Act I (R3C4LL, CR055H41R, 3 carriers) ->
    Act II doors at 4 -> Act II (|-|34D1NG, V3|2D1C7, 2 carriers, Spool's cache) -> save /
    CONTINUE -> Act III doors at 8 -> D14|_3C7 (+ a death mid-fight) + 2 carriers -> Key 14 ->
    Deep Core hatch -> the Mirror (real counter-hacks) -> handle ROOT BREACH (fails once) ->
    PATCH ending -> credits -> MAIN MENU."""
    run = g.run
    check(run is not None and g.state == game.PLAY, "a run is active")
    GUARD[0] = True                             # stray hits never end the scripted run early
    listen()
    prog = run.progression
    quiet_zone()

    # --- Prologue: the scripted F1R3W4LL carrier at Rack A
    if "prologue" not in prog.collected:
        rack = run.world.rack("A")
        teleport(zone_spot(rack))
        st = prog.carriers.get("prologue")
        carrier = None
        for _ in range(900):
            protect_player()
            frames(1)
            cs = [m for m in run.minions.live() if getattr(m, "key_source", None) == "prologue"]
            if cs:
                carrier = cs[0]
                break
        check(carrier is not None and carrier.kind == "f1r3w4ll" and carrier.rack_id == "A",
              "the prologue Key carrier is a Rack A F1R3W4LL")
        check(run.bosses.bosses["r3c4ll"].state in ("DORMANT", "ZONE"), "R3C4LL stays out of the fight before Key 1")
        frames(30)
        shot("40_prologue_carrier")
        p = run.player
        carrier.pos = V(free_spot_near((p.pos.x + 26, p.pos.y), 12))
        carrier.state = minions_mod.HUNTING
        carrier.expose()
        carrier.grace_t = 0.0
        h, res = hack_minion(carrier)
        check(res.get("key_source") == "prologue", "the carrier's packet carries the Key")
        check(any(pk.get("parts") == 2 for pk in h.picks) or all(len(h.shown[w]) < 4 for w in h.targets),
              "prologue Key packet has a two-part word")
        if run.key_banner is not None:
            frames(20)
            shot("40_key_banner")
        frames(int(game.KEY_BANNER_S * 60) + 5)
        del st
    check(prog.keys.get(0) == "prologue" and prog.act == 1, "prologue Key 1 -> Act I")
    say("prologue: F1R3W4LL carrier, two-part Key packet -> Key 1, ACT I")

    # --- Act I
    boss_fight("r3c4ll", "plain", extra=r3c4ll_checks)
    take_carrier("fw:A1", real_wait=True)
    check(prog.key_count == 3 and not prog.gate_open("act2"), "3 Keys: Act II still sealed")
    door_test("act2", "south")
    take_carrier("fw:B1", kill_test=True)
    frames(5)
    check(prog.gate_open("act2") and run.world.door_open("act2") and prog.act == 2, "4 Keys open Act II")
    check(events("gate_opened") and events("door_opened"), "gate_opened / door_opened events")
    door_test("act2", "south")
    run.intel.record({"outcome": "success", "fragment_id": "B-05", "source": "B1T", "gaps": []})
    boss_fight("cr055h41r", "presolved", extra=cr055h41r_checks)
    take_carrier("fw:B2")
    check(prog.key_count == 6, "Act I done: 6 Keys (%d)" % prog.key_count)

    # --- Act II
    boss_fight("h34d1ng", "fail", extra=h34d1ng_checks)
    check(prog.key_count == 7 and not prog.gate_open("act3"), "7 Keys: Act III still sealed")
    door_test("act3", "south")
    take_carrier("fw:C1")
    check(prog.key_count == 8 and prog.gate_open("act3") and prog.act == 3, "8 Keys open Act III (%d)" % prog.key_count)
    run.intel.record({"outcome": "success", "fragment_id": "E-04", "source": "B1T", "gaps": []})
    boss_fight("v3rd1c7", "presolved", extra=v3rd1c7_checks)
    take_carrier("fw:E1")
    # Spool's cache: sealed until both Act II Daemons fall (they have), then a jack target
    g.teleport_to_zone("cache")
    frames(int(game.INPUT_LOCK_S * 60) + 3)
    check(run.world.cache_state == "open", "the cache shows open (%s)" % run.world.cache_state)
    check(run.jack_target is not None and run.jack_target.kind == "cache", "Spool's cache is a jack target")
    shot("50_cache_open")
    protect_player()
    h = jack_in()
    answer_hack(h)
    wait_until(lambda: g.state == game.SCENE, 60, "cache scene")
    frames(40)
    shot("51_cache_scene")
    close_scene()
    check("cache" in prog.collected and prog.key_count == 11, "the cache Key (%d/14)" % prog.key_count)
    frames(3)
    check(run.world.cache_state == "empty", "the cache is empty after")
    say("Act II: |-|34D1NG + V3|2D1C7 cleared, fw:C1 + fw:E1, cache Key -> %d/14" % prog.key_count)

    # --- save at a Clean Node (walk-in auto save) -> quit -> CONTINUE
    run = save_and_continue()
    prog = run.progression

    # --- Act III
    door_test("act3", "south")
    boss_fight("d14l3c7", "plain", die=True, extra=d14l3c7_checks)
    take_carrier("fw:D1")
    take_carrier("fw:D2")
    check(prog.key_count == 14, "all 14 Keys (%d)" % prog.key_count)
    wait_until(lambda: g.state == game.SCENE, 90, "Key 14 scene")
    check(g.scene_spec.get("kind") == "key14" and prog.key14_played, "Key 14 plays its scene")
    frames(60)
    g.scene_screen.page = len(g.scene_spec["pages"]) - 1
    frames(60)
    shot("60_key14")
    close_scene()
    frames(5)
    shot("61_handle_bar")
    pct = decode_intel(72)
    check(prog.patch_ready(), "14 Keys and %.0f%% intel: the PATCH ending is possible" % pct)
    say("Act III: D14|_3C7 cleared, fw:D1 + fw:D2 -> 14/14, Key 14 scene; intel %.0f%%" % pct)

    # --- Deep Core
    g.teleport_to_zone("hatch")
    frames(int(game.INPUT_LOCK_S * 60) + 3)
    check(run.world.hatch_open, "the hatch shows open at 12+ Keys")
    check(run.interact_hint is not None and "DEEP CORE" in run.interact_hint[1], "hatch hint: %r" % (run.interact_hint,))
    shot("62_hatch")
    frames(1, gdev.tap(K.K_f))
    frames(2)
    check(g.state == game.SCENE and g.scene_spec.get("on_close") == "enter_deep_core", "hatch opens the descent")
    check(g.scene_spec["pages"][0]["lines"][0].startswith("All fourteen"), "descent text at 14 Keys: %r"
          % g.scene_spec["pages"][0]["lines"][0])
    close_scene()
    check(run.in_deep_core and run.world.kind == "deep_core" and run.finale.active, "in the Deep Core arena")
    check(g.has_save(), "entering the Deep Core saves")
    check(FULL["breach_presolved"] >= 1 and FULL["breach_plain"] >= 1 and FULL["breach_failed"] >= 1 and
          len(FULL["fights"]) == 5, "breaches: %r" % FULL)
    mirror_fight(patch=True)
    GUARD[0] = False


def r3c4ll_checks(b, when):
    """respawn_from_cache: a wave's original, a cached copy after a weapon kill (stale packet),
    flushed when the original is hacked."""
    if when != "siege":
        return
    run = g.run
    mod = [m for m in b.modifiers if m.id == "respawn_from_cache"][0]
    run.director.state = "BUILD"
    run.director.budget = 20.0
    b.wave_t = min(b.wave_t, 0.2)
    wait_until(lambda: [m for m in run.minions.live("A") if m.original and m.state != minions_mod.SPAWNING]
               and len([m for m in run.minions.live("A") if m.wave_id and not m.original]) >= 1, 900,
               "a R3C4LL wave with its 0x original")
    orig = [m for m in run.minions.live("A") if m.original][0]
    wid = orig.wave_id
    copy_src = [m for m in run.minions.live("A") if m.wave_id == wid and not m.original][0]
    shot("41_r3c4ll_original")
    copy_src.expose()
    copy_src.grace_t = 0.0
    copy_src.kill("weapon")
    wait_until(lambda: [m for m in run.minions.live("A") if m.cached and m.wave_id == wid], 400,
               "a weapon kill comes back as a cached copy")
    cached = [m for m in run.minions.live("A") if m.cached and m.wave_id == wid][0]
    ctx = run.minion_hack_ctx(cached)
    check(ctx.get("stale") is True, "a cached copy's packet is stale")
    # hack the original: flush
    p = run.player
    orig.pos = V(free_spot_near((p.pos.x + 26, p.pos.y), 12))
    orig.state = minions_mod.HUNTING
    orig.expose()
    orig.grace_t = 0.0
    fw = b.firewall
    hack_minion(orig)
    frames(3)
    check(not cached.alive and cached.dead_cause == "flush", "hacking the original flushes the cache (%s)" % cached.dead_cause)
    check(b.firewall == max(0, fw - 1), "the original's hack costs a firewall point")
    say("R3C4LL respawn_from_cache: original 0x, cached copy (stale), flushed by hacking the original")


def cr055h41r_checks(b, when):
    if when != "siege":
        return
    run = g.run
    b.plan[0]["t"] = 0.05
    wait_until(lambda: [a for a in run.attacks.active if a.owner is b], 60, "a CR055H41R attack")
    a = [a for a in run.attacks.active if a.owner is b][0]
    want = run.profiler.top_weapon()[0] or "PISTOL"
    check(a.params.get("weapon") == want, "CR055H41R copies your top weapon (%s vs %s)" % (a.params.get("weapon"), want))
    frames(20)
    shot("42_cr055h41r_attack")
    say("CR055H41R copy_player_weapon: its %s uses %s" % (a.id, want))


def h34d1ng_checks(b, when):
    if when != "siege":
        return
    run = g.run
    b.plan[0]["t"] = 0.05
    wait_until(lambda: [a for a in run.attacks.active if a.owner is b], 60, "a |-|34D1NG attack")
    a = [a for a in run.attacks.active if a.owner is b][0]
    check(float(a.params.get("delay_s", 0)) >= 1.0, "|-|34D1NG attacks land 1 s late (%r)" % a.params.get("delay_s"))
    frames(20)
    shot("43_h34d1ng_attack")


def v3rd1c7_checks(b, when):
    if when != "siege":
        return
    run = g.run
    info = run.boss_hud()
    q = info.get("queue") or []
    check(len(q) == 3 and all(not e.get("real_time") for e in q), "V3|2D1C7 shows its next 3 attacks: %r" % q)
    # the verdict clock during a hack of its minion
    m = spawn_exposed("b1t", b.rack.id, {"boss_id": b.id})
    protect_player()
    frames(1)
    h = jack_in()
    frames(30)
    q = run.boss_hud().get("queue") or []
    check(q and q[0].get("real_time") and "V3" in q[0]["label"], "verdict entry heads the queue while hacking: %r" % q[:1])
    shot("44_v3rd1c7_verdict")
    answer_hack(h)
    frames(3)
    check(not [e for e in (run.boss_hud().get("queue") or []) if e.get("real_time")], "verdict entry gone after the hack")
    say("V3|2D1C7: visible queue of 3, verdict clock while hacking (shown beside the terminal)")


def d14l3c7_checks(b, when):
    if when != "siege":
        return
    run = g.run
    m = run.minions.spawn("b1t", free_spot_near((run.player.pos.x + 40, run.player.pos.y), 12), b.rack.id)
    m.boss_id = b.id
    ctx = run.minion_hack_ctx(m)
    d = ctx.get("dialect") or {}
    check(d.get("worst"), "D14|_3C7's packets speak your dialect: %r" % d)
    m.kill("despawn")
    say("D14|_3C7 mirror_dialect: worst glyphs %s" % " ".join(d["worst"]))


def save_and_continue():
    """Walk onto Clean Node 2's terminal (auto save), quit to the menu, CONTINUE, compare."""
    run = g.run
    prog = run.progression
    quiet_zone()
    term = [i for i in run.world.interactables if i.kind == "node_terminal" and i.node_id == "N2"][0]
    teleport(free_spot_near((term.pos.x + 260, term.pos.y), 14))
    frames(5)
    saves = len(events("node_visited"))
    run.player.hp = 2
    walk_to(term.pos, max_frames=400, tol=100)
    frames(3)
    check(len(events("node_visited")) == saves + 1 and prog.last_node == "N2", "walked onto CLEAN NODE 2: visit")
    check(run.player.hp == run.player.max_hp and g.has_save(), "node visit heals and saves")
    before = {"keys": dict(prog.keys), "cleared": set(run.bosses.cleared), "exploits": set(prog.exploits),
              "intel": run.intel.count(), "shards": prog.shards, "bits": run.bits, "codex": dict(run.codex.hits),
              "doors": [d.open for d in run.world.doors], "last": prog.last_node, "stats_hacks": run.stats["hacks"]}
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PAUSE and "SAVE" in g.pause_menu.info, "pause shows the save line: %r" % g.pause_menu.info)
    shot("52_pause_save")
    frames(1, gdev.click(g.pause_menu.item_rects()[4].center))
    check(g.state == game.MENU and g.run is None, "QUIT TO MENU")
    check(g.main_menu.items[0][1] == "continue", "MAIN MENU offers CONTINUE")
    frames(30)
    shot("53_menu_continue")
    frames(1, gdev.tap(K.K_RETURN))
    check(g.state == game.PLAY and g.run is not None, "CONTINUE starts the saved run")
    run = g.run
    listen()
    prog = run.progression
    after = {"keys": dict(prog.keys), "cleared": set(run.bosses.cleared), "exploits": set(prog.exploits),
             "intel": run.intel.count(), "shards": prog.shards, "bits": run.bits, "codex": dict(run.codex.hits),
             "doors": [d.open for d in run.world.doors], "last": prog.last_node, "stats_hacks": run.stats["hacks"]}
    for k in before:
        check(before[k] == after[k], "after CONTINUE %s: %r != %r" % (k, after[k], before[k]))
    check((run.player.pos - term.pos).length() < 300, "CONTINUE puts you at CLEAN NODE 2")
    check(run.world.door("act2").anim == 0, "doors open on load without replaying the animation")
    frames(10)
    say("save at CLEAN NODE 2 -> QUIT -> CONTINUE: keys %d, cleared %s, exploits %d, intel %d, doors %s" % (
        prog.key_count, ",".join(sorted(after["cleared"])), len(after["exploits"]), after["intel"], after["doors"]))
    return run


def mirror_fight(patch=True):
    """In the arena: the INTRO, then real weapon hits on the Mirror's firewall, an EXPOSED counter-
    hack with real picks, a death in the arena, the rest of the cores (firewall skipped, every
    counter-hack real), then the handle ROOT BREACH (PATCH: fail once, then win) or PURGE."""
    run = g.run
    fin = run.finale
    m = fin.mirror
    check(fin.state == "INTRO", "the Mirror re-compiles first (%s)" % fin.state)
    frames(30)
    shot("70_deep_core" if patch else "80_deep_core_purge")
    if patch:
        zoom_shot("70b_deep_core_overview")
    wait_until(lambda: fin.state == "FIGHT", 60 * 30, "the Mirror fight begins")
    check(fin.eligible == patch, "PATCH eligibility %s" % fin.eligible)
    check(run.boss_hud() is not None and run.boss_hud()["name"], "the Mirror has a boss bar")
    frames(1, gdev.tap(K.K_2))
    # real weapon hits strip its firewall
    fw0 = fin.fw
    fire_at(lambda: m.pos, 240, stop=lambda: fin.fw < fw0 - 2 or m.mode != "hunt")
    check(fin.fw < fw0 or m.mode != "hunt", "pistol hits strip the Mirror's firewall (%.0f -> %.0f)" % (fw0, fin.fw))
    shot("71_mirror_fight" if patch else "81_mirror_fight_purge")
    say("Mirror (%s): pistol hits firewall %.0f -> %.0f" % (m.temperament, fw0, fin.fw))
    fin.fw = 0.0                                     # dev: the rest of this firewall
    counter_hack()
    check(fin.phase == 1, "a counter-hack breaks a sub-model core (phase %d)" % fin.phase)
    if patch:
        # death in the arena: back at the arena start, the Mirror back to the start of its phase
        p = run.player
        for _ in range(p.max_hp + 2):
            p.invuln = 0.0
            p.take_damage(1, (p.pos.x + 10, p.pos.y))
            frames(1)
        check(p.dead, "killed by the Mirror")
        wait_until(lambda: not p.dead, int(game.DEATH_S * 60) + 40, "respawn in the arena")
        check((p.pos - V(run.world.player_start)).length() < 40 and fin.phase == 1 and fin.fw == fin.fw_max,
              "arena respawn: player start, phase kept, firewall reset")
        say("death in the Deep Core -> arena start, phase %d kept" % fin.phase)
    if not patch:
        return
    while fin.phase < 5:
        frames(5)
        fin.fw = 0.0                                 # dev: the firewall strip is tested above
        counter_hack()
    wait_until(lambda: fin.state == "HANDLE_BREACH" and fin.target is not None, 120, "the handle breach port")
    shot("72_mirror_down")
    # the handle ROOT BREACH (reversed: plain letters, pick the right leet spelling): fail once
    at_jack_target(fin.target.pos)
    h = jack_in()
    check(isinstance(h, hack.BreachSession) and h.mode == "reversed", "the Mirror's breach is the reversed handle")
    check(h.chunks == finale_mod.handle_chunks(run.handle), "handle chunks %r" % h.chunks)
    frames(14)
    shot("73_handle_breach")
    h.trace_mult = 5.0                       # easy tuning: wrong picks alone rarely BACKTRACE now
    res = answer_breach(h, wrong=True)
    check(res["outcome"] == "backtrace", "a failed handle breach backtraces (%r)" % res["outcome"])
    frames(3)
    check(fin.state == "FIGHT" and fin.resealing, "failed handle breach -> the Mirror re-seals")
    fin.fw = 0.0
    wait_until(lambda: fin.state == "HANDLE_BREACH" and fin.target is not None, 120, "the port reopens")
    at_jack_target(fin.target.pos)
    h = jack_in()
    res = answer_breach(h)
    check(res["outcome"] == "success", "handle breach: %r" % res["outcome"])
    wait_until(lambda: g.state == game.ENDING, 60 * 6, "the PATCH ending")
    ending_screens("PATCH")


def counter_hack():
    run = g.run
    fin = run.finale
    m = fin.mirror
    wait_until(lambda: m.mode == "exposed" and fin.target is not None, 90, "the Mirror EXPOSED")
    phase = fin.phase
    at_jack_target(m.pos)
    check(run.jack_target is fin.target, "the EXPOSED Mirror is the jack target")
    h = jack_in()
    check(h.dialect, "counter-hacks speak your dialect")
    check(h.reversed == (m.temperament == "saboteur"), "saboteur counter-hacks are reversed")
    if phase == 0:
        frames(14)
        shot("7%d_counter_hack" % (4 if fin.eligible else 5))
    res = answer_hack(h)
    check(res["outcome"] == "success", "counter-hack: %r" % res["outcome"])
    frames(3)
    check(fin.phase == phase + 1, "core %d broken" % (phase + 1))


def ending_screens(kind):
    check(g.state == game.ENDING and g.run is None, "ENDING state")
    spec = g.ending_screen.spec
    check(spec["ending"] == kind and spec["credits"] and spec.get("stat_line"), "%s ending spec: %r" % (kind, spec.get("ending")))
    frames(1, gdev.tap(K.K_RETURN))
    check(g.state == game.ENDING, "ENDING ignores input for the first moment")
    frames(400)
    shot("9%d_ending_%s" % (0 if kind == "PATCH" else 2, kind.lower()))
    frames(1, gdev.tap(K.K_RETURN))
    check(g.state == game.ENDING and g.ending_screen.page == 1, "credits page")
    frames(120)
    shot("9%d_credits_%s" % (1 if kind == "PATCH" else 3, kind.lower()))
    frames(1, gdev.tap(K.K_RETURN))
    check(g.state == game.MENU and not g.has_save(), "credits -> MAIN MENU; the finished save no longer continues")
    say("%s ending -> credits (%s) -> MAIN MENU" % (kind, spec.get("stat_line")))


def test_purge_run():
    """A second run: 12 Keys (dev), the hatch, a real counter-hack, then the Mirror broken by
    force -> PURGE (fewer than 14 Keys)."""
    g.new_run(seed=99, handle="PURGER")
    GUARD[0] = True
    listen()
    run = g.run
    frames(10)
    g.grant_keys(12)
    frames(3)
    check(run.world.door_open("act2") and run.world.door_open("act3") and run.progression.gate_open("deep_core"),
          "12 Keys open every gate")
    g.teleport_to_zone("hatch")
    frames(int(game.INPUT_LOCK_S * 60) + 3)
    check(run.interact_hint is not None and "PATCH needs 14 KEYS" in run.interact_hint[1],
          "hatch hint warns PATCH is out of reach: %r" % (run.interact_hint,))
    check("for PATCH" in run.objective_text() and "PURGE only" in run.objective_text(),
          "12 Keys: the objective says PATCH needs the rest: %r" % run.objective_text())
    frames(1, gdev.tap(K.K_f))
    frames(2)
    check(g.state == game.PLAY and not run.in_deep_core and run.hatch_confirm > 0,
          "first F at the hatch only warns (one way, PURGE only)")
    check(any("ONE WAY" in t[0] for t in run.toasts), "the one-way warning toast")
    shot("70_hatch_warning")
    frames(1, gdev.tap(K.K_f))
    frames(2)
    check(g.state == game.SCENE, "descent scene")
    check(any("Twelve clusters" in ln for ln in g.scene_spec["pages"][0]["lines"]) and
          any("PURGE" in ln for ln in g.scene_spec["pages"][0]["lines"]), "descent text counts 12 Keys, warns")
    close_scene()
    check(run.in_deep_core and not run.finale.patch_eligible(), "Deep Core with 12 Keys: PURGE only")
    mirror_fight(patch=False)
    fin = run.finale
    # pause and settings work in the arena too
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PAUSE, "Esc pauses in the Deep Core")
    frames(1, gdev.click(g.pause_menu.item_rects()[2].center))
    check(g.state == game.SETTINGS, "SETTINGS from pause in the Deep Core")
    sm = g.settings_menu
    sm.selected = sm.rows.index("zoom")
    z0 = run.camera.zoom
    frames(1, gdev.tap(K.K_RIGHT))
    frames(2)
    check(run.camera.zoom != z0, "zoom applies live in the arena (%s -> %s)" % (z0, run.camera.zoom))
    frames(1, gdev.tap(K.K_LEFT))
    frames(1, gdev.tap(K.K_ESCAPE))
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PLAY and run.camera.zoom == z0, "back in the fight at zoom %s" % run.camera.zoom)
    # the core broken above saved the run: QUIT -> CONTINUE lands back in the arena, phase kept
    frames(5)
    check(g.has_save(), "a broken Mirror core saves the run")
    frames(1, gdev.tap(K.K_ESCAPE))
    frames(1, gdev.click(g.pause_menu.item_rects()[4].center))
    check(g.state == game.MENU and g.main_menu.items[0][1] == "continue", "QUIT TO MENU from the Deep Core")
    frames(1, gdev.tap(K.K_RETURN))
    run = g.run
    listen()
    fin = run.finale
    check(g.state == game.PLAY and run.in_deep_core and fin.active and fin.phase == 1,
          "CONTINUE in the Deep Core: arena, Mirror at core 2 (phase %d)" % fin.phase)
    say("Deep Core: pause / settings (zoom) / QUIT -> CONTINUE back in the arena at core %d" % (fin.phase + 1))
    wait_until(lambda: fin.state == "FIGHT", 60 * 15, "the Mirror fight resumes")
    g.finish_finale()
    frames(2)
    ending_screens("PURGE")
    # DECRYPT screen of the finished run
    frames(1, gdev.click(g.main_menu.item_rects()[g.main_menu.index_of("intel")].center))
    check(g.state == game.INTEL, "DECRYPT from main menu")
    entries = g.intel_screen.entries()
    for e in entries:
        check(e["text"] and e["title"], "intel entry has a title and text: %r" % e)
    frames(1, gdev.tap(K.K_DOWN))
    frames(30)
    shot("94_decrypt")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.MENU, "back to MENU")
    GUARD[0] = False


# ------------------------------------------------------------------ minion archetypes

def key_shard_fallback():
    """doc 5.2 last resort: a zone that cannot spawn its carrier (3 refusals) spools the Key to
    the nearest Clean Node as a hackable data shard (a progression jack target)."""
    run = g.run
    prog = run.progression
    quiet_zone()
    rack = run.world.rack("A")
    teleport(zone_spot(rack))
    frames(3)
    st = prog.carriers.setdefault("fw:A1", {"t": 0.1, "lost": 0, "fails": 0})
    real = run.minions.spawn_wave
    run.minions.spawn_wave = lambda *a, **k: 0          # the zone is full / its ports are dead
    try:
        for _ in range(600):
            st = prog.carriers.get("fw:A1", st)
            st["t"] = min(st["t"], 0.05)
            frames(1)
            if "fw:A1" in prog.node_shards:
                break
    finally:
        run.minions.spawn_wave = real
    check("fw:A1" in prog.node_shards, "3 refused carrier spawns drop the Key as a node shard")
    node = prog.node_shards["fw:A1"]
    t = prog._shard_target("fw:A1")
    check(t is not None and "shard" in run.objective_text().lower(), "objective points at the shard: %r" % run.objective_text())
    at_jack_target(t.pos)
    check(run.jack_target is t, "the Key shard is a jack target")
    frames(5)
    shot("34_key_shard")
    keys = prog.key_count
    h = jack_in()
    answer_hack(h)
    frames(3)
    check(prog.key_count == keys + 1 and "fw:A1" in prog.collected and not prog.node_shards, "the shard's Key")
    say("Key shard fallback: fw:A1 spooled to CLEAN NODE %s, hacked there -> Key %d" % (node[1:], prog.key_count))


def test_minion_types():
    """PR0B3 upload / cancel, F1R3W4LL shield block + links, W0RM split / no split."""
    g.new_run(seed=5, handle="PROBER")
    listen()
    run = g.run
    frames(10)
    g.grant_keys(1)
    b = run.bosses.bosses["cr055h41r"]
    rack = run.world.rack("B")
    teleport(zone_spot(rack))
    frames(5)
    run.director.force_relax(999)               # no zone waves: only the minions spawned here
    run.progression.collected |= set(["fw:B1", "fw:B2"])     # and no Key carriers
    quiet_zone()
    p = run.player

    # PR0B3 upload: scan_s of line of sight on screen, then it runs to its rack and uploads
    since = len(EVENTS)
    adapt0 = b.adaptation
    pr = run.minions.spawn("pr0b3", free_spot_near((p.pos.x + 70, p.pos.y), 12), "B")
    scan_s = minions_mod.KINDS["pr0b3"]["scan_s"]
    wait_until(lambda: pr.probe_mode == "upload", int(60 * (scan_s + 3)),
               "PR0B3 starts its upload after ~%.0fs watching" % scan_s)
    check(pr.scan_t >= scan_s - 0.1, "PR0B3 watched for %.1fs" % pr.scan_t)
    frames(20)
    shot("30_probe_upload")
    wait_until(lambda: not pr.alive, 60 * 45, "PR0B3 reaches its rack")
    check(pr.dead_cause == "upload" and events("probe_uploaded", since), "probe_uploaded emitted")
    check(b.adaptation == adapt0 + 1, "an upload adds +1 Adaptation to CR055H41R")
    say("PR0B3: watched %.1fs, ran home, uploaded (adaptation %d -> %d)" % (scan_s, adapt0, b.adaptation))

    # PR0B3 cancel: hacked (TURN) mid-upload -> never uploads, and it poisons the profile
    since = len(EVENTS)
    teleport(zone_spot(rack))
    frames(3)
    pr = run.minions.spawn("pr0b3", free_spot_near((p.pos.x + 70, p.pos.y), 12), "B")
    pr.scan_t = scan_s - 0.1
    wait_until(lambda: pr.probe_mode == "upload", 120, "second PR0B3 uploading")
    pr.pos = V(free_spot_near((p.pos.x + 26, p.pos.y), 12))
    pr.expose()
    pr.grace_t = 0.0
    hack_minion(pr, payload="TURN")
    check(pr.alive and pr.team == "player" and pr.probe_mode != "upload", "a TURNED PR0B3 cancels its upload")
    check(run.profiler.poisoned_t > 50, "a TURNED PR0B3 poisons the profile (%.0fs)" % run.profiler.poisoned_t)
    frames(60 * 8)
    check(not events("probe_uploaded", since), "the hacked PR0B3 never uploads")
    say("PR0B3: hacked mid-upload -> cancelled, profile poisoned %.0fs" % run.profiler.poisoned_t)
    quiet_zone()

    # F1R3W4LL shield: frontal shots are blocked, shots from behind strip it
    frames(1, gdev.tap(K.K_2))
    fw_ = run.minions.spawn("f1r3w4ll", free_spot_near((p.pos.x + 90, p.pos.y), 14), "B")
    fw_.speed = 0.0
    fw_.shield_dir = 4                              # facing west: at the player
    fw_.shield_turn_t = 99.0
    frames(2)
    f0 = fw_.firewall
    fire_at(fw_.pos, 40)
    frames(30)
    check(fw_.firewall == f0, "frontal pistol shots are blocked by the shield (%d -> %d)" % (f0, fw_.firewall))
    shot("31_firewall_block")
    fw_.shield_dir = 0                              # now facing away
    fire_at(fw_.pos, 40, stop=lambda: fw_.firewall < f0 or not fw_.alive)
    frames(20)
    check(fw_.firewall < f0 or not fw_.alive, "shots from behind the shield strip it (%d -> %d)" % (f0, fw_.firewall))
    say("F1R3W4LL shield: frontal shots blocked (%d), from behind %d -> %d" % (f0, f0, fw_.firewall))
    quiet_zone()

    # F1R3W4LL links: up to 3 allies within 160px; hacking it EXPOSES them
    w_ = run.minions.spawn("f1r3w4ll", free_spot_near((p.pos.x + 30, p.pos.y), 14), "B")
    allies = [run.minions.spawn("b1t", free_spot_near((p.pos.x + 30 + dx, p.pos.y + dy), 12), "B")
              for dx, dy in ((60, -40), (70, 30), (-10, 70))]
    for a in allies:
        a.speed = 0.0
    w_.speed = 0.0
    wait_until(lambda: len(w_.links) == 3, 90, "F1R3W4LL links 3 allies")
    a0 = allies[0]
    a0.firewall = a0.firewall_max - 1
    frames(int(w_.info["regen_s"] * 60) + 10)
    check(a0.firewall == a0.firewall_max, "a linked ally regenerates firewall")
    frames(10)
    shot("32_firewall_links")
    w_.expose()
    w_.grace_t = 0.0
    hack_minion(w_)
    frames(2)
    check(all(a.state == minions_mod.EXPOSED for a in allies), "hacking the F1R3W4LL EXPOSES its linked allies")
    say("F1R3W4LL links: 3 allies, regen, hacked -> all 3 EXPOSED")
    quiet_zone()

    # W0RM: a weapon kill splits it into 2 B1Ts; an EXTRACT does not
    wm = run.minions.spawn("w0rm", free_spot_near((p.pos.x + 80, p.pos.y), 12), "B")
    wm.speed = 0.0
    wm.expose()
    wm.grace_t = 0.0
    frames(1)
    fire_at(wm.pos, 30, stop=lambda: not wm.alive)
    frames(3)
    check(wm.dead_cause == "weapon", "W0RM shot dead (%s)" % wm.dead_cause)
    bits = [m for m in run.minions.live("B", kind="b1t")]
    check(len(bits) == 2, "a weapon-killed W0RM splits into 2 B1Ts (%d)" % len(bits))
    frames(10)
    shot("33_worm_split")
    quiet_zone()
    wm = spawn_exposed("w0rm", "B")
    hack_minion(wm)
    frames(5)
    check(not wm.alive and wm.dead_cause == "extract" and not run.minions.live("B", kind="b1t"),
          "an EXTRACTed W0RM does not split")
    say("W0RM: weapon kill -> 2 B1Ts; EXTRACT -> no split")
    # W0RM trail slows the player
    quiet_zone()
    teleport(free_spot_near((p.pos.x, p.pos.y), 30))
    run.minions.trail = [[p.pos.x + dx, p.pos.y + 3, 5.0] for dx in range(0, 200, 6)]
    x0 = p.pos.x
    frames(1, [gdev.key_event(K.K_d)])
    frames(29)
    frames(1, [gdev.key_event(K.K_d, False)])
    slow = p.pos.x - x0
    check(slow < 80, "W0RM slime slows the player (%.0f px in 0.5s)" % slow)
    say("W0RM slime: walked %.0f px in 0.5 s (normal ~90)" % slow)
    key_shard_fallback()
    g.end_run()
    g._set_state(game.MENU)


def test_review_fixes():
    """Regression checks for the review findings (fresh run, ends back at MENU)."""
    import menu
    # pure helpers
    check(hack.callout({"changes": {1: "B", 2: "B"}}, ["N", "3", "3", "D"]) == "3 = E, not B",
          "callout names a repeated misread once")
    counts = set(hack.target_count(hack.LEVELS[1], random.Random(i)) for i in range(40))
    check(counts == set([1, 2]), "L1 packets have 1-2 target words (%r)" % counts)
    check(hack.target_count(hack.LEVELS[1], random.Random(0), short=True) == 1, "first hack / EASY: 1 word")

    g.new_run(seed=11)
    run = g.run
    frames(15)
    rack = run.world.rack("A")
    spot = free_spot_near((rack.bars[0].spawn_exit[0], rack.bars[0].spawn_exit[1] + 300))
    teleport(spot)
    if rack.in_zone(run.player.pos):
        check("RACK A's ports" in run.objective_text(), "in-zone objective points at the ports")
    teleport(run.world.player_start)
    run.minions.clear()
    frames(10)

    # 1) correct LAST pick with under PICK_LOCK_S left is a success (1-word and 2-word packets)
    for diff, words in (("NORMAL", 1), ("HARD", 2)):
        g.settings["hack_difficulty"] = diff
        m = exposed_minion_near()
        protect_player()
        h = jack_in()
        check(len(h.targets) == words, "%s B1T packet has %d word(s): %r" % (diff, words, h.target_words()))
        while True:
            wait_until(lambda: h.input_ready(), 90, "decode input ready")
            last = h.ti == len(h.targets) - 1
            if last:
                h.time_left = 0.10
            frames(1, gdev.tap(K.K_1 + h.correct_slot()))
            if last:
                break
        wait_until(lambda: h.phase != hack.DECODE, 30, "decode ends after the last pick")
        check(h.result["outcome"] == "success" and not m.hardened,
              "last pick with 0.10s left -> success (got %r)" % h.result["outcome"])
        res = answer_hack(h)
        frames(20)
        say("last pick with 0.10s left (%s, %d word%s): %s" % (diff, words, "s" * (words - 1), res["outcome"]))
    g.settings["hack_difficulty"] = "NORMAL"

    # 2) hasty window counts from when input unlocks: wrong pick 0.45s after unlock is hasty
    m = exposed_minion_near()
    protect_player()
    h = jack_in()
    wait_until(lambda: h.input_ready(), 60, "decode input ready")
    frames(27)
    frames(1, gdev.tap(K.K_1 + h.wrong_slot()))
    check(h.trace == hack.TRACE_HASTY, "wrong pick 0.45s after unlock costs %d (got %.0f)" % (
        hack.TRACE_HASTY, h.trace))
    frames(1, gdev.tap(K.K_ESCAPE))
    wait_until(lambda: g.state == game.PLAY, 60, "abort")
    say("hasty window from unlock: wrong pick at 0.45s -> TRACE %.0f" % h.trace)
    frames(20)

    # 3) OVERLOAD-chain getaway slows the world, not the player
    a = exposed_minion_near((30, 0))
    run.minions.spawn("b1t", free_spot_near((a.pos.x + 60, a.pos.y), 12))
    protect_player()
    h = jack_in()
    answer_hack(h, payload="OVERLOAD")
    frames(1)
    check(run.hack is not None, "OVERLOAD chained")
    answer_hack(run.hack, payload="EXTRACT")
    frames(int(game.INPUT_LOCK_S * 60) + 2)
    check(run.getaway > 0.5, "getaway running (%.2f)" % run.getaway)
    run.minions.clear()
    x0 = run.player.pos.x
    frames(1, [gdev.key_event(K.K_d)])
    frames(29)
    scale = run.time_scale
    frames(1, [gdev.key_event(K.K_d, False)])
    moved = run.player.pos.x - x0
    check(scale == game.WORLD_SLOWMO and moved > 70, "getaway: world %.2f, player moved %.1f px in 0.5s" % (
        scale, moved))
    say("getaway: world at %.2f, player walked %.0f px in 0.5 s" % (scale, moved))
    frames(int(run.getaway * 60) + 5)

    # 4) the terminal never covers the target or the player, 5) focus loss mid-hack pauses
    m = exposed_minion_near((0, -40))
    protect_player()
    h = jack_in()
    cam = run.camera
    for i in range(40):
        protect_player()
        frames(1)
        if h.age < 0.25:
            continue                      # terminal still unfolding, camera still settling
        for who in (m, run.player):
            pt = g._zoomed(cam.world_to_internal(who.pos))
            check(not h.window.colliderect(hack._body_rect(pt[0], pt[1], cam.zoom)),
                  "hack window %r covers %s at %r" % (h.window, getattr(who, "label", "player"), pt))
    shot("19_hack_layout")
    aborts, trace = run.stats["aborts"], h.trace
    frames(1, [pygame.event.Event(pygame.WINDOWFOCUSLOST)])
    check(g.state == game.PAUSE and run.hack is None, "focus lost in HACK -> PAUSE")
    check(h.result["outcome"] == "abort" and run.stats["aborts"] == aborts + 1 and not m.hardened,
          "focus loss aborts cleanly: %r hardened=%s" % (h.result["outcome"], m.hardened))
    check(h.trace == trace, "no TRACE for a focus abort")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PLAY, "resume after focus pause")
    say("hack window clear of target and player; focus loss mid-hack -> clean abort + PAUSE")

    # 6) the intel log has a clickable BACK
    frames(15)
    frames(1, gdev.tap(K.K_TAB))
    check(g.state == game.INTEL and len(g.intel_screen.entries()) > 0, "intel log open with entries")
    frames(1, gdev.click(g.intel_screen.back_rect().center))
    check(g.state == game.PLAY, "intel log BACK button returns to PLAY")
    say("intel log BACK button OK")
    check(menu.game_start, "run still active")
    g.end_run()
    g._set_state(game.MENU)


# ------------------------------------------------------------------ chaos

CHAOS_KEYS = [K.K_w, K.K_a, K.K_s, K.K_d, K.K_UP, K.K_DOWN, K.K_LEFT, K.K_RIGHT, K.K_SPACE,
              K.K_RETURN, K.K_ESCAPE, K.K_TAB, K.K_f, K.K_r, K.K_1, K.K_2, K.K_3, K.K_4,
              K.K_z, K.K_x, K.K_c, K.K_q, K.K_e, K.K_BACKSPACE]


# ------------------------------------------------------------------ fixer regressions

def _close_scenes():
    for _ in range(6):
        if g.state != game.SCENE:
            return
        close_scene()


def _zone_spot_outside_trigger(rack, trigger):
    """Free floor in the rack's Influence Zone but outside its fight trigger."""
    c = pygame.math.Vector2(rack.shell.center)
    trig = rack.shell.inflate(2 * trigger, 2 * trigger)
    for dist in range(int(min(rack.shell.w, rack.shell.h) / 2 + trigger + 20), 1200, 30):
        for i in range(16):
            q = c + pygame.math.Vector2(dist, 0).rotate(i * 22.5)
            if not rack.in_zone(q) or trig.collidepoint(q):
                continue
            rect = pygame.Rect(0, 0, 14, 14)
            rect.center = (int(q.x), int(q.y))
            if g.run.world.rect_free(rect):
                return rect.center
    raise SmokeFail("no free spot in zone %s outside its trigger" % rack.id)


def test_fixer_regressions():
    """Regression checks for the fixer pass (one fresh run per group)."""
    import bosses as bosses_mod
    import hud as hud_mod
    import progression as prog_mod
    import shop as shop_mod
    import player as player_mod
    GUARD[0] = True
    check(settings_mod.DEFAULTS["hack_difficulty"] == "EASY", "hacks default to EASY (L1)")
    g.settings["hack_difficulty"] = "ADAPTIVE"          # these regressions run with the Breach Tuner on

    # 1) focus loss while a Key hack is already won: the Key save is written before PAUSE
    g.new_run(seed=7, handle="FIXER")
    run = g.run
    frames(20)
    g.save_game("baseline")
    p = run.player
    m = run.minions.spawn("b1t", free_spot_near((p.pos.x + 24, p.pos.y), 12), "A")
    m.key_source = "prologue"
    m.expose()
    m.grace_t = 0.0
    frames(2)
    g._start_hack(m)
    h = run.hack
    h.dev_complete()
    wait_until(lambda: h.phase == hack.PAYLOAD and h.input_ready(), 90, "Key hack reaches PAYLOAD")
    frames(1, [pygame.event.Event(pygame.WINDOWFOCUSLOST, {})])
    check(g.state == game.PAUSE and run.progression.key_count == 1, "focus loss in PAYLOAD -> PAUSE, Key granted")
    check(not [r for r in run.requests if r[0] == "save"], "no save left pending under PAUSE")
    check("0m 00s" in g.pause_menu.info, "pause shows the fresh save: %r" % g.pause_menu.info)
    g._do("quit_to_menu")
    check(g.load_save() and g.run.progression.key_count == 1, "CONTINUE after QUIT keeps the Key")
    say("focus loss in PAYLOAD: Key saved before PAUSE, QUIT -> CONTINUE keeps it")

    # 2) Breach Tuner moves the level in an ADAPTIVE run
    run = g.run
    t = run.tuner
    lvl0 = t.level_for("p1ng")
    for _ in range(4):
        t.record({"outcome": "success"})
    check(t.adaptive() and t.level_for("p1ng") == lvl0 + 2, "tuner staircase: L%d -> L%d" % (lvl0, t.level_for("p1ng")))
    t.record({"outcome": "backtrace"})
    check(t.level_for("p1ng") == lvl0 + 1, "a BACKTRACE steps the level down")
    say("Breach Tuner (ADAPTIVE): L%d -> L%d after 4 wins, -1 after a BACKTRACE" % (lvl0, lvl0 + 2))

    # 3) F1R3W4LL: two-part words on every hack; carriers get their rack Daemon's hooks
    p = run.player
    fw = run.minions.spawn("f1r3w4ll", free_spot_near((p.pos.x + 30, p.pos.y), 14), "A")
    check(run.minion_hack_ctx(fw).get("two_part"), "a plain F1R3W4LL's packet is two-part")
    hs = hack.HackSession(fw, g.settings, run.codex, run.intel, run.rng, run.minion_hack_ctx(fw))
    vmod = run.bosses.bosses["v3rd1c7"].script
    check(vmod.picks_left(hs) == len(hs.picks), "verdict counts the session's picks (%d)" % len(hs.picks))
    rack_d = run.world.rack("D")
    carrier = run.minions.spawn("f1r3w4ll", rack_d.bars[0].spawn_exit, "D")
    carrier.key_source = "fw:D1"
    dl = run.bosses.bosses["d14l3c7"]
    check(dl.speaks_for(carrier) and "dialect" in run.bosses.hack_context(carrier),
          "a Key carrier from D14|_3C7's ports gets its dialect")
    run.minions.clear()
    say("F1R3W4LL two-part words on every hack (%d picks); carriers speak their Daemon's dialect" % len(hs.picks))

    # 4) Trace Damper only softens wrong picks
    m = exposed_minion_near()
    ctx = dict(run.minion_hack_ctx(m), trace_mult=0.75)
    hs = hack.HackSession(m, g.settings, run.codex, run.intel, run.rng, ctx)
    hs.on_player_damaged()
    check(abs(hs.trace - hack.TRACE_HIT) < 0.01, "a hit while jacked in is full TRACE with Trace Damper (%.1f)" % hs.trace)
    run.minions.clear()

    # 5) Patch Notes counters lapse 2 patches after they were applied, even if the habit stays
    pn = run.profiler.patches
    rb = run.bosses.bosses["r3c4ll"]
    pn.active = {}
    pn.habits = lambda: [(1.0, "BIT_SPEED", "Y0U K173 F4R", None)]
    try:
        pn._patch(rb, False)
        check("BIT_SPEED" in pn.active and pn.active["BIT_SPEED"]["patches_left"] == 2, "counter applied")
        pn._patch(rb, False)
        check(pn.active.get("BIT_SPEED", {}).get("patches_left") == 1, "a refresh keeps the expiry")
        pn._patch(rb, False)
        check("BIT_SPEED" not in pn.active, "the counter lapses after 2 patches")
    finally:
        del pn.habits
        pn.active = {}
    say("Patch Notes: a steady habit's counter still lapses after 2 patches")

    # 6) |-|34D1NG leads by the whole time to impact: running straight is hit, stopping is not
    h34 = g.start_boss("h34d1ng")
    frames(2)
    run.attacks.clear()
    run.director.state = "BUILD"                     # no dodge-predictor nudge for this check
    p.vel = pygame.math.Vector2(player_mod.SPEED, 0)
    a = h34.fire({"id": "packet_rain", "aim": "lead"})
    total = a.telegraph_total
    want = p.pos + pygame.math.Vector2(player_mod.SPEED * total, 0)
    check(total >= 1.9 and (a.centre - want).length() < 2, "lead circle %.0f px ahead (want %.0f, %.1fs)"
          % ((a.centre - p.pos).length(), player_mod.SPEED * total, total))
    check(not a.lands_on(p.pos), "stopping where you were dodges it")
    p.vel = pygame.math.Vector2(0, 0)
    a2 = h34.fire({"id": "packet_rain", "aim": "lead"})
    check(a2.lands_on(p.pos), "standing still (or rooted) is hit")
    run.attacks.clear()
    run.bosses.active.force_dormant()
    run.bosses.active = None
    say("|-|34D1NG lead: %.0f px ahead at walking speed (%.1fs to impact); standing still is hit"
        % ((a.centre - p.pos).length(), total))

    # 7) EXPOSURE lights the nearest core, adds the walk, frames it under the HUD; leaving / saving
    g.new_run(seed=8, handle="FIXER")
    run = g.run
    g.grant_keys(1)
    frames(5)
    b = g.start_boss("r3c4ll")
    rk = b.rack
    far = b.cores[2]
    face = pygame.math.Vector2(bosses_mod.face_lane(rk, rk.bars[far["bar"]])[2].center)
    out = face - pygame.math.Vector2(rk.shell.center)
    out = out.normalize() if out.length_squared() else pygame.math.Vector2(0, 1)
    teleport(free_spot_near(face + out * 60, 12))
    b.firewall = 0
    frames(2)
    check(b.state == "EXPOSURE" and b.exposed_core == 2, "the core nearest the player lights (got %r)" % b.exposed_core)
    check(b.window_t >= float(b.d["exposure_window_s"]) - 0.1, "a full window to shoot")
    frames(40)
    fy = run.camera.world_to_internal(face)[1]
    check(fy >= hud_mod.SAFE_RECT.top + 20, "the exposed face is below the HUD band (row %d)" % fy)
    shot("80_exposure_framed")
    half = b._half_firewall()
    check(run.bosses.to_save()["firewall"]["r3c4ll"] == half, "a save mid-EXPOSURE stores half the firewall")
    g.teleport_to_zone("N1")
    frames(3)
    check(b.state == "DORMANT" and b.firewall == half, "leaving mid-EXPOSURE: window lost, firewall at half")
    d = run.bosses.to_save()
    d["firewall"]["r3c4ll"] = 0
    run.bosses.from_save(d)
    check(b.firewall == half, "an old save at firewall 0 loads at half (no free EXPOSURE)")
    say("EXPOSURE: nearest core (2), full window, face framed at row %d; save/leave -> firewall %d" % (fy, half))

    # 8) objective arrows in a Daemon's zone, the carrier countdown, the zone label, boss bar text
    b.force_dormant()
    run.bosses.active = None
    teleport(_zone_spot_outside_trigger(rk, float(b.d.get("fight_trigger_px", 380))))
    frames(3)
    pt = run.progression.objective_point()
    check(b.state == "ZONE" and pt is not None and pt[1] == b.leet_name, "in the zone: arrow to the rack (%r)" % (pt,))
    for bid in ("r3c4ll", "cr055h41r"):
        run.bosses.bosses[bid].state = "CLEARED"     # shortcut: both Act I Daemons down
    frames(3)
    txt = run.objective_text()
    pt = run.progression.objective_point()
    check("compiles at RACK A's ports (" in txt and pt is not None and pt[1] == "P0RTS",
          "carrier wait: countdown and the ports (%r, %r)" % (txt, pt))
    label, size, room = hud_mod.zone_label_fit(gdev.INTERNAL_SIZE[0], run.progression.zone_label_forms())
    check(gdev.text_size(label, size)[0] <= room, "zone label fits beside the name bar: %r" % label)
    check("_" not in hud_mod.STATE_LABELS["BREACH_READY"], "boss bar state text is leet, no underscore")
    g.grant_keys(7)
    run.bosses.bosses["v3rd1c7"].state = "CLEARED"
    g.teleport_to_zone("E")
    frames(3)
    check(run.progression.place_act() == 2 and run.progression.zone_label().startswith("ACT II"),
          "at 8 Keys, Rack E still reads ACT II: %r" % run.progression.zone_label())
    label, size, room = hud_mod.zone_label_fit(gdev.INTERNAL_SIZE[0], run.progression.zone_label_forms())
    check(gdev.text_size(label, size)[0] <= room, "long zone label falls back: %r" % label)
    shot("81_zone_label")
    say("zone arrow -> %s, carrier countdown, label %r fits" % (b.leet_name, label))

    # 9) the Clean Node shop: Bits upgrades, shard tools (Remote Jack), saved
    g.new_run(seed=9, handle="FIXER")
    run = g.run
    frames(20)
    run.bits = 400
    run.progression.shards = 5
    term = [i for i in run.world.interactables if i.kind == "node_terminal" and i.node_id == "N1"][0]
    teleport(free_spot_near(term.pos, 12))
    frames(20)
    check(run.interact_hint is not None and "SH0P" in run.interact_hint[1], "node hint offers the shop: %r"
          % (run.interact_hint,))
    frames(1, gdev.tap(K.K_f))
    frames(2)
    check(g.state == game.SHOP, "F at a Clean Node opens the SHOP")
    shop_scr = g.shop_screen
    ids = [r["id"] if r else None for r in shop_scr.rows]
    shop_scr.selected = 0
    for _ in range(ids.index("MAG_PLUS")):
        frames(1, gdev.tap(K.K_s))
    frames(1, gdev.tap(K.K_RETURN))
    check("MAG_PLUS" in run.player.upgrades and run.player.mag == player_mod.PISTOL_MAG + player_mod.MAG_PLUS and run.bits == 400 - 60,
          "bought +2 MAGAZINE (mag %d, bits %d)" % (run.player.mag, run.bits))
    frames(1, gdev.click(shop_scr.row_rects()[ids.index("REMOTE_JACK")].center))
    check(run.progression.has_exploit("REMOTE_JACK") and run.progression.shards == 0, "bought REMOTE JACK for 5 shards")
    frames(1, gdev.click(shop_scr.row_rects()[ids.index("SPLIT_NOCK")].center))
    frames(1, gdev.click(shop_scr.row_rects()[ids.index("ARC_SHOT")].center))
    frames(1, gdev.click(shop_scr.row_rects()[ids.index("RIPOSTE")].center))
    frames(1, gdev.click(shop_scr.row_rects()[ids.index("RICOCHET")].center))
    check(not shop_mod.buy(run, "LONG_EDGE")[0] and run.bits == 400 - 60 - 90 - 60 - 90 - 90,
          "no Bits left for LONG EDGE (bits %d)" % run.bits)
    frames(10)
    shot("82_shop")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.PLAY, "Esc leaves the shop")
    p = run.player
    p.weapon_index = 0
    p.charge_t = player_mod.BOW_FULL
    n0 = len(p.projectiles)
    p._fire_arrow()
    arrows = p.projectiles[n0:]
    check(len(arrows) == 3 and all(x.arc for x in arrows) and arrows[0].core_mult == player_mod.CORE_ARROW_MULT,
          "Split Nock: 3 arcing arrows")

    class _Wall(object):
        def projectile_hit(self, rect):
            return ("wall", None, None) if rect.left < 100 else None
    bl = player_mod.Projectile((110, 50), (-520, 0), 1, "bullet", "player", 4, 1.0)
    bl.bounces = 1
    bl.update(0.05, _Wall())
    check(bl.alive and bl.vel.x > 0, "Ricochet: a bullet bounces off a wall")
    p.weapon_index = 2
    p.parry_t = player_mod.PARRY_WINDOW
    p.invuln = 0.0                                   # (GUARD keeps i-frames between steps)
    n0 = len(p.projectiles)
    check(not p.take_damage(1, (p.pos.x + 20, p.pos.y)), "the parry blocks")
    check(len(p.projectiles) == n0 + 1 and p.projectiles[-1].damage == player_mod.RIPOSTE_DAMAGE,
          "Riposte answers the parry with a cut")
    p.weapon_index = 1
    m = run.minions.spawn("b1t", free_spot_near((p.pos.x + 200, p.pos.y), 12), "A")
    m.expose()
    m.grace_t = 0.0
    m.pinned = True
    frames(1)
    tgt, prov = g._find_jack()
    check(tgt is m and prov == "minion", "Remote Jack: a pinned minion 200 px away is a jack target")
    run.minions.clear()
    check(g.save_game("test") and g.load_save(), "save + CONTINUE")
    check("MAG_PLUS" in g.run.player.upgrades and g.run.player.mag == player_mod.PISTOL_MAG + player_mod.MAG_PLUS and
          g.run.progression.has_exploit("REMOTE_JACK"), "upgrades and bought tools survive CONTINUE")
    say("shop: +2 MAG, Split Nock, Arc Shot, Riposte, Ricochet for Bits, Remote Jack for 5 shards; saved")

    # 10) the Mirror: a clean disconnect keeps the exposure window
    g.new_run(seed=10, handle="FIXER")
    run = g.run
    frames(5)
    g.grant_keys(12)
    g.enter_deep_core()
    fin = run.finale
    for _ in range(600):
        frames(1)
        if g.state == game.SCENE:
            _close_scenes()
        if fin.state == "FIGHT":
            break
    check(fin.state == "FIGHT", "Mirror fight started")
    fin.fw = 0
    frames(2)
    mr = fin.mirror
    check(mr.mode == "exposed", "Mirror exposed")
    teleport(g._free_spot((mr.pos.x - 20, mr.pos.y)))
    h = jack_in()
    left = fin.expose_t
    wait_until(lambda: h.input_ready(), 90, "counter-hack input ready")
    frames(1, gdev.tap(K.K_ESCAPE))
    wait_until(lambda: run.hack is None, 90, "counter-hack closes after Esc")
    check(run.hack is None and mr.mode == "exposed" and abs(fin.expose_t - left) < 0.5,
          "Esc on a counter-hack keeps the window (%.1fs, mode %s)" % (fin.expose_t, mr.mode))
    check(any("still open" in t[0] for t in run.toasts), "the toast says the window is still open")
    say("Mirror counter-hack: Esc keeps the exposure window (%.1fs left)" % fin.expose_t)

    # 11) far EXPOSED minions draw a small ring, not the full jack range
    g.new_run(seed=11, handle="FIXER")
    run = g.run
    frames(5)
    p = run.player
    near = exposed_minion_near((20, 0))
    far_px = minions_mod.JACK_RING_PX * minions_mod.JACK_RING_NEAR + 40
    farm = run.minions.spawn("b1t", free_spot_near((p.pos.x + far_px, p.pos.y), 12), "A")
    farm.expose()
    farm.grace_t = 0.0
    frames(2)
    check(near.ring_full and not farm.ring_full, "only nearby EXPOSED minions draw the full jack ring")
    run.minions.clear()
    GUARD[0] = False
    say("jack rings: full range only near the player")


# Runs in a child process with PIXEL_APOCALYPSE_DATA_DIR set (fresh gdev / game / settings modules).
DATA_DIR_CHILD = """
import json, os, sys
sys.path.insert(0, %r)
import gdev, game, settings as settings_mod
g = game.Game(headless_ok=True)
g.settings["show_fps"] = True
g._setting_changed("show_fps")
g.new_run(seed=5, handle="DATADIR")
for _ in range(3):
    g.step([])
saved = g.save_game()
print(json.dumps({"data_dir": gdev.DATA_DIR, "no_bytecode": sys.dont_write_bytecode, "saved": saved,
                  "has_save": g.has_save(), "reload": settings_mod.load()["show_fps"],
                  "save_file": gdev.data_path(game.SAVE_FILE),
                  "settings_file": gdev.data_path(settings_mod.SETTINGS_FILE)}))
"""


def _code_snapshot():
    snap = {}
    for root, _dirs, files in os.walk(CODE):
        for f in files:
            full = os.path.join(root, f)
            try:
                st = os.stat(full)
            except OSError:
                continue
            snap[full] = (st.st_size, st.st_mtime)
    return snap


def test_data_dir():
    """PIXEL_APOCALYPSE_DATA_DIR (set by the macOS app's launcher): every runtime write lands
    under it, reads come back from it, and the code folder is never written."""
    check(gdev.DATA_DIR == gdev.BASE_DIR or os.environ.get(gdev.DATA_DIR_ENV), "DATA_DIR defaults to the code folder")
    check(gdev.data_path(game.SAVE_FILE) == game.SAVE_FILE, "an absolute SAVE_FILE override is used as it is")
    base = tempfile.mkdtemp(prefix="pa_data_")
    data = os.path.join(base, "Application Support", "Pixel Apocalypse")    # spaces, not created yet
    env = dict(os.environ)
    env.update({gdev.DATA_DIR_ENV: data, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
                "PYGAME_HIDE_SUPPORT_PROMPT": "1"})
    env.pop("PYTHONDONTWRITEBYTECODE", None)        # the game itself must keep the bundle clean
    before = _code_snapshot()
    out = subprocess.run([sys.executable, "-c", DATA_DIR_CHILD % CODE], cwd=base, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=180)
    check(out.returncode == 0, "data dir child exited %d: %s" % (out.returncode, out.stderr[-800:]))
    r = json.loads(out.stdout.strip().splitlines()[-1])
    check(r["data_dir"] == data and r["no_bytecode"], "child sees DATA_DIR %r, no bytecode" % r["data_dir"])
    check(r["saved"] and r["has_save"] and r["reload"] is True, "save + settings written and read back: %r" % r)
    check(r["save_file"] == os.path.join(data, "save", "run.json") and os.path.isfile(r["save_file"]),
          "run save under the data dir: %s" % r["save_file"])
    check(r["settings_file"] == os.path.join(data, "save", "settings.json") and os.path.isfile(r["settings_file"]),
          "settings under the data dir: %s" % r["settings_file"])
    # the real entry point, started from another folder, quits cleanly after N frames
    out = subprocess.run([sys.executable, os.path.join(CODE, "init.py"), "--dev", "--frames", "30"], cwd=base,
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                         timeout=120)
    check(out.returncode == 0, "init.py with a data dir exited %d: %s" % (out.returncode, out.stderr[-800:]))
    after = _code_snapshot()
    added = sorted(set(after) - set(before))
    changed = sorted(f for f in before if f in after and after[f] != before[f])
    check(not added and not changed, "code folder untouched with a data dir (new %s, changed %s)"
          % (added[:5], changed[:5]))
    say("data dir: run + settings saved under %s, code folder untouched (%d files)" % (data, len(after)))


def random_event(rng):
    r = rng.random()
    if r < 0.45:
        return gdev.key_event(rng.choice(CHAOS_KEYS), rng.random() < 0.6)
    pos = (rng.randint(-20, 980), rng.randint(-20, 520))
    if r < 0.75:
        return gdev.mouse_move(pos)
    if r < 0.93:
        return gdev.mouse_button(pos, rng.choice((1, 1, 3, 2)), rng.random() < 0.6)
    return pygame.event.Event(K.MOUSEWHEEL, {"x": 0, "y": rng.choice((-1, 1)), "flipped": False})


def chaos(n_frames=4000, seed=1234):
    rng = random.Random(seed)
    seen = set()
    for i in range(n_frames):
        evs = [random_event(rng) for _ in range(rng.choice((0, 0, 1, 1, 2, 4)))]
        if rng.random() < 0.002:
            evs.append(pygame.event.Event(K.WINDOWFOCUSLOST, {}))
        g.step(evs)
        if not g.running:                      # QUIT from the menu is legal: keep going
            g.running = True
        seen.add(g.state)
        # steer into the states random input rarely reaches on its own
        if i % 500 == 0 and g.state != game.PLAY:
            g.new_run(seed=i, handle="CHAOS")
        if g.state == game.PLAY and g.run is not None and i % 97 == 0:
            p = g.run.player
            if not p.dead:
                exposed_minion_near((24, 0))
                g.step([gdev.key_event(K.K_f, True)])
                for _ in range(30):
                    g.step([])
                g.step([gdev.key_event(K.K_f, False)])
                seen.add(g.state)
        if g.state == game.PLAY and g.run is not None and i % 1500 == 1000:
            g.finish_finale()                      # jump to the ENDING
        if g.state == game.PLAY and g.run is not None and i % 700 == 300:
            g.grant_keys(1, quiet=False)
            if g.run is not None and g.run.progression.key_count < 12:
                g.start_boss("r3c4ll")
                g.finish_breach()                  # -> SCENE (Core Dump), if the Daemon was not cleared
        if g.state == game.PLAY and g.run is not None and i % 1300 == 650:
            r = g.run.world.rack("A")             # None inside the Deep Core
            if r is not None:
                teleport(free_spot_near((r.bars[0].spawn_exit[0], r.bars[0].spawn_exit[1] + 120)))
    g.keys_down.clear()
    g.mouse_buttons.clear()
    return seen


# ------------------------------------------------------------------ perf

def measure(n):
    """Average ms per g.step (update + draw) over n frames, firing now and then."""
    run = g.run
    t_total = 0.0
    counts = []
    for i in range(n):
        run.player.invuln = 99.0
        run.player.hp = run.player.max_hp
        ev = [aim_at((run.player.pos.x + 100, run.player.pos.y))]
        if i % 12 == 0:
            ev.append(gdev.mouse_button(g.mouse_pos, 1, True))
        elif i % 12 == 1:
            ev.append(gdev.mouse_button(g.mouse_pos, 1, False))
        t0 = time.perf_counter()
        g.step(ev)
        t_total += time.perf_counter() - t0
        counts.append(run.minions.live_count())
    return 1000.0 * t_total / n, min(counts), max(counts)


def crowd(spot, n, rack_id, kinds, tags=None, seed=3):
    run = g.run
    rng = random.Random(seed)
    while run.minions.live_count() < n:
        ang = rng.uniform(0, 6.283)
        dist = rng.uniform(60, 420)
        pos = (spot[0] + dist * pygame.math.Vector2(1, 0).rotate_rad(ang).x,
               spot[1] + dist * pygame.math.Vector2(1, 0).rotate_rad(ang).y)
        try:
            pos = free_spot_near(pos, 12)
        except SmokeFail:
            continue
        m = run.minions.spawn(rng.choice(kinds), pos, rack_id)
        for k, v in (tags or {}).items():
            setattr(m, k, v)


def perf(n_minions=40, n_frames=300):
    """Baseline (empty PLAY frame, same machine, same moment), then PLAY / HACK with a crowd of
    ~40, then a Daemon fight (D14|_3C7: waves, phase attacks, every archetype) with ~40."""
    out = {}
    g.new_run(seed=7)
    run = g.run
    run.director.force_relax(999)
    run.progression.collected.add("prologue")
    frames(5)
    out["BASE"] = measure(120)
    rack = run.world.rack("A")
    spot = free_spot_near((rack.bars[1].spawn_exit[0], rack.bars[1].spawn_exit[1] + 180))
    teleport(spot)
    crowd(spot, n_minions, "A", ["b1t"] * 9 + ["p1ng"])
    frames(5)
    out["PLAY"] = measure(n_frames)
    shot("17_perf_crowd")
    # the same crowd, during a hack (greyscale + terminal on top)
    exposed_minion_near((20, 0))
    run.player.invuln = 99.0
    g.step([gdev.key_event(K.K_f, True)])
    for _ in range(30):
        run.player.invuln = 99.0
        g.step([])
    g.step([gdev.key_event(K.K_f, False)])
    if g.state == game.HACK:
        run.hack.time_left = 1e6               # keep the window open while measuring
        out["HACK"] = measure(120)
        shot("18_perf_hack")
        run.hack.abort("perf")
        frames(5)
    g.end_run()
    g._set_state(game.MENU)
    # a boss fight: SIEGE with its waves and phase attacks, every archetype, ~40 minions
    g.new_run(seed=8)
    run = g.run
    frames(3)
    b = g.start_boss("d14l3c7")
    frames(3)
    spot = (run.player.pos.x, run.player.pos.y)
    crowd(spot, n_minions, "D", ["b1t", "b1t", "p1ng", "pr0b3", "f1r3w4ll", "w0rm"], {"boss_id": "d14l3c7"})
    frames(5)
    check(b.state == "SIEGE", "perf boss fight running")
    out["BOSS"] = measure(n_frames)
    out["BOSS_ATTACKS"] = len(run.attacks.active)
    shot("19_perf_boss")
    g.end_run()
    g._set_state(game.MENU)
    return out


# ------------------------------------------------------------------ main

def main():
    global g
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    print("Pixel Apocalypse smoke test (pygame %s, python %s)" % (
        pygame.version.ver, sys.version.split()[0]))
    t0 = time.time()
    g = game.Game(headless_ok=True)
    steps = [("menus", test_menus), ("move/shoot/pause", test_move_shoot_pause),
             ("rack A fight + first hack", test_rack_a_real_fight),
             ("kill + death", test_kill_and_death),
             ("wrong pick + aborts", test_wrong_pick_and_aborts), ("payloads", test_payloads),
             ("full run: prologue -> Acts I-III -> Deep Core -> PATCH", test_full_run),
             ("second run: PURGE", test_purge_run), ("minion archetypes", test_minion_types),
             ("review fixes", test_review_fixes), ("fixer regressions", test_fixer_regressions),
             ("data dir (macOS app)", test_data_dir)]
    only = [x for x in os.environ.get("SMOKE_ONLY", "").split(",") if x]
    try:
        for name, fn in steps:
            if only and fn.__name__ not in only and fn is not test_menus:
                continue
            print("[%s]" % name)
            if only and g.run is None and fn.__name__ in ("test_full_run",):
                g.new_run(handle="DHRUVA")
            fn()
        if only and "chaos" not in only:
            print("\nSELECTED CHECKS PASSED in %.1fs (%d frames)" % (time.time() - t0, g.frame))
            return 0
        print("[chaos]")
        seen = chaos()
        say("chaos: 4000 frames of random input, states visited: %s" % ", ".join(sorted(seen)))
        check({game.MENU, game.PLAY, game.HACK, game.SCENE, game.ENDING}.issubset(seen),
              "chaos reached MENU, PLAY, HACK, SCENE and ENDING")
        print("[perf]")
        res = perf()
        base = res["BASE"][0]
        say("perf BASE : %.2f ms/frame (empty PLAY frame: this machine, right now)" % base)
        for k in ("PLAY", "HACK", "BOSS"):
            if k in res:
                say("perf %-4s : %.2f ms/frame (update+draw), %d-%d minions alive, x%.1f the baseline" % (
                    (k,) + res[k] + (res[k][0] / max(0.01, base),)))
        loaded = base > 2.5                    # a busy machine: judge by the ratio, not the clock
        limit = max(16.0, base * 6.0) if loaded else 16.0
        say("perf budget: %.1f ms/frame (%s)" % (limit, "machine loaded: 6x baseline" if loaded else "16 ms frame"))
        for k in ("PLAY", "HACK", "BOSS"):
            if k in res:
                check(res[k][0] < limit, "%s frame with ~40 minions: %.2f ms over %.1f ms" % (k, res[k][0], limit))
        if not loaded:
            check(res["PLAY"][0] < 10.0, "PLAY frame with ~40 minions under the 10 ms minion budget")
    except Exception as ex:
        print("\nSMOKE TEST FAILED: %s" % ex)
        traceback.print_exc()
        try:
            shot("FAIL")
            print("frame saved to " + shots[-1])
        except Exception:
            pass
        return 1
    print("\nALL CHECKS PASSED in %.1fs (%d frames)" % (time.time() - t0, g.frame))
    print("screenshots:")
    for p in shots:
        print("  " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
