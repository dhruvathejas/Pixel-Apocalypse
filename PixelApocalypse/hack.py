"""JACK-IN / DECODE: the button hack (doc section 5), the glyph codex and the intel log.

One HackSession runs in three phases:
    DECODE   The minion's intel fragment is shown as boxed glyph clusters. Its target
             words (magenta) are decoded left to right. Each target gets a row of option
             buttons: the correct reading plus decoys built from glyph misreadings
             (make_options). Pick with LMB or 1-3. Esc / RMB aborts.
    PAYLOAD  After ACCESS GRANTED the title bar turns into EXTRACT / TURN / OVERLOAD
             (Z X C or click) for 1.2s. EXTRACT is the default.
    DONE     .finished is True and .result holds the outcome for Game.

Coordinates: mouse events reach handle_event() with `pos` already in INTERNAL pixels
(960x500), and draw() draws on the internal surface. update() gets REAL dt; Game slows
the world, not this module.

Data: data/intel.json (fragments stored as glyph clusters, doc 5.4 readability rule)
and data/misreads.json (misread table, look-alike letters, real words, blocklist).

Self-check: /usr/bin/python3 hack.py --check  runs the decoy generator over every
fragment at every Breach Level under several codex states, and exits with 1 if any
option set breaks an invariant. Add --samples to print example option sets.
"""
import math
import random
import sys

import gdev            # first: puts the code folder on sys.path and hides the pygame banner
import pygame

import hud
import settings as settings_mod

INTEL_FILE = "data/intel.json"
MISREADS_FILE = "data/misreads.json"

# Glyph table, doc 5.4 (cluster -> plain letter)
GLYPHS = {
    "3": "E", "0": "O", "1": "I", "4": "A", "5": "S", "7": "T", "@": "A", "$": "S", "|": "I",
    "|-|": "H", "/-\\": "A", "|<": "K", "|_": "L", "|2": "R", "|>": "P", "()": "O",
    "\\/": "V", "|\\/|": "M", "|=": "F", "|_|": "U", "(": "C",
}
TIER1 = set(["3", "0", "1", "4", "5", "7", "@", "$", "|"])
LETTER_TIER1 = {"E": "3", "O": "0", "I": "1", "A": "4", "S": "5", "T": "7"}

# Used only when data/misreads.json is missing (same table as the doc).
DEFAULT_MISREADS = {
    "3": [["B", 1.0]], "0": [["D", 1.0], ["Q", 0.6]], "1": [["L", 1.0], ["T", 0.8]],
    "4": [["H", 0.8], ["Y", 0.5]], "5": [["Z", 1.0]], "7": [["Y", 0.8], ["L", 0.8]],
    "@": [["O", 1.0]], "$": [["Z", 0.8]], "|": [["L", 1.0], ["T", 0.6]],
    "|-|": [["N", 1.0]], "/-\\": [["N", 1.0], ["V", 0.6]], "|<": [["IC", 1.0], ["X", 0.7]],
    "|_": [["U", 1.0]], "|2": [["IZ", 1.0], ["B", 0.6]], "|>": [["D", 1.0], ["B", 0.5]],
    "()": [["C", 0.8], ["D", 0.8]], "\\/": [["U", 1.0], ["Y", 0.6]],
    "|\\/|": [["N", 1.0], ["W", 0.8], ["IVI", 0.5]], "|=": [["E", 1.0], ["T", 0.6]],
    "|_|": [["L", 1.0], ["V", 0.7]], "(": [["G", 1.0], ["O", 0.6]],
}
DEFAULT_LOOKALIKES = ["OQD", "EF", "ILT", "UV", "MN"]

DECODE = "DECODE"
PAYLOAD = "PAYLOAD"
DONE = "DONE"
PAYLOADS = ["EXTRACT", "TURN", "OVERLOAD"]
PAYLOAD_KEYS = {pygame.K_z: "EXTRACT", pygame.K_x: "TURN", pygame.K_c: "OVERLOAD"}
PAYLOAD_HELP = {"EXTRACT": "delete it  +intel", "TURN": "it fights for you 15s",
                "OVERLOAD": "EMP: exposes minions nearby"}
OPTION_KEYS = {pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2, pygame.K_4: 3,
               pygame.K_KP1: 0, pygame.K_KP2: 1, pygame.K_KP3: 2, pygame.K_KP4: 3}

# Tuning knobs (doc 5.6)
INPUT_LOCK_S = 0.2          # all input ignored at the start of DECODE and PAYLOAD
PICK_LOCK_S = 0.15          # after every pick
HASTY_S = 0.6               # a wrong pick sooner than this after its set appeared is "hasty"
TRACE_WRONG = 35
TRACE_HASTY = 50
TRACE_HIT = 35              # player damaged while decoding
BURN_AFTER_WRONG = 2        # wrong picks on one word before it burns
REDACTED_THRESHOLD = 0.5
PAYLOAD_WINDOW_S = 1.2
GRANT_FREEZE_S = 0.15       # ACCESS GRANTED hit-freeze before the payload buttons go live
OUTRO_S = 0.7               # REDACTED / BACKTRACE stamp shown before the window closes
CONFIRM_S = 0.12            # pressed payload button stays lit this long
FLIP_S = 0.04               # split-flap: per cluster
SHORT_SET_BONUS_S = 0.5     # extra time when a word could only get a short option set
REDACT = u"████"   # "████"

# Breach Levels (doc 5.6). decoys: "far" = 2 glyphs differ, "near" = 1, "mixed" = 50/50.
# "targets" is a count or a (min, max) range rolled per packet (see target_count).
LEVELS = {
    1: {"targets": (1, 2), "length": (3, 4), "tier2": 0, "options": 3, "decoys": "far",
        "length_tells": True, "real_words": 0, "mult": 1.25, "target_worst": False},
    2: {"targets": 2, "length": (4, 5), "tier2": 0, "options": 3, "decoys": "mixed",
        "length_tells": True, "real_words": 1, "mult": 1.10, "target_worst": False},
    3: {"targets": 2, "length": (5, 6), "tier2": 1, "options": 3, "decoys": "near",
        "length_tells": False, "real_words": 1, "mult": 1.00, "target_worst": False},
    4: {"targets": 2, "length": (6, 7), "tier2": 1, "options": 4, "decoys": "near",
        "length_tells": False, "real_words": 1, "mult": 1.00, "target_worst": False},
    5: {"targets": 3, "length": (6, 8), "tier2": 1, "options": 4, "decoys": "near",
        "length_tells": False, "real_words": 2, "mult": 0.90, "target_worst": False},
    6: {"targets": 3, "length": (7, 8), "tier2": 1, "options": 4, "decoys": "near",
        "length_tells": False, "real_words": 2, "mult": 0.80, "target_worst": True},
}


def target_count(cfg, rng=None, short=False):
    """Number of target words for one packet. A (min, max) range is rolled with rng;
    short=True (the run's first hack, or EASY difficulty) always takes the minimum."""
    n = cfg["targets"]
    if not isinstance(n, (tuple, list)):
        return int(n)
    lo, hi = int(n[0]), int(n[1])
    if short or rng is None:
        return lo if short else hi
    return rng.randint(lo, hi)


def session_level(settings, kind):
    """Breach Level for one packet: the setting's base level plus the archetype offset."""
    base = settings_mod.breach_level(settings)
    if str(kind).lower() == "b1t":
        return max(1, min(3, base - 1))          # B1T: -1, clamped to L1-L3
    return max(1, min(6, base))


# ------------------------------------------------------------------ data

def load_fragments():
    """Fragments from data/intel.json (a list, or {"fragments": [...]}); [] if missing."""
    data = gdev.load_json(INTEL_FILE, [])
    if isinstance(data, dict):
        data = data.get("fragments", [])
    return data if isinstance(data, list) else []


_misread_data = None


def misread_data():
    """{"misreads": {glyph: [(reads, weight)]}, "lookalikes": [str], "real_words": set,
    "blocklist": [str]} from data/misreads.json (loaded once)."""
    global _misread_data
    if _misread_data is None:
        d = gdev.load_json(MISREADS_FILE, {}) or {}
        table = d.get("misreads") or DEFAULT_MISREADS
        _misread_data = {
            "misreads": dict((g, [(str(r).upper(), float(w)) for r, w in reads]) for g, reads in table.items()),
            "lookalikes": ["".join(g).upper() for g in (d.get("lookalikes") or DEFAULT_LOOKALIKES)],
            "real_words": set(str(w).upper() for w in d.get("real_words", [])),
            "blocklist": [str(w).upper() for w in d.get("blocklist", []) if w],
        }
    return _misread_data


