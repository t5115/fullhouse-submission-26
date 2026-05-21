"""Adaptive Hammer: controlled LAG pressure bot.

The old aggressor raised blindly. This version still attacks often, but it
uses hand strength, equity, board texture, position, and opponent tendencies.
"""

import itertools
import random
import time
from collections import defaultdict, deque


BOT_NAME = "Adaptive Hammer"
BB = 100
START = 10000
R = {r: i for i, r in enumerate("23456789TJQKA", 2)}
DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]

STYLE_RISK = 0.80
STYLE_BLUFF = 0.78
STYLE_VALUE = 0.56
STYLE_LOOSE = 0.10

STATS = defaultdict(lambda: defaultdict(int))
SEEN = set()
MOMENTUM = deque(maxlen=20)
LAST_STACK = None


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def rv(cards):
    return [R[c[0]] for c in cards]


def straight_high(vals):
    vals = set(vals)
    if 14 in vals:
        vals.add(1)
    high = 0
    for lo in range(1, 11):
        if {lo, lo + 1, lo + 2, lo + 3, lo + 4} <= vals:
            high = lo + 4
    return high


def eval5(cards):
    rs = rv(cards)
    cnt = defaultdict(int)
    for r in rs:
        cnt[r] += 1
    groups = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    st = straight_high(cnt)
    if flush and st:
        return (8, st)
    if groups[0][1] == 4:
        q = groups[0][0]
        return (7, q, max(r for r in rs if r != q))
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5, *sorted(rs, reverse=True))
    if st:
        return (4, st)
    if groups[0][1] == 3:
        t = groups[0][0]
        return (3, t, *sorted([r for r in rs if r != t], reverse=True))
    if groups[0][1] == 2 and groups[1][1] == 2:
        p1, p2 = groups[0][0], groups[1][0]
        return (2, p1, p2, max(r for r in rs if r != p1 and r != p2))
    if groups[0][1] == 2:
        p = groups[0][0]
        return (1, p, *sorted([r for r in rs if r != p], reverse=True))
    return (0, *sorted(rs, reverse=True))


def eval7(cards):
    best = None
    for combo in itertools.combinations(cards, 5):
        score = eval5(combo)
        if best is None or score > best:
            best = score
    return best


def preflop_strength(cards):
    a, b = sorted(rv(cards), reverse=True)
    suited = cards[0][1] == cards[1][1]
    gap = abs(a - b)
    if a == b:
        return clamp(0.50 + (a - 2) / 13.0, 0.0, 1.0)
    s = 0.18 + a / 22.0 + b / 34.0
    if suited:
        s += 0.09
    if gap == 1:
        s += 0.08
    elif gap == 2:
        s += 0.04
    elif gap >= 5:
        s -= 0.08
    if a == 14:
        s += 0.08
    if a >= 11 and b >= 10:
        s += 0.07
    return clamp(s + STYLE_LOOSE, 0.0, 1.0)


def texture(board):
    if not board:
        return {"wet": 0.0, "scary": False}
    vals = set(rv(board))
    suits = defaultdict(int)
    for c in board:
        suits[c[1]] += 1
    if 14 in vals:
        vals.add(1)
    connected = any(len({lo, lo + 1, lo + 2, lo + 3, lo + 4} & vals) >= 3 for lo in range(1, 11))
    flushy = max(suits.values()) >= 3
    paired = len({c[0] for c in board}) < len(board)
    wet = (0.35 if connected else 0.0) + (0.35 if flushy else 0.0) + (0.15 if paired else 0.0)
    return {"wet": clamp(wet, 0, 1), "scary": any(R[c[0]] >= 12 for c in board)}


def draws(hole, board):
    cards = hole + board
    suits = defaultdict(int)
    for c in cards:
        suits[c[1]] += 1
    vals = set(rv(cards))
    if 14 in vals:
        vals.add(1)
    fd = len(board) < 5 and max(suits.values()) >= 4
    sd = any(len({lo, lo + 1, lo + 2, lo + 3, lo + 4} & vals) >= 4 for lo in range(1, 11))
    return fd, sd, fd and sd


