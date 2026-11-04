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
and data/misreads.json (misread table, look-alike letters, real words, blocklist, and the
reversed-pick mis-encodings).

HackContext (ctx dict, see the architecture contract 2.6) changes a session:
    fragment / key_source / two_part   KEY packets from progression (F1R3W4LL words = 2 picks)
    stale / replay                     R3C4LL cached copies: a replay of a snapshot(), pays nothing
    dialect                            D14|_3C7 / the Mirror: worst glyphs, decoys from misread_history
    reversed                           the word is shown plain, the options are leet spellings
    lexicon / trace_mult / timer_adjust / tier2_bonus / level / title / payloads
BreachSession is the ROOT BREACH (doc 7.2) and, with mode "reversed", the PATCH handle breach.

Self-check: /usr/bin/python3 hack.py --check  runs the decoy generator over every
fragment at every Breach Level under several codex states, plus every boss password,
the Telemetry templates, the Key packets (two-part words), dialect packets built from
sampled misread histories and reversed picks for sampled handles. It exits with 1 if any
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
# Reversed picks (doc 5.4 / 8.4): extra wrong spellings per letter, on top of the ones derived
# from the misread table run backwards. The extended-letter entries are the doc's list.
DEFAULT_MISENCODINGS = {
    "N": [["|-|", 1.0]], "W": [["\\/", 1.0]], "G": [["(", 1.0]], "Y": [["\\/", 1.0]], "B": [["3", 1.0]],
    "D": [["|>", 1.0]], "X": [["|<", 1.0]], "Z": [["5", 1.0]], "J": [["|", 1.0], ["_/", 0.6]],
    "Q": [["0", 1.0]], "A": [["/\\", 1.0], ["/-", 0.6]], "F": [["|-", 1.0]], "I": [["!", 0.8]],
    "P": [["|)", 1.0]], "S": [["2", 1.0]],
}

DECODE = "DECODE"
PAYLOAD = "PAYLOAD"
DONE = "DONE"
PAYLOADS = ["EXTRACT", "TURN", "OVERLOAD"]
PAYLOAD_KEYS = {pygame.K_z: "EXTRACT", pygame.K_x: "TURN", pygame.K_c: "OVERLOAD"}
PAYLOAD_LETTER = {"EXTRACT": "Z", "TURN": "X", "OVERLOAD": "C"}
PAYLOAD_HELP = {"EXTRACT": "delete it  +intel", "TURN": "it fights for you %ds",
                "OVERLOAD": "EMP: exposes minions nearby"}
OPTION_KEYS = {pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2, pygame.K_4: 3,
               pygame.K_KP1: 0, pygame.K_KP2: 1, pygame.K_KP3: 2, pygame.K_KP4: 3}

# Tuning knobs (doc 5.6)
INPUT_LOCK_S = 0.2          # all input ignored at the start of DECODE and PAYLOAD
PICK_LOCK_S = 0.15          # after every pick
HASTY_S = 0.6               # a wrong pick sooner than this after its set appeared is "hasty"
TRACE_WRONG = 20
TRACE_HASTY = 25
TRACE_HIT = 15              # player damaged while decoding
BURN_AFTER_WRONG = 3        # wrong picks on one word before it burns
REDACTED_THRESHOLD = 0.5
PAYLOAD_WINDOW_S = 3.0
GRANT_FREEZE_S = 0.15       # ACCESS GRANTED hit-freeze before the payload buttons go live
OUTRO_S = 0.7               # REDACTED / BACKTRACE stamp shown before the window closes
CONFIRM_S = 0.12            # pressed payload button stays lit this long
FLIP_S = 0.04               # split-flap: per cluster
SHORT_SET_BONUS_S = 0.5     # extra time when a word could only get a short option set
REVERSED_BONUS_S = 0.5      # extra time per reversed pick (four leet spellings to read)
REDACT = u"████"   # "████"

# ROOT BREACH (doc 7.2)
BREACH_MIN_LEVEL = 4        # every chunk is a 4-option pick
BREACH_BASE_S = 8.0         # breach_timer_s = 8 + 6 x chunks left
BREACH_PER_CHUNK_S = 6.0
CHUNK_SEP = u"·"            # "·" between password chunks
GRANTED_S = 0.55            # breach ACCESS GRANTED stamp before the window closes

# Zone -> act, for IntelLog.pick_fragment when a fragment has no "act" (doc 8.2)
ZONE_ACTS = {"A": "I", "B": "I", "C": "II", "E": "II", "D": "III"}
FRAGMENT_TIERS = ("CHATTER",)       # tiers pick_fragment serves (KEY / TELEMETRY come through ctx)

