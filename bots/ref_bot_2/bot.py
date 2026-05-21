"""Counterpunch: adaptive anti-aggro value and trap bot."""

import itertools
import random
import time
from collections import defaultdict, deque


BOT_NAME = "Counterpunch"
BB = 100
START = 10000
R = {r: i for i, r in enumerate("23456789TJQKA", 2)}
DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]

OPP = defaultdict(lambda: defaultdict(int))
SEEN = set()
MOMENTUM = deque(maxlen=18)
LAST_STACK = None


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def vals(cards):
    return [R[c[0]] for c in cards]


def straight_high(rs):
    rs = set(rs)
    if 14 in rs:
        rs.add(1)
    best = 0
    for lo in range(1, 11):
        if {lo, lo + 1, lo + 2, lo + 3, lo + 4} <= rs:
            best = lo + 4
    return best


def eval5(cards):
    rs = vals(cards)
    counts = defaultdict(int)
    for r in rs:
        counts[r] += 1
    groups = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    straight = straight_high(counts)
    if flush and straight:
        return (8, straight)
    if groups[0][1] == 4:
        q = groups[0][0]
        return (7, q, max(r for r in rs if r != q))
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5, *sorted(rs, reverse=True))
    if straight:
        return (4, straight)
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
        s = eval5(combo)
        if best is None or s > best:
            best = s
    return best


def pf_strength(hole):
    a, b = sorted(vals(hole), reverse=True)
    suited = hole[0][1] == hole[1][1]
    gap = abs(a - b)
    if a == b:
        return clamp(0.50 + (a - 2) / 13.0, 0, 1)
    s = 0.15 + a / 23.0 + b / 37.0
    if suited:
        s += 0.08
    if gap == 1:
        s += 0.07
    elif gap == 2:
        s += 0.035
    elif gap >= 5:
        s -= 0.08
    if a == 14:
        s += 0.08
    if a >= 12 and b >= 10:
        s += 0.06
    return clamp(s, 0, 1)


def texture(board):
    if not board:
        return 0.0, False
    suits = defaultdict(int)
    for c in board:
        suits[c[1]] += 1
    rs = set(vals(board))
    if 14 in rs:
        rs.add(1)
    connected = any(len({lo, lo + 1, lo + 2, lo + 3, lo + 4} & rs) >= 3 for lo in range(1, 11))
    flushy = max(suits.values()) >= 3
    paired = len({c[0] for c in board}) < len(board)
    return clamp((0.34 if connected else 0) + (0.34 if flushy else 0) + (0.12 if paired else 0), 0, 1), any(R[c[0]] >= 12 for c in board)


def draw_bonus(hole, board):
    cards = hole + board
    suits = defaultdict(int)
    for c in cards:
        suits[c[1]] += 1
    rs = set(vals(cards))
    if 14 in rs:
        rs.add(1)
    fd = len(board) < 5 and max(suits.values()) >= 4
    sd = any(len({lo, lo + 1, lo + 2, lo + 3, lo + 4} & rs) >= 4 for lo in range(1, 11))
    return 0.30 if fd and sd else 0.18 if fd or sd else 0.0


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
        st = OPP[bid]
        st["actions"] += 1
        if a in ("call", "raise", "all_in"):
            st["vpip"] += 1
        if a in ("raise", "all_in"):
            st["raises"] += 1
        if a == "call":
            st["calls"] += 1
        if a == "fold":
            st["folds"] += 1


def position(state):
    n = len(state["players"])
    hero = state["seat_to_act"]
    active = [p["seat"] for p in state["players"] if not p.get("is_folded") and not p.get("is_all_in")]
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
    order = [s for i in range(n) for s in [(start + i) % n] if s in active or s == hero]
    return 0.5 if hero not in order or len(order) <= 1 else order.index(hero) / (len(order) - 1)


def hand_num(state):
    for e in reversed(state.get("match_action_log", [])):
        if "hand_num" in e:
            return int(e["hand_num"])
    return 0


def table_rates(state):
    folds = []
    raises = []
    calls = []
    for p in state["players"]:
        st = OPP.get(p.get("bot_id"))
        if st and st["actions"] >= 5:
            actions = max(1, st["actions"])
            folds.append(st["folds"] / actions)
            raises.append(st["raises"] / actions)
            calls.append(st["calls"] / actions)
    return (
        sum(folds) / len(folds) if folds else 0.33,
        sum(raises) / len(raises) if raises else 0.18,
        sum(calls) / len(calls) if calls else 0.25,
    )


def risk_numbers(state, pos):
    stacks = [p.get("stack", 0) for p in state["players"] if not p.get("is_folded")]
    avg = sum(stacks) / max(1, len(stacks))
    ratio = state["your_stack"] / max(1, avg)
    phase = clamp(hand_num(state) / 400.0, 0, 1)
    foldy, raisey, cally = table_rates(state)
    mom = sum(MOMENTUM) / START
    agg = 0.34 + pos * 0.11 + phase * 0.12 + clamp(ratio - 1, -1, 2) * 0.12 + foldy * 0.14 + clamp(mom, -1, 1) * 0.08
    fe = 0.34 + foldy * 0.24 + pos * 0.10 + agg * 0.10 - cally * 0.08
    trap = clamp(raisey * 1.8, 0, 0.35)
    if state["amount_owed"] > 0:
        fe -= 0.12
    opps = len([p for p in state["players"] if p["seat"] != state["seat_to_act"] and not p.get("is_folded")])
    fe -= max(0, opps - 1) * 0.08
    return clamp(agg, 0.12, 0.80), clamp(fe, 0.05, 0.78), trap


