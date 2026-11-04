"""Adaptive AI systems, all local (doc 9): the Profiler ("Mirror Log"), the Director ("Kernel Load
Balancer"), the Breach Tuner, the dodge predictor, the Telemetry writer and Patch Notes.

Everything learns from Run events (Profiler.on_event); the only polling is a cheap look at the
live PR0B3s (learning rate) and at the player's surroundings (Director intensity, retreat HP).
Every gameplay change it makes is capped and announced (Patch Notes cards, doc 9.4), and the
COUNTER-ADAPTATION setting turns the applied part off (cards still print).

Direction tokens are ints 0..7: 0 = right (E), then clockwise in screen space: 1 SE, 2 S (down),
3 SW, 4 W (left), 5 NW, 6 N (up), 7 NE. DIR_NAMES maps them to "RIGHT", "DOWN-RIGHT", ...

Learning rate (doc 9.1): the profile always learns at 0.3. While a PR0B3 of the current zone is
alive the rate is 1.0, but the extra 0.7 of every dodge sample rides on the probes: it reaches
the profile only when a probe uploads at its rack. A probe that is killed or hacked first takes
its samples with it ("hacking a PR0B3 before it reaches its rack cancels its upload").
"""
import math
import random

import gdev
import hack
import settings as settings_mod

DIR_NAMES = ["RIGHT", "DOWN-RIGHT", "DOWN", "DOWN-LEFT", "LEFT", "UP-LEFT", "UP", "UP-RIGHT"]
WEAPONS = ["BOW", "PISTOL", "KATANA"]
COUNTER_IDS = ["FIREWALL_WEIGHT", "BIT_SPEED", "LEAD_SHOTS", "FAST_KITE", "MORE_PROBES", "TIER2_SHARE"]

BASE_RATE = 0.3                 # learning rate with no PR0B3 watching
PROBE_RATE = 1.0                # ... while one of this zone's PR0B3s is alive
WEAPON_ALPHA = 0.05             # weapon share EMA per attack (x learning rate)
ENGAGE_ALPHA = 0.08             # engagement distance EMA per attack (x learning rate)
ENGAGE_MAX_PX = 600             # attacks with no enemy this close are not engagements
RETREAT_WATCH_S = 1.5           # after a hit: backing off this soon counts as a retreat ...
RETREAT_PX = 100                # ... if the nearest enemy ends up this much further away
POISON_S = 60.0                 # a TURNED PR0B3 writes mirrored data this long (doc 9.4)
PROBE_POLL_S = 0.25
HEAT_CELL = 512
DECISIONS_KEPT = 200

# Telemetry (doc 9.6): leet density of the non-target words by act, and built-in templates
# used when intel.json has none for an act. [brackets] mark target words.
TELEMETRY_DENSITY = {"I": 0.35, "II": 0.55, "III": 0.8}
DEFAULT_TELEMETRY = {
    "I": ["SUBJECT FAVORS [{weapon}] {pct}%.",
          "SUBJECT [HESITATES] {s}S ON GLYPH {glyph}.",
          "SUBJECT PICKS [OPTION] {slot} FIRST {pct}%.",
          "SUBJECT [LOGGED]. SAMPLING [CONTINUES]."],
    "II": ["THE SUBJECT [DODGES] {dir} {pct}% OF THE TIME.",
           "SUBJECT [MISREADS] {glyph} {n} TIMES.",
           "SUBJECT [SPENDS] {s}S PER PICK. MODEL [ADJUSTS]."],
    "III": ["Y0U 4LW4Y5 [D0DG3] {dir}.",
            "Y0U W1LL [PICK] {slot} N3XT.",
            "Y0U [HESITATE] {s}S ON {glyph}. W3 D0 N0T.",
            "W3 [KNOW] Y0U [PREFER] {weapon}."],
}

# Patch Notes (doc 9.4): base value of each counter at strength 1.0, and the card wording.
COUNTER_BASE = {"FIREWALL_WEIGHT": 1.25, "BIT_SPEED": 1.15, "FAST_KITE": 1.2, "MORE_PROBES": 1.5,
                "TIER2_SHARE": 0.2, "LEAD_SHOTS": 0.3}
COUNTER_TEXT = {"FIREWALL_WEIGHT": "F1R3W4LL w31gh7", "BIT_SPEED": "B1T 5p33d", "LEAD_SHOTS": "P1NG l34d",
                "FAST_KITE": "P1NG k173", "MORE_PROBES": "PR0B3 c0un7", "TIER2_SHARE": "T13R 2 5h4r3"}
MAX_COUNTERS = 1
COUNTER_LIFE = 2                # patches a counter survives without its habit being seen again
MIN_ATTACKS = 12                # samples a habit rule needs before it may fire
MIN_DODGES = 6
MIN_ENGAGE = 6
MIN_KILLS_HACKS = 8
MIN_GLYPH_PICKS = 20

# Director (doc 9.2). tick() runs every 30 frames = 0.5 s of real time.
TICK_S = 0.5
PEAK_AT = 0.7
PEAK_S = (8.0, 15.0)
RELAX_S = (10.0, 20.0)
HOT = 0.9                       # a PEAK whose mean intensity is above this is "hot" ...
HOT_PEAKS = 3                   # ... and this many in a row force a Patch Notes rollback
BUDGET_MAX = 12.0
NEAR_PX = 200

# Breach Tuner (doc 9.3)
TUNER_WINDOW = 10
TIMER_SAMPLES = 8               # decisions needed before the timer adjustment moves