# Breach Levels (doc 5.6). decoys: "far" = 2 glyphs differ, "near" = 1, "mixed" = 50/50.
# "targets" is a count or a (min, max) range rolled per packet (see target_count).
LEVELS = {
    1: {"targets": (1, 2), "length": (3, 4), "tier2": 0, "options": 3, "decoys": "far",
        "length_tells": True, "real_words": 0, "mult": 2.00, "target_worst": False},
    2: {"targets": 2, "length": (4, 5), "tier2": 0, "options": 3, "decoys": "mixed",
        "length_tells": True, "real_words": 1, "mult": 1.75, "target_worst": False},
    3: {"targets": 2, "length": (5, 6), "tier2": 1, "options": 3, "decoys": "near",
        "length_tells": False, "real_words": 1, "mult": 1.60, "target_worst": False},
    4: {"targets": 2, "length": (6, 7), "tier2": 1, "options": 4, "decoys": "near",
        "length_tells": False, "real_words": 1, "mult": 1.60, "target_worst": False},
    5: {"targets": 3, "length": (6, 8), "tier2": 1, "options": 4, "decoys": "near",
        "length_tells": False, "real_words": 2, "mult": 1.45, "target_worst": False},
    6: {"targets": 3, "length": (7, 8), "tier2": 1, "options": 4, "decoys": "near",
        "length_tells": False, "real_words": 2, "mult": 1.30, "target_worst": True},
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
            "misencodings": dict((str(l).upper(), [(str(c), float(w)) for c, w in encs])
                                 for l, encs in (d.get("misencodings") or DEFAULT_MISENCODINGS).items()
                                 if not str(l).startswith("_")),
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


# Handle-only extended set (doc 5.4): every letter has a leet form.
HANDLE_GLYPHS = {"B": "8", "D": "|)", "G": "6", "J": "_|", "N": "|\\|", "Q": "0_", "W": "\\/\\/",
                 "X": "><", "Y": "`/", "Z": "2"}
LETTER_TIER2 = dict((v, k) for k, v in GLYPHS.items() if k not in TIER1)    # letter -> Tier 2 glyph


def leetify(text, tier2=True, extended=False, density=1.0, rng=None):
    """Plain text -> list of glyph clusters (doc 5.4).
    tier2=True prefers a Tier 2 glyph where one exists, else Tier 1, else the plain letter;
    extended=True adds the handle-only set so every letter encodes; density < 1 leaves a
    random share of letters plain (rng required then). Spaces become " " clusters."""
    out = []
    for ch in str(text).upper():
        if density < 1.0 and rng is not None and rng.random() > density:
            out.append(ch)
            continue
        if tier2 and ch in LETTER_TIER2:
            out.append(LETTER_TIER2[ch])
        elif ch in LETTER_TIER1:
            out.append(LETTER_TIER1[ch])
        elif extended and ch in HANDLE_GLYPHS:
            out.append(HANDLE_GLYPHS[ch])
        else:
            out.append(ch)
    return out


def leetify_handle(handle):
    """The player's handle in leetspeak with the extended set (Key 14, doc 8.3)."""
    return leetify(handle, tier2=True, extended=True)


def glyph_table(extended=False):
    """cluster -> letter: the packet glyphs, plus the handle-only set when extended."""
    if not extended:
        return GLYPHS
    t = dict(GLYPHS)
    for letter, g in HANDLE_GLYPHS.items():
        t[g] = letter
    return t


def letter_glyphs(letter, extended=False):
    """Every cluster that validly encodes one plain letter (Tier 2 first, then Tier 1)."""
    letter = str(letter).upper()
    t2 = sorted(g for g, l in GLYPHS.items() if l == letter and g not in TIER1)
    t1 = sorted(g for g, l in GLYPHS.items() if l == letter and g in TIER1)
    out = t2 + t1
    if extended and letter in HANDLE_GLYPHS:
        out.append(HANDLE_GLYPHS[letter])
    return out


_WORD_PUNCT = ".,!?:;"


def _is_literal(token):
    """Numbers, percentages and glyph names in Telemetry ("61%", "1.4S", "|<"): shown as-is."""
    letters = [c for c in token if "A" <= c <= "Z"]
    digits = [c for c in token if c.isdigit()]
    if not letters:
        return True
    return bool(digits) and token[0].isdigit() and len(letters) <= 2 and token[-len(letters):].isalpha()


def _plain_token(token):
    """A template token that may already be leet ("4LW4Y5") -> plain letters, longest glyph first."""
    out, i = [], 0
    keys = sorted(GLYPHS, key=len, reverse=True)
    while i < len(token):
        for k in keys:
            if token.startswith(k, i):
                out.append(GLYPHS[k])
                i += len(k)
                break
        else:
            out.append(token[i])
            i += 1
    return "".join(out)


def text_to_words(text, density=1.0, rng=None, target_tier2=True):
    """Generated text (Telemetry templates, doc 9.6) -> intel.json-style words.
    Words in [brackets] are targets: every letter with a glyph is leetified (Tier 1, plus one
    Tier 2 cluster when target_tier2), so a target always has something to decode. Other words
    are Tier 1 only, with `density` of their letters leetified (doc 5.4 readability rule).
    Number tokens ("61%", "1.4S") and glyph names ("|<") become literal words, never decoded."""
    rng = rng or random.Random(0)
    words = []
    inside = False                              # within [ ... ] (a bracket may span words)
    for raw in str(text).upper().split():
        if raw.startswith("["):
            inside = True
        target = inside
        if "]" in raw:
            inside = False
        tok = raw.replace("[", "").replace("]", "")
        p = ""
        while tok and tok[-1] in _WORD_PUNCT and len(tok) > 1:
            p = tok[-1] + p
            tok = tok[:-1]
        if not tok:
            continue
        if _is_literal(tok):
            words.append({"c": [tok], "lit": True, "p": p})
            continue
        letters = [c for c in _plain_token(tok) if "A" <= c <= "Z"]
        if not letters:
            continue
        if target:
            cl = [LETTER_TIER1.get(c, c) for c in letters]
            if target_tier2:
                spots = [i for i, c in enumerate(letters) if c in LETTER_TIER2]
                if spots:
                    i = spots[rng.randrange(len(spots))]
                    cl[i] = LETTER_TIER2[letters[i]]
            if not any(c in GLYPHS for c in cl):
                target = False              # nothing to read (e.g. "WHY"): an ordinary word
            words.append({"c": cl, "target": target, "p": p} if target else {"c": cl, "p": p})
        else:
            cl = [LETTER_TIER1[c] if c in LETTER_TIER1 and rng.random() < density else c for c in letters]
            words.append({"c": cl, "p": p})
    return words


def parse_fragment(frag):
    """intel.json words -> [{"c": [clusters], "target": bool, "p": punctuation, "min_level",
    "max_level"}]. A word is a plain list of clusters (not a target), or a dict
    {"c", "target", "p", "min_level", "max_level"}; the levels limit where a target
    candidate may be picked (e.g. a word with no real-word decoy in Tier 1 gets min_level 3).
    Optional keys: "split" (cluster index where a two-part F1R3W4LL word is cut) and "lit"
    (a literal word such as a Telemetry number: shown as-is, never leet-decoded)."""
    words = []
    for w in (frag or {}).get("words", []):
        if isinstance(w, dict):
            lit = bool(w.get("lit"))
            split = w.get("split")
            words.append({"c": [str(c) for c in w.get("c", [])], "target": bool(w.get("target")) and not lit,
                          "p": str(w.get("p", "")), "min_level": int(w.get("min_level", 1)),
                          "max_level": int(w.get("max_level", 6)), "lit": lit,
                          "split": int(split) if split is not None else None})
        else:
            words.append({"c": [str(c) for c in w], "target": False, "p": "", "min_level": 1, "max_level": 6,
                          "lit": False, "split": None})
    return [w for w in words if w["c"]]


def word_plain(word):
    """Plain text of one parsed word (a literal word is its own text)."""
    return "".join(word["c"]) if word.get("lit") else plain(word["c"])


def fragment_text(words, gaps=()):
    """Display text of a fragment: plain words, "████" for the word indices in gaps."""
    parts = []
    for i, w in enumerate(words):
        parts.append((REDACT if i in gaps else word_plain(w)) + w["p"])
    return " ".join(parts)


def valid_readings(clusters, limit=64, extended=False):
    """Every plain string the raw glyph text could be read as, with any segmentation.
    A decoy must never be one of these (e.g. "|_" + "|" also reads as "|_|" = U).
    extended=True also reads the handle-only glyphs (the PATCH handle breach)."""
    raw = "".join(clusters)
    table = glyph_table(extended)
    tokens = sorted(set(table) | set(c for c in raw if c.isalpha()), key=len, reverse=True)
    memo = {}

    def read(i):
        if i == len(raw):
            return [""]
        if i in memo:
            return memo[i]
        out = []
        for t in tokens:
            if raw.startswith(t, i):
                head = table.get(t, t)
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


def candidate_decoys(clusters, cfg, codex=None, history=None):
    """All allowed decoys for a word, by kind: {"hist": [...], "near": [...], "far": [...],
    "look": [...], "look_far": [...]}. Duplicates, the answer and other valid readings are
    removed. history = [(glyph, read_as)] (mirror_dialect): misreads the player has actually
    made become "hist" decoys, which win a duplicate text over every other kind."""
    md = misread_data()
    letters = [GLYPHS.get(c, c) for c in clusters]
    answer = "".join(letters)
    hist = []
    if history:
        counts = {}
        for g, r in history:
            r = str(r or "").upper()
            if g in GLYPHS and r and r.isalpha() and r != GLYPHS[g]:
                counts[(g, r)] = counts.get((g, r), 0) + 1
        for i, c in enumerate(clusters):
            for (g, r), n in sorted(counts.items()):
                if g != c or (len(r) != len(letters[i]) and not cfg["length_tells"]):
                    continue
                hist.append(_decoy(letters, {i: r}, [(c, r)], 2.0 * n, "hist"))
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
    pools = {"hist": hist, "near": near, "far": far, "look": look, "look_far": look_far}
    out = {}
    for kind in ("hist", "near", "far", "look", "look_far"):    # earlier kinds win a duplicate text
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


def build_option_set(clusters, level, codex=None, rng=None, history=None, options=None):
    """make_options() plus notes: returns (options, info).
    options: [{"text", "correct", "tests": [(glyph, reads_as)], "real", "kind"}], shuffled.
    info: {"weak": no real-word decoy where one is required, "short": fewer options than
    the level wants, "rebuilt": the set failed its invariants and was rebuilt}.
    history (mirror_dialect, doc 7.3): [(glyph, read_as)]; the player's own past misreads are
    drawn first. options overrides the level's option count (ROOT BREACH: 4)."""
    cfg = LEVELS[max(1, min(6, level))]
    if options is not None:
        cfg = dict(cfg, options=int(options))
    rng = rng or random.Random(0)
    answer = plain(clusters)
    need = cfg["options"] - 1
    pools = candidate_decoys(clusters, cfg, codex, history)
    mode = cfg["decoys"]
    if mode == "far":
        order = ["far", "near", "look_far", "look"]
    elif mode == "near":
        order = ["near", "look", "far", "look_far"]
    else:
        order = ["near", "far", "look", "look_far"]
    order = ["hist"] + order
    chosen = []
    used = set()
    taken = set()
    while len(chosen) < need:
        avail = dict((k, [d for d in pools[k] if d["text"] not in taken]) for k in order)
        pool = None
        if avail["hist"]:
            pool = avail["hist"]            # your own misreads first, then the misread table
        elif mode == "mixed" and avail["near"] and avail["far"]:
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


# ------------------------------------------------------------------ reversed picks (doc 8.4)
# The terminal shows a plain word; the buttons are leet spellings of it. Exactly one uses only
# valid glyphs (5.4). The decoys are mis-encodings: the misread table run backwards (a glyph
# that is misread AS the letter, e.g. L written |_|; or a multi-letter misread spelled out,
# e.g. R written |Z, K written |C), a look-alike letter's glyph (M written |\|), and the
# authored list in misreads.json "misencodings" (the doc's list for the extended letters).

_misenc_cache = {}


def misencodings(letter):
    """[(cluster, weight)] that look like `letter` but do not encode it, even with the
    handle-only set. Sorted, heaviest first; cached."""
    letter = str(letter).upper()
    if letter in _misenc_cache:
        return _misenc_cache[letter]
    md = misread_data()
    found = {}

    def add(cl, w):
        if cl and cl != letter and w > found.get(cl, 0.0):
            found[cl] = w

    for g, reads in sorted(md["misreads"].items()):
        for r, w in reads:
            if r == letter:
                add(g, w)                                     # "|_|" is misread as L
            elif GLYPHS.get(g) == letter and len(r) > 1:
                add(r.replace("I", "|"), w)                   # |2 misread as IZ -> R written |Z
    for grp in md["lookalikes"]:
        if letter in grp:
            for o in grp:
                if o != letter:
                    for g in letter_glyphs(o, extended=True):
                        add(g, 0.5)                           # a look-alike letter's glyph
    for cl, w in md["misencodings"].get(letter, []):
        add(cl, w)
    out = []
    for cl, w in found.items():
        if letter in valid_readings([cl], extended=True) or _blocked(cl):
            continue
        out.append((cl, w))
    out.sort(key=lambda x: (-x[1], x[0]))
    _misenc_cache[letter] = out
    return out


def reverse_spelling(word, extended=False, prefer=()):
    """The one valid leet spelling a reversed pick asks for: per letter a glyph from `prefer`
    (the dialect's worst glyphs) if one encodes it, else Tier 2, else Tier 1, else (extended)
    the handle-only glyph, else the plain letter."""
    out = []
    for ch in str(word).upper():
        valid = letter_glyphs(ch, extended=False)
        pref = [g for g in prefer if g in valid]
        if pref:
            out.append(pref[0])
        elif valid:
            out.append(valid[0])
        elif extended and ch in HANDLE_GLYPHS:
            out.append(HANDLE_GLYPHS[ch])
        else:
            out.append(ch)
    return out


def _cluster_key(clusters):
    return " ".join(clusters)


def build_reversed_set(word, n_options, codex=None, rng=None, extended=False, dialect=None):
    """Option set for a reversed pick: returns (options, info) like build_option_set.
    options: [{"text" (clusters joined by spaces), "clusters", "correct", "tests": [(cluster,
    letter)], "changes": {i: cluster}, "kind", "real": False}], shuffled. Decoys swap one letter
    (two only when one is not enough) for a mis-encoding, weighted x3 toward dialect["worst"],
    x(1 + 2 recent misses) by the codex, and x4 for (cluster, letter) misreads in the history."""
    rng = rng or random.Random(0)
    word = str(word).upper()
    dialect = dialect or {}
    worst = list(dialect.get("worst") or [])
    weights = dialect.get("weights") or {}
    hist = {}
    if dialect.get("decoys_from_history", True):
        for g, r in dialect.get("history") or []:
            if r:
                hist[(g, str(r).upper())] = hist.get((g, str(r).upper()), 0) + 1
    answer = reverse_spelling(word, extended, worst)
    need = max(1, int(n_options) - 1)
    singles = []
    for i, ch in enumerate(word):
        for cl, w in misencodings(ch):
            if cl == answer[i]:
                continue
            if cl in worst:
                w *= 1.0 + 2.0 * float(weights.get(cl, 1.0))
            if codex is not None and cl in GLYPHS:
                w *= 1.0 + 2.0 * codex.recent_misses(cl)
            if (cl, ch) in hist:
                w *= 4.0 * hist[(cl, ch)]
            singles.append({"changes": {i: cl}, "w": w, "idx": set([i]), "kind": "hist" if (cl, ch) in hist else "near"})
    cands = list(singles)
    if len(singles) < need * 2:                      # few single swaps: allow two-letter swaps
        for a in range(len(singles)):
            for b in range(a + 1, len(singles)):
                if singles[a]["idx"] & singles[b]["idx"]:
                    continue
                ch = dict(singles[a]["changes"])
                ch.update(singles[b]["changes"])
                cands.append({"changes": ch, "w": singles[a]["w"] * singles[b]["w"] * 0.3,
                              "idx": singles[a]["idx"] | singles[b]["idx"], "kind": "far"})
    seen = set([_cluster_key(answer)])
    pool = []
    for c in cands:
        cl = [c["changes"].get(i, a) for i, a in enumerate(answer)]
        key = _cluster_key(cl)
        if key in seen or word in valid_readings(cl, extended=True) or _blocked("".join(cl)):
            continue
        seen.add(key)
        c["clusters"] = cl
        c["text"] = key
        pool.append(c)
    pool.sort(key=lambda c: c["text"])                 # deterministic before weighted picks
    chosen, used = [], set()
    while len(chosen) < need and pool:
        hp = [c for c in pool if c["kind"] == "hist"]
        d = _pick_weighted(hp or pool, rng, used, False)
        chosen.append(d)
        used |= d["idx"]
        pool = [c for c in pool if c is not d]
    options = [{"text": _cluster_key(answer), "clusters": list(answer), "correct": True, "tests": [],
                "changes": {}, "kind": "answer", "real": True}]
    for d in chosen:
        tests = [(cl, word[i]) for i, cl in sorted(d["changes"].items())]
        options.append({"text": d["text"], "clusters": d["clusters"], "correct": False, "tests": tests,
                        "changes": dict(d["changes"]), "kind": d["kind"], "real": False})
    rng.shuffle(options)
    info = {"weak": False, "short": len(chosen) < need, "rebuilt": False, "answer": answer}
    errors = check_reversed(options, word, extended, n_options)
    if errors:
        print("[hack] bad reversed set for %s: %s -> trimmed" % (word, "; ".join(errors)))
        options = [o for o in options if o["correct"]] + \
                  [o for o in options if not o["correct"] and word not in valid_readings(o["clusters"], extended=True)][:1]
        info["rebuilt"] = True
    return options, info


def check_reversed(options, word, extended=False, n_options=None):
    """Runtime invariants for a reversed set: exactly one option is a valid spelling of `word`
    (and it is the correct one), no duplicates, nothing blocklisted, 2..n options."""
    errors = []
    word = str(word).upper()
    valid = [o for o in options if word in valid_readings(o["clusters"], extended=extended)]
    correct = [o for o in options if o["correct"]]
    if len(correct) != 1 or len(valid) != 1 or valid[0] is not correct[0]:
        errors.append("needs exactly one valid spelling (valid %d, correct %d)" % (len(valid), len(correct)))
    for o in options:
        if not o["correct"] and word in valid_readings(o["clusters"], extended=True):
            errors.append("decoy %s is valid with the handle glyphs" % o["text"])
        if _blocked("".join(o["clusters"])):
            errors.append("option %s is blocklisted" % o["text"])
    texts = [o["text"] for o in options]
    if len(set(texts)) != len(texts):
        errors.append("duplicate options")
    if len(options) < 2 or (n_options is not None and len(options) > n_options):
        errors.append("bad option count %d" % len(options))
    return errors


def callout_reversed(option, word):
    """The mis-encoding a wrong reversed pick made, e.g. "|_| = U, not L"."""
    parts = []
    table = glyph_table(True)
    for cl, letter in option.get("tests", []):
        if cl in table:
            part = "%s = %s, not %s" % (cl, table[cl], letter)
        else:
            part = "%s is not %s" % (cl, letter)
        if part not in parts:
            parts.append(part)
    return "   ".join(parts) or "WRONG SPELLING"


def dialect_clusters(clusters, dialect, rng):
    """mirror_dialect (doc 7.3): re-encode a target word with the dialect's worst glyphs, even
    past the level's Tier 2 cap. A glyph with weight w (thinned by correct picks) is used with
    probability w. At most 3 swaps per word, so it stays one word."""
    worst = list((dialect or {}).get("worst") or [])
    weights = (dialect or {}).get("weights") or {}
    out = list(clusters)
    swaps = 0
    for k, c in enumerate(out):
        if swaps >= 3:
            break
        letter = GLYPHS.get(c, c)
        for g in worst:
            if g in GLYPHS and GLYPHS[g] == letter and g != c:
                if rng.random() < float(weights.get(g, 1.0)):
                    out[k] = g
                    swaps += 1
                break
    return out


def add_tier2(clusters, bonus, cap, rng):
    """TIER2_SHARE counter (doc 9.4): with probability `bonus`, upgrade one letter of a target
    to its Tier 2 glyph while the word has fewer than `cap` Tier 2 clusters."""
    if bonus <= 0 or cap <= 0 or tier2_count(clusters) >= cap or rng.random() >= bonus:
        return list(clusters)
    spots = [k for k, c in enumerate(clusters) if GLYPHS.get(c, c) in LETTER_TIER2 and
             (c not in GLYPHS or c in TIER1)]
    if not spots:
        return list(clusters)
    out = list(clusters)
    k = spots[rng.randrange(len(spots))]
    out[k] = LETTER_TIER2[GLYPHS.get(out[k], out[k])]
    return out


def default_dialect(codex, n=5):
    """A dialect from the codex alone (worst glyphs, misread history), for a session whose
    ctx has none: the PATCH handle breach still speaks your dialect (doc 8.4)."""
    if codex is None:
        return {"worst": [], "weights": {}, "history": [], "decoys_from_history": True}
    scored = []
    for g in set(codex.hits) | set(codex.misses):
        s = codex.misses.get(g, 0) - 0.5 * codex.hits.get(g, 0)
        if s > 0:
            scored.append((-s, g))
    scored.sort()
    return {"worst": [g for _s, g in scored[:n]], "weights": {},
            "history": [(g, r) for g, r in codex.misread_history if r][-60:], "decoys_from_history": True}


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
                 if not w.get("lit") and len(plain(w["c"])) >= 3 and any(c in GLYPHS for c in w["c"])]
    lo, hi = cfg["length"]

    def dist(i):
        n = len(plain(words[i]["c"]))
        return lo - n if n < lo else (n - hi if n > hi else 0)

    keyed = [(dist(i), rng.random(), i) for i in cands]
    keyed.sort()
    return sorted(i for _d, _r, i in keyed[:count])