def equity(hole, board, opps, sims):
    known = set(hole + board)
    deck = [c for c in DECK if c not in known]
    need_board = 5 - len(board)
    need = need_board + opps * 2
    wins = ties = done = 0
    end = time.perf_counter() + 0.58
    while need <= len(deck) and done < sims and time.perf_counter() < end:
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
        wins += hero > best
        ties += hero == best
        done += 1
    return (wins + ties * 0.5) / max(1, done)


def legal_raise(state, desired):
    cap = state["your_stack"] + state["your_bet_this_street"]
    return min(max(int(desired), state["min_raise_to"]), cap)


def size(state, kind, agg, wet):
    pot = max(BB, state["pot"])
    if kind == "value":
        mult = random.choice([0.66, 0.82, 1.05])
    elif kind == "trap":
        mult = random.choice([0.45, 0.58, 0.70])
    elif kind == "semi":
        mult = random.choice([0.54, 0.70, 0.88])
    else:
        mult = random.choice([0.42, 0.56, 0.74])
    return legal_raise(state, state["current_bet"] + pot * (mult + agg * 0.10 + wet * 0.07))


def preflop(state, pos, agg, fe, trap):
    s = pf_strength(state["your_cards"])
    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    score = s + pos * 0.10 + fe * 0.13 + agg * 0.09
    if state["your_stack"] / BB <= 11 and score > 0.83:
        return {"action": "all_in"}
    if owed == 0:
        if score > 0.64:
            return {"action": "raise", "amount": legal_raise(state, state["min_raise_to"] * random.choice([2.2, 2.8, 3.4]))}
        return {"action": "check"}
    if s > 0.82:
        if trap > 0.20 and owed < state["your_stack"] * 0.18 and random.random() < trap:
            return {"action": "call"}
        return {"action": "raise", "amount": legal_raise(state, max(state["min_raise_to"], state["current_bet"] * random.choice([2.7, 3.5, 4.5])))}
    price = owed / max(1, pot + owed)
    if score > 0.76 and owed < state["your_stack"] * 0.22:
        return {"action": "raise", "amount": legal_raise(state, state["current_bet"] + pot * random.choice([0.8, 1.1]))}
    if price < s * 0.34 + 0.055:
        return {"action": "call"}
    return {"action": "check"} if state["can_check"] else {"action": "fold"}


def postflop(state, pos, agg, fe, trap):
    hole, board = state["your_cards"], state["community_cards"]
    made = eval7(hole + board)
    cat = made[0]
    wet, scary = texture(board)
    opps = max(1, len([p for p in state["players"] if p["seat"] != state["seat_to_act"] and not p.get("is_folded")]))
    eq = equity(hole, board, opps, 920 if opps == 1 else 560)
    draw = draw_bonus(hole, board)
    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    price = owed / max(1, pot + owed)
    bluff = fe * 0.40 + agg * 0.22 + pos * 0.11 + draw - wet * 0.13 - max(0, opps - 1) * 0.09
    if scary and pos > 0.45:
        bluff += 0.06

    if state["can_check"]:
        if cat >= 5 or eq > 0.80:
            kind = "trap" if trap > 0.18 and random.random() < trap else "value"
            return {"action": "raise", "amount": size(state, kind, agg, wet)}
        if cat >= 2 or eq > 0.63:
            return {"action": "raise", "amount": size(state, "value", agg, wet)} if random.random() < 0.62 else {"action": "check"}
        if draw and bluff > 0.48:
            return {"action": "raise", "amount": size(state, "semi", agg, wet)}
        if opps <= 2 and bluff > 0.61 and random.random() < bluff * 0.45:
            return {"action": "raise", "amount": size(state, "bluff", agg, wet)}
        return {"action": "check"}

    if cat >= 5 or eq > 0.83:
        if trap > 0.18 and owed < pot * 0.55 and random.random() < trap:
            return {"action": "call"}
        return {"action": "raise", "amount": size(state, "value", agg, wet)}
    if cat >= 2 and eq > price + 0.09:
        return {"action": "call"}
    if draw and eq + draw > price + 0.04 and owed < state["your_stack"] * 0.32:
        if bluff > 0.62 and random.random() < 0.42:
            return {"action": "raise", "amount": size(state, "semi", agg, wet)}
        return {"action": "call"}
    if bluff > 0.72 and opps == 1 and owed < state["your_stack"] * 0.18 and random.random() < 0.30:
        return {"action": "raise", "amount": size(state, "bluff", agg, wet)}
    if eq > price + 0.08 and not (owed > pot * 0.70 and cat == 0):
        return {"action": "call"}
    return {"action": "fold"}


def decide(state):
    if state.get("type") == "warmup":
        return {"action": "check"}
    update(state)
    pos = position(state)
    agg, fe, trap = risk_numbers(state, pos)
    if state["street"] == "preflop":
        return preflop(state, pos, agg, fe, trap)
    return postflop(state, pos, agg, fe, trap)