def dir_token(vx, vy):
    """8-direction token (see module doc) of a vector; None for a zero vector."""
    if abs(vx) < 1e-6 and abs(vy) < 1e-6:
        return None
    a = math.atan2(vy, vx)
    return int(round(a / (math.pi / 4))) % 8


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def leet_word(s):
    """Light leet for card and stat text ("DOWN-LEFT" -> "D0WN-L3F7", "KATANA" -> "K474N4")."""
    out = str(s).upper()
    for a, b in (("E", "3"), ("O", "0"), ("I", "1"), ("T", "7"), ("A", "4"), ("S", "5")):
        out = out.replace(a, b)
    return out


def act_key(act):
    """Run act (int 0..4 or "I".."III") -> telemetry act key "I" / "II" / "III"."""
    if act in ("I", "II", "III"):
        return act
    try:
        n = int(act)
    except (TypeError, ValueError):
        return "I"
    return {0: "I", 1: "I", 2: "II"}.get(n, "III")


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Director(object):
    """doc 9.2 "Kernel Load Balancer". tick() every 30 frames (Game calls it); bosses (and the
    finale) call request_spawn(cost).
      state "BUILD"|"PEAK"|"RELAX", intensity 0..1, budget float
      BUILD  the spawn budget regenerates; intensity above 0.7 starts a PEAK
      PEAK   8-15 s, the budget regenerates faster
      RELAX  10-20 s with no new spawns (the safe hacking window); also forced after story beats
    Intensity rises with damage taken, near misses and minions within 200 px, and decays.
    Three hot PEAKs in a row (mean intensity above 0.9) roll the Patch Notes counters back."""

    def __init__(self, run):
        self.run = run
        self.state = "BUILD"
        self.intensity = 0.0
        self.budget = 6.0
        self.relax_t = 0.0
        self.peak_t = 0.0
        self.peak_sum = 0.0
        self.peak_ticks = 0
        self.hot_peaks = 0
        self.rollbacks = 0
        self.rng = random.Random(0xD1EC)        # never run.rng: pacing must not shift gameplay rolls

    def _near_count(self):
        run = self.run
        p = run.player
        if p.dead:
            return 0
        r2 = NEAR_PX * NEAR_PX
        n = 0
        for m in run.minions.minions:
            if m.alive and m.team == "enemy" and (m.pos - p.pos).length_squared() <= r2:
                n += 1
        return n

    def tick(self):
        self.intensity = _clamp(self.intensity + min(0.06, 0.012 * self._near_count()), 0.0, 1.0)
        decay = 0.06 if self.state == "RELAX" else 0.03
        self.intensity = max(0.0, self.intensity - decay)
        if self.state == "RELAX":
            self.relax_t = max(0.0, self.relax_t - TICK_S)
            if self.relax_t <= 0:
                self.state = "BUILD"
            return
        if self.state == "PEAK":
            self.budget = min(BUDGET_MAX, self.budget + 1.4)
            self.peak_sum += self.intensity
            self.peak_ticks += 1
            self.peak_t -= TICK_S
            if self.peak_t <= 0:
                self._end_peak()
            return
        self.budget = min(BUDGET_MAX, self.budget + 0.9)
        if self.intensity > PEAK_AT:
            self.state = "PEAK"
            self.peak_t = self.rng.uniform(*PEAK_S)
            self.peak_sum = 0.0
            self.peak_ticks = 0

    def _end_peak(self):
        mean = self.peak_sum / max(1, self.peak_ticks)
        self.hot_peaks = self.hot_peaks + 1 if mean > HOT else 0
        self.state = "RELAX"
        self.relax_t = self.rng.uniform(*RELAX_S)
        if self.hot_peaks >= HOT_PEAKS:
            self.hot_peaks = 0
            self.rollbacks += 1
            prof = getattr(self.run, "profiler", None)
            if prof is not None:
                prof.patches.rollback()

    def request_spawn(self, cost):
        if self.state == "RELAX" or self.budget < cost:
            return False
        self.budget -= cost
        return True

    def force_relax(self, seconds):
        if self.state == "PEAK":
            self.peak_ticks = 0             # a story beat cut it short: not a hot PEAK
            self.hot_peaks = 0
        self.relax_t = max(self.relax_t, float(seconds))
        self.state = "RELAX"

    def on_event(self, name, data):
        if name == "player_damaged":
            self.intensity = min(1.0, self.intensity + (0.2 if data.get("rooted") else 0.15))
        elif name == "near_miss":
            self.intensity = min(1.0, self.intensity + 0.05)
        elif name == "minion_killed":
            self.intensity = max(0.0, self.intensity - 0.02)
        elif name == "hack_finished" and (data.get("result") or {}).get("outcome") == "backtrace":
            self.intensity = min(1.0, self.intensity + 0.1)
        elif name in ("boss_defeated", "key_collected"):
            self.force_relax(12.0)
        elif name == "player_respawned":
            self.intensity = 0.0
            self.force_relax(6.0)