def display_clusters(word, is_target, cfg):
    """Clusters as shown this session: targets keep up to cfg["tier2"] Tier 2 clusters,
    every other word is Tier 1 only (doc 5.4). A literal word (a Telemetry number or glyph
    name) is shown exactly as written."""
    if word.get("lit"):
        return list(word["c"])
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

HINT_AFTER = 5                  # correct picks that tested a glyph before the codex shows its hint
PICK_TIMES_KEPT = 20            # decision times kept per glyph


class Codex(object):
    """Per-glyph reading record (doc 9.3).
      hits {glyph: int}, misses {glyph: int}, misread_history [(glyph, read_as)]
      pick_times {glyph: [seconds]}  decision time of each pick that tested the glyph
      record_hit(glyph), record_miss(glyph, read_as), recent_misses(glyph) -> int
      record_pick_time(glyph, s), accuracy(glyph) -> 0..1|None, top_misread(glyph) -> (read_as, n)|None,
      median_pick_s(glyph) -> s|None, hint(glyph) -> "|2 = R"|None (after 5 correct picks)
      rows() -> [(glyph, letter, hits, misses)] for every glyph tested so far
    """
    def __init__(self):
        self.hits = {}
        self.misses = {}
        self.misread_history = []
        self.pick_times = {}

    def record_hit(self, glyph):
        self.hits[glyph] = self.hits.get(glyph, 0) + 1

    def record_miss(self, glyph, read_as=None):
        self.misses[glyph] = self.misses.get(glyph, 0) + 1
        self.misread_history.append((glyph, read_as))

    def recent_misses(self, glyph):
        return sum(1 for g, _r in self.misread_history[-20:] if g == glyph)

    def record_pick_time(self, glyph, s):
        times = self.pick_times.setdefault(glyph, [])
        times.append(round(float(s), 3))
        del times[:-PICK_TIMES_KEPT]

    def accuracy(self, glyph):
        n = self.hits.get(glyph, 0) + self.misses.get(glyph, 0)
        return self.hits.get(glyph, 0) / float(n) if n else None

    def top_misread(self, glyph):
        counts = {}
        for g, r in self.misread_history:
            if g == glyph and r:
                counts[r] = counts.get(r, 0) + 1
        if not counts:
            return None
        best = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0]
        return best[0], best[1]

    def median_pick_s(self, glyph):
        times = sorted(self.pick_times.get(glyph, []))
        if not times:
            return None
        mid = len(times) // 2
        return times[mid] if len(times) % 2 else (times[mid - 1] + times[mid]) / 2.0

    def hint(self, glyph):
        if self.hits.get(glyph, 0) >= HINT_AFTER and glyph in GLYPHS:
            return "%s = %s" % (glyph, GLYPHS[glyph])
        return None

    def worst(self, glyphs=None):
        """The glyph (optionally among `glyphs`) with the most misses over hits, or None."""
        best, score = None, 0
        for g in sorted(glyphs if glyphs is not None else self.misses):
            s = self.misses.get(g, 0) - self.hits.get(g, 0) * 0.5
            if self.misses.get(g, 0) > 0 and s > score:
                best, score = g, s
        return best

    def to_save(self):
        return {"hits": dict(self.hits), "misses": dict(self.misses),
                "misread_history": [list(x) for x in self.misread_history[-200:]],
                "pick_times": dict((g, list(t)) for g, t in self.pick_times.items())}

    def from_save(self, d):
        d = d or {}
        self.hits = dict((str(g), int(n)) for g, n in d.get("hits", {}).items())
        self.misses = dict((str(g), int(n)) for g, n in d.get("misses", {}).items())
        self.misread_history = [tuple(x) for x in d.get("misread_history", []) if len(x) == 2]
        self.pick_times = dict((str(g), [float(s) for s in t][-PICK_TIMES_KEPT:])
                               for g, t in (d.get("pick_times") or {}).items())

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
      pick_fragment(minion, rng) -> dict|None   next CHATTER fragment for a packet
      add_fragment(frag)          register a fragment from outside intel.json (KEY, Telemetry)
      to_save() / from_save(d)
    Entries also carry "act" and "zone" (DECRYPT groups the log by act).
    """
    def __init__(self, fragments=None):
        self.fragments = list(fragments if fragments is not None else load_fragments())
        self.entries = []
        self.served = {}            # fragment id -> times used for a packet (seeds the decoys)
        self.extra = []             # ids registered with add_fragment (saved with the run)

    def count(self):
        return len(self.entries)

    def add_fragment(self, frag):
        """Register a fragment that is not in intel.json (KEY packets, Telemetry). A fragment
        with a known id replaces nothing: the first copy wins."""
        if frag and frag.get("id") and self.get(frag["id"]) is None:
            self.fragments.append(frag)
            self.extra.append(frag["id"])

    def to_save(self):
        extra = [f for f in self.fragments if f.get("id") in set(self.extra)]
        return {"entries": list(self.entries), "served": dict(self.served), "extra_fragments": extra}

    def from_save(self, d):
        d = d or {}
        self.entries = [dict(e) for e in d.get("entries", []) if isinstance(e, dict) and e.get("id")]
        self.served = dict((str(k), int(v)) for k, v in d.get("served", {}).items())
        for f in d.get("extra_fragments", []):
            self.add_fragment(f)

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
        """CHATTER only (KEY and Telemetry packets come through the HackContext).
        REDACTED fragments of this archetype first (a later hack completes them; this zone's
        first), then fragments not found yet from the minion's zone, then its act, then any;
        when everything is found, the least used one of the narrowest pool."""
        kind = str(getattr(minion, "kind", "b1t")).upper()
        zone = getattr(minion, "rack_id", None)
        frags = sorted([f for f in self.fragments if str(f.get("tier", "CHATTER")).upper() in FRAGMENT_TIERS],
                       key=lambda f: str(f.get("id")))
        mine = [f for f in frags if kind in [str(s).upper() for s in f.get("sources", [kind])]] or frags
        if not mine:
            return None
        act = ZONE_ACTS.get(zone)
        zone_acts = [f.get("act") for f in mine if zone is not None and f.get("zone") == zone and f.get("act")]
        if zone_acts:
            act = max(set(zone_acts), key=zone_acts.count)
        in_zone = [f for f in mine if zone is not None and f.get("zone") == zone]
        in_act = [f for f in mine if act is not None and f.get("act") == act]
        pools = [p for p in (in_zone, in_act, mine) if p]
        redacted = [f for f in mine if (self.entry(f.get("id")) or {}).get("redacted")]
        if redacted:
            local = [f for f in redacted if f in in_zone]
            return (local or redacted)[0]
        for pool in pools:
            fresh = [f for f in pool if not self.has(f.get("id"))]
            if fresh:
                return rng.choice(fresh)
        pool = pools[0]
        least = min(self.served.get(f.get("id"), 0) for f in pool)
        return rng.choice([f for f in pool if self.served.get(f.get("id"), 0) == least])

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
                 "author": frag.get("author", ""), "act": frag.get("act", ""), "zone": frag.get("zone"),
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
      HackSession(minion, settings, codex, intel_db, rng, ctx=None)   intel_db is an IntelLog,
                                 ctx the HackContext (contract 2.6; every key is optional)
      phase DECODE|PAYLOAD|DONE, trace 0..100, time_left, time_total (real seconds)
      minion, finished bool, result dict (valid once finished):
          {"outcome": "success"|"redacted"|"backtrace"|"abort", "fragment_id": str|None,
           "decoded_text": str, "clean": bool, "payload": "EXTRACT"|"TURN"|"OVERLOAD"|None,
           plus info keys: "gaps", "level", "wrong_picks", "words", "decoded", "source", "reason",
           "kind" ("hack"), "stale", "key_source", "tier", "decision_times", "reversed", "picks"}
      handle_event(e)            KEYDOWN 1-4 / Z X C / Esc, MOUSEBUTTONDOWN (LMB pick, RMB abort),
                                 MOUSEMOTION (hover). Positions are internal pixels.
      update(real_dt)
      draw(surface, anchor=None, avoid=())  anchor = minion's internal-surface position (window
                                 is placed above it, clamped into hud.SAFE_RECT, or below it when
                                 that would cover the anchor or an `avoid` point); None = centred
      on_player_damaged()        +35 TRACE (Game calls it when the rooted player is hit)
      abort(reason="abort")      end now (target died, player died...)
      snapshot()                 what a stale replay of this packet needs (R3C4LL cached copies)
    Each target word is one pick, or two with ctx "two_part" (any F1R3W4LL packet: F1R3 + W4LL).
    Test helpers: correct_slot(), wrong_slot(), slot_center(slot), payload_center(name),
                  target_words() -> [plain words to decode], input_ready(), dev_complete().
    """

    extended = False                # reversed picks with the handle-only glyphs (the PATCH breach)

    def __init__(self, minion, settings, codex, intel_db, rng, ctx=None):
        self.body_scale = 1          # camera zoom; Game sets it so the window keeps sprites clear
        self.minion = minion
        self.settings = settings
        self.codex = codex
        self.intel = intel_db
        self.rng = rng
        self._setup_ctx(ctx)
        self.kind = str(getattr(minion, "kind", "b1t")).lower()

        replay = self.ctx.get("replay") if self.stale else None
        self.replay = replay if isinstance(replay, dict) and replay.get("shown") and replay.get("targets") else None
        if self.replay is not None:
            level = self.replay.get("level")
            self.reversed = bool(self.replay.get("reversed", self.reversed))
        else:
            level = self.ctx.get("level") or session_level(settings, self.kind)
        self.level = max(1, min(6, int(level or 1)))
        self.cfg = LEVELS[self.level]

        if self.replay is not None:
            self.fragment = self.replay.get("fragment")
        elif self.ctx.get("fragment") is not None:
            self.fragment = self.ctx["fragment"]
            if intel_db is not None and not self.stale:
                intel_db.add_fragment(self.fragment)
        else:
            self.fragment = intel_db.pick_fragment(minion, rng) if intel_db is not None else None
        frag = self.fragment or FALLBACK_FRAGMENT
        self.fid = str(frag.get("id", "NOISE"))
        if intel_db is not None:
            self.attempt = intel_db.served.get(self.fid, 0)
            if not self.stale:                 # a stale copy does not use up a packet
                intel_db.served[self.fid] = self.attempt + 1
        else:
            self.attempt = 0
        self.words = parse_fragment(frag) or parse_fragment(FALLBACK_FRAGMENT)

        if self.replay is not None and not self._replay_fits():
            self.replay = None
        if self.replay is not None:
            self.targets = [int(i) for i in self.replay["targets"]]
            self.shown = [list(c) for c in self.replay["shown"]]
        else:
            pick_rng = random.Random("%s|%d|targets|%d" % (self.fid, self.attempt, self.level))
            first_hack = intel_db is not None and intel_db.count() == 0
            easy = settings.get("hack_difficulty") == "EASY"
            n = target_count(self.cfg, pick_rng, short=first_hack or easy)
            if self.ctx.get("all_targets"):
                n = len([w for w in self.words if w["target"]])
            self.targets = choose_targets(self.words, self.cfg, pick_rng, self.level, n)
            self.shown = self._shown_clusters(pick_rng)
        self.picks = self._make_picks()
        self._init_state()

        tier2 = sum(tier2_count(self.shown[i]) for i in self.targets)
        self.time_total = self._timer(1.5 + 2.0 * len(self.picks) + 0.3 * tier2, self.cfg["mult"])
        self.time_left = self.time_total
        self._init_result()
        self._open()

    # -------------------------------------------------------------- setup

    def _setup_ctx(self, ctx):
        self.ctx = dict(ctx or {})
        self.emit = self.ctx.get("emit")
        self.stale = bool(self.ctx.get("stale"))
        self.reversed = bool(self.ctx.get("reversed"))
        self.dialect = self.ctx.get("dialect") or None
        self.lexicon = bool(self.ctx.get("lexicon"))
        self.trace_mult = max(0.1, float(self.ctx.get("trace_mult") or 1.0))
        self.timer_adjust = max(0.75, min(1.25, float(self.ctx.get("timer_adjust") or 1.0)))
        self.tier2_bonus = max(0.0, min(1.0, float(self.ctx.get("tier2_bonus") or 0.0)))
        allowed = self.ctx.get("payloads") or PAYLOADS
        self.payloads = [p for p in PAYLOADS if p in allowed] or ["EXTRACT"]
        self.default_payload = "EXTRACT" if "EXTRACT" in self.payloads else self.payloads[0]
        self.title = self.ctx.get("title")
        self.turn_s = int(self.ctx.get("turn_s") or 15)
        self.last_input = "key"

    def _replay_fits(self):
        """A snapshot only replays onto the same fragment layout."""
        r = self.replay
        try:
            if len(r["shown"]) != len(self.words):
                return False
            return all(0 <= int(i) < len(self.words) for i in r["targets"])
        except (KeyError, TypeError, ValueError):
            return False

    def _shown_clusters(self, rng):
        """Clusters as shown this session: the level's Tier 2 cap (display_clusters), then the
        TIER2_SHARE bonus and the mirror_dialect re-encoding on target words."""
        tset = set(self.targets)
        cap = max(1, self.cfg["tier2"]) if self.level >= 2 else 0
        out = []
        for i, w in enumerate(self.words):
            cl = display_clusters(w, i in tset, self.cfg)
            if i in tset and not w.get("lit"):
                if self.tier2_bonus > 0:
                    cl = add_tier2(cl, self.tier2_bonus, cap, rng)
                if self.dialect:
                    cl = dialect_clusters(cl, self.dialect, rng)
            out.append(cl)
        return out

    def _make_picks(self):
        """One pick per target word; with two_part, words of 4+ clusters are two picks, cut at
        the fragment's "split" index (else len // 2), each part at least 2 clusters."""
        if self.replay is not None and self.replay.get("picks"):
            return [dict(p) for p in self.replay["picks"]]
        picks = []
        for wi in self.targets:
            n = len(self.shown[wi])
            if self.ctx.get("two_part") and n >= 4:
                split = self.words[wi].get("split") or n // 2
                split = max(2, min(n - 2, int(split)))
                picks.append({"word": wi, "lo": 0, "hi": split, "part": 0, "parts": 2})
                picks.append({"word": wi, "lo": split, "hi": n, "part": 1, "parts": 2})
            else:
                picks.append({"word": wi, "lo": 0, "hi": n, "part": 0, "parts": 1})
        return picks

    def _init_state(self):
        self.word_state = dict((i, "pending") for i in self.targets)   # pending/decoded/burned
        self.part_done = {}         # word index -> clusters decoded so far (two-part words)
        self.display_targets = set(self.targets)    # words drawn large (breach: every chunk)
        self.locked = set()         # breach chunks solved before this session
        self.ti = 0                 # index into self.picks
        self.phase = DECODE
        self.phase_age = 0.0
        self.age = 0.0
        self.trace = 0.0
        self.wrong_picks = 0
        self.finished = False
        self.closing = None         # seconds until finished, once the outcome is decided
        self.outro = None           # "success" / "granted" / "redacted" / "backtrace" while closing
        self.payload_t = PAYLOAD_WINDOW_S
        self.chosen_payload = None
        self.layouts = {}           # pick index -> its first option layout (for snapshot())
        self.first_wrong = {}       # pick index -> slot (first layout) of its first wrong pick
        self.rerolls = {}           # pick index -> times re-rolled (ROOT BREACH burns)
        self.reroll_pending = False
        self.reroll_on_burn = False
        self.flip = {}              # word index -> seconds since it was decoded

    def _init_result(self):
        self.result = {"outcome": None, "fragment_id": self.fragment.get("id") if self.fragment else None,
                       "decoded_text": "", "clean": True, "payload": None, "gaps": [],
                       "level": self.level, "wrong_picks": 0, "words": len(self.targets),
                       "decoded": 0, "source": self.kind.upper(), "reason": None,
                       "kind": "hack", "stale": self.stale, "key_source": self.ctx.get("key_source"),
                       "tier": (self.fragment or {}).get("tier", "CHATTER"), "decision_times": [],
                       "reversed": self.reversed, "picks": len(self.picks), "replay": self.replay is not None}

    def _open(self):
        # option buttons: slots[i] = {"opt": option, "state": "live"|"struck"|"correct"} or None
        self.slots = []
        self.set_age = 0.0
        self.word_wrong = 0
        self.word_missed = set()
        self.pick_lock = 0.0
        self.advance_pending = False
        self.callout = None         # [text, color, seconds left]
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
        if self.picks:
            self._new_option_set()

    def _timer(self, base, mult):
        if self.reversed:
            base += REVERSED_BONUS_S * len(self.picks)
        return base * mult * settings_mod.timer_mult(self.settings) * self.timer_adjust

    # -------------------------------------------------------------- helpers for tests

    def target_words(self):
        return [plain(self.shown[i]) for i in self.targets]

    def active_pick(self):
        """The pick being decoded ({"word", "lo", "hi", "part", "parts"}), or None."""
        if self.phase != DECODE or self.ti >= len(self.picks) or self.closing is not None:
            return None
        return self.picks[self.ti]

    def active_word(self):
        """Word index being decoded, or None."""
        p = self.active_pick()
        return p["word"] if p is not None else None

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
                self.last_input = "key"
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                for i, r in enumerate(self.slot_rects):
                    if r.collidepoint(e.pos):
                        slot = i
                        self.last_input = "mouse"
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
        add = TRACE_HIT                      # Trace Damper only softens wrong picks (doc 10)
        self.trace = min(100.0, self.trace + add)
        self.trace_pulse = 0.4
        if self.phase == DECODE and self.closing is None:
            self.callout = ["HIT WHILE DECODING  +%d TRACE" % int(round(add)), gdev.RED, 1.4]
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

    def _pick_clusters(self, p):
        return self.shown[p["word"]][p["lo"]:p["hi"]]

    def _history(self):
        d = self.dialect or {}
        if d and d.get("decoys_from_history", True):
            return d.get("history") or None
        return None

    def _reversed_dialect(self):
        return self.dialect or default_dialect(self.codex)

    def _replay_set(self, ti):
        """The snapshot's option layout for pick ti ([option or None per slot]), or None."""
        if self.replay is None:
            return None
        sets = self.replay.get("options") or []
        if ti < len(sets) and sets[ti]:
            return [dict(o) if o else None for o in sets[ti]]
        texts = self.replay.get("option_sets") or []
        if ti >= len(texts) or not texts[ti] or ti >= len(self.picks):
            return None
        clusters = self._pick_clusters(self.picks[ti])
        answer = _cluster_key(reverse_spelling(plain(clusters))) if self.reversed else plain(clusters)
        out = []
        for t in texts[ti]:
            if t is None:
                out.append(None)
                continue
            o = {"text": t, "correct": t == answer, "tests": [], "changes": {}, "real": False, "kind": "replay"}
            if self.reversed:
                o["clusters"] = t.split(" ")
            out.append(o)
        if len([o for o in out if o and o["correct"]]) != 1:
            return None
        return out

    def _replay_wrong(self, ti):
        w = (self.replay or {}).get("wrong") or {}
        slot = w.get(ti, w.get(str(ti)))
        return int(slot) if slot is not None else None

    def _prestrike_count(self):
        """Decoys struck before the set goes live: Lexicon strikes 1 on the first set of a hack."""
        return 1 if self.lexicon and self.ti == 0 and not self.rerolls.get(0) else 0

    def _new_option_set(self):
        p = self.picks[self.ti]
        wi = p["word"]
        clusters = self._pick_clusters(p)
        reroll = self.rerolls.get(self.ti, 0)
        seed = "%s|%d|%d" % (self.fid, wi, self.attempt)
        if p["part"] or reroll:
            seed += "|%d|%d" % (p["part"], reroll)
        rng = random.Random(seed)
        n = self.cfg["options"]
        replayed = self._replay_set(self.ti) if reroll == 0 else None
        if replayed is not None:
            opts = replayed
            n = max(n, len(opts))
        else:
            if self.reversed:
                opts, info = build_reversed_set(plain(clusters), n, self.codex, rng, self.extended,
                                                self._reversed_dialect())
            else:
                opts, info = build_option_set(clusters, self.level, self.codex, rng, history=self._history())
            if info["short"]:
                self.time_left += SHORT_SET_BONUS_S
                self.time_total += SHORT_SET_BONUS_S
        self.slots = [None] * n
        for i, o in enumerate(opts[:n]):
            if o is not None:
                self.slots[i] = {"opt": o, "state": "live"}
        if self.ti not in self.layouts:
            self.layouts[self.ti] = [s["opt"] if s else None for s in self.slots]
        self.shuffle_rng = rng
        self.set_age = 0.0
        self.word_wrong = 0
        self.word_missed = set()
        note = None
        if replayed is not None:
            ws = self._replay_wrong(self.ti)
            if ws is not None and ws < n and self.slots[ws] and not self.slots[ws]["opt"]["correct"] \
                    and self._live_decoys() > 1:
                self.slots[ws]["state"] = "struck"
                self.slots[ws]["pre"] = True
                note = "ST4L3 R3PL4Y :: your last miss is struck"
        k = self._prestrike_count()
        while k > 0 and self._live_decoys() > 1:
            live = [i for i, s in enumerate(self.slots) if s and s["state"] == "live" and not s["opt"]["correct"]]
            i = live[rng.randrange(len(live))]
            self.slots[i]["state"] = "struck"
            self.slots[i]["pre"] = True
            k -= 1
            note = self._prestrike_note()
        if note:
            self.callout = [note, gdev.CYAN, 1.8]

    def _prestrike_note(self):
        return "L3X1C0N :: 1 decoy struck"

    def _live_decoys(self):
        return len([s for s in self.slots if s and s["state"] == "live" and not s["opt"]["correct"]])

    def _pick_tier(self):
        if self.reversed:
            for s in self.slots:
                if s and s["opt"]["correct"]:
                    return 2 if tier2_count(s["opt"].get("clusters", [])) else 1
            return 1
        return 2 if tier2_count(self._pick_clusters(self.picks[self.ti])) else 1

    def _pick(self, slot):
        if slot >= len(self.slots) or self.slots[slot] is None or self.slots[slot]["state"] != "live":
            return                                   # empty or struck slot: no pick, no TRACE
        s = self.slots[slot]
        opt = s["opt"]
        p = self.picks[self.ti]
        wi = p["word"]
        self.press[slot] = 0.12
        self.pick_lock = PICK_LOCK_S
        decision = round(self.set_age, 3)
        self.result["decision_times"].append(decision)
        tested = sorted(set(g for o in self.slots if o for g, _r in o["opt"]["tests"] if g in GLYPHS))
        mine = [g for g, _r in opt["tests"] if g in GLYPHS]
        hasty = (not opt["correct"]) and self.set_age < HASTY_S
        if self.codex is not None:
            for g in (tested if opt["correct"] else mine):
                self.codex.record_pick_time(g, decision)
        if self.emit is not None:
            self.emit("pick", correct=bool(opt["correct"]), hasty=hasty, decision_s=self.set_age,
                      glyphs=tested if opt["correct"] else mine,
                      read_as=None if opt["correct"] else opt["text"], slot=slot, input=self.last_input,
                      tier=self._pick_tier(), session=self)
        if opt["correct"]:
            s["state"] = "correct"
            if self.codex is not None:
                for g in sorted(set(tested) - self.word_missed):
                    self.codex.record_hit(g)
            if p["part"] < p["parts"] - 1:
                self.part_done[wi] = p["hi"]         # first half of a two-part word read
            else:
                self.word_state[wi] = "decoded"
                self.flip[wi] = 0.0
            self.advance_pending = True
            return
        add = (TRACE_HASTY if hasty else TRACE_WRONG) * self.trace_mult
        self.trace = min(100.0, self.trace + add)
        self.trace_pulse = 0.4
        self.wrong_picks += 1
        self.word_wrong += 1
        s["state"] = "struck"
        self.shake[slot] = 0.25
        if self.ti not in self.first_wrong:
            for k, o in enumerate(self.layouts.get(self.ti, [])):
                if o is opt:
                    self.first_wrong[self.ti] = k
        for g, reads in opt["tests"]:
            self.word_missed.add(g)
            if self.codex is not None and g in GLYPHS:
                self.codex.record_miss(g, reads)
        if self.reversed:
            text = callout_reversed(opt, plain(self._pick_clusters(p)))
        else:
            text = callout(opt, self._pick_clusters(p))
        if hasty:
            text += "   HASTY +%d" % int(round(add))
        self.callout = [text, gdev.AMBER, 2.2]
        if self.trace >= 100:
            self._decide("backtrace")
            return
        if self.word_wrong >= BURN_AFTER_WRONG:
            if self.reroll_on_burn:                  # ROOT BREACH: a fresh option set, same chunk
                self.reroll_pending = True
                self.callout = [text + "   CHUNK RE-ROLLED", gdev.RED, 2.2]
            else:
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
        if self.reroll_pending:
            self.reroll_pending = False
            self.rerolls[self.ti] = self.rerolls.get(self.ti, 0) + 1
            self._new_option_set()
            return
        self.ti += 1
        while self.ti < len(self.picks) and self.word_state.get(self.picks[self.ti]["word"]) == "burned":
            self.ti += 1                             # the rest of a burned two-part word
        if self.ti < len(self.picks):
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
        if self.intel is not None and self.fragment is not None and not self.result.get("stale"):
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
        if outcome == "redacted" and self.intel is not None and self.fragment is not None and \
           not self.result.get("stale"):
            self.intel.record(self.result)
        self.outro = outcome
        self.closing = OUTRO_S

    def _choose_payload(self, name):
        if self.chosen_payload is not None or name not in self.payloads:
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
                    self._choose_payload(self.default_payload)

    # -------------------------------------------------------------- dev / replay

    def dev_complete(self):
        """Tests and --dev: decode every remaining word correctly, right now."""
        if self.finished or self.phase != DECODE or self.closing is not None:
            return
        for i in self.targets:
            if self.word_state.get(i) != "decoded":
                self.word_state[i] = "decoded"
                self.flip[i] = 0.0
        self.ti = len(self.picks)
        self.advance_pending = False
        self.reroll_pending = False
        self._grant()

    def snapshot(self):
        """What a stale replay needs (respawn_from_cache, doc 7.3): the same words in the same
        option layout, with the first wrong pick of each set struck again.
        {"fragment_id", "fragment", "targets", "level", "shown", "picks", "reversed",
         "option_sets": [[option text or None per slot]], "options": [[option dict or None]],
         "wrong": {pick index: slot}}"""
        n = len(self.picks)
        return {"fragment_id": self.fid, "fragment": self.fragment, "targets": list(self.targets),
                "level": self.level, "shown": [list(c) for c in self.shown],
                "picks": [dict(p) for p in self.picks], "reversed": self.reversed,
                "option_sets": [[o["text"] if o else None for o in self.layouts.get(i, [])] for i in range(n)],
                "options": [[dict(o) if o else None for o in self.layouts.get(i, [])] for i in range(n)],
                "wrong": dict(self.first_wrong)}

    # -------------------------------------------------------------- layout

    def _disp(self, i, c):
        """Text drawn for cluster c of word i: reversed targets show the plain letter."""
        if self.reversed and i in self.display_targets:
            return GLYPHS.get(c, c)
        return c

    def _boxed(self, i, c):
        return (c in GLYPHS and not self.words[i].get("lit")) or (self.reversed and i in self.display_targets)

    def _cluster_w(self, c, size, bold, boxed=None):
        w = gdev.text_size(c, size, bold)[0]
        if boxed is None:
            boxed = c in GLYPHS
        return w + (8 if boxed else 0)              # glyph clusters get a box

    def _layout(self):
        """Word and cluster rects relative to the window's top-left, and the window size."""
        tset = self.display_targets
        inner = WIN_W - 2 * PAD
        self.word_boxes = []        # per word: (Rect word, [Rect cluster], punct_x)
        x, line = 0, 0
        for i, w in enumerate(self.words):
            target = i in tset
            size = SIZE_TARGET if target else SIZE_WORD
            br = 10 if target else 0          # room for the active-word brackets
            widths = [self._cluster_w(self._disp(i, c), size, target, self._boxed(i, c)) for c in self.shown[i]]
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
        k = len(self.payloads)
        pw = (r.w - 4 - 4 * k) // k
        self.payload_rects = [(pygame.Rect(r.x + 4 + i * (pw + 4), r.y + 2, pw, TITLE_H - 4), name)
                              for i, name in enumerate(self.payloads)]

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
        elif self.phase == PAYLOAD or self.outro == "granted":
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
        if self.outro == "backtrace" and not self.settings.get("reduced_glitch"):
            self._draw_tear(surface, r)

    def _title_text(self):
        if self.title:
            return str(self.title)
        return "JACK-IN :: %s" % getattr(self.minion, "label", "MINION")

    def _level_text(self):
        t = "BREACH L%d" % self.level
        if self.stale:
            t += "  ST4L3"
        if self.reversed:
            t += "  R3V3R53D"
        return t

    def _draw_title(self, surface, r):
        title = pygame.Rect(r.x + 1, r.y + 1, r.w - 2, TITLE_H - 1)
        pygame.draw.rect(surface, gdev.GREEN_DARK, title)
        if self.phase == PAYLOAD:
            live = self.phase_age >= GRANT_FREEZE_S + INPUT_LOCK_S
            for br, name in self.payload_rects:
                hover = live and br.collidepoint(self.mouse)
                picked = self.chosen_payload == name
                default = name == self.default_payload and self.chosen_payload is None
                if picked or hover:
                    pygame.draw.rect(surface, gdev.CYAN, br)
                    col = gdev.BG
                else:
                    pygame.draw.rect(surface, (6, 40, 46), br)
                    pygame.draw.rect(surface, gdev.CYAN if default else gdev.GREEN_DIM, br, 2 if default else 1)
                    col = gdev.CYAN if live else gdev.GREEN_DIM
                gdev.text(surface, "[%s] %s" % (PAYLOAD_LETTER[name], name), br.center, 14, col, "center", True)
            if live and self.chosen_payload is None:   # 1.2s window draining under the buttons
                frac = max(0.0, self.payload_t / PAYLOAD_WINDOW_S)
                pygame.draw.rect(surface, gdev.CYAN, (r.x + 4, r.y + TITLE_H - 2, int((r.w - 8) * frac), 2))
            return
        # [ TRACE ####...... ] from the right edge; the title and level fit in what is left
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
        meter = gdev.text(surface, "[ TRACE", (x - 4, r.y + 6), 13, col, "topright", True)
        room = meter.left - 16 - (r.x + 10)
        name, level = self._title_text(), self._level_text()
        lw = gdev.text_size(level, 13)[0]
        if gdev.text_size(name, 13, True)[0] + 14 + lw > room:
            level, lw = "L%d" % self.level, gdev.text_size("L%d" % self.level, 13)[0]
        while len(name) > 4 and gdev.text_size(name, 13, True)[0] + 14 + lw > room:
            name = name[:-3].rstrip() + ".."
        tr = gdev.text(surface, name, (r.x + 10, r.y + 6), 13, gdev.GREEN, bold=True)
        gdev.text(surface, level, (tr.right + 14, r.y + 6), 13, gdev.CYAN if self.reversed else gdev.GREEN_MID)

    def _draw_sentence(self, surface, r):
        active = self.active_pick()
        aw = active["word"] if active is not None else None
        tset = self.display_targets
        for i, w in enumerate(self.words):
            wrect, rects, px = self.word_boxes[i]
            wrect = wrect.move(r.x, r.y)
            state = self.word_state.get(i) if i in tset else None
            if state is None:
                for c, cr in zip(self.shown[i], rects):
                    cr = cr.move(r.x, r.y)
                    if self._boxed(i, c):
                        pygame.draw.rect(surface, gdev.GREEN_DIM, cr.inflate(0, -6), 1)
                    gdev.text(surface, c, cr.center, SIZE_WORD, gdev.GREEN_MID, "center")
            elif state == "burned":
                pygame.draw.rect(surface, (120, 30, 100), wrect.inflate(-16, -6))
            elif state == "decoded":
                t = self.flip.get(i, 9.0)
                if i in self.locked:                      # pre-solved breach chunk: locked in cyan
                    box = wrect.inflate(-12, 0)
                    pygame.draw.rect(surface, (8, 60, 70), box)
                    pygame.draw.rect(surface, gdev.CYAN, box, 1)
                    gdev.text(surface, word_plain(w), box.center, SIZE_TARGET, gdev.CYAN, "center", True)
                elif t >= len(rects) * FLIP_S + 0.07:    # settled: tight plain word
                    gdev.text(surface, word_plain(w), wrect.center, SIZE_TARGET, gdev.CYAN, "center", True)
                    pygame.draw.line(surface, gdev.CYAN, (wrect.x + 10, wrect.bottom),
                                     (wrect.right - 10, wrect.bottom))
                else:
                    for k, (c, cr) in enumerate(zip(self.shown[i], rects)):
                        cr = cr.move(r.x, r.y)
                        tk = t - k * FLIP_S
                        if tk < 0:
                            pygame.draw.rect(surface, (60, 14, 48), cr.inflate(0, -2))
                            gdev.text(surface, self._disp(i, c), cr.center, SIZE_TARGET, gdev.MAGENTA, "center", True)
                        elif tk < 0.07:
                            ch = SCRAMBLE[self.fx.randrange(len(SCRAMBLE))]
                            pygame.draw.rect(surface, gdev.WHITE, cr.inflate(0, -2), 1)
                            gdev.text(surface, ch, cr.center, SIZE_TARGET, gdev.WHITE, "center", True)
                        else:
                            gdev.text(surface, GLYPHS.get(c, c), cr.center, SIZE_TARGET, gdev.CYAN, "center", True)
                    pygame.draw.line(surface, gdev.CYAN, (wrect.x + 10, wrect.bottom), (wrect.right - 10, wrect.bottom))
            else:
                hot = i == aw
                lo, hi = (active["lo"], active["hi"]) if hot else (0, len(rects))
                done = self.part_done.get(i, 0)
                if done:                                  # first part of a two-part word: read
                    part = rects[0].unionall(rects[1:done]).move(r.x, r.y)
                    gdev.text(surface, plain(self.shown[i][:done]), part.center, SIZE_TARGET, gdev.CYAN, "center", True)
                    pygame.draw.line(surface, gdev.CYAN, (part.x + 2, part.bottom), (part.right - 2, part.bottom))
                for k, (c, cr) in enumerate(zip(self.shown[i], rects)):
                    cr = cr.move(r.x, r.y)
                    if k < done:
                        continue
                    lit = hot and lo <= k < hi
                    pygame.draw.rect(surface, (60, 14, 48) if lit else (34, 10, 30), cr.inflate(0, -2))
                    pygame.draw.rect(surface, gdev.MAGENTA if lit else (120, 30, 100), cr.inflate(0, -2), 1)
                    gdev.text(surface, self._disp(i, c), cr.center, SIZE_TARGET,
                              gdev.MAGENTA if lit else (170, 50, 140), "center", True)
                if hot:
                    blink = int(self.age * 3) % 2 == 0
                    col = gdev.WHITE if blink else gdev.MAGENTA
                    gdev.text(surface, "[", (wrect.x + 4, wrect.centery), SIZE_TARGET, col, "center", True)
                    gdev.text(surface, "]", (wrect.right - 4, wrect.centery), SIZE_TARGET, col, "center", True)
                    y = wrect.bottom + 2
                    x0 = r.x + rects[lo].x if lo < len(rects) else wrect.x + 10
                    x1 = r.x + rects[hi - 1].right if 0 < hi <= len(rects) else wrect.right - 10
                    for cx in range(x0, max(x0 + 1, x1 - 5), 7):
                        pygame.draw.polygon(surface, gdev.MAGENTA, [(cx, y + 3), (cx + 3, y), (cx + 6, y + 3)])
            if w["p"]:
                gdev.text(surface, w["p"], (r.x + px, wrect.centery), SIZE_WORD, gdev.GREEN_MID, "midleft")

    def _progress_label(self):
        p = self.picks[min(self.ti, len(self.picks) - 1)] if self.picks else None
        n = len(self.targets)
        k = self.targets.index(p["word"]) + 1 if p is not None and p["word"] in self.targets else n
        text = "WORD %d/%d" % (k, n)
        if p is not None and p["parts"] > 1:
            text += "  PART %d/%d" % (p["part"] + 1, p["parts"])
        return text

    def _hint_text(self):
        n = len(self.slots) or self.cfg["options"]
        what = "pick the valid leet spelling" if self.reversed else "click or press 1-%d" % n
        return "%s    ESC / RMB abort" % what

    def _draw_callout_row(self, surface, r):
        y = r.y + self.callout_y
        gdev.text(surface, self._progress_label(), (r.x + PAD, y + 2), 12, gdev.MAGENTA, bold=True)
        if self.callout is not None:
            text, col, t = self.callout
            alpha = min(255, int(255 * t / 0.4))
            tw = gdev.text_size(text, 13, True)[0]
            box = pygame.Rect(0, y, tw + 16, 18)
            box.midtop = (r.centerx + 30, y)
            # the pill fades with its text (no empty bordered box in the last 0.4 s)
            pill = pygame.Surface(box.size, pygame.SRCALPHA)
            fill = (40, 12, 16) if col != gdev.CYAN else (6, 34, 40)
            pill.fill((fill[0], fill[1], fill[2], alpha))
            pygame.draw.rect(pill, (col[0], col[1], col[2], alpha), pill.get_rect(), 1)
            surface.blit(pill, box.topleft)
            gdev.text(surface, text, box.center, 13, col, "center", True, alpha=alpha)
        else:
            gdev.text(surface, self._hint_text(), (r.right - PAD, y + 2), 12, gdev.GREEN_DIM, "topright")

    def _draw_option_label(self, surface, opt, br, color):
        """Option text centred in its button; a reversed option draws its clusters boxed."""
        cl = opt.get("clusters")
        if not cl:
            size = SIZE_TARGET
            text = opt["text"]
            while size > 14 and gdev.text_size(text, size, True)[0] > br.w - 64:
                size -= 2
            gdev.text(surface, text, (br.centerx + 6, br.centery + 1), size, color, "center", True)
            return
        size = SIZE_TARGET
        while True:
            widths = [gdev.text_size(c, size, True)[0] + 6 for c in cl]
            total = sum(widths) + 3 * (len(cl) - 1)
            if total <= br.w - 40 or size <= 12:
                break
            size -= 2
        x = br.centerx + 10 - total // 2
        for c, cw in zip(cl, widths):
            box = pygame.Rect(x, br.y + 6, cw, br.h - 12)
            pygame.draw.rect(surface, color, box, 1)
            gdev.text(surface, c, box.center, size, color, "center", True)
            x += cw + 3

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
                pre = s.get("pre")                    # struck before the set went live (Lexicon, replay)
                xcol = gdev.CYAN if pre else gdev.RED
                pygame.draw.rect(surface, xcol, br, 1)
                cx, cy = br.right - 18, br.centery
                pygame.draw.line(surface, xcol, (cx - 6, cy - 6), (cx + 6, cy + 6), 3)
                pygame.draw.line(surface, xcol, (cx - 6, cy + 6), (cx + 6, cy - 6), 3)
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
            self._draw_option_label(surface, s["opt"], br, tcol)

    def _draw_timer(self, surface, r):
        frac = max(0.0, min(1.0, self.time_left / self.time_total)) if self.time_total > 0 else 0.0
        bar = pygame.Rect(r.x + PAD, r.y + self.timer_y, r.w - 2 * PAD - 56, 10)
        col = gdev.GREEN if frac > 0.4 else (gdev.AMBER if frac > 0.2 else gdev.RED)
        pygame.draw.rect(surface, gdev.GREEN_DIM, bar, 1)
        pygame.draw.rect(surface, col, (bar.x + 2, bar.y + 2, int((bar.w - 4) * frac), bar.h - 4))
        gdev.text(surface, "%.1fs" % self.time_left, (r.right - PAD, bar.centery), 14, col, "midright", True)

    def _draw_payload_help(self, surface, r):
        live = self.phase_age >= GRANT_FREEZE_S + INPUT_LOCK_S
        for br, name in self.payload_rects:
            help_text = PAYLOAD_HELP[name] % self.turn_s if name == "TURN" else PAYLOAD_HELP[name]
            gdev.text(surface, help_text, (br.centerx, r.y + self.callout_y + 2), 12,
                      gdev.CYAN if live else gdev.GREEN_DIM, "midtop")
        if live and self.chosen_payload is None:
            gdev.text(surface, "CHOOSE PAYLOAD :: default %s in %.1fs" % (self.default_payload, max(0.0, self.payload_t)),
                      (r.centerx, r.y + self.timer_y), 12, gdev.GREEN_MID, "midtop")

    def _draw_stamp(self, surface, r):
        if self.phase == PAYLOAD:
            if self.result.get("stale"):
                text, col = "ACCESS GRANTED // ST4L3", gdev.CYAN
            elif self.result.get("clean"):
                text, col = "ACCESS GRANTED // CLEAN", gdev.CYAN
            else:
                text, col = "ACCESS GRANTED", gdev.CYAN
            t = self.phase_age
        elif self.outro == "granted":
            text, col, t = "ACCESS GRANTED", gdev.CYAN, GRANTED_S - (self.closing or 0)
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


