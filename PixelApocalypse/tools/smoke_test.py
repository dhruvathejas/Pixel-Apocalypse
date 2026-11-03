"""Headless smoke test: drives a whole session through Game.step() with fake events.

Run from the code folder:
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy /usr/bin/python3 tools/smoke_test.py

What it does (and asserts along the way):
  menus by keyboard and mouse -> SETTINGS change -> HOW TO PLAY -> INTEL LOG (empty)
  -> PLAY -> move, shoot, reload -> PAUSE / resume -> Rack A: real B1Ts spawn,
  pistol strips one, jack in with F, decode (keys + mouse), payload
  -> wrong pick, abort, TURN, OVERLOAD chain, weapon kill, death + respawn
  -> 5 hacks -> Key -> VICTORY (input lock) -> MAIN MENU -> INTEL LOG
  -> review regressions (last pick at the buzzer, hasty window, getaway speed, window
     placement, focus loss mid-hack, intel log BACK, L1 word counts, callout) -> chaos -> perf.

Screenshots go to $SMOKE_OUT (default: the session scratchpad folder below).
Settings are written to a temporary file, never to the real save/settings.json.
Exit code 0 = everything passed.
"""
import os
import random
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

OUT = os.environ.get("SMOKE_OUT", "/private/tmp/claude-503/-Users-dhruva-Documents-workspace-"
                     "Pixel-Apocalypse/658bf41c-43b0-433f-8da0-da93713e023e/scratchpad/proto")
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