def plain(clusters):
    return "".join(GLYPHS.get(c, c) for c in clusters)


def tier2_count(clusters):
    return len([c for c in clusters if c in GLYPHS and c not in TIER1])


def tier1_form(clusters):
    """The same word with every Tier 2 cluster swapped for its Tier 1 glyph or plain letter."""
    out = []
    for c in clusters:
        if c in GLYPHS and c not in TIER1:
            letter = GLYPHS[c]
            out.append(LETTER_TIER1.get(letter, letter))
        else:
            out.append(c)
    return out


def parse_fragment(frag):
    """intel.json words -> [{"c": [clusters], "target": bool, "p": punctuation, "min_level",
    "max_level"}]. A word is a plain list of clusters (not a target), or a dict
    {"c", "target", "p", "min_level", "max_level"}; the levels limit where a target
    candidate may be picked (e.g. a word with no real-word decoy in Tier 1 gets min_level 3)."""
    words = []
    for w in (frag or {}).get("words", []):
        if isinstance(w, dict):
            words.append({"c": [str(c) for c in w.get("c", [])], "target": bool(w.get("target")),
                          "p": str(w.get("p", "")), "min_level": int(w.get("min_level", 1)),
                          "max_level": int(w.get("max_level", 6))})
        else:
            words.append({"c": [str(c) for c in w], "target": False, "p": "", "min_level": 1, "max_level": 6})
    return [w for w in words if w["c"]]


def fragment_text(words, gaps=()):
    """Display text of a fragment: plain words, "████" for the word indices in gaps."""
    parts = []
    for i, w in enumerate(words):
        parts.append((REDACT if i in gaps else plain(w["c"])) + w["p"])
    return " ".join(parts)


def valid_readings(clusters, limit=64):
    """Every plain string the raw glyph text could be read as, with any segmentation.
    A decoy must never be one of these (e.g. "|_" + "|" also reads as "|_|" = U)."""
    raw = "".join(clusters)
    tokens = sorted(set(GLYPHS) | set(c for c in raw if c.isalpha()), key=len, reverse=True)
    memo = {}

    def read(i):
        if i == len(raw):
            return [""]
        if i in memo:
            return memo[i]
        out = []
        for t in tokens:
            if raw.startswith(t, i):
                head = GLYPHS.get(t, t)
                for tail in read(i + len(t)):
                    out.append(head + tail)
                    if len(out) >= limit:
                        break
        memo[i] = out
        return out

    return set(read(0))


# ------------------------------------------------------------------ decoy generator

def _decoy(letters, changes, tests, weight, kind):
    text = "".join(changes.get(i, l) for i, l in enumerate(letters))
    return {"text": text, "changes": dict(changes), "tests": list(tests), "w": weight,
            "kind": kind, "idx": set(changes)}


def candidate_decoys(clusters, cfg, codex=None):
    """All allowed decoys for a word, by kind: {"near": [...], "far": [...], "look": [...],
    "look_far": [...]}. Duplicates, the answer and other valid readings are removed."""
    md = misread_data()
    letters = [GLYPHS.get(c, c) for c in clusters]
    answer = "".join(letters)
    near, look = [], []
    for i, c in enumerate(clusters):
        boost = 1.0
        if codex is not None and c in GLYPHS:
            boost = 1.0 + 2.0 * codex.recent_misses(c)          # spaced repetition
        for reads, w in md["misreads"].get(c, []):
            if len(reads) != len(letters[i]) and not cfg["length_tells"]:
                continue
            near.append(_decoy(letters, {i: reads}, [(c, reads)], w * boost, "near"))
        for grp in md["lookalikes"]:
            if letters[i] in grp:
                for o in grp:
                    if o != letters[i]:
                        tests = [(c, o)] if c in GLYPHS else []
                        look.append(_decoy(letters, {i: o}, tests, 0.5 * boost, "look"))
    far, look_far = [], []
    for a in range(len(near)):
        for b in range(a + 1, len(near)):
            if near[a]["idx"] & near[b]["idx"]:
                continue
            ch = dict(near[a]["changes"])
            ch.update(near[b]["changes"])
            far.append(_decoy(letters, ch, near[a]["tests"] + near[b]["tests"],
                              near[a]["w"] * near[b]["w"], "far"))
        for d in look:
            if near[a]["idx"] & d["idx"]:
                continue
            ch = dict(near[a]["changes"])
            ch.update(d["changes"])
            look_far.append(_decoy(letters, ch, near[a]["tests"] + d["tests"],
                                   near[a]["w"] * d["w"], "look_far"))
    banned = valid_readings(clusters) | set([answer])
    seen = {}
    pools = {"near": near, "far": far, "look": look, "look_far": look_far}
    out = {}
    for kind in ("near", "far", "look", "look_far"):        # earlier kinds win a duplicate text
        keep = []
        for d in pools[kind]:
            t = d["text"]
            if t in banned or t in seen or _blocked(t):
                continue
            if not cfg["length_tells"] and len(t) != len(answer):
                continue
            d["real"] = t in md["real_words"]
            seen[t] = d
            keep.append(d)
        keep.sort(key=lambda d: d["text"])                  # deterministic order
        out[kind] = keep
    return out


def _blocked(text):
    for b in misread_data()["blocklist"]:
        if b and b in text:
            return True
    return False


def _pick_weighted(cands, rng, used_idx, real_bonus):
    weights = []
    for d in cands:
        w = d["w"]
        if d["idx"] & used_idx:
            w *= 0.3                     # prefer decoys that test a different glyph
        if real_bonus and d.get("real"):
            w *= 3.0                     # real words look right: harder
        weights.append(max(w, 0.0001))
    r = rng.random() * sum(weights)
    for d, w in zip(cands, weights):
        r -= w
        if r <= 0:
            return d
    return cands[-1]


def build_option_set(clusters, level, codex=None, rng=None):
    """make_options() plus notes: returns (options, info).
    options: [{"text", "correct", "tests": [(glyph, reads_as)], "real", "kind"}], shuffled.
    info: {"weak": no real-word decoy where one is required, "short": fewer options than
    the level wants, "rebuilt": the set failed its invariants and was rebuilt}."""
    cfg = LEVELS[max(1, min(6, level))]
    rng = rng or random.Random(0)
    answer = plain(clusters)
    need = cfg["options"] - 1
    pools = candidate_decoys(clusters, cfg, codex)
    mode = cfg["decoys"]
    if mode == "far":
        order = ["far", "near", "look_far", "look"]
    elif mode == "near":
        order = ["near", "look", "far", "look_far"]
    else:
        order = ["near", "far", "look", "look_far"]
    chosen = []
    used = set()
    taken = set()
    while len(chosen) < need:
        avail = dict((k, [d for d in pools[k] if d["text"] not in taken]) for k in order)
        pool = None
        if mode == "mixed" and avail["near"] and avail["far"]:
            pool = avail["near"] if rng.random() < 0.5 else avail["far"]
        else:
            for k in order:
                if avail[k]:
                    pool = avail[k]
                    break
        if not pool:
            break
        d = _pick_weighted(pool, rng, used, cfg["real_words"] > 0)
        chosen.append(d)
        taken.add(d["text"])
        used |= d["idx"]

    # Real-word rule (L2+): force real words in from any allowed pool if none were drawn.
    if cfg["real_words"] > 0:
        allowed = [k for k in order if not (mode == "near" and k in ("far", "look_far"))]
        while len([d for d in chosen if d["real"]]) < cfg["real_words"]:
            extra = [d for k in allowed for d in pools[k] if d["real"] and d["text"] not in taken]
            if not extra:
                break
            d = _pick_weighted(extra, rng, used, False)
            fakes = [i for i, x in enumerate(chosen) if not x["real"]]
            if len(chosen) < need:
                chosen.append(d)
            elif fakes:
                taken.discard(chosen[fakes[-1]]["text"])
                chosen[fakes[-1]] = d
            else:
                break
            taken.add(d["text"])
            used |= d["idx"]

    # L6: one decoy aims at the player's worst glyph in this word.
    if cfg["target_worst"] and codex is not None and chosen:
        worst = codex.worst([c for c in clusters if c in GLYPHS])
        if worst and not any(worst in [g for g, _r in d["tests"]] for d in chosen):
            aimed = [d for k in order for d in pools[k]
                     if d["text"] not in taken and worst in [g for g, _r in d["tests"]]]
            if aimed:
                victim = len(chosen) - 1
                for i, x in enumerate(chosen):
                    if not x["real"]:
                        victim = i
                taken.discard(chosen[victim]["text"])
                chosen[victim] = aimed[0]
                taken.add(aimed[0]["text"])

    info = {"weak": cfg["real_words"] > 0 and not any(d["real"] for d in chosen),
            "short": len(chosen) < need, "rebuilt": False}
    if not chosen:
        chosen = [_emergency_decoy(clusters)]
        info["rebuilt"] = True
    options = [{"text": answer, "correct": True, "tests": [], "real": True, "kind": "answer"}]
    for d in chosen:
        options.append({"text": d["text"], "correct": False, "tests": d["tests"],
                        "real": d["real"], "kind": d["kind"], "changes": d["changes"]})
    rng.shuffle(options)
    errors = check_options(options, clusters, cfg)
    if errors:
        print("[hack] bad option set for %s: %s -> rebuilt" % (answer, "; ".join(errors)))
        options = _rebuild_lookalike(clusters, cfg, rng)
        info["rebuilt"] = True
    return options, info