class _BreachTarget(object):
    """Stands in for the minion a HackSession expects (label / kind for the title bar)."""
    def __init__(self, label):
        self.label = label
        self.kind = "breach"


def breach_clusters(chunk, level=BREACH_MIN_LEVEL, codex=None):
    """A ROOT BREACH chunk leetified with Tier 2 wherever possible (doc 7.2). If that spelling
    cannot fill a 4-option set of near decoys, the Tier 2 letters are stepped down to Tier 1
    one at a time (then S/A/I take their other Tier 1 glyph) until one can."""
    base = leetify(chunk, tier2=True)
    tries = [base]
    for k, c in enumerate(base):
        if c in GLYPHS and c not in TIER1:
            alt = list(base)
            alt[k] = LETTER_TIER1.get(GLYPHS[c], GLYPHS[c])
            tries.append(alt)
    alt1 = {"5": "$", "4": "@", "1": "|"}
    for k, c in enumerate(base):
        if c in alt1:
            alt = list(base)
            alt[k] = alt1[c]
            tries.append(alt)
    for cl in tries:
        _opts, info = build_option_set(cl, max(BREACH_MIN_LEVEL, level), None, random.Random("breach|" + chunk))
        if not info["short"] and not info["rebuilt"]:
            return cl
    return base


class BreachSession(HackSession):
    """ROOT BREACH (doc 7.2): the rack's password_word, shown leetified with Tier 2 wherever
    possible and split into its chunks (Y 3 5 · 7 3 |2 · D /-\\ Y); one 4-option pick of near
    decoys per chunk at spec["level"] (at least 4). No PAYLOAD: ACCESS GRANTED closes it.
    spec: {"title", "password_word", "chunks", "presolved": [chunk idx], "prestrike": bool,
           "level", "mode": "normal"|"reversed", "boss_id"}
      presolved   chunks locked in cyan from the start (and kept across tries)
      prestrike   every remaining chunk starts with one decoy struck (the found password);
                  Lexicon strikes one per chunk too (they do not stack: 1 per chunk)
      reversed    the PATCH handle breach (doc 8.4): each chunk shown plain, the options are
                  leet spellings with the handle-only glyphs, decoys = mis-encodings (dialect)
    Timer 8 + 6 x chunks left (real time, x timer_mult x timer_adjust). TRACE as a hack: three
    strikes = BACKTRACE. Two wrong picks on a chunk re-roll its option set (TRACE stays).
    Esc / RMB disconnects: outcome "abort", progress kept, no TRACE.
    result: {"kind": "breach", "outcome": "success"|"backtrace"|"abort", "locked": [chunk idx]
             (pre-solved plus every chunk read this try), "boss_id", ...the HackSession keys}"""

    def __init__(self, spec, settings, codex, rng, ctx=None):
        self.spec = dict(spec or {})
        self.body_scale = 1
        self.minion = _BreachTarget(self.spec.get("title") or "ROOT BREACH")
        self.settings = settings
        self.codex = codex
        self.intel = None
        self.rng = rng
        c = dict(ctx or {})
        for k in ("stale", "replay", "two_part", "fragment", "all_targets", "payloads"):
            c.pop(k, None)
        c.setdefault("title", self.spec.get("title") or "R00T BR34CH")
        self.mode = "reversed" if str(self.spec.get("mode", "normal")).lower() == "reversed" else "normal"
        if self.mode == "reversed":
            c["reversed"] = True
        self._setup_ctx(c)
        self.extended = self.reversed
        self.kind = "breach"
        self.replay = None
        self.level = max(BREACH_MIN_LEVEL, min(6, int(self.spec.get("level") or BREACH_MIN_LEVEL)))
        self.cfg = LEVELS[self.level]
        if self.reversed and not self.dialect:
            self.dialect = default_dialect(codex)
        word = str(self.spec.get("password_word") or "").upper()
        chunks = [str(ch).upper() for ch in (self.spec.get("chunks") or []) if str(ch).strip()]
        self.chunks = chunks or [word or "ROOT"]
        self.fid = "BREACH-%s" % self.spec.get("boss_id", "x")
        self.attempt = rng.randrange(1 << 20) if rng is not None else 0   # every try rolls fresh sets
        self.fragment = {"id": self.fid, "tier": "BREACH", "title": "ROOT BREACH", "words": []}
        self.words, self.shown = [], []
        last = len(self.chunks) - 1
        for i, ch in enumerate(self.chunks):
            cl = list(ch) if self.reversed else breach_clusters(ch, self.level, codex)
            self.words.append({"c": cl, "target": True, "p": CHUNK_SEP if i < last else "", "min_level": 1,
                               "max_level": 6, "lit": False, "split": None})
            self.shown.append(cl)
        pre = set()
        for i in self.spec.get("presolved") or []:
            if 0 <= int(i) < len(self.chunks):
                pre.add(int(i))
        self.targets = [i for i in range(len(self.chunks)) if i not in pre]
        self.picks = [{"word": i, "lo": 0, "hi": len(self.shown[i]), "part": 0, "parts": 1} for i in self.targets]
        self.prestrike = bool(self.spec.get("prestrike"))
        self._init_state()
        self.reroll_on_burn = True
        self.locked = pre
        self.display_targets = set(range(len(self.chunks)))
        for i in pre:
            self.word_state[i] = "decoded"
            self.flip[i] = 99.0
        self.time_total = self._timer(BREACH_BASE_S + BREACH_PER_CHUNK_S * len(self.targets), 1.0)
        self.time_left = self.time_total
        self._init_result()
        self.result.update({"kind": "breach", "stale": False, "key_source": None, "tier": "BREACH",
                            "fragment_id": None, "source": "BREACH", "locked": sorted(pre),
                            "boss_id": self.spec.get("boss_id"), "mode": self.mode})
        self._open()
        if not self.picks:                          # everything was pre-solved
            self._grant()

    def _prestrike_count(self):
        return 1 if (self.prestrike or self.lexicon) else 0

    def _prestrike_note(self):
        if self.prestrike:
            return "P455W0RD F0UND :: 1 decoy struck"
        return "L3X1C0N :: 1 decoy struck"

    def _locked_now(self):
        return sorted(self.locked | set(i for i in self.targets if self.word_state.get(i) == "decoded"))

    def _progress_label(self):
        p = self.picks[min(self.ti, len(self.picks) - 1)] if self.picks else None
        n = len(self.chunks)
        k = p["word"] + 1 if p is not None else n
        text = "CHUNK %d/%d" % (k, n)
        if self.locked:
            text += "  %d LOCKED" % len(self.locked)
        return text

    def _hint_text(self):
        what = "pick your handle's leet spelling" if self.reversed else "click or press 1-4"
        return "%s    ESC / RMB disconnect" % what

    def _partial_outcome(self):
        return "backtrace"                          # no REDACTED password: the rack backtraces

    def _fill_result(self, outcome):
        HackSession._fill_result(self, outcome)
        self.result["locked"] = list(range(len(self.chunks))) if outcome == "success" else self._locked_now()

    def _grant(self):
        self._fill_result("success")
        self.outro = "granted"
        self.closing = GRANTED_S
        self.callout = None

    def abort(self, reason="abort"):
        undecided = not self.finished and self.closing is None
        HackSession.abort(self, reason)
        if undecided:
            self.result["locked"] = self._locked_now()