class BreachTuner(object):
    """doc 9.3. level_for(kind) -> 1..6 (archetype offset applied), timer_adjust() -> 0.75..1.25,
    breach_level(zone_level=None) -> 4..6, record(result).
    With hack_difficulty ADAPTIVE a 2-up/1-down staircase over the last minion hacks moves the
    level (two successes in a row: +1; a REDACTED or BACKTRACE: -1), which settles near 70%
    success; the timer adjustment then follows the median decision time. With a fixed setting
    it returns the setting's level and no timer adjustment."""

    def __init__(self, run):
        self.run = run
        self.level = settings_mod.breach_level(run.settings)
        self.streak = 0
        self.history = []               # last TUNER_WINDOW outcomes (bools: success)

    def adaptive(self):
        return settings_mod.adaptive(self.run.settings)

    def level_for(self, kind):
        base = self.level if self.adaptive() else settings_mod.breach_level(self.run.settings)
        if str(kind).lower() == "b1t":
            return _clamp(base - 1, 1, 3)
        return _clamp(base, 1, 6)

    def breach_level(self, zone_level=None):
        """ROOT BREACH runs at the zone's Breach Level +1, and always with 4 options (L4+)."""
        return _clamp((zone_level or self.level_for("p1ng")) + 1, 4, 6)

    def success_rate(self):
        return sum(1 for h in self.history if h) / float(len(self.history)) if self.history else None

    def timer_adjust(self):
        """Slow, careful readers get a little more time, fast readers a little less."""
        if not self.adaptive():
            return 1.0
        prof = getattr(self.run, "profiler", None)
        if prof is None or len(prof.decision_times[1]) + len(prof.decision_times[2]) < TIMER_SAMPLES:
            return 1.0
        m = prof.median_pick_s()
        return round(_clamp(1.0 + (m - 1.5) * 0.25, 0.75, 1.25), 3)

    def record(self, result):
        outcome = result.get("outcome")
        if outcome not in ("success", "redacted", "backtrace"):
            return                      # aborts and interruptions say nothing about reading
        ok = outcome == "success"
        self.history = (self.history + [ok])[-TUNER_WINDOW:]
        if not self.adaptive():
            return
        if ok:
            self.streak += 1
            if self.streak >= 2:
                self.level = min(6, self.level + 1)
                self.streak = 0
        else:
            self.level = max(1, self.level - 1)
            self.streak = 0

    def to_save(self):
        return {"level": self.level, "streak": self.streak, "history": list(self.history)}

    def from_save(self, d):
        d = d or {}
        self.level = _clamp(int(d.get("level", self.level)), 1, 6)
        self.streak = int(d.get("streak", 0))
        self.history = [bool(x) for x in d.get("history", [])][-TUNER_WINDOW:]


class DodgePredictor(object):
    """doc 9.5. Order-2 n-gram over dash direction tokens (fed during boss fights only), with an
    order-1 fallback while a two-dash context has not been seen yet.
      observe(token), predict() -> (token, probability) | None"""

    def __init__(self):
        self.counts = {}                # (a, b) -> {next: n}
        self.counts1 = {}               # a -> {next: n}
        self.last = []
        self.observed = 0

    def observe(self, token):
        if token is None:
            return
        token = int(token) % 8
        if len(self.last) == 2:
            row = self.counts.setdefault(tuple(self.last), {})
            row[token] = row.get(token, 0) + 1
        if self.last:
            row = self.counts1.setdefault(self.last[-1], {})
            row[token] = row.get(token, 0) + 1
        self.last = (self.last + [token])[-2:]
        self.observed += 1

    def predict(self):
        """(token, probability) of the next dash, or None while there is too little data."""
        row = self.counts.get(tuple(self.last)) if len(self.last) == 2 else None
        if not row or sum(row.values()) < 2:
            row = self.counts1.get(self.last[-1]) if self.last else None
            if not row or sum(row.values()) < 3:
                return None
        tok = max(sorted(row), key=lambda k: row[k])
        return tok, row[tok] / float(sum(row.values()))

    def to_save(self):
        return {"n2": [[a, b, t, n] for (a, b), row in self.counts.items() for t, n in row.items()],
                "n1": [[a, t, n] for a, row in self.counts1.items() for t, n in row.items()],
                "last": list(self.last), "observed": self.observed}

    def from_save(self, d):
        d = d or {}
        self.counts, self.counts1 = {}, {}
        for a, b, t, n in d.get("n2", []):
            self.counts.setdefault((int(a), int(b)), {})[int(t)] = int(n)
        for a, t, n in d.get("n1", []):
            self.counts1.setdefault(int(a), {})[int(t)] = int(n)
        self.last = [int(x) for x in d.get("last", [])][-2:]
        self.observed = int(d.get("observed", 0))


