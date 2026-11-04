"""V3|2D1C7's one-off script (doc 7.3, slot 4): the verdict clock.

bosses.load_script() imports this file; Boss calls the hooks below. When the player jacks into one
of V3|2D1C7's minions during the fight, a magenta "V3|2D1C7" entry is pushed to the front of the
visible queue. It counts in REAL time (the only boss timer that does):
    verdict_s = clamp(median pick time x picks left x factor[phase], min_s, max_s)  (x1.5 LONG timer)
At 0 during the hack: a packet_rain circle on the rooted spot (damage -> +35 TRACE via Game).
Finishing or aborting first stamps 0V3|2|2UL3D and removes the entry.

Hooks (all optional; boss is the bosses.Boss, its script_state dict holds this script's state):
    on_phase(boss, phase_index)
    on_tick(boss, wdt, real_dt)
    on_hack(boss, session, minion)       a HackSession on one of this boss's minions just opened
    on_hack_end(boss, result, minion)    that session finished (any outcome)
    hud_queue(boss) -> [entry] | None    extra queue entries, shown before the visible_queue ones
"""
import gdev
import settings as settings_mod

LABEL = "V3|2D1C7"
DEFAULT_PICK_S = 1.6        # median pick time before the profiler has any
DEFAULTS = {"factor": [1.15, 1.0, 0.85], "min_s": 2.0, "max_s": 8.0}
LIVE_STATES = ("SIEGE", "EXPOSURE", "BREACH_READY")


def _cfg(boss):
    c = dict(DEFAULTS)
    c.update(boss.d.get("verdict") or {})
    return c


def picks_left(session):
    """Picks the session still needs (a two-part F1R3W4LL word is two picks): its own pick list."""
    picks = getattr(session, "picks", None)
    if picks:
        return max(1, len(picks) - int(getattr(session, "ti", 0) or 0))
    try:
        return max(1, len(session.target_words()))
    except Exception:
        return 2


def verdict_seconds(boss, session):
    cfg = _cfg(boss)
    factors = cfg["factor"] or [1.0]
    factor = float(factors[min(boss.phase, len(factors) - 1)])
    med = boss.run.profiler.median_pick_s() or DEFAULT_PICK_S
    s = max(float(cfg["min_s"]), min(float(cfg["max_s"]), med * picks_left(session) * factor))
    return s * settings_mod.timer_mult(boss.run.settings)


def on_phase(boss, phase_index):
    boss.script_state.pop("entry", None)


def on_hack(boss, session, minion):
    if boss.state not in LIVE_STATES or session is None:
        return
    s = verdict_seconds(boss, session)
    boss.script_state["entry"] = {"label": LABEL, "t": s, "color": gdev.MAGENTA, "real_time": True}
    boss.script_state["minion"] = minion
    boss.script_state["session"] = session
    boss.run.float_text((minion.pos.x, minion.pos.y - 20), "%s :: %.1fs" % (LABEL, s), gdev.MAGENTA, 1.2)


def on_tick(boss, wdt, real_dt):
    st = boss.script_state
    e = st.get("entry")
    if e is None:
        return
    run = boss.run
    if run.hack is None or run.hack is not st.get("session"):
        st.pop("entry", None)                 # the session went away without an end event
        return
    e["t"] -= real_dt
    if e["t"] > 0:
        return
    st.pop("entry", None)
    p = run.player
    run.attacks.spawn("packet_rain", p.pos, {"telegraph_s": 0.0, "radius": 24, "aim_point": p.pos.copy()}, boss)
    run.float_text((p.pos.x, p.pos.y - 22), "V3RD1C7: GU1LTY", gdev.RED, 1.2)
    run.shake(5, 0.2)


def on_hack_end(boss, result, minion):
    st = boss.script_state
    if st.get("entry") is None or st.get("minion") is not minion:
        return
    st.pop("entry", None)
    p = boss.run.player
    boss.run.float_text((p.pos.x, p.pos.y - 22), "0V3|2|2UL3D", gdev.CYAN, 1.4)


def hud_queue(boss):
    e = boss.script_state.get("entry")
    return [e] if e is not None else None