# ------------------------------------------------------------------ self-check

def validate_fragment(frag, min_targets=2):
    """Authoring problems in one intel.json fragment (list of strings)."""
    out = []
    fid = frag.get("id")
    if not fid:
        out.append("fragment without id")
    words = parse_fragment(frag)
    if not words:
        out.append("%s: no words" % fid)
    for w in words:
        if w.get("lit"):
            continue
        for c in w["c"]:
            if c not in GLYPHS and not (len(c) == 1 and "A" <= c <= "Z"):
                out.append("%s: bad cluster %r" % (fid, c))
        if not w["target"] and tier2_count(w["c"]):
            out.append("%s: non-target word %s uses Tier 2 glyphs" % (fid, "".join(w["c"])))
        if w["target"] and tier2_count(w["c"]) > 1:
            out.append("%s: target %s has more than one Tier 2 cluster" % (fid, plain(w["c"])))
        if w["split"] is not None and not (2 <= w["split"] <= len(w["c"]) - 2):
            out.append("%s: split %d of %s leaves a part under 2 clusters" % (fid, w["split"], plain(w["c"])))
    if len([w for w in words if w["target"]]) < min_targets:
        out.append("%s: fewer than %d target candidates" % (fid, min_targets))
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


def _check_histories():
    """Sampled misread_history lists for the mirror_dialect checks: empty, one habit, every
    misread in the table, junk entries, and the (glyph, letter) pairs reversed picks record."""
    md = misread_data()
    every = [(g, r) for g, reads in sorted(md["misreads"].items()) for r, _w in reads]
    r = random.Random(11)
    sampled = [every[r.randrange(len(every))] for _ in range(40)]
    return [("none", []), ("|2 habit", [("|2", "IZ")] * 4 + [("|2", "B")] * 2), ("table", every),
            ("sampled", sampled), ("junk", [("|2", None), ("7", ""), ("x", "Q"), ("3", "e"), ("|<", "IC")]),
            ("reversed", [("|_|", "L"), ("|-|", "N"), ("7", "L"), ("(", "G"), ("|=", "T")] * 2)]