def make_options(clusters, level, codex=None, rng=None):
    """Option set for one target word (doc 5.5): the answer plus 1-3 decoys, shuffled."""
    return build_option_set(clusters, level, codex, rng)[0]


def _emergency_decoy(clusters):
    # Last resort for a word with no misreadable letter: change the first letter.
    letters = [GLYPHS.get(c, c) for c in clusters]
    first = letters[0][0] if letters and letters[0] else "A"
    swap = "B" if first != "B" else "D"
    d = _decoy(letters, {0: swap + letters[0][1:]}, [], 1.0, "look")
    d["real"] = False
    return d


def _rebuild_lookalike(clusters, cfg, rng):
    answer = plain(clusters)
    pools = candidate_decoys(clusters, dict(cfg, length_tells=False))
    picks = (pools["look"] or pools["near"])[:cfg["options"] - 1]
    if not picks:
        picks = [_emergency_decoy(clusters)]
    options = [{"text": answer, "correct": True, "tests": [], "real": True, "kind": "answer"}]
    for d in picks:
        options.append({"text": d["text"], "correct": False, "tests": d["tests"],
                        "real": d.get("real", False), "kind": d["kind"], "changes": d["changes"]})
    rng.shuffle(options)
    return options


def check_options(options, clusters, cfg=None):
    """Runtime invariants (doc 5.5). Returns a list of problems ([] when the set is fine)."""
    errors = []
    answer = plain(clusters)
    texts = [o["text"] for o in options]
    correct = [o for o in options if o["correct"]]
    if len(correct) != 1 or correct[0]["text"] != answer:
        errors.append("needs exactly one correct option")
    if len(set(texts)) != len(texts):
        errors.append("duplicate options")
    if len(options) < 2 or (cfg is not None and len(options) > cfg["options"]):
        errors.append("bad option count %d" % len(options))
    readings = valid_readings(clusters)
    for o in options:
        if o["correct"]:
            continue
        if o["text"] == answer or o["text"] in readings:
            errors.append("decoy %s is a valid reading" % o["text"])
        if _blocked(o["text"]):
            errors.append("decoy %s is blocklisted" % o["text"])
        if cfg is not None and not cfg["length_tells"] and len(o["text"]) != len(answer):
            errors.append("decoy %s gives a length tell" % o["text"])
    return errors


def callout(option, clusters):
    """The misread a wrong pick made, e.g. "|2 = R, not IZ"."""
    parts = []
    for i in sorted(option.get("changes", {})):
        c = clusters[i]
        reads = option["changes"][i]
        if c in GLYPHS:
            part = "%s = %s, not %s" % (c, GLYPHS[c], reads)
        else:
            part = "%s, not %s" % (c, reads)
        if part not in parts:          # N33D read as NBBD names "3 = E, not B" once
            parts.append(part)
    return "   ".join(parts) or "WRONG READING"


# ------------------------------------------------------------------ fragments -> packets

def choose_targets(words, cfg, rng, level=None, count=None):
    """Indices of the words to decode: candidates closest to the level's word length.
    count = how many (default: the level's maximum)."""
    if count is None:
        count = target_count(cfg)
    cands = [i for i, w in enumerate(words) if w["target"]]
    if level is not None:
        fits = [i for i in cands if words[i]["min_level"] <= level <= words[i]["max_level"]]
        if len(fits) >= count:
            cands = fits
    if not cands:      # unmarked fragment: any word with a glyph in it
        cands = [i for i, w in enumerate(words)
                 if len(plain(w["c"])) >= 3 and any(c in GLYPHS for c in w["c"])]
    lo, hi = cfg["length"]

    def dist(i):
        n = len(plain(words[i]["c"]))
        return lo - n if n < lo else (n - hi if n > hi else 0)

    keyed = [(dist(i), rng.random(), i) for i in cands]
    keyed.sort()
    return sorted(i for _d, _r, i in keyed[:count])


def display_clusters(word, is_target, cfg):
    """Clusters as shown this session: targets keep up to cfg["tier2"] Tier 2 clusters,
    every other word is Tier 1 only (doc 5.4)."""
    if not is_target or cfg["tier2"] <= 0:
        return tier1_form(word["c"])
    out, n = [], 0
    for c in word["c"]:
        if c in GLYPHS and c not in TIER1:
            n += 1
            if n > cfg["tier2"]:
                letter = GLYPHS[c]
                c = LETTER_TIER1.get(letter, letter)
        out.append(c)
    return out


FALLBACK_FRAGMENT = {"id": "NOISE", "title": "UNSORTED PACKET", "tier": "CHATTER",
                     "words": [["7", "H", "3"], {"c": ["R", "4", "C", "K", "5"], "target": True},
                               ["4", "R", "3"], {"c": ["L", "0", "U", "D"], "target": True, "p": "."}]}


# ------------------------------------------------------------------ codex & intel log

class Codex(object):
    """Per-glyph reading record.
      hits {glyph: int}, misses {glyph: int}, misread_history [(glyph, read_as)]
      record_hit(glyph), record_miss(glyph, read_as), recent_misses(glyph) -> int
      rows() -> [(glyph, letter, hits, misses)] for every glyph tested so far
    """
    def __init__(self):
        self.hits = {}
        self.misses = {}
        self.misread_history = []

    def record_hit(self, glyph):
        self.hits[glyph] = self.hits.get(glyph, 0) + 1

    def record_miss(self, glyph, read_as=None):
        self.misses[glyph] = self.misses.get(glyph, 0) + 1
        self.misread_history.append((glyph, read_as))

    def recent_misses(self, glyph):
        return sum(1 for g, _r in self.misread_history[-20:] if g == glyph)

    def worst(self, glyphs=None):
        """The glyph (optionally among `glyphs`) with the most misses over hits, or None."""
        best, score = None, 0
        for g in sorted(glyphs if glyphs is not None else self.misses):
            s = self.misses.get(g, 0) - self.hits.get(g, 0) * 0.5
            if self.misses.get(g, 0) > 0 and s > score:
                best, score = g, s
        return best

    def rows(self):
        keys = set(self.hits) | set(self.misses)
        out = [(g, GLYPHS.get(g, "?"), self.hits.get(g, 0), self.misses.get(g, 0)) for g in keys]
        out.sort(key=lambda r: (-(r[2] + r[3]), r[0]))
        return out