def update(state):
    global LAST_STACK
    stack = state.get("your_stack")
    if stack is not None:
        if LAST_STACK is not None and abs(stack - LAST_STACK) >= BB:
            MOMENTUM.append(stack - LAST_STACK)
        LAST_STACK = stack

    me = state["players"][state["seat_to_act"]]["bot_id"]
    for e in state.get("match_action_log", []):
        key = (e.get("hand_num"), e.get("seat"), e.get("bot_id"), e.get("action"), e.get("amount"))
        if key in SEEN:
            continue
        SEEN.add(key)
        if len(SEEN) > 900:
            SEEN.clear()
        bid = e.get("bot_id")
        if not bid or bid == me:
            continue
        a = e.get("action")
        st = STATS[bid]
        st["actions"] += 1
        st["hands_" + str(e.get("hand_num"))] = 1
        if a in ("call", "raise", "all_in"):
            st["vpip"] += 1
        if a in ("raise", "all_in"):
            st["raises"] += 1
        if a == "call":
            st["calls"] += 1
        if a == "fold":
            st["folds"] += 1
        if a == "check":
            st["checks"] += 1


def pos_score(state):
    n = len(state["players"])
    hero = state["seat_to_act"]
    active = [p["seat"] for p in state["players"] if not p.get("is_folded") and not p.get("is_all_in")]
    if hero not in active:
        active.append(hero)
    sb = bb = None
    for a in state.get("action_log", [])[:4]:
        if a.get("action") == "small_blind":
            sb = a.get("seat")
        if a.get("action") == "big_blind":
            bb = a.get("seat")
    if sb is None or bb is None:
        return hero / max(1, n - 1)
    dealer = sb if n == 2 else (sb - 1) % n
    start = (bb + 1) % n if state["street"] == "preflop" else (dealer + 1) % n
    order = [s for i in range(n) for s in [(start + i) % n] if s in active]
    return 0.5 if hero not in order or len(order) <= 1 else order.index(hero) / (len(order) - 1)


def hand_num(state):
    for e in reversed(state.get("match_action_log", [])):
        if "hand_num" in e:
            return int(e["hand_num"])
    return 0


def table_foldiness(state):
    vals = []
    for p in state["players"]:
        bid = p.get("bot_id")
        st = STATS.get(bid)
        if st and st["actions"] >= 5:
            vals.append(st["folds"] / max(1, st["actions"]))
    return sum(vals) / len(vals) if vals else 0.34


def aggression(state, pos):
    stacks = [p.get("stack", 0) for p in state["players"] if not p.get("is_folded")]
    avg = sum(stacks) / max(1, len(stacks))
    ratio = state["your_stack"] / max(1, avg)
    phase = clamp(hand_num(state) / 400.0, 0, 1)
    mom = sum(MOMENTUM) / START
    a = 0.45 + STYLE_RISK * 0.24 + pos * 0.15 + phase * 0.14
    a += clamp(ratio - 1, -1, 2) * 0.16 + table_foldiness(state) * 0.15 + clamp(mom, -1, 1) * 0.12
    if state["your_stack"] < 10 * BB:
        a -= 0.12
    return clamp(a, 0.20, 0.96)


def fold_equity(state, pos, agg):
    opps = len([p for p in state["players"] if p["seat"] != state["seat_to_act"] and not p.get("is_folded")])
    fe = 0.45 - 0.08 * max(0, opps - 1) + pos * 0.12 + agg * 0.12 + table_foldiness(state) * 0.18
    if state["amount_owed"] > 0:
        fe -= 0.10
    if any(a.get("action") in ("raise", "all_in") for a in state.get("action_log", [])[-4:]):
        fe -= 0.09
    return clamp(fe, 0.05, 0.82)


def equity(hole, board, opps, sims):
    known = set(hole + board)
    deck = [c for c in DECK if c not in known]
    need_board = 5 - len(board)
    need = need_board + opps * 2
    if need > len(deck):
        return 0.0
    wins = ties = done = 0
    end = time.perf_counter() + 0.55
    while done < sims and time.perf_counter() < end:
        sample = random.sample(deck, need)
        runout = board + sample[:need_board]
        idx = need_board
        hero = eval7(hole + runout)
        best = None
        for _ in range(opps):
            score = eval7(sample[idx:idx + 2] + runout)
            idx += 2
            if best is None or score > best:
                best = score
        if hero > best:
            wins += 1
        elif hero == best:
            ties += 1
        done += 1
    return (wins + ties * 0.5) / max(1, done)


def raise_to(state, total):
    cap = state["your_stack"] + state["your_bet_this_street"]
    return min(max(int(total), state["min_raise_to"]), cap)


