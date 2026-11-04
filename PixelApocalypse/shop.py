"""Clean Node shop (doc 10, M12): BITS buy weapon upgrades, Exploit shards buy hacking tools.

Press F at a Clean Node terminal: it saves, heals, then opens the SHOP screen (menu.ShopScreen).

    items(run) -> [row dict]    every row: id, kind "weapon"|"tool", weapon, label, desc, price,
                                currency "BITS"|"SHARDS", owned bool, affordable bool
    buy(run, item_id) -> (ok, message)

Weapon upgrades (2 per weapon) live on run.player.upgrades and their effects in player.py.
Tools are progression.EXPLOITS: 5 shards buy any tool you have not unlocked yet, so you can
get one before its Daemon (a Core Dump whose tool you already own refunds the 5 shards).
REMOTE JACK can only be bought here.
"""
import progression as progression_mod

TOOL_PRICE = 5

# (id, weapon, label, description, price in Bits). Prices: doc 13 leaves them open.
WEAPON_UPGRADES = [
    ("SPLIT_NOCK", "BOW", "SPLIT NOCK", "a full-charge shot looses 3 arrows", 90),
    ("ARC_SHOT", "BOW", "ARC SHOT", "arrows lob over F1R3W4LL shields", 60),
    ("MAG_PLUS", "PISTOL", "+2 MAGAZINE", "8 rounds before the reload", 60),
    ("RICOCHET", "PISTOL", "RICOCHET", "bullets bounce once off walls", 90),
    ("LONG_EDGE", "KATANA", "LONG EDGE", "a wider, longer arc", 60),
    ("RIPOSTE", "KATANA", "RIPOSTE", "a parried hit answers with a 2-damage cut", 90),
]

TOOL_TEXT = {
    "LEXICON": "every first option set starts with 1 decoy struck",
    "WIDE_PORT": "jack-in range 64 -> 96 px",
    "TRACE_DAMPER": "-25% TRACE per wrong pick",
    "CHAIN_DAEMON": "OVERLOAD radius +50%",
    "LONG_TURN": "TURN lasts 30 s",
    "REMOTE_JACK": "jack into a PINNED minion from range",
}


def items(run):
    out = []
    p = run.player
    for uid, weapon, label, desc, price in WEAPON_UPGRADES:
        owned = uid in p.upgrades
        out.append({"id": uid, "kind": "weapon", "weapon": weapon, "label": label, "desc": desc,
                    "price": price, "currency": "BITS", "owned": owned,
                    "affordable": not owned and run.bits >= price})
    prog = run.progression
    for tid in progression_mod.EXPLOITS:
        owned = prog.has_exploit(tid)
        out.append({"id": tid, "kind": "tool", "weapon": "TOOL", "label": progression_mod.EXPLOIT_LABELS.get(tid, tid),
                    "desc": TOOL_TEXT.get(tid, ""), "price": TOOL_PRICE, "currency": "SHARDS", "owned": owned,
                    "affordable": not owned and prog.shards >= TOOL_PRICE})
    return out


def buy(run, item_id):
    row = None
    for r in items(run):
        if r["id"] == item_id:
            row = r
    if row is None:
        return False, "NO SUCH ITEM"
    if row["owned"]:
        return False, "%s :: ALREADY OWNED" % row["label"]
    if not row["affordable"]:
        return False, "%s :: NEEDS %d %s" % (row["label"], row["price"], row["currency"])
    if row["kind"] == "weapon":
        run.bits -= row["price"]
        run.player.add_upgrade(item_id)
    else:
        run.progression.shards -= row["price"]
        run.progression.unlock_exploit(item_id, refund=False)
    run.emit("shop_bought", item=item_id, kind=row["kind"], price=row["price"])
    return True, "%s :: INSTALLED" % row["label"]