class TelemetryWriter(object):
    """doc 9.6. make_fragment(act, rng) -> an intel fragment dict (tier TELEMETRY) built from
    intel.json "telemetry_templates" (by act "I"/"II"/"III"; built-in ones otherwise) and real
    profile stats. Placeholders: {weapon} {pct} {dir} {s} {glyph} {slot} {n}. A template is only
    used when every placeholder it names has data. Words in [brackets] are the targets.
    The voice moves from clinical third person (Act I) to second person (Act III), and in
    Act III {dir} is the dodge predictor's guess at your NEXT move when it has one."""

    def __init__(self, profiler):
        self.profiler = profiler
        self.n = 0
        self.templates = None

    def _templates(self, key):
        if self.templates is None:
            d = getattr(self.profiler.run.progression, "content", None) or {}
            self.templates = d.get("telemetry_templates") or {}
        return list(self.templates.get(key) or DEFAULT_TELEMETRY[key])

    def values(self, key):
        """Placeholder values from the profile (missing ones are left out)."""
        p = self.profiler
        v = {}
        w, pct = p.top_weapon()
        if w is not None and p.attacks >= 3:
            v["weapon"], v["weapon_pct"] = w, pct
        guess = p.predictor.predict() if key == "III" else None
        if guess is not None:
            v["dir"], v["dir_pct"] = DIR_NAMES[guess[0]].replace("-", " "), int(round(100 * guess[1]))
        else:
            d, share = p.dodge_bias()
            if d is not None:
                v["dir"], v["dir_pct"] = d.replace("-", " "), int(round(100 * share))
        m = p.median_pick_s()
        if m is not None:
            v["s"] = "%.1f" % m
        worst = p.worst_glyphs(1)
        if worst:
            v["glyph"] = worst[0]
            v["n"] = p.run.codex.misses.get(worst[0], 0)
        total = sum(p.option_bias)
        if total >= 3:
            slot = max(range(4), key=lambda i: p.option_bias[i])
            v["slot"], v["slot_pct"] = slot + 1, int(round(100.0 * p.option_bias[slot] / total))
        if "n" not in v and p.hacks:
            v["n"] = p.hacks
        return v

    def fill(self, template, v):
        """One template filled with the values, or None if a placeholder has no data."""
        names = ("weapon", "pct", "dir", "s", "glyph", "slot", "n")
        pct = None
        for src in ("weapon", "slot", "dir"):
            if "{%s}" % src in template and src + "_pct" in v:
                pct = v[src + "_pct"]
                break
        vals = dict(v)
        if pct is not None:
            vals["pct"] = pct
        for k in names:
            if "{%s}" % k in template and k not in vals:
                return None
        try:
            return template.format(**dict((k, vals.get(k, "")) for k in names))
        except (KeyError, IndexError, ValueError):
            return None

    def make_text(self, act, rng):
        key = act_key(act)
        v = self.values(key)
        options = [t for t in (self.fill(t, v) for t in self._templates(key)) if t]
        if not options:
            options = [t for t in (self.fill(t, v) for t in DEFAULT_TELEMETRY[key]) if t]
        if not options:
            return None
        # prefer templates with real data in them over the data-free fallbacks
        rich = [t for t in options if any(ch.isdigit() for ch in t)] or options
        return rng.choice(rich)

    def words_for(self, text, key, rng):
        density = TELEMETRY_DENSITY.get(key, 0.5)
        maker = getattr(hack, "text_to_words", None)
        if maker is not None:
            words = maker(text, density, rng)
        else:
            words = []
            for tok in text.replace("[", "").replace("]", "").split():
                words.append({"c": hack.leetify(tok, tier2=False, density=density, rng=rng)})
        if words and not any(isinstance(w, dict) and w.get("target") for w in words):
            # no bracketed word survived: decode the longest plain word instead
            best = max(range(len(words)), key=lambda i: len(words[i].get("c", [])) if
                       isinstance(words[i], dict) and not words[i].get("lit") else -1)
            if isinstance(words[best], dict) and not words[best].get("lit"):
                words[best] = dict(words[best], target=True)
        return words

    def make_fragment(self, act, rng):
        key = act_key(act)
        text = self.make_text(act, rng)
        if text is None:
            return None
        words = self.words_for(text, key, rng)
        if not words:
            return None
        self.n += 1
        run = self.profiler.run
        return {"id": "TEL-%03d" % self.n, "tier": "TELEMETRY", "act": key,
                "zone": getattr(run.progression, "zone", None),
                "title": "TELEMETRY 0x%02X" % (self.n % 256), "author": "PR0B3", "sources": ["PR0B3"],
                "tags": ["telemetry"], "words": words, "text": text}