def _dialects(codexes):
    out = []
    worst_sets = [["|2", "7", "(", "4", "|<"], ["|_|", "|_", "|=", "\\/", "|\\/|"], ["3", "0", "1", "5", "@"]]
    for (hname, hist), worst in zip(_check_histories(), worst_sets * 2):
        out.append((hname, {"worst": worst, "weights": {worst[0]: 0.5}, "history": hist,
                            "decoys_from_history": True, "thin_on_correct": True}))
    out.append(("codex", default_dialect(codexes[1][1])))
    return out


def _handle_chunks(handle):
    """finale.handle_chunks when it imports, else the same rule: clamp(ceil(len / 3), 2, 4)
    chunks of 1-3 letters."""
    try:
        import finale
        return finale.handle_chunks(handle)
    except Exception:
        h = str(handle).upper()
        n = max(2, min(4, int(math.ceil(len(h) / 3.0))))
        out, i = [], 0
        for k in range(n):
            size = int(math.ceil((len(h) - i) / float(n - k)))
            out.append(h[i:i + size])
            i += size
        return [c for c in out if c]


def _sample_handles(n=60):
    fixed = ["TECH", "DHRUVA", "BOB", "ZZZ", "JQX", "QWERTYZXJBNM", "ABCDEFGHIJKL", "MNOPQRSTUVWX", "YZA",
             "WIZARD", "GHOSTBYTE", "NULLJUMP"]
    r = random.Random(5)
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return fixed + ["".join(r.choice(letters) for _ in range(r.randint(3, 12))) for _ in range(n)]