def bet_size(state, kind, agg, tex):
    pot = max(BB, state["pot"])
    if kind == "value":
        m = random.choice([0.65, 0.80, 1.00])
    elif kind == "bluff":
        m = random.choice([0.45, 0.58, 0.72])
    elif kind == "semi":
        m = random.choice([0.55, 0.72, 0.90])
    else:
        m = random.choice([1.05, 1.30, 1.60])
    m += agg * 0.16 + tex["wet"] * 0.08
    return raise_to(state, state["current_bet"] + pot * m)


def preflop(state, pos, agg, fe):
    s = preflop_strength(state["your_cards"])
    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    pressure = s + pos * 0.15 + fe * 0.20 + agg * 0.16
    stack_bb = state["your_stack"] / BB

    if stack_bb <= 12 and pressure > 0.88:
        return {"action": "all_in"}
    if owed == 0:
        if pressure > 0.58:
            return {"action": "raise", "amount": raise_to(state, state["min_raise_to"] * random.choice([2.3, 2.8, 3.4, 4.2]))}
        return {"action": "check"}
    if s > 0.78:
        target = raise_to(state, max(state["min_raise_to"], state["current_bet"] * random.choice([2.6, 3.4, 4.4])))
        return {"action": "all_in"} if target > (state["your_stack"] + state["your_bet_this_street"]) * 0.84 else {"action": "raise", "amount": target}
    if pressure > 0.74 and owed < state["your_stack"] * 0.25:
        return {"action": "raise", "amount": raise_to(state, state["current_bet"] + pot * random.choice([0.8, 1.1, 1.5]))}
    call_price = owed / max(1, pot + owed)
    if call_price < s * 0.30 + 0.06:
        return {"action": "call"}
    return {"action": "check"} if state["can_check"] else {"action": "fold"}


def postflop(state, pos, agg, fe):
    hole, board = state["your_cards"], state["community_cards"]
    made = eval7(hole + board)
    cat = made[0]
    tex = texture(board)
    fd, sd, combo = draws(hole, board)
    opps = max(1, len([p for p in state["players"] if p["seat"] != state["seat_to_act"] and not p.get("is_folded")]))
    eq = equity(hole, board, opps, 800 if opps == 1 else 520)
    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    price = owed / max(1, pot + owed)
    draw_bonus = 0.26 if combo else 0.16 if (fd or sd) else 0
    bluff = fe * 0.55 + agg * STYLE_BLUFF * 0.35 + pos * 0.18 + draw_bonus - tex["wet"] * 0.15 - max(0, opps - 1) * 0.08
    if tex["scary"] and pos > 0.45:
        bluff += 0.08

    if state["can_check"]:
        if cat >= 5 or eq > 0.76:
            return {"action": "raise", "amount": bet_size(state, "over", agg, tex)}
        if cat >= 2 or eq > STYLE_VALUE:
            return {"action": "raise", "amount": bet_size(state, "value", agg, tex)}
        if (combo or fd or sd) and bluff > 0.52:
            return {"action": "raise", "amount": bet_size(state, "semi", agg, tex)}
        if opps <= 2 and bluff > 0.58 and random.random() < bluff:
            return {"action": "raise", "amount": bet_size(state, "bluff", agg, tex)}
        return {"action": "check"}

    if cat >= 5 or eq > 0.80:
        if agg + fe > 1.05:
            target = bet_size(state, "value", agg, tex)
            return {"action": "all_in"} if target > (state["your_stack"] + state["your_bet_this_street"]) * 0.84 else {"action": "raise", "amount": target}
        return {"action": "call"}
    if (combo or fd or sd) and eq + draw_bonus > price + 0.03 and owed < state["your_stack"] * 0.35:
        if bluff > 0.62 and random.random() < 0.55:
            return {"action": "raise", "amount": bet_size(state, "semi", agg, tex)}
        return {"action": "call"}
    if cat >= 2 and eq > price + 0.10:
        return {"action": "call"}
    if bluff > 0.72 and opps == 1 and owed < state["your_stack"] * 0.22 and random.random() < 0.42:
        return {"action": "raise", "amount": bet_size(state, "bluff", agg, tex)}
    if eq > price + 0.08 and not (owed > pot * 0.65 and cat == 0):
        return {"action": "call"}
    return {"action": "fold"}


def decide(state):
    if state.get("type") == "warmup":
        return {"action": "check"}
    update(state)
    pos = pos_score(state)
    agg = aggression(state, pos)
    fe = fold_equity(state, pos, agg)
    return preflop(state, pos, agg, fe) if state["street"] == "preflop" else postflop(state, pos, agg, fe)