class PatchNotes(object):
    """doc 9.4. A deterministic rules table (habit -> counter), limited to the boss's
    "adaptation_pool"; at most 2 counters active, each lapsing after 2 patches unless its habit
    shows up again. on_fight_start(boss) / on_core_dump(boss) -> card dict:
        {"version": "v0.4.1", "lines": ["+ P1NG l34d +30% (Y0U D0DG3 L3F7 71%)"], "sign": "-- U.H.",
         "boss": leet name, "applied": bool}
    Strength = 0.3 + 0.7 x kills / (kills + hacks) for that boss's minions (+0.1 per Adaptation
    point, capped at 1.0): hacking deletes what it learned. Values at strength 1.0:
        FIREWALL_WEIGHT 1.25, BIT_SPEED 1.15, FAST_KITE 1.2, MORE_PROBES 1.5, TIER2_SHARE 0.2,
        LEAD_SHOTS {"side": token, "lead": 0.3}
    effects() -> {counter id: value} that minions / bosses / hack read; {} when counter-adaptation
    is OFF in settings (cards still print). capped = True (the finale's PATCH fight) stops new
    counters. rollback() drops every counter (the Director forces it, doc 9.4)."""

    def __init__(self, profiler):
        self.profiler = profiler
        self.version = [0, 1, 0]
        self.active = {}                # id -> {"value", "patches_left", "evidence", "score"}
        self.capped = False
        self.history = []               # every card, oldest first (credits / tests)

    # ------------------------------------------------------------ rules

    def strength(self, boss=None):
        p = self.profiler
        rack = getattr(getattr(boss, "rack", None), "id", None)
        k, h = p.rack_counts(rack) if rack else (p.kills, p.hacks)
        s = 0.3 + 0.7 * (k / float(k + h) if k + h > 0 else 0.5)
        s += 0.1 * int(getattr(boss, "adaptation", 0) or 0)
        return _clamp(s, 0.3, 1.0)

    def habits(self):
        """[(score 0..1, counter id, evidence text, side token|None)] for every habit seen now."""
        p = self.profiler
        out = []
        ws = p.weapon_share
        if p.attacks >= MIN_ATTACKS and ws:
            ranged = ws.get("BOW", 0.0) + ws.get("PISTOL", 0.0)
            if ranged > 0.6:
                out.append(((ranged - 0.6) / 0.4, "FIREWALL_WEIGHT", "R4NG3D 5H4R3 %d%%" % int(100 * ranged), None))
            kat = ws.get("KATANA", 0.0)
            if kat > 0.5:
                out.append(((kat - 0.5) / 0.5, "FAST_KITE", "K474N4 5H4R3 %d%%" % int(100 * kat), None))
        if p.engagement_n >= MIN_ENGAGE and p.engagement_px > 200:
            out.append((min(1.0, (p.engagement_px - 200) / 200.0), "BIT_SPEED",
                        "Y0U K33P %dPX 4W4Y" % int(p.engagement_px), None))
        if sum(p.dodge_bins) >= MIN_DODGES * BASE_RATE:
            name, share = p.dodge_bias()
            if name is not None and share > 0.6:
                tok = DIR_NAMES.index(name)
                out.append(((share - 0.6) / 0.4, "LEAD_SHOTS",
                            "Y0U D0DG3 %s %d%%" % (leet_word(name), int(100 * share)), tok))
        if p.kills + p.hacks >= MIN_KILLS_HACKS and p.hacks * 4 <= p.kills:
            out.append((min(1.0, p.kills / float(max(1, p.hacks)) / 12.0), "MORE_PROBES",
                        "Y0U K1LL %d, H4CK %d" % (p.kills, p.hacks), None))
        acc, n = p.glyph_accuracy()
        if n >= MIN_GLYPH_PICKS and acc > 0.8:
            out.append(((acc - 0.8) / 0.2, "TIER2_SHARE", "GLYPH 4CCUR4CY %d%%" % int(100 * acc), None))
        out.sort(key=lambda h: (-h[0], COUNTER_IDS.index(h[1])))
        return out

    def _value(self, cid, strength, side):
        base = COUNTER_BASE[cid]
        if cid == "LEAD_SHOTS":
            return {"side": side, "lead": round(base * strength, 2)}
        if cid == "TIER2_SHARE":
            return round(base * strength, 2)
        return round(1.0 + (base - 1.0) * strength, 2)

    @staticmethod
    def _pct(cid, value):
        if cid == "LEAD_SHOTS":
            return int(round(100 * value.get("lead", 0)))
        if cid == "TIER2_SHARE":
            return int(round(100 * value))
        return int(round(100 * (value - 1.0)))

    def _line(self, cid):
        a = self.active[cid]
        return "+ %s +%d%% (%s)" % (COUNTER_TEXT[cid], self._pct(cid, a["value"]), a["evidence"])

    def _patch(self, boss, minor):
        if minor:
            self.version[1] += 1
            self.version[2] = 0
        else:
            self.version[2] += 1
        lines = []
        lapsed = set()
        for cid in sorted(self.active):                         # decay (doc 9.4: after 2 patches)
            a = self.active[cid]
            a["patches_left"] -= 1
            if a["patches_left"] <= 0:
                del self.active[cid]
                lapsed.add(cid)
                lines.append("- %s R3V3R73D" % COUNTER_TEXT[cid])
        added = []
        if not self.capped:
            d = getattr(boss, "d", None) or {}
            pool = d.get("adaptation_pool") or COUNTER_IDS
            strength = self.strength(boss)
            for score, cid, evidence, side in self.habits():
                if cid not in pool or cid in lapsed:
                    continue                    # a counter that just lapsed sits this patch out
                entry = {"value": self._value(cid, strength, side), "patches_left": COUNTER_LIFE,
                         "evidence": evidence, "score": score}
                if cid in self.active:
                    # refresh the value and evidence, but keep its expiry: a steady habit does
                    # not earn a permanent counter
                    entry["patches_left"] = self.active[cid]["patches_left"]
                    self.active[cid] = entry
                elif len(self.active) < MAX_COUNTERS:
                    self.active[cid] = entry
                else:
                    weakest = min(self.active, key=lambda k: (self.active[k]["score"], k))
                    if self.active[weakest]["score"] >= score or weakest in added:
                        continue
                    del self.active[weakest]
                    lines.append("- %s R3PL4C3D" % COUNTER_TEXT[weakest])
                    self.active[cid] = entry
                added.append(cid)
                if len(added) >= MAX_COUNTERS:
                    break
        for cid in added:
            lines.append(self._line(cid))
        if not lines:
            lines.append("+ N0 CH4NG35 (1N5UFF1C13N7 D4T4)" if not self.capped else
                         "+ N0 CH4NG35 (Y0U UND3R574ND 17)")
        applied = self.applied()
        if not applied:
            lines.append("[C0UN73R-4D4P74710N 0FF :: N07 4PPL13D]")
        card = {"version": "v%d.%d.%d" % tuple(self.version), "lines": lines, "sign": "-- U.H.",
                "boss": getattr(boss, "leet_name", ""), "applied": applied}
        self.history.append(card)
        self.history = self.history[-20:]
        return card

    # ------------------------------------------------------------ API

    def applied(self):
        return bool(self.profiler.run.settings.get("counter_adaptation", True))

    def on_fight_start(self, boss):
        return self._patch(boss, minor=False)

    def on_core_dump(self, boss):
        return self._patch(boss, minor=True)

    def rollback(self):
        """Drop every active counter (Director: three hot PEAKs in a row). Returns the card."""
        dropped = sorted(self.active)
        self.active = {}
        self.version[2] += 1
        card = {"version": "v%d.%d.%d" % tuple(self.version), "sign": "-- K3RN3L",
                "lines": ["- %s R0LL3D B4CK" % COUNTER_TEXT[c] for c in dropped] or ["+ N0 C0UN73R5 70 R0LL B4CK"],
                "boss": "", "applied": self.applied()}
        self.history.append(card)
        run = self.profiler.run
        if dropped and hasattr(run, "toast"):
            run.toast("R0LLB4CK %s :: K3RN3L 7HR0773L3D 7H3 M0D3L" % card["version"], gdev.CYAN, 3.0)
        return card

    def effects(self):
        if not self.active or not self.applied():
            return {}
        return dict((k, v["value"]) for k, v in self.active.items())

    def to_save(self):
        return {"version": list(self.version), "active": dict((k, dict(v)) for k, v in self.active.items())}

    def from_save(self, d):
        d = d or {}
        self.version = list(d.get("version", self.version))
        self.active = dict((k, dict(v)) for k, v in (d.get("active") or {}).items() if k in COUNTER_IDS)