TELEMETRY_SAMPLES = [
    {"weapon": "BOW", "pct": 64, "dir": "LEFT", "s": "1.4", "glyph": "|<", "slot": 2, "n": 212},
    {"weapon": "PISTOL", "pct": 71, "dir": "UP", "s": "0.8", "glyph": "|2", "slot": 1, "n": 3},
    {"weapon": "KATANA", "pct": 100, "dir": "DOWN RIGHT", "s": "2.9", "glyph": "7", "slot": 4, "n": 41},
]
TELEMETRY_DENSITY = {"P": 0.3, "I": 0.3, "II": 0.45, "III": 0.6}     # doc 5.7: diegetic leet capped at 60%


def _check_set(label, clusters, level, codex, seed, problems, history=None, options=None, want_full=False):
    """Build one forward option set twice (determinism) and check its invariants."""
    cfg = LEVELS[level] if options is None else dict(LEVELS[level], options=options)
    opts, info = build_option_set(clusters, level, codex, random.Random(seed), history=history, options=options)
    again, _i = build_option_set(clusters, level, codex, random.Random(seed), history=history, options=options)
    errs = check_options(opts, clusters, cfg)
    if [o["text"] for o in opts] != [o["text"] for o in again]:
        errs.append("not deterministic")
    if info["rebuilt"]:
        errs.append("needed a rebuild")
    if want_full and info["short"]:
        errs.append("short set (%d options): %s" % (len(opts), " / ".join(o["text"] for o in opts)))
    if errs:
        problems.append("%s L%d %s: %s" % (label, level, " ".join(clusters), "; ".join(errs)))
    return opts, info