def frames(n=1, events=None):
    g.step(list(events or []))
    for _ in range(n - 1):
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

    # PLAY by mouse click
    frames(1, gdev.click(rects[0].center))
    check(g.state == game.PLAY and g.run is not None, "click PLAY starts a run")
    import menu
    check(menu.game_start, "menu.game_start is True while a run is active")
    say("PLAY started by mouse click")


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
        target = run.minions.nearest_exposed(p.pos, 200)
        if target is not None:
            break
        live = [m for m in run.minions.minions if m.alive and m.team == "enemy" and
                m.state in (minions_mod.HUNTING, minions_mod.HARDENED)]
        if not live:
            frames(1)
            continue
        live.sort(key=lambda m: (m.pos - p.pos).length_squared())
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
    if not run.complete:
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

    # death -> SIGNAL LOST -> respawn at Clean Node 1, half Bits, intel kept
    bits = run.bits
    intel = run.intel.count()
    for _ in range(p.max_hp + 2):
        p.invuln = 0.0
        p.take_damage(1, (p.pos.x + 10, p.pos.y))
        frames(1)
    check(p.dead, "5 hits kill the player")
    frames(30)
    shot("13_death")
    wait_until(lambda: not p.dead, int(game.DEATH_S * 60) + 30, "respawn after death")
    check(run.stats["deaths"] == 1, "death counted")
    check(run.bits == bits - bits // 2, "half the Bits lost (%d -> %d)" % (bits, run.bits))
    check(run.intel.count() == intel, "intel kept through death")
    check(p.hp == p.max_hp, "full HP after respawn")
    n1 = run.world.node("N1")
    check((p.pos - pygame.math.Vector2(n1.rect.center)).length() < 600, "respawned at Clean Node 1")
    say("death + respawn OK (bits %d -> %d)" % (bits, run.bits))
    frames(30)


def test_finish_to_victory():
    run = g.run
    n = 0
    check(run is not None, "run still active before the last hacks")
    while len(run.hacked_uids) < game.OBJECTIVE_HACKS:
        n += 1
        check(n < 15, "too many hacks without reaching the objective")
        exposed_minion_near((28 if n % 2 else -28, 0))
        h = jack_in()
        res = answer_hack(h, use_mouse=(n % 2 == 0), payload="EXTRACT")
        say("hack: %s %s (%d/%d)" % (res["outcome"], res["fragment_id"], len(run.hacked_uids),
                                     game.OBJECTIVE_HACKS))
        frames(30)
    check(run.complete and run.victory_t is not None, "5 hacks recover the Key")
    check(game.FIRST_KEY_INDEX in run.keys, "first name-bar cluster decoded")
    check(run.time_scale == 0.0 or run.victory_t < 0.05, "world frozen for the Key flourish")
    frames(max(1, int((1.2 - run.victory_t) * 60)))
    shot("14_key_flourish")
    stats_snapshot = dict(run.stats)
    wait_until(lambda: g.state == game.VICTORY, 400, "VICTORY screen after the flourish")
    # a dash or click still in flight as the flourish ends must not skip the stats
    frames(1, gdev.tap(K.K_SPACE))
    frames(1, gdev.click(g.victory.item_rects()[0].center))
    check(g.state == game.VICTORY, "VICTORY ignores Space / clicks while the stats type in")
    frames(150)
    shot("15_victory")
    st = g.victory.stats
    check(st["hacks"] >= 5 and st["clean_hacks"] >= 1, "victory stats: %r" % st)
    check(st["deaths"] == 1 and st["kills"] >= 1 and st["time"] > 0, "victory stats: %r" % st)
    check(not g.run and g.last_run is not None, "run ended at victory")
    say("VICTORY stats: time %.1fs kills %d hacks %d clean %d deaths %d intel %d" % (
        st["time"], st["kills"], st["hacks"], st["clean_hacks"], st["deaths"], st["intel"]))
    del stats_snapshot

    # MAIN MENU by mouse, then the intel log of the finished run
    frames(1, gdev.click(g.victory.item_rects()[1].center))
    check(g.state == game.MENU, "VICTORY -> MAIN MENU")
    import menu
    check(not menu.game_start, "menu.game_start False after the run")
    frames(1, gdev.click(g.main_menu.item_rects()[3].center))
    check(g.state == game.INTEL, "INTEL LOG from main menu")
    entries = g.intel_screen.entries()
    check(len(entries) >= 5, "intel log has the decoded fragments (%d)" % len(entries))
    for e in entries:
        check(e["text"] and e["title"], "intel entry has a title and text: %r" % e)
    frames(1, gdev.tap(K.K_DOWN))
    frames(30)
    shot("16_intel_log")
    check(len(g.intel_screen.codex.rows()) > 0, "glyph codex has rows")
    frames(1, gdev.tap(K.K_ESCAPE))
    check(g.state == game.MENU, "back to MENU")
    say("intel log: %d entries, codex %d glyphs" % (len(entries), len(g.intel_screen.codex.rows())))


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
            g.new_run(seed=i)
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
            g._recover_key(game.FIRST_KEY_INDEX)   # jump to the Key flourish -> VICTORY
        if g.state == game.PLAY and g.run is not None and i % 1300 == 650:
            r = g.run.world.rack("A")
            teleport(free_spot_near((r.bars[0].spawn_exit[0], r.bars[0].spawn_exit[1] + 120)))
    g.keys_down.clear()
    g.mouse_buttons.clear()
    return seen


# ------------------------------------------------------------------ perf

def perf(n_minions=40, n_frames=300):
    g.new_run(seed=7)
    run = g.run
    rack = run.world.rack("A")
    spot = free_spot_near((rack.bars[1].spawn_exit[0], rack.bars[1].spawn_exit[1] + 180))
    teleport(spot)
    rng = random.Random(3)
    while run.minions.live_count() < n_minions:
        ang = rng.uniform(0, 6.283)
        dist = rng.uniform(60, 420)
        pos = (spot[0] + dist * pygame.math.Vector2(1, 0).rotate_rad(ang).x,
               spot[1] + dist * pygame.math.Vector2(1, 0).rotate_rad(ang).y)
        try:
            pos = free_spot_near(pos, 12)
        except SmokeFail:
            continue
        kind = "p1ng" if rng.random() < 0.1 else "b1t"
        run.minions.spawn(kind, pos, "A")
    frames(5)

    def measure(label, n):
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
        avg = 1000.0 * t_total / n
        return avg, min(counts), max(counts)

    play = measure("PLAY", n_frames)
    shot("17_perf_crowd")
    # the same crowd, during a hack (greyscale + terminal on top)
    exposed_minion_near((20, 0))
    run.player.invuln = 99.0
    g.step([gdev.key_event(K.K_f, True)])
    for _ in range(30):
        run.player.invuln = 99.0
        g.step([])
    g.step([gdev.key_event(K.K_f, False)])
    hackp = None
    if g.state == game.HACK:
        run.hack.time_left = 1e6               # keep the window open while measuring
        hackp = measure("HACK", 120)
        shot("18_perf_hack")
        run.hack.abort("perf")
        frames(5)
    g.end_run()
    g._set_state(game.MENU)
    return play, hackp


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
             ("victory + intel log", test_finish_to_victory), ("review fixes", test_review_fixes)]
    try:
        for name, fn in steps:
            print("[%s]" % name)
            fn()
        print("[chaos]")
        seen = chaos()
        say("chaos: 4000 frames of random input, states visited: %s" % ", ".join(sorted(seen)))
        check({game.MENU, game.PLAY, game.HACK, game.VICTORY}.issubset(seen),
              "chaos reached MENU, PLAY, HACK and VICTORY")
        print("[perf]")
        play, hackp = perf()
        say("perf PLAY : %.2f ms/frame (update+draw), %d-%d minions alive" % play)
        if hackp:
            say("perf HACK : %.2f ms/frame (update+draw), %d-%d minions alive" % hackp)
        check(play[0] < 10.0, "PLAY frame with ~40 minions should be well under 16 ms")
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