class IntelLog(object):
    """The run's fragment database (from intel.json) plus what has been decoded.
      fragments [dict]            raw intel.json entries
      entries [dict]              decoded log, in order found. Each entry:
          {"id", "source", "tier", "title", "text", "redacted": bool, "gaps": [word index]}
          "text" is display-ready plain text with "████" for undecoded words.
      record(result)              store a finished session's result (success/redacted);
                                  HackSession calls this itself -- Game never does.
      count() -> int              number of entries
      has(fragment_id) -> bool
      pick_fragment(minion, rng) -> dict|None   next fragment for a packet
    """
    def __init__(self, fragments=None):
        self.fragments = list(fragments if fragments is not None else load_fragments())
        self.entries = []
        self.served = {}            # fragment id -> times used for a packet (seeds the decoys)

    def count(self):
        return len(self.entries)

    def has(self, fragment_id):
        return self.entry(fragment_id) is not None

    def entry(self, fragment_id):
        for e in self.entries:
            if e["id"] == fragment_id:
                return e
        return None

    def get(self, fragment_id):
        for f in self.fragments:
            if f.get("id") == fragment_id:
                return f
        return None

    def pick_fragment(self, minion, rng):
        """REDACTED fragments of this archetype first (a later hack completes them), then
        fragments not found yet, then the least used one."""
        kind = str(getattr(minion, "kind", "b1t")).upper()
        frags = sorted(self.fragments, key=lambda f: str(f.get("id")))
        mine = [f for f in frags if kind in [str(s).upper() for s in f.get("sources", [kind])]] or frags
        if not mine:
            return None
        redacted = [f for f in mine if (self.entry(f.get("id")) or {}).get("redacted")]
        if redacted:
            return redacted[0]
        fresh = [f for f in mine if not self.has(f.get("id"))]
        if fresh:
            return rng.choice(fresh)
        least = min(self.served.get(f.get("id"), 0) for f in mine)
        return rng.choice([f for f in mine if self.served.get(f.get("id"), 0) == least])

    def record(self, result):
        fid = result.get("fragment_id")
        if result.get("outcome") not in ("success", "redacted") or not fid:
            return
        frag = self.get(fid) or {}
        gaps = set(result.get("gaps", []))
        if result["outcome"] == "success":
            gaps = set()
        old = self.entry(fid)
        if old is not None:
            if not old["redacted"]:
                return
            gaps &= set(old.get("gaps", []))        # merge: a word read in either hack is known
        words = parse_fragment(frag)
        text = fragment_text(words, gaps) if words else result.get("decoded_text", "")
        entry = {"id": fid, "source": result.get("source") or "B1T",
                 "tier": frag.get("tier", "CHATTER"), "title": frag.get("title", fid),
                 "author": frag.get("author", ""), "act": frag.get("act", ""),
                 "text": text, "redacted": bool(gaps), "gaps": sorted(gaps)}
        if old is not None:
            self.entries[self.entries.index(old)] = entry
        else:
            self.entries.append(entry)


# ------------------------------------------------------------------ the session

def _body_rect(sx, sy, k=1):
    """Screen area of a 32x32-canvas sprite (with its bars) whose position is (sx, sy),
    drawn k times bigger by the camera zoom."""
    return pygame.Rect(sx - 12 * k, sy - 26 * k, 24 * k, 34 * k)


TITLE_H = 26
PAD = 14
LINE_H = 34
BOX_H = 28
BTN_H = 38
WIN_W = 860                 # wide and short, so the rooted player below stays visible
SIZE_TARGET = 24            # doc 5.4: target glyphs and option text >= 24px internal
SIZE_WORD = 18
SCRAMBLE = "#%&@$!?/\\|<>=+*01"