def _check_fragment_sets(f, codexes, problems, weak, seen_weak, verbose, two_part=False):
    words = parse_fragment(f)
    n_sets = 0
    for level in sorted(LEVELS):
        cfg = LEVELS[level]
        counts = cfg["targets"] if isinstance(cfg["targets"], (tuple, list)) else (cfg["targets"],) * 2
        for attempt in range(3 * (counts[1] - counts[0] + 1)):
            count = counts[0] + attempt // 3
            targets = choose_targets(words, cfg, random.Random("%s|%d|targets|%d" % (f.get("id"), attempt, level)),
                                     level, count)
            for i in targets:
                clusters = display_clusters(words[i], True, cfg)
                parts = [clusters]
                if two_part and len(clusters) >= 4:
                    split = max(2, min(len(clusters) - 2, int(words[i].get("split") or len(clusters) // 2)))
                    parts = [clusters[:split], clusters[split:]]
                for part in parts:
                    for cname, codex in codexes:
                        for a in range(2):
                            seed = "%s|%d|%d|%d" % (f.get("id"), i, attempt, a)
                            opts, info = _check_set("%s (%s)" % (f.get("id"), cname), part, level, codex, seed,
                                                    problems)
                            n_sets += 1
                            key = (f.get("id"), level, plain(part))
                            if info["weak"] and level <= 3 and cname == "empty" and key not in seen_weak and \
                                    not two_part:
                                seen_weak.add(key)
                                weak.append((f.get("id"), level, " ".join(part), [o["text"] for o in opts]))
                            if verbose and cname == "empty" and a == 0 and attempt == 0:
                                print("  %-6s L%d %-22s -> %s" % (f.get("id"), level, " ".join(part),
                                      "  ".join(("*" if o["correct"] else "") + o["text"] +
                                                ("(w)" if o["real"] and not o["correct"] else "") for o in opts)))
    return n_sets


def self_check(verbose=False):
    """Run the decoy generator over every fragment, level and sampled codex state, then over the
    runtime-built packets: Key packets (two-part words), boss passwords (ROOT BREACH chunks at
    L4-L6), Telemetry templates filled with sample values, mirror_dialect packets from sampled
    misread histories, and reversed picks (sampled handles, the Mirror's counter-hacks).
    Returns (problems, weak, n_sets, notes): invariant breaks, weak sets (L2+ without a
    real-word decoy) as (fragment id, level, word, [options]), and authoring notes."""
    content = gdev.load_json(INTEL_FILE, {}) or {}
    if not isinstance(content, dict):
        content = {"fragments": content}
    frags = load_fragments()
    problems, weak, notes, n_sets = [], [], [], 0
    if not frags:
        problems.append("no fragments in %s" % INTEL_FILE)
    codexes = _check_codexes()
    seen_weak = set()

    # 1) CHATTER fragments, as before; a F1R3W4LL's packet splits 4+ cluster words in two picks
    for f in frags:
        problems.extend(validate_fragment(f))
        n_sets += _check_fragment_sets(f, codexes, problems, weak, seen_weak, verbose)
        if "F1R3W4LL" in [str(x).upper() for x in f.get("sources", ["F1R3W4LL"])]:
            n_sets += _check_fragment_sets(f, codexes[:2], problems, weak, seen_weak, False, two_part=True)

    # 2) Key packets: F1R3W4LL words are two picks
    for k in content.get("keys", []) or []:
        if not k.get("words"):
            notes.append("key %s has no words yet" % k.get("id"))
            continue
        problems.extend(validate_fragment(k))
        n_sets += _check_fragment_sets(k, codexes[:2], problems, weak, seen_weak, False, two_part=True)

    # 3) Boss passwords: every chunk a full 4-option set at L4-L6
    import glob
    import json
    for path in sorted(glob.glob(gdev.path("bosses", "*.json"))):
        try:
            with open(path) as fh:
                boss = json.load(fh)
        except (IOError, OSError, ValueError) as err:
            problems.append("%s: %s" % (path, err))
            continue
        bid = boss.get("id") or path
        word = str(boss.get("password_word", "")).upper()
        chunks = [str(c).upper() for c in boss.get("password_chunks") or []]
        if "".join(chunks) != word or not chunks:
            problems.append("%s: password_chunks %s do not join to %s" % (bid, chunks, word))
        pid = boss.get("password_intel_id")
        if pid and not any(f.get("id") == pid for f in frags):
            notes.append("%s: password fragment %s is not in intel.json yet" % (bid, pid))
        for level in (4, 5, 6):
            for ch in chunks:
                cl = breach_clusters(ch, level)
                for cname, codex in codexes:
                    _check_set("%s chunk %s (%s)" % (bid, ch, cname), cl, level, codex, "%s|%s|%d" % (bid, ch, level),
                               problems, options=4, want_full=True)
                    n_sets += 1
                if verbose and level == 4:
                    opts, _i = build_option_set(cl, level, None, random.Random(1), options=4)
                    print("  %-10s %-6s %-16s -> %s" % (bid, ch, " ".join(cl), "  ".join(
                        ("*" if o["correct"] else "") + o["text"] for o in opts)))

    # 4) Telemetry templates filled with sample values
    templates = content.get("telemetry_templates") or {}
    for act in sorted(templates):
        for ti, tpl in enumerate(templates[act] or []):
            for si, vals in enumerate(TELEMETRY_SAMPLES):
                try:
                    text = str(tpl).format(**vals)
                except (KeyError, IndexError, ValueError) as err:
                    problems.append("telemetry %s #%d: bad placeholder %s in %r" % (act, ti, err, tpl))
                    break
                words = text_to_words(text, TELEMETRY_DENSITY.get(act, 0.5), random.Random("%s|%d|%d" % (act, ti, si)))
                frag = {"id": "TEL-%s-%d-%d" % (act, ti, si), "tier": "TELEMETRY", "words": words}
                problems.extend(validate_fragment(frag, min_targets=1))
                n_sets += _check_fragment_sets(frag, codexes[:2], problems, [], set(), False)

    # 5) mirror_dialect packets: dialect clusters past the cap, decoys from sampled histories
    dialects = _dialects(codexes)
    for f in frags:
        words = parse_fragment(f)
        for level in (3, 4, 5, 6):
            cfg = LEVELS[level]
            targets = choose_targets(words, cfg, random.Random("%s|dialect|%d" % (f.get("id"), level)), level)
            for i in targets:
                for dname, dialect in dialects:
                    rng = random.Random("%s|%d|%s" % (f.get("id"), i, dname))
                    cl = dialect_clusters(display_clusters(words[i], True, cfg), dialect, rng)
                    if plain(cl) != plain(words[i]["c"]):
                        problems.append("%s: dialect changed the word %s -> %s" % (f.get("id"), plain(words[i]["c"]),
                                                                                  " ".join(cl)))
                    _check_set("%s dialect %s" % (f.get("id"), dname), cl, level, codexes[1][1],
                               "%s|%d|%s|%d" % (f.get("id"), i, dname, level), problems, history=dialect["history"])
                    n_sets += 1

    # 6) Reversed picks: every letter can be mis-encoded 3 ways; sampled handles; Mirror counter-hacks
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if len(misencodings(letter)) < 3:
            problems.append("reversed: letter %s has only %d mis-encodings %s" % (letter, len(misencodings(letter)),
                                                                                   misencodings(letter)))
    for handle in _sample_handles():
        chunks = _handle_chunks(handle)
        if "".join(chunks) != handle or not (2 <= len(chunks) <= 4) or any(not 1 <= len(c) <= 3 for c in chunks):
            problems.append("handle %s: bad chunks %s" % (handle, chunks))
        for ch in chunks:
            for dname, dialect in dialects[:4]:
                opts, info = build_reversed_set(ch, 4, codexes[1][1], random.Random("%s|%s" % (ch, dname)), True, dialect)
                n_sets += 1
                errs = check_reversed(opts, ch, True, 4)
                if info["short"]:
                    errs.append("short set")
                if info["rebuilt"]:
                    errs.append("needed a rebuild")
                if errs:
                    problems.append("handle %s chunk %s (%s): %s -> %s" % (handle, ch, dname, "; ".join(errs),
                                                                          [o["text"] for o in opts]))
        if verbose and handle in ("TECH", "DHRUVA", "JQX"):
            for ch in chunks:
                opts, _i = build_reversed_set(ch, 4, None, random.Random(1), True)
                print("  handle %-8s %-4s -> %s" % (handle, ch, "  ".join(("*" if o["correct"] else "") + o["text"]
                                                                          for o in opts)))
    for f in frags:
        words = parse_fragment(f)
        for i, w in enumerate(words):
            if not w["target"]:
                continue
            for dname, dialect in dialects[:3]:
                word = plain(w["c"])
                opts, info = build_reversed_set(word, 4, None, random.Random("%s|%d|%s" % (f.get("id"), i, dname)),
                                                False, dialect)
                n_sets += 1
                errs = check_reversed(opts, word, False, 4)
                if errs or info["rebuilt"]:
                    problems.append("%s reversed %s (%s): %s" % (f.get("id"), word, dname, "; ".join(errs) or "rebuilt"))

    # 6b) The Mirror's counter-hack packets (intel.json mirror.packets): forward and reversed
    packets = [p for p in ((content.get("mirror") or {}).get("packets") or []) if isinstance(p, dict)]
    for pk in packets:
        if not pk.get("words"):
            notes.append("mirror packet %s has no words" % pk.get("id"))
            continue
        problems.extend(validate_fragment(pk))
        n_sets += _check_fragment_sets(pk, codexes, problems, weak, seen_weak, False)
        for i, w in enumerate(parse_fragment(pk)):
            if not w["target"]:
                continue
            for dname, dialect in dialects[:3]:
                word = plain(w["c"])
                opts, info = build_reversed_set(word, 4, None, random.Random("%s|%d|%s" % (pk.get("id"), i, dname)),
                                                False, dialect)
                n_sets += 1
                errs = check_reversed(opts, word, False, 4)
                if errs or info["rebuilt"]:
                    problems.append("mirror %s reversed %s (%s): %s" % (pk.get("id"), word, dname,
                                                                       "; ".join(errs) or "rebuilt"))

    # 7) Sessions on the runtime paths (breach per boss, handle breach, two-part, replay)
    problems.extend(_check_sessions(content))
    return problems, weak, n_sets, notes


def _check_sessions(content):
    """Build real sessions headless and finish them: a ROOT BREACH per boss (normal and pre-solved),
    the PATCH handle breach, a two-part Key packet and a stale replay of a snapshot."""
    import glob
    import json
    out = []
    s = dict(settings_mod.DEFAULTS)
    codex = Codex()
    try:
        for path in sorted(glob.glob(gdev.path("bosses", "*.json"))):
            with open(path) as fh:
                boss = json.load(fh)
            chunks = boss.get("password_chunks") or []
            for pre in ([], list(range(int(math.ceil(len(chunks) / 2.0))))):
                spec = {"title": "R00T BR34CH :: %s" % boss.get("leet_name"), "password_word": boss.get("password_word"),
                        "chunks": chunks, "presolved": pre, "prestrike": bool(pre), "level": 4, "mode": "normal",
                        "boss_id": boss.get("id")}
                b = BreachSession(spec, s, codex, random.Random(1), {"lexicon": True})
                b.dev_complete()
                for _ in range(120):
                    b.update(1 / 60.0)
                if b.result.get("outcome") != "success" or b.result.get("locked") != list(range(len(chunks))):
                    out.append("session: breach %s presolved %s -> %r" % (boss.get("id"), pre, b.result.get("outcome")))
        for handle in ("TECH", "QWERTYZXJBNM", "JAX"):
            spec = {"title": handle, "password_word": handle, "chunks": _handle_chunks(handle), "presolved": [],
                    "prestrike": False, "level": 4, "mode": "reversed", "boss_id": "mirror"}
            b = BreachSession(spec, s, codex, random.Random(2), {})
            if len([x for x in b.slots if x]) != 4:
                out.append("session: handle breach %s shows %d options" % (handle, len([x for x in b.slots if x])))
        keys = [k for k in content.get("keys", []) or [] if k.get("words")]
        intel = IntelLog()
        for k in keys[:3] or [FALLBACK_FRAGMENT]:
            m = _BreachTarget("F1R3W4LL")
            m.kind = "f1r3w4ll"
            h = HackSession(m, s, codex, intel, random.Random(3),
                            {"fragment": dict(k), "key_source": "check", "two_part": True, "level": 4, "lexicon": True})
            snap = h.snapshot()
            h.dev_complete()
            r = HackSession(m, s, codex, intel, random.Random(4), {"stale": True, "replay": snap})
            if r.target_words() != h.target_words() or not r.result.get("stale"):
                out.append("session: replay of %s changed its words" % k.get("id"))
    except Exception as err:                         # a crash here is exactly what --check is for
        import traceback
        traceback.print_exc()
        out.append("session: %s: %s" % (type(err).__name__, err))
    return out


if __name__ == "__main__":
    if "--check" in sys.argv:
        problems, weak, n, notes = self_check("--samples" in sys.argv)
        print("hack self-check: %d fragments, %d option sets, %d problems, %d weak sets (L2-L3, no real-word decoy)"
              % (len(load_fragments()), n, len(problems), len(weak)))
        for p in problems[:50]:
            print("  PROBLEM", p)
        if len(problems) > 50:
            print("  ... %d more" % (len(problems) - 50))
        for fid, level, word, opts in weak:
            print("  weak    %s L%d %-20s %s" % (fid, level, word, " / ".join(opts)))
        for note in notes:
            print("  note    %s" % note)
        sys.exit(1 if problems else 0)
    print("usage: python3 hack.py --check [--samples]")