class Profiler(object):
    """doc 9.1 "Mirror Log". Public surface (contract):
      director, tuner, predictor, telemetry, patches     (the sub-systems above)
      learning_rate (1.0 while a PR0B3 of the current zone is alive, else 0.3), poisoned_t
      weapon_share {name: 0..1}, top_weapon() -> (name|None, pct int), attacks (count)
      kills, hacks, kill_hack_ratio() -> kills / max(1, hacks), rack_counts(rack_id) -> (kills, hacks)
      decision_times {1: [s], 2: [s]}, median_pick_s(tier=None) -> float|None
      dodge_bins [8 floats], dodge_bias() -> (dir name|None, share 0..1)
      engagement_px (EMA), engagement_n, retreat_hp (EMA of the HP you back off at)|None
      heatmap {(cx, cy): seconds}, top_cell() -> (cx, cy)|None
      option_bias [4 counts], input_counts {"mouse", "key"}
      worst_glyphs(n=5) -> [glyph]  (from run.codex), glyph_accuracy() -> (0..1, picks)
      stat_line(kind) -> str   "heatmap"|"weapon_share"|"dodge_bias"|"decision"|"worst_glyphs"
      summary() -> dict for the credits: {"weapon", "weapon_pct", "hacks", "kills", "worst_glyph",
                                          "median_pick", "dodge", "clean_hacks", ...}
      on_event(name, data), update(dt, wdt), hack_context(minion) -> dict
      to_save() -> dict, from_save(d)
    """

    def __init__(self, run):
        self.run = run
        self.director = Director(run)
        self.tuner = BreachTuner(run)
        self.predictor = DodgePredictor()
        self.telemetry = TelemetryWriter(self)
        self.patches = PatchNotes(self)
        self.learning_rate = BASE_RATE
        self.poisoned_t = 0.0
        self.weapon_share = {}
        self.attacks = 0
        self.kills = 0
        self.hacks = 0
        self.racks = {}                 # rack id -> [kills, hacks] of that rack's minions
        self.decision_times = {1: [], 2: []}
        self.dodge_bins = [0.0] * 8
        self.engagement_px = 0.0
        self.engagement_n = 0
        self.retreat_hp = None
        self.retreat_n = 0
        self.heatmap = {}
        self.option_bias = [0, 0, 0, 0]
        self.input_counts = {"mouse": 0, "key": 0}
        self.uploads = 0
        self.probe_buffers = {}         # PR0B3 uid -> [8] dodge samples it carries to its rack
        self._probes = []               # this zone's live enemy PR0B3s (polled)
        self._poll_t = 0.0
        self._watch = None              # retreat watch after a hit: {"hp", "t", "d0"}

    # ------------------------------------------------------------ queries

    def top_weapon(self):
        if not self.weapon_share:
            return None, 0
        w = max(WEAPONS, key=lambda k: self.weapon_share.get(k, 0.0))
        return w, int(round(100 * self.weapon_share.get(w, 0.0)))

    def kill_hack_ratio(self):
        return self.kills / float(max(1, self.hacks))

    def rack_counts(self, rack_id):
        k, h = self.racks.get(rack_id, (0, 0))
        return k, h

    def median_pick_s(self, tier=None):
        xs = self.decision_times.get(tier, []) if tier else self.decision_times[1] + self.decision_times[2]
        return median(xs)

    def dodge_bias(self):
        total = sum(self.dodge_bins)
        if total <= 0:
            return None, 0.0
        i = max(range(8), key=lambda k: self.dodge_bins[k])
        return DIR_NAMES[i], self.dodge_bins[i] / total

    def top_cell(self):
        if not self.heatmap:
            return None
        return max(sorted(self.heatmap), key=lambda k: self.heatmap[k])

    def worst_glyphs(self, n=5):
        codex = self.run.codex
        scored = []
        for g in set(codex.hits) | set(codex.misses):
            score = codex.misses.get(g, 0) - 0.5 * codex.hits.get(g, 0) + 0.5 * codex.recent_misses(g)
            scored.append((score, g))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [g for s, g in scored if s > 0 and codex.misses.get(g, 0) > 0][:n]

    def glyph_accuracy(self):
        codex = self.run.codex
        h = sum(codex.hits.values())
        n = h + sum(codex.misses.values())
        return (h / float(n) if n else 0.0), n

    def stat_line(self, kind):
        """The Core Dump's profile column (doc 7.2 step 4), from real data."""
        if kind == "weapon_share":
            if not self.weapon_share:
                return "W34P0N 5H4R3 :: N0 D474"
            parts = ["%s %d%%" % (leet_word(w), int(round(100 * self.weapon_share.get(w, 0.0))))
                     for w in sorted(WEAPONS, key=lambda k: -self.weapon_share.get(k, 0.0))]
            return "W34P0N 5H4R3 :: " + " / ".join(parts)
        if kind == "dodge_bias":
            d, share = self.dodge_bias()
            if d is None:
                return "D0DG3 B145 :: N0 D474"
            guess = self.predictor.predict()
            tail = " :: N3X7 %s %d%%" % (leet_word(DIR_NAMES[guess[0]]), int(100 * guess[1])) if guess else ""
            return "D0DG3 B145 :: %s %d%%%s" % (leet_word(d), int(round(100 * share)), tail)
        if kind == "decision":
            m = self.median_pick_s()
            m2 = self.median_pick_s(2)
            t2 = " (T13R 2 %.1fs)" % m2 if m2 is not None else ""
            return "M3D14N P1CK %s%s :: K1LL:H4CK %.1f" % ("%.1fs" % m if m is not None else "--", t2,
                                                          self.kill_hack_ratio())
        if kind == "worst_glyphs":
            worst = self.worst_glyphs()
            if not worst:
                return "W0R57 GLYPH5 :: N0N3 Y37"
            top = self.run.codex.top_misread(worst[0])
            tail = " :: %s R34D 45 %s x%d" % (worst[0], top[0], top[1]) if top else ""
            return "W0R57 GLYPH5 :: %s%s" % ("  ".join(worst), tail)
        cell = self.top_cell()
        if cell is None:
            return "H34TM4P :: N0 D474"
        total = sum(self.heatmap.values()) or 1.0
        return "H34TM4P :: C3LL %d,%d H0LD5 %d%% 0F Y0UR 71M3" % (cell[0], cell[1],
                                                                 int(round(100 * self.heatmap[cell] / total)))

    def summary(self):
        w, pct = self.top_weapon()
        worst = self.worst_glyphs(1)
        m = self.median_pick_s()
        d, share = self.dodge_bias()
        st = self.run.stats
        return {"weapon": w or "PISTOL", "weapon_pct": pct, "hacks": st.get("hacks", 0),
                "kills": st.get("kills", 0), "clean_hacks": st.get("clean_hacks", 0),
                "worst_glyph": worst[0] if worst else "--", "median_pick": m, "dodge": d,
                "dodge_pct": int(round(100 * share)), "kill_hack_ratio": round(self.kill_hack_ratio(), 2),
                "engagement_px": int(self.engagement_px), "uploads": self.uploads,
                "patch": "v%d.%d.%d" % tuple(self.patches.version)}

    # ------------------------------------------------------------ learning

    def _nearest_enemy_px(self):
        p = self.run.player
        best = None
        for m in self.run.minions.minions:
            if m.alive and m.team == "enemy":
                d = (m.pos - p.pos).length_squared()
                if best is None or d < best:
                    best = d
        return math.sqrt(best) if best is not None else None

    def _learn_weapon(self, weapon):
        if weapon not in WEAPONS:
            return
        if not self.weapon_share:
            self.weapon_share = dict((k, 1.0 / len(WEAPONS)) for k in WEAPONS)
        a = WEAPON_ALPHA * self.learning_rate
        poisoned = self.poisoned_t > 0
        for k in WEAPONS:
            if poisoned:                    # inverted: the model learns you avoid this weapon
                target = 0.0 if k == weapon else 0.5
            else:
                target = 1.0 if k == weapon else 0.0
            v = self.weapon_share.get(k, 0.0)
            self.weapon_share[k] = v + a * (target - v)

    def _learn_dodge(self, tok):
        tok = int(tok) % 8
        if self.poisoned_t > 0:
            tok = (tok + 4) % 8             # mirrored: rotated 180 degrees
        self.dodge_bins[tok] += BASE_RATE
        probes = [m for m in self._probes if m.alive]
        if probes:                          # the boost rides on the probes until they upload
            share = (PROBE_RATE - BASE_RATE) / len(probes)
            for m in probes:
                self.probe_buffers.setdefault(m.uid, [0.0] * 8)[tok] += share

    def _count(self, minion, slot):
        rid = getattr(minion, "rack_id", None)
        if rid:
            row = self.racks.setdefault(rid, [0, 0])
            row[slot] += 1

    def _poll_probes(self):
        run = self.run
        zone = None if run.in_deep_core else getattr(run.progression, "zone", None)
        probes = run.minions.live(kind="pr0b3")
        if zone is not None:
            probes = [m for m in probes if m.rack_id == zone]
        self._probes = probes
        self.learning_rate = PROBE_RATE if probes else BASE_RATE

    # ------------------------------------------------------------ events

    def on_event(self, name, data):
        self.director.on_event(name, data)
        if name == "player_attack":
            self.attacks += 1
            self._learn_weapon(data.get("weapon"))
            d = self._nearest_enemy_px()
            if d is not None and d <= ENGAGE_MAX_PX:
                a = ENGAGE_ALPHA * self.learning_rate
                self.engagement_px = d if self.engagement_n == 0 else self.engagement_px + a * (d - self.engagement_px)
                self.engagement_n += 1
        elif name == "minion_killed":
            self.kills += 1
            self._count(data.get("minion"), 0)
        elif name == "hack_finished" and data.get("provider") == "minion":
            res = data.get("result") or {}
            m = data.get("target")
            if res.get("outcome") == "success":
                self.hacks += 1
                self._count(m, 1)
                if getattr(m, "kind", "") == "pr0b3":
                    self.probe_buffers.pop(getattr(m, "uid", None), None)   # its samples die with it
                    if res.get("payload") == "TURN":
                        self.poisoned_t = POISON_S
            self.tuner.record(res)
        elif name == "pick":
            tier = 2 if data.get("tier") == 2 else 1
            s = data.get("decision_s")
            if s is not None:
                self.decision_times[tier] = (self.decision_times[tier] + [round(float(s), 3)])[-DECISIONS_KEPT:]
            slot = data.get("slot")
            if isinstance(slot, int) and 0 <= slot < 4:
                self.option_bias[slot] += 1
            inp = data.get("input", "key")
            self.input_counts[inp] = self.input_counts.get(inp, 0) + 1
        elif name == "dash":
            run = self.run
            fight = run.bosses.active is not None or (run.in_deep_core and getattr(run.finale, "active", False))
            tok = data.get("dir")
            if fight and tok is not None:
                self.predictor.observe((int(tok) + 4) % 8 if self.poisoned_t > 0 else tok)
        elif name == "near_miss":
            tok = data.get("dir")
            if tok is not None:
                self._learn_dodge(tok)
        elif name == "probe_uploaded":
            m = data.get("minion")
            buf = self.probe_buffers.pop(getattr(m, "uid", None), None)
            self.uploads += 1
            if buf:
                for i in range(8):
                    self.dodge_bins[i] += buf[i]
        elif name == "minion_died":
            m = data.get("minion")
            if getattr(m, "kind", "") == "pr0b3" and data.get("cause") != "upload":
                self.probe_buffers.pop(getattr(m, "uid", None), None)
        elif name == "player_damaged":
            p = self.run.player
            if not data.get("rooted") and not p.dead:
                d0 = self._nearest_enemy_px()
                if d0 is not None:
                    self._watch = {"hp": p.hp, "t": RETREAT_WATCH_S, "d0": d0}
        elif name in ("player_died", "deep_core_entered"):
            self._watch = None
            self.probe_buffers = {}

    def update(self, dt, wdt):
        p = self.run.player
        if not p.dead and wdt > 0:
            cell = (int(p.pos.x // HEAT_CELL), int(p.pos.y // HEAT_CELL))
            self.heatmap[cell] = self.heatmap.get(cell, 0.0) + wdt
        self.poisoned_t = max(0.0, self.poisoned_t - wdt)
        self._poll_t -= dt
        if self._poll_t <= 0:
            self._poll_t = PROBE_POLL_S
            self._poll_probes()
        w = self._watch
        if w is not None:
            w["t"] -= wdt
            d = self._nearest_enemy_px() if not p.dead else None
            if d is not None and d >= w["d0"] + RETREAT_PX:
                hp = float(w["hp"])
                self.retreat_hp = hp if self.retreat_hp is None else self.retreat_hp + 0.2 * (hp - self.retreat_hp)
                self.retreat_n += 1
                self._watch = None
            elif w["t"] <= 0 or d is None:
                self._watch = None

    def hack_context(self, minion):
        ctx = {}
        eff = self.patches.effects()
        if "TIER2_SHARE" in eff:
            ctx["tier2_bonus"] = eff["TIER2_SHARE"]
        if getattr(minion, "kind", "") == "pr0b3" and not getattr(minion, "key_source", None):
            frag = self.telemetry.make_fragment(self.run.progression.act, self.run.rng)
            if frag is not None:
                ctx["fragment"] = frag
                ctx["title"] = "PR0B3 :: 73L3M37RY"
        return ctx

    # ------------------------------------------------------------ save

    def to_save(self):
        return {"weapon_share": dict(self.weapon_share), "attacks": self.attacks,
                "kills": self.kills, "hacks": self.hacks,
                "racks": dict((k, list(v)) for k, v in self.racks.items()),
                "decision_times": {"1": self.decision_times[1], "2": self.decision_times[2]},
                "dodge_bins": list(self.dodge_bins), "engagement_px": self.engagement_px,
                "engagement_n": self.engagement_n, "retreat_hp": self.retreat_hp, "retreat_n": self.retreat_n,
                "heatmap": [[k[0], k[1], v] for k, v in self.heatmap.items()],
                "option_bias": list(self.option_bias), "input_counts": dict(self.input_counts),
                "uploads": self.uploads, "poisoned_t": self.poisoned_t,
                "tuner": self.tuner.to_save(), "patches": self.patches.to_save(),
                "predictor": self.predictor.to_save(), "telemetry_n": self.telemetry.n}

    def from_save(self, d):
        d = d or {}
        self.weapon_share = dict((k, float(v)) for k, v in (d.get("weapon_share") or {}).items() if k in WEAPONS)
        self.attacks = int(d.get("attacks", 0))
        self.kills = int(d.get("kills", 0))
        self.hacks = int(d.get("hacks", 0))
        self.racks = dict((str(k), [int(v[0]), int(v[1])]) for k, v in (d.get("racks") or {}).items())
        dt = d.get("decision_times") or {}
        self.decision_times = {1: [float(x) for x in dt.get("1", [])][-DECISIONS_KEPT:],
                               2: [float(x) for x in dt.get("2", [])][-DECISIONS_KEPT:]}
        bins = list(d.get("dodge_bins") or [0.0] * 8)
        self.dodge_bins = [float(x) for x in (bins + [0.0] * 8)[:8]]
        self.engagement_px = float(d.get("engagement_px", 0.0))
        self.engagement_n = int(d.get("engagement_n", 0))
        self.retreat_hp = d.get("retreat_hp")
        self.retreat_n = int(d.get("retreat_n", 0))
        self.heatmap = dict(((int(a), int(b)), float(v)) for a, b, v in d.get("heatmap", []))
        ob = list(d.get("option_bias") or [0, 0, 0, 0])
        self.option_bias = [int(x) for x in (ob + [0, 0, 0, 0])[:4]]
        self.input_counts = dict(d.get("input_counts") or {"mouse": 0, "key": 0})
        self.uploads = int(d.get("uploads", 0))
        self.poisoned_t = float(d.get("poisoned_t", 0.0))
        if "tuner" in d:
            self.tuner.from_save(d.get("tuner"))
        elif "tuner_level" in d:                        # the stub's save shape
            self.tuner.level = int(d["tuner_level"])
        if "patches" in d:
            self.patches.from_save(d.get("patches"))
        elif "patch_version" in d:
            self.patches.version = list(d["patch_version"])
        pred = d.get("predictor")
        if isinstance(pred, dict):
            self.predictor.from_save(pred)
        elif isinstance(pred, list):
            for a, b, t, n in pred:
                self.predictor.counts.setdefault((int(a), int(b)), {})[int(t)] = int(n)
        # Telemetry ids must never repeat a saved one (IntelLog keeps the first copy of an id)
        n = int(d.get("telemetry_n", 0))
        for f in getattr(self.run.intel, "fragments", []):
            fid = str(f.get("id", ""))
            if fid.startswith("TEL-") and fid[4:].isdigit():
                n = max(n, int(fid[4:]))
        self.telemetry.n = n