class HackSession(object):
    """One jack-in. Public surface:
      HackSession(minion, settings, codex, intel_db, rng)   intel_db is an IntelLog
      phase DECODE|PAYLOAD|DONE, trace 0..100, time_left, time_total (real seconds)
      minion, finished bool, result dict (valid once finished):
          {"outcome": "success"|"redacted"|"backtrace"|"abort", "fragment_id": str|None,
           "decoded_text": str, "clean": bool, "payload": "EXTRACT"|"TURN"|"OVERLOAD"|None,
           plus info keys: "gaps", "level", "wrong_picks", "words", "decoded", "source", "reason"}
      handle_event(e)            KEYDOWN 1-3 / Z X C / Esc, MOUSEBUTTONDOWN (LMB pick, RMB abort),
                                 MOUSEMOTION (hover). Positions are internal pixels.
      update(real_dt)
      draw(surface, anchor=None, avoid=())  anchor = minion's internal-surface position (window
                                 is placed above it, clamped into hud.SAFE_RECT, or below it when
                                 that would cover the anchor or an `avoid` point); None = centred
      on_player_damaged()        +35 TRACE (Game calls it when the rooted player is hit)
      abort(reason="abort")      end now (target died, player died...)
    Test helpers: correct_slot(), wrong_slot(), slot_center(slot), payload_center(name),
                  target_words() -> [plain words to decode].
    """

    def __init__(self, minion, settings, codex, intel_db, rng):
        self.body_scale = 1          # camera zoom; Game sets it so the window keeps sprites clear
        self.minion = minion
        self.settings = settings
        self.codex = codex
        self.intel = intel_db
        self.rng = rng
        self.kind = str(getattr(minion, "kind", "b1t")).lower()
        self.level = session_level(settings, self.kind)
        self.cfg = LEVELS[self.level]

        self.fragment = intel_db.pick_fragment(minion, rng) if intel_db is not None else None
        frag = self.fragment or FALLBACK_FRAGMENT
        self.fid = str(frag.get("id", "NOISE"))
        if intel_db is not None:
            self.attempt = intel_db.served.get(self.fid, 0)
            intel_db.served[self.fid] = self.attempt + 1
        else:
            self.attempt = 0
        self.words = parse_fragment(frag) or parse_fragment(FALLBACK_FRAGMENT)
        pick_rng = random.Random("%s|%d|targets|%d" % (self.fid, self.attempt, self.level))
        first_hack = intel_db is not None and intel_db.count() == 0
        easy = settings.get("hack_difficulty") == "EASY"
        n = target_count(self.cfg, pick_rng, short=first_hack or easy)
        self.targets = choose_targets(self.words, self.cfg, pick_rng, self.level, n)
        tset = set(self.targets)
        self.shown = [display_clusters(w, i in tset, self.cfg) for i, w in enumerate(self.words)]
        self.word_state = dict((i, "pending") for i in self.targets)   # pending/decoded/burned
        self.ti = 0

        tier2 = sum(tier2_count(self.shown[i]) for i in self.targets)
        self.time_total = ((1.5 + 2.0 * len(self.targets) + 0.3 * tier2) * self.cfg["mult"]
                           * settings_mod.timer_mult(settings))
        self.time_left = self.time_total

        self.phase = DECODE
        self.phase_age = 0.0
        self.age = 0.0
        self.trace = 0.0
        self.wrong_picks = 0
        self.finished = False
        self.closing = None         # seconds until finished, once the outcome is decided
        self.outro = None           # "success" / "redacted" / "backtrace" stamp while closing
        self.payload_t = PAYLOAD_WINDOW_S
        self.chosen_payload = None
        self.result = {"outcome": None, "fragment_id": self.fragment.get("id") if self.fragment else None,
                       "decoded_text": "", "clean": True, "payload": None, "gaps": [],
                       "level": self.level, "wrong_picks": 0, "words": len(self.targets),
                       "decoded": 0, "source": self.kind.upper(), "reason": None}

        # option buttons: slots[i] = {"opt": option, "state": "live"|"struck"|"correct"} or None
        self.slots = []
        self.set_age = 0.0
        self.word_wrong = 0
        self.word_missed = set()
        self.pick_lock = 0.0
        self.advance_pending = False
        self.callout = None         # [text, color, seconds left]
        self.flip = {}              # word index -> seconds since it was decoded
        self.press = {}             # slot -> press feedback seconds
        self.shake = {}             # slot -> wrong-pick shake seconds
        self.trace_pulse = 0.0
        self.mouse = (-1000, -1000)
        self.fx = random.Random()   # visual noise only, never gameplay
        self.slot_rects = []
        self.payload_rects = []
        self.below = False          # window placed under the target (no room above it)
        self._layout()
        self.window.center = hud.SAFE_RECT.center
        self._place(None)
        self._new_option_set()

    # -------------------------------------------------------------- helpers for tests

    def target_words(self):
        return [plain(self.shown[i]) for i in self.targets]

    def active_word(self):
        """Word index being decoded, or None."""
        if self.phase != DECODE or self.ti >= len(self.targets) or self.closing is not None:
            return None
        return self.targets[self.ti]

    def correct_slot(self):
        for i, s in enumerate(self.slots):
            if s and s["opt"]["correct"] and s["state"] == "live":
                return i
        return None

    def wrong_slot(self):
        for i, s in enumerate(self.slots):
            if s and not s["opt"]["correct"] and s["state"] == "live":
                return i
        return None

    def slot_center(self, slot):
        return self.slot_rects[slot].center

    def payload_center(self, name):
        for r, n in self.payload_rects:
            if n == name:
                return r.center
        return None

    def input_ready(self):
        """True when a pick or payload key would be accepted right now."""
        if self.finished or self.closing is not None:
            return False
        if self.phase == DECODE:
            return self.phase_age >= INPUT_LOCK_S and self.pick_lock <= 0
        return self.phase == PAYLOAD and self.phase_age >= GRANT_FREEZE_S + INPUT_LOCK_S

    # -------------------------------------------------------------- input

    def handle_event(self, e):
        if self.finished:
            return
        if e.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN):
            self.mouse = e.pos
        if e.type == pygame.MOUSEMOTION or self.closing is not None:
            return
        if self.phase == DECODE:
            if self.phase_age < INPUT_LOCK_S:
                return                                   # input_lock_s: everything ignored
            if (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE) or \
               (e.type == pygame.MOUSEBUTTONDOWN and e.button == 3):
                self.abort("player")
                return
            if self.pick_lock > 0:
                return
            slot = None
            if e.type == pygame.KEYDOWN and e.key in OPTION_KEYS:
                slot = OPTION_KEYS[e.key]
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                for i, r in enumerate(self.slot_rects):
                    if r.collidepoint(e.pos):
                        slot = i
            if slot is not None:
                self._pick(slot)
        elif self.phase == PAYLOAD:
            if self.phase_age < GRANT_FREEZE_S + INPUT_LOCK_S:
                return
            if e.type == pygame.KEYDOWN and e.key in PAYLOAD_KEYS:
                self._choose_payload(PAYLOAD_KEYS[e.key])
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                for rect, name in self.payload_rects:
                    if rect.collidepoint(e.pos):
                        self._choose_payload(name)

    def on_player_damaged(self):
        self.trace = min(100.0, self.trace + TRACE_HIT)
        self.trace_pulse = 0.4
        if self.phase == DECODE and self.closing is None:
            self.callout = ["HIT WHILE DECODING  +%d TRACE" % TRACE_HIT, gdev.RED, 1.4]
            if self.trace >= 100:
                self._decide("backtrace")

    def abort(self, reason="abort"):
        if self.finished:
            return
        if self.closing is not None or self.phase == PAYLOAD:
            self._close()                    # outcome already decided; keep it
            return
        self.result.update({"outcome": "abort", "clean": False, "payload": None, "reason": reason})
        self._close()

    # -------------------------------------------------------------- decode logic

    def _new_option_set(self):
        wi = self.targets[self.ti]
        rng = random.Random("%s|%d|%d" % (self.fid, wi, self.attempt))
        opts, info = build_option_set(self.shown[wi], self.level, self.codex, rng)
        if info["short"]:
            self.time_left += SHORT_SET_BONUS_S
            self.time_total += SHORT_SET_BONUS_S
        self.slots = [None] * self.cfg["options"]
        for i, o in enumerate(opts[:len(self.slots)]):
            self.slots[i] = {"opt": o, "state": "live"}
        self.shuffle_rng = rng
        self.set_age = 0.0
        self.word_wrong = 0
        self.word_missed = set()

    def _pick(self, slot):
        if slot >= len(self.slots) or self.slots[slot] is None or self.slots[slot]["state"] != "live":
            return                                   # empty or struck slot: no pick, no TRACE
        s = self.slots[slot]
        opt = s["opt"]
        wi = self.targets[self.ti]
        self.press[slot] = 0.12
        self.pick_lock = PICK_LOCK_S
        if opt["correct"]:
            s["state"] = "correct"
            tested = set()
            for other in self.slots:
                if other and not other["opt"]["correct"]:
                    tested |= set(g for g, _r in other["opt"]["tests"])
            if self.codex is not None:
                for g in sorted(tested - self.word_missed):
                    self.codex.record_hit(g)
            self.word_state[wi] = "decoded"
            self.flip[wi] = 0.0
            self.advance_pending = True
            return
        hasty = self.set_age < HASTY_S
        self.trace = min(100.0, self.trace + (TRACE_HASTY if hasty else TRACE_WRONG))
        self.trace_pulse = 0.4
        self.wrong_picks += 1
        self.word_wrong += 1
        s["state"] = "struck"
        self.shake[slot] = 0.25
        for g, reads in opt["tests"]:
            self.word_missed.add(g)
            if self.codex is not None:
                self.codex.record_miss(g, reads)
        text = callout(opt, self.shown[wi])
        if hasty:
            text += "   HASTY +%d" % TRACE_HASTY
        self.callout = [text, gdev.AMBER, 2.2]
        if self.trace >= 100:
            self._decide("backtrace")
            return
        if self.word_wrong >= BURN_AFTER_WRONG:
            self.word_state[wi] = "burned"
            self.callout = [text + "   WORD BURNED", gdev.RED, 2.2]
            self.advance_pending = True
            return
        if not self.settings.get("fixed_button_positions"):
            self._reshuffle()

    def _reshuffle(self):
        # Live buttons swap among their own slots; struck ones keep their slot.
        live = [i for i, s in enumerate(self.slots) if s and s["state"] == "live"]
        opts = [self.slots[i]["opt"] for i in live]
        self.shuffle_rng.shuffle(opts)
        for i, o in zip(live, opts):
            self.slots[i] = {"opt": o, "state": "live"}

    def _advance(self):
        self.ti += 1
        if self.ti < len(self.targets):
            self._new_option_set()
            return
        decoded = len([i for i in self.targets if self.word_state[i] == "decoded"])
        if decoded == len(self.targets):
            self._grant()
        else:
            self._decide(self._partial_outcome())

    def _partial_outcome(self):
        decoded = len([i for i in self.targets if self.word_state[i] == "decoded"])
        n = len(self.targets)
        if n >= 2 and decoded >= REDACTED_THRESHOLD * n:
            return "redacted"
        return "backtrace"

    def _fill_result(self, outcome):
        gaps = [i for i in self.targets if self.word_state.get(i) != "decoded"]
        if outcome == "success":
            gaps = []
        self.result.update({
            "outcome": outcome, "gaps": gaps, "wrong_picks": self.wrong_picks,
            "decoded": len(self.targets) - len(gaps) if outcome != "success" else len(self.targets),
            "decoded_text": fragment_text(self.words, set(gaps)),
            "clean": outcome == "success" and self.wrong_picks == 0,
        })

    def _grant(self):
        self._fill_result("success")
        if self.intel is not None and self.fragment is not None:
            self.intel.record(self.result)
        self.phase = PAYLOAD
        self.phase_age = 0.0
        self.payload_t = PAYLOAD_WINDOW_S
        self.slots = []
        self.callout = None

    def _decide(self, outcome):
        """REDACTED or BACKTRACE: save what was decoded, show the stamp, then close."""
        self._fill_result(outcome)
        self.result["payload"] = None
        if outcome == "redacted" and self.intel is not None and self.fragment is not None:
            self.intel.record(self.result)
        self.outro = outcome
        self.closing = OUTRO_S

    def _choose_payload(self, name):
        if self.chosen_payload is not None:
            return
        self.chosen_payload = name
        self.result["payload"] = name
        self.outro = "success"
        self.closing = CONFIRM_S

    def _close(self):
        self.phase = DONE
        self.finished = True
        self.closing = None

    # -------------------------------------------------------------- flow

    def update(self, real_dt):
        if self.finished:
            return
        self.age += real_dt
        self.phase_age += real_dt
        for d in (self.press, self.shake):
            for k in list(d):
                d[k] -= real_dt
                if d[k] <= 0:
                    del d[k]
        for k in self.flip:
            self.flip[k] += real_dt
        self.trace_pulse = max(0.0, self.trace_pulse - real_dt)
        if self.callout is not None:
            self.callout[2] -= real_dt
            if self.callout[2] <= 0:
                self.callout = None
        if self.closing is not None:
            self.closing -= real_dt
            if self.closing <= 0:
                self._close()
            return
        if self.phase == DECODE:
            if self.phase_age >= INPUT_LOCK_S:
                # The hasty window counts from when the set can be picked (input unlocked).
                self.set_age += real_dt
                # A word picked or burned is settling (pick_lock): the clock stops, so a
                # correct last pick with <0.15s left still wins.
                if not self.advance_pending:
                    self.time_left -= real_dt
            if self.pick_lock > 0:
                self.pick_lock -= real_dt
                if self.pick_lock <= 0 and self.advance_pending:
                    self.advance_pending = False
                    self._advance()
                    return
            if self.time_left <= 0:
                self.time_left = 0.0
                if all(self.word_state[i] == "decoded" for i in self.targets):
                    self._grant()                   # every word read: that is a success
                else:
                    self._decide(self._partial_outcome())
        elif self.phase == PAYLOAD:
            if self.phase_age >= GRANT_FREEZE_S + INPUT_LOCK_S:
                self.payload_t -= real_dt
                if self.payload_t <= 0:
                    self._choose_payload("EXTRACT")

    # -------------------------------------------------------------- layout

    def _cluster_w(self, c, size, bold):
        w = gdev.text_size(c, size, bold)[0]
        return w + (8 if c in GLYPHS else 0)        # glyph clusters get a box

    def _layout(self):
        """Word and cluster rects relative to the window's top-left, and the window size."""
        tset = set(self.targets)
        inner = WIN_W - 2 * PAD
        self.word_boxes = []        # per word: (Rect word, [Rect cluster], punct_x)
        x, line = 0, 0
        for i, w in enumerate(self.words):
            target = i in tset
            size = SIZE_TARGET if target else SIZE_WORD
            br = 10 if target else 0          # room for the active-word brackets
            widths = [self._cluster_w(c, size, target) for c in self.shown[i]]
            pw = gdev.text_size(w["p"], SIZE_WORD)[0] if w["p"] else 0
            ww = sum(widths) + 2 * (len(widths) - 1) + 2 * br
            if x > 0 and x + ww + pw > inner:
                x, line = 0, line + 1
            y = TITLE_H + 8 + line * LINE_H
            rects = []
            cx = PAD + x + br
            for cw in widths:
                rects.append(pygame.Rect(cx, y, cw, BOX_H))
                cx += cw + 2
            self.word_boxes.append((pygame.Rect(PAD + x, y, ww, BOX_H), rects, PAD + x + ww))
            x += ww + pw + 12
        self.lines = line + 1
        self.callout_y = TITLE_H + 8 + self.lines * LINE_H + 2
        self.button_y = self.callout_y + 20
        self.timer_y = self.button_y + BTN_H + 8
        self.window = pygame.Rect(0, 0, WIN_W, self.timer_y + 10 + 8)

    def _place(self, anchor, avoid=()):
        """Window above the anchor (the target), clamped into hud.SAFE_RECT. If that would
        cover the target or a point in `avoid` (the player) and there is room under them,
        the window goes below instead. Game aims the camera so 'above' normally fits."""
        if anchor is not None:
            ax, ay = int(anchor[0]), int(anchor[1])
            k = self.body_scale
            bodies = [_body_rect(ax, ay, k)] + [_body_rect(int(x), int(y), k) for x, y in avoid]
            above = self.window.copy()
            above.midbottom = (ax, min(b.top for b in bodies) - 8)
            above.clamp_ip(hud.SAFE_RECT)
            below = self.window.copy()
            below.midtop = (ax, max(b.bottom for b in bodies) + 8)
            below.clamp_ip(hud.SAFE_RECT)
            above_ok = above.collidelist(bodies) == -1
            below_ok = below.collidelist(bodies) == -1
            if self.below and not below_ok and above_ok:
                self.below = False
            elif not self.below and not above_ok and below_ok:
                self.below = True
            self.window = below if self.below else above
        else:
            self.window.clamp_ip(hud.SAFE_RECT)
        r = self.window
        n = self.cfg["options"]
        gap = 12
        bw = (r.w - 2 * PAD - gap * (n - 1)) // n
        self.slot_rects = [pygame.Rect(r.x + PAD + i * (bw + gap), r.y + self.button_y, bw, BTN_H)
                           for i in range(n)]
        pw = (r.w - 16) // 3
        self.payload_rects = [(pygame.Rect(r.x + 4 + i * (pw + 4), r.y + 2, pw, TITLE_H - 4), name)
                              for i, name in enumerate(PAYLOADS)]

    # -------------------------------------------------------------- drawing

    def draw(self, surface, anchor=None, avoid=()):
        self._place(anchor, avoid)
        r = self.window
        opening = min(1.0, self.age / 0.12)
        if opening < 1.0:                     # CRT power-on: the window unfolds from a line
            h = max(2, int(r.h * opening))
            gdev.panel(surface, (r.x, r.centery - h // 2, r.w, h), alpha=217, border=gdev.CYAN)
            return
        border = gdev.GREEN
        if self.trace_pulse > 0 or self.outro == "backtrace":
            border = gdev.RED
        elif self.phase == PAYLOAD:
            border = gdev.CYAN
        gdev.panel(surface, r, alpha=217, border=border)
        self._draw_title(surface, r)
        self._draw_sentence(surface, r)
        if self.phase == DECODE:
            self._draw_callout_row(surface, r)
            self._draw_buttons(surface)
            self._draw_timer(surface, r)
        else:
            self._draw_payload_help(surface, r)
        gdev.draw_scanlines(surface, 26, r)
        self._draw_stamp(surface, r)
        if self.outro == "backtrace":
            self._draw_tear(surface, r)

    def _draw_title(self, surface, r):
        title = pygame.Rect(r.x + 1, r.y + 1, r.w - 2, TITLE_H - 1)
        pygame.draw.rect(surface, gdev.GREEN_DARK, title)
        if self.phase == PAYLOAD:
            live = self.phase_age >= GRANT_FREEZE_S + INPUT_LOCK_S
            for i, (br, name) in enumerate(self.payload_rects):
                hover = live and br.collidepoint(self.mouse)
                picked = self.chosen_payload == name
                default = name == "EXTRACT" and self.chosen_payload is None
                if picked or hover:
                    pygame.draw.rect(surface, gdev.CYAN, br)
                    col = gdev.BG
                else:
                    pygame.draw.rect(surface, (6, 40, 46), br)
                    pygame.draw.rect(surface, gdev.CYAN if default else gdev.GREEN_DIM, br, 2 if default else 1)
                    col = gdev.CYAN if live else gdev.GREEN_DIM
                gdev.text(surface, "[%s] %s" % ("ZXC"[i], name), br.center, 14, col, "center", True)
            if live and self.chosen_payload is None:   # 1.2s window draining under the buttons
                frac = max(0.0, self.payload_t / PAYLOAD_WINDOW_S)
                pygame.draw.rect(surface, gdev.CYAN, (r.x + 4, r.y + TITLE_H - 2, int((r.w - 8) * frac), 2))
            return
        gdev.text(surface, "JACK-IN :: %s" % getattr(self.minion, "label", "MINION"), (r.x + 10, r.y + 6),
                  13, gdev.GREEN, bold=True)
        gdev.text(surface, "BREACH L%d" % self.level, (r.x + 210, r.y + 6), 13, gdev.GREEN_MID)
        # [ TRACE ####...... ]
        segs = 10
        filled = int(math.ceil(self.trace / 100.0 * segs - 0.001))
        col = gdev.RED if self.trace_pulse > 0 and int(self.age * 16) % 2 == 0 else gdev.MAGENTA
        pct = gdev.text(surface, "%d%%" % int(self.trace), (r.right - 10, r.y + 6), 13, col, "topright", True)
        x = pct.left - 8
        tr = gdev.text(surface, "]", (x, r.y + 6), 13, col, "topright", True)
        x = tr.left - 4
        for s in range(segs - 1, -1, -1):
            cell = pygame.Rect(x - 8, r.y + 8, 7, 10)
            if s < filled:
                pygame.draw.rect(surface, col, cell)
            else:
                pygame.draw.rect(surface, (70, 20, 55), cell, 1)
            x -= 9
        gdev.text(surface, "[ TRACE", (x - 4, r.y + 6), 13, col, "topright", True)

    def _draw_sentence(self, surface, r):
        active = self.active_word()
        tset = set(self.targets)
        for i, w in enumerate(self.words):
            wrect, rects, px = self.word_boxes[i]
            wrect = wrect.move(r.x, r.y)
            state = self.word_state.get(i) if i in tset else None
            if state is None:
                for c, cr in zip(self.shown[i], rects):
                    cr = cr.move(r.x, r.y)
                    if c in GLYPHS:
                        pygame.draw.rect(surface, gdev.GREEN_DIM, cr.inflate(0, -6), 1)
                    gdev.text(surface, c, cr.center, SIZE_WORD, gdev.GREEN_MID, "center")
            elif state == "burned":
                pygame.draw.rect(surface, (120, 30, 100), wrect.inflate(-16, -6))
            elif state == "decoded":
                t = self.flip.get(i, 9.0)
                if t >= len(rects) * FLIP_S + 0.07:      # settled: tight plain word
                    gdev.text(surface, plain(self.shown[i]), wrect.center, SIZE_TARGET, gdev.CYAN, "center", True)
                    pygame.draw.line(surface, gdev.CYAN, (wrect.x + 10, wrect.bottom),
                                     (wrect.right - 10, wrect.bottom))
                    if w["p"]:
                        gdev.text(surface, w["p"], (r.x + px, wrect.centery), SIZE_WORD, gdev.GREEN_MID, "midleft")
                    continue
                for k, (c, cr) in enumerate(zip(self.shown[i], rects)):
                    cr = cr.move(r.x, r.y)
                    tk = t - k * FLIP_S
                    if tk < 0:
                        pygame.draw.rect(surface, (60, 14, 48), cr.inflate(0, -2))
                        gdev.text(surface, c, cr.center, SIZE_TARGET, gdev.MAGENTA, "center", True)
                    elif tk < 0.07:
                        ch = SCRAMBLE[self.fx.randrange(len(SCRAMBLE))]
                        pygame.draw.rect(surface, gdev.WHITE, cr.inflate(0, -2), 1)
                        gdev.text(surface, ch, cr.center, SIZE_TARGET, gdev.WHITE, "center", True)
                    else:
                        gdev.text(surface, GLYPHS.get(c, c), cr.center, SIZE_TARGET, gdev.CYAN, "center", True)
                pygame.draw.line(surface, gdev.CYAN, (wrect.x + 10, wrect.bottom), (wrect.right - 10, wrect.bottom))
            else:
                hot = i == active
                for c, cr in zip(self.shown[i], rects):
                    cr = cr.move(r.x, r.y)
                    pygame.draw.rect(surface, (60, 14, 48) if hot else (34, 10, 30), cr.inflate(0, -2))
                    pygame.draw.rect(surface, gdev.MAGENTA if hot else (120, 30, 100), cr.inflate(0, -2), 1)
                    gdev.text(surface, c, cr.center, SIZE_TARGET, gdev.MAGENTA if hot else (170, 50, 140),
                              "center", True)
                if hot:
                    blink = int(self.age * 3) % 2 == 0
                    col = gdev.WHITE if blink else gdev.MAGENTA
                    gdev.text(surface, "[", (wrect.x + 4, wrect.centery), SIZE_TARGET, col, "center", True)
                    gdev.text(surface, "]", (wrect.right - 4, wrect.centery), SIZE_TARGET, col, "center", True)
                    y = wrect.bottom + 2
                    for cx in range(wrect.x + 10, wrect.right - 10, 7):
                        pygame.draw.polygon(surface, gdev.MAGENTA, [(cx, y + 3), (cx + 3, y), (cx + 6, y + 3)])
            if w["p"]:
                gdev.text(surface, w["p"], (r.x + px, wrect.centery), SIZE_WORD, gdev.GREEN_MID, "midleft")

    def _draw_callout_row(self, surface, r):
        y = r.y + self.callout_y
        n = len(self.targets)
        gdev.text(surface, "WORD %d/%d" % (min(self.ti + 1, n), n), (r.x + PAD, y + 2), 12, gdev.MAGENTA, bold=True)
        if self.callout is not None:
            text, col, t = self.callout
            alpha = min(255, int(255 * t / 0.4))
            tw = gdev.text_size(text, 13, True)[0]
            box = pygame.Rect(0, y, tw + 16, 18)
            box.midtop = (r.centerx + 30, y)
            pygame.draw.rect(surface, (40, 12, 16), box)
            pygame.draw.rect(surface, col, box, 1)
            gdev.text(surface, text, box.center, 13, col, "center", True, alpha=alpha)
        else:
            gdev.text(surface, "click or press 1-%d    ESC / RMB abort" % self.cfg["options"],
                      (r.right - PAD, y + 2), 12, gdev.GREEN_DIM, "topright")

    def _draw_buttons(self, surface):
        ready = self.phase_age >= INPUT_LOCK_S and self.closing is None
        for i, base in enumerate(self.slot_rects):
            s = self.slots[i] if i < len(self.slots) else None
            br = base.copy()
            if s is None:                               # empty slot: dotted outline
                for x in range(br.x, br.right, 6):
                    pygame.draw.line(surface, gdev.GREEN_DIM, (x, br.y), (x + 2, br.y))
                    pygame.draw.line(surface, gdev.GREEN_DIM, (x, br.bottom - 1), (x + 2, br.bottom - 1))
                for y in range(br.y, br.bottom, 6):
                    pygame.draw.line(surface, gdev.GREEN_DIM, (br.x, y), (br.x, y + 2))
                    pygame.draw.line(surface, gdev.GREEN_DIM, (br.right - 1, y), (br.right - 1, y + 2))
                continue
            state = s["state"]
            hover = state == "live" and ready and br.collidepoint(self.mouse)
            if i in self.press:
                br.y += 2                               # pressed down
            elif hover:
                br.y -= 2                               # lift on hover
            if i in self.shake:
                br.x += int(4 * math.sin(self.shake[i] * 60))
            text = s["opt"]["text"]
            if state == "correct":
                pygame.draw.rect(surface, gdev.CYAN, br)
                pygame.draw.lines(surface, gdev.BG, False, [(br.right - 26, br.centery), (br.right - 20, br.centery + 6),
                                                          (br.right - 10, br.centery - 7)], 3)
                tcol, bcol = gdev.BG, gdev.BG
            elif state == "struck":
                pygame.draw.rect(surface, (40, 10, 16), br)
                clip = surface.get_clip()
                surface.set_clip(br)
                for x in range(br.x - br.h, br.right, 8):          # diagonal hatching
                    pygame.draw.line(surface, (90, 24, 34), (x, br.bottom), (x + br.h, br.y))
                surface.set_clip(clip)
                pygame.draw.rect(surface, gdev.RED, br, 1)
                cx, cy = br.right - 18, br.centery
                pygame.draw.line(surface, gdev.RED, (cx - 6, cy - 6), (cx + 6, cy + 6), 3)
                pygame.draw.line(surface, gdev.RED, (cx - 6, cy + 6), (cx + 6, cy - 6), 3)
                tcol, bcol = (120, 70, 80), (120, 70, 80)
            else:
                pygame.draw.rect(surface, (14, 52, 28) if hover else gdev.GREEN_DARK, br)
                pygame.draw.rect(surface, gdev.GREEN_MID if ready else gdev.GREEN_DIM, br, 1)
                tcol = gdev.WHITE if hover else (gdev.GREEN if ready else gdev.GREEN_MID)
                bcol = gdev.GREEN
                if hover:                               # "> <" bracket border
                    for sx, d in ((br.x - 1, 1), (br.right, -1)):
                        pygame.draw.line(surface, gdev.WHITE, (sx, br.y - 1), (sx, br.bottom), 3)
                        pygame.draw.line(surface, gdev.WHITE, (sx, br.y - 1), (sx + 8 * d, br.y - 1), 3)
                        pygame.draw.line(surface, gdev.WHITE, (sx, br.bottom), (sx + 8 * d, br.bottom), 3)
            badge = pygame.Rect(br.x + 6, br.centery - 9, 18, 18)
            pygame.draw.rect(surface, bcol, badge, 1)
            gdev.text(surface, str(i + 1), badge.center, 13, bcol, "center", True)
            size = SIZE_TARGET
            while size > 14 and gdev.text_size(text, size, True)[0] > br.w - 64:
                size -= 2
            gdev.text(surface, text, (br.centerx + 6, br.centery + 1), size, tcol, "center", True)

    def _draw_timer(self, surface, r):
        frac = max(0.0, min(1.0, self.time_left / self.time_total)) if self.time_total > 0 else 0.0
        bar = pygame.Rect(r.x + PAD, r.y + self.timer_y, r.w - 2 * PAD - 56, 10)
        col = gdev.GREEN if frac > 0.4 else (gdev.AMBER if frac > 0.2 else gdev.RED)
        pygame.draw.rect(surface, gdev.GREEN_DIM, bar, 1)
        pygame.draw.rect(surface, col, (bar.x + 2, bar.y + 2, int((bar.w - 4) * frac), bar.h - 4))
        gdev.text(surface, "%.1fs" % self.time_left, (r.right - PAD, bar.centery), 14, col, "midright", True)

    def _draw_payload_help(self, surface, r):
        live = self.phase_age >= GRANT_FREEZE_S + INPUT_LOCK_S
        for i, (br, name) in enumerate(self.payload_rects):
            gdev.text(surface, PAYLOAD_HELP[name], (br.centerx, r.y + self.callout_y + 2), 12,
                      gdev.CYAN if live else gdev.GREEN_DIM, "midtop")
        if live and self.chosen_payload is None:
            gdev.text(surface, "CHOOSE PAYLOAD :: default EXTRACT in %.1fs" % max(0.0, self.payload_t),
                      (r.centerx, r.y + self.timer_y), 12, gdev.GREEN_MID, "midtop")

    def _draw_stamp(self, surface, r):
        if self.phase == PAYLOAD:
            if self.result.get("clean"):
                text, col = "ACCESS GRANTED // CLEAN", gdev.CYAN
            else:
                text, col = "ACCESS GRANTED", gdev.CYAN
            t = self.phase_age
        elif self.outro == "redacted":
            text, col, t = "REDACTED", gdev.MAGENTA, OUTRO_S - (self.closing or 0)
        elif self.outro == "backtrace":
            text, col, t = "BACKTRACE", gdev.RED, OUTRO_S - (self.closing or 0)
        else:
            return
        k = min(1.0, t / GRANT_FREEZE_S)
        scale = 1.0 + (1.0 - k) * 0.8                  # slams down from 1.8x
        if self.phase == PAYLOAD:                      # then settles to 0.7x inside the empty option row
            settle = max(0.0, min(1.0, (t - GRANT_FREEZE_S - 0.1) / 0.15))
            scale -= 0.3 * settle
        img = gdev.render_text(text, 26, col, True)
        box = pygame.Surface((img.get_width() + 24, img.get_height() + 12), pygame.SRCALPHA)
        box.fill((4, 16, 9, 220))
        pygame.draw.rect(box, col, box.get_rect(), 3)
        box.blit(img, (12, 6))
        stamp = pygame.transform.rotozoom(box, 2, scale)
        cy = r.y + self.button_y + BTN_H // 2      # over the option row: the sentence stays readable
        surface.blit(stamp, stamp.get_rect(center=(r.centerx, cy)))

    def _draw_tear(self, surface, r):
        # BACKTRACE: horizontal slices of the window shoved sideways, tinted red.
        area = r.clip(surface.get_rect())
        if area.w <= 0 or area.h <= 0:
            return
        snap = surface.subsurface(area).copy()
        for _ in range(7):
            h = self.fx.randint(3, 12)
            y = self.fx.randint(0, max(0, area.h - h))
            dx = self.fx.choice((-1, 1)) * self.fx.randint(4, 16)
            surface.blit(snap, (area.x + dx, area.y + y), (0, y, area.w, h))
            tint = pygame.Surface((area.w, h), pygame.SRCALPHA)
            tint.fill((255, 40, 60, 60))
            surface.blit(tint, (area.x + dx, area.y + y))


# ------------------------------------------------------------------ self-check

def validate_fragment(frag):
    """Authoring problems in one intel.json fragment (list of strings)."""
    out = []
    fid = frag.get("id")
    if not fid:
        out.append("fragment without id")
    words = parse_fragment(frag)
    if not words:
        out.append("%s: no words" % fid)
    for w in words:
        for c in w["c"]:
            if c not in GLYPHS and not (len(c) == 1 and "A" <= c <= "Z"):
                out.append("%s: bad cluster %r" % (fid, c))
        if not w["target"] and tier2_count(w["c"]):
            out.append("%s: non-target word %s uses Tier 2 glyphs" % (fid, "".join(w["c"])))
        if w["target"] and tier2_count(w["c"]) > 1:
            out.append("%s: target %s has more than one Tier 2 cluster" % (fid, plain(w["c"])))
    if len([w for w in words if w["target"]]) < 2:
        out.append("%s: fewer than 2 target candidates" % fid)
    return out


def _check_codexes():
    empty = Codex()
    noisy = Codex()
    r = random.Random(7)
    for g in sorted(GLYPHS):
        for _ in range(r.randint(0, 4)):
            noisy.record_hit(g)
        for _ in range(r.randint(0, 3)):
            noisy.record_miss(g, "?")
    out = [("empty", empty), ("random", noisy)]
    for g in ("7", "|2", "("):
        c = Codex()
        for _ in range(6):
            c.record_miss(g, "?")
        out.append(("always-miss %s" % g, c))
    return out


def self_check(verbose=False):
    """Run the decoy generator over every fragment, level and sampled codex state.
    Returns (problems, weak, n_sets): invariant breaks, and weak sets (L2+ without a
    real-word decoy) as (fragment id, level, word, [options])."""
    frags = load_fragments()
    problems, weak, n_sets = [], [], 0
    if not frags:
        problems.append("no fragments in %s" % INTEL_FILE)
    codexes = _check_codexes()
    seen_weak = set()
    for f in frags:
        problems.extend(validate_fragment(f))
        words = parse_fragment(f)
        for level in sorted(LEVELS):
            cfg = LEVELS[level]
            lo, hi = cfg["length"]
            counts = cfg["targets"] if isinstance(cfg["targets"], (tuple, list)) else (cfg["targets"],) * 2
            for attempt in range(3 * (counts[1] - counts[0] + 1)):
                count = counts[0] + attempt // 3
                targets = choose_targets(words, cfg, random.Random("%s|%d|targets|%d" % (f.get("id"), attempt, level)),
                                         level, count)
                tset = set(targets)
                for i, w in enumerate(words):
                    if i not in tset:
                        continue
                    clusters = display_clusters(w, True, cfg)
                    for cname, codex in codexes:
                        for a in range(2):
                            seed = "%s|%d|%d|%d" % (f.get("id"), i, attempt, a)
                            opts, info = build_option_set(clusters, level, codex, random.Random(seed))
                            again, _i = build_option_set(clusters, level, codex, random.Random(seed))
                            n_sets += 1
                            errs = check_options(opts, clusters, cfg)
                            if [o["text"] for o in opts] != [o["text"] for o in again]:
                                errs.append("not deterministic")
                            if info["rebuilt"]:
                                errs.append("needed a rebuild")
                            if errs:
                                problems.append("%s L%d %s (%s): %s" % (f.get("id"), level, plain(clusters),
                                                                       cname, "; ".join(errs)))
                            key = (f.get("id"), level, plain(clusters))
                            if info["weak"] and level <= 3 and cname == "empty" and key not in seen_weak:
                                seen_weak.add(key)
                                weak.append((f.get("id"), level, " ".join(clusters), [o["text"] for o in opts]))
                            if verbose and cname == "empty" and a == 0 and attempt == 0:
                                print("  %-5s L%d %-22s -> %s" % (f.get("id"), level, " ".join(clusters),
                                      "  ".join(("*" if o["correct"] else "") + o["text"] + ("(w)" if o["real"] and not o["correct"] else "")
                                                for o in opts)))
    return problems, weak, n_sets


if __name__ == "__main__":
    if "--check" in sys.argv:
        problems, weak, n = self_check("--samples" in sys.argv)
        print("hack self-check: %d fragments, %d option sets, %d problems, %d weak sets (L2-L3, no real-word decoy)"
              % (len(load_fragments()), n, len(problems), len(weak)))
        for p in problems[:50]:
            print("  PROBLEM", p)
        for fid, level, word, opts in weak:
            print("  weak    %s L%d %-20s %s" % (fid, level, word, " / ".join(opts)))
        sys.exit(1 if problems else 0)
    print("usage: python3 hack.py --check [--samples]")
