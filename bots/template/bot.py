"""t5115: tournament submission bot.

Pure Python strategy tuned for the Fullhouse sandbox constraints:
768 MB RAM, 0.5 CPU, and a 2 second decision clock.
"""

import itertools
import random
import time
from collections import defaultdict, deque


BOT_NAME = "t5115"
BOT_AVATAR = "robot_1"
BB = 100
START = 10000
MAX_EQUITY_SECONDS = 0.46
R = {r: i for i, r in enumerate("23456789TJQKA", 2)}
DECK = [r + s for r in "23456789TJQKA" for s in "shdc"]

OPP = defaultdict(lambda: defaultdict(int))
SEEN = set()
RAISED_HANDS = set()
MOMENTUM = deque(maxlen=20)
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
        score = eval5(combo)
        if best is None or score > best:
            best = score
    return best


def preflop_strength(hole):
    a, b = sorted(vals(hole), reverse=True)
    suited = hole[0][1] == hole[1][1]
    gap = abs(a - b)
    if a == b:
        return clamp(0.50 + (a - 2) / 13.0, 0, 1)
    s = 0.16 + a / 22.5 + b / 35.0
    if suited:
        s += 0.085
    if gap == 1:
        s += 0.075
    elif gap == 2:
        s += 0.04
    elif gap >= 5:
        s -= 0.08
    if a == 14:
        s += 0.08
    if a >= 12 and b >= 10:
        s += 0.065
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
    straighty = any(len({lo, lo + 1, lo + 2, lo + 3, lo + 4} & rs) >= 3 for lo in range(1, 11))
    flushy = max(suits.values()) >= 3
    paired = len({c[0] for c in board}) < len(board)
    wet = (0.34 if straighty else 0) + (0.34 if flushy else 0) + (0.12 if paired else 0)
    scary = any(R[c[0]] >= 12 for c in board)
    return clamp(wet, 0, 1), scary


def draw_info(hole, board):
    cards = hole + board
    suits = defaultdict(int)
    for c in cards:
        suits[c[1]] += 1
    rs = set(vals(cards))
    if 14 in rs:
        rs.add(1)
    fd = len(board) < 5 and max(suits.values()) >= 4
    sd = False
    open_ended = False
    gutshot = False
    for lo in range(1, 11):
        run = {lo, lo + 1, lo + 2, lo + 3, lo + 4}
        have = len(run & rs)
        if have >= 4:
            sd = True
            missing = list(run - rs)
            if missing and missing[0] in (lo, lo + 4):
                open_ended = True
            else:
                gutshot = True
    top_board = max(vals(board), default=0)
    overcards = sum(1 for r in vals(hole) if r > top_board) if len(board) == 3 else 0
    if fd and sd:
        bonus = 0.30
    elif fd and open_ended:
        bonus = 0.25
    elif fd or open_ended:
        bonus = 0.18
    elif sd:
        bonus = 0.12 if gutshot else 0.15
    else:
        bonus = 0.0
    if overcards == 2:
        bonus += 0.045
    elif overcards == 1:
        bonus += 0.025
    return {
        "bonus": min(0.34, bonus),
        "flush": fd,
        "straight": sd,
        "open": open_ended,
        "gutshot": gutshot,
        "overcards": overcards,
    }


def draw_bonus(hole, board):
    return draw_info(hole, board)["bonus"]


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
        hand = e.get("hand_num")
        bid = e.get("bot_id")
        if not bid or bid == me:
            if e.get("action") in ("raise", "all_in"):
                RAISED_HANDS.add(hand)
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
        if a == "check":
            st["checks"] += 1
        if a == "all_in":
            st["allins"] += 1
        if a in ("raise", "all_in"):
            RAISED_HANDS.add(hand)
        if a == "fold":
            st["folds"] += 1
            if hand in RAISED_HANDS:
                st["fold_to_raise"] += 1
        if len(RAISED_HANDS) > 120:
            RAISED_HANDS.clear()


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
    hand_id = str(state.get("hand_id", ""))
    if "_h" in hand_id:
        try:
            return int(hand_id.rsplit("_h", 1)[1])
        except ValueError:
            pass
    return 0


def table_profile(state):
    folds = []
    calls = []
    raises = []
    fold_to_raises = []
    for p in state["players"]:
        st = OPP.get(p.get("bot_id"))
        if st and st["actions"] >= 5:
            actions = max(1, st["actions"])
            folds.append(st["folds"] / actions)
            calls.append(st["calls"] / actions)
            raises.append(st["raises"] / actions)
            if st["folds"]:
                fold_to_raises.append(st["fold_to_raise"] / max(1, st["folds"]))
    return (
        sum(folds) / len(folds) if folds else 0.33,
        sum(calls) / len(calls) if calls else 0.25,
        sum(raises) / len(raises) if raises else 0.18,
        sum(fold_to_raises) / len(fold_to_raises) if fold_to_raises else 0.35,
    )


def active_profile(state):
    calls = []
    raises = []
    folds = []
    for p in state["players"]:
        if p["seat"] == state["seat_to_act"] or p.get("is_folded"):
            continue
        st = OPP.get(p.get("bot_id"))
        if st and st["actions"] >= 5:
            actions = max(1, st["actions"])
            calls.append(st["calls"] / actions)
            raises.append(st["raises"] / actions)
            folds.append(st["folds"] / actions)
    if not calls:
        return 0.25, 0.18, 0.33
    return sum(calls) / len(calls), sum(raises) / len(raises), sum(folds) / len(folds)


def recent_pressure(state):
    hero = state["seat_to_act"]
    heat = 0.0
    raise_count = 0
    all_in = False
    for action in state.get("action_log", [])[-8:]:
        if action.get("seat") == hero:
            continue
        act = action.get("action")
        if act == "raise":
            raise_count += 1
            heat += 0.22
        elif act == "all_in":
            raise_count += 1
            all_in = True
            heat += 0.36
    owed = state.get("amount_owed", 0)
    pot = max(1, state.get("pot", 1))
    heat += clamp(owed / max(1, pot), 0, 1.2) * 0.24
    heat += clamp(owed / max(1, state.get("your_stack", 1)), 0, 1.2) * 0.30
    return clamp(heat, 0, 1), raise_count, all_in


def risk_numbers(state, pos):
    stacks = [p.get("stack", 0) for p in state["players"] if not p.get("is_folded")]
    avg = sum(stacks) / max(1, len(stacks))
    ratio = state["your_stack"] / max(1, avg)
    phase = clamp(hand_num(state) / 400.0, 0, 1)
    foldy, cally, raisey, fold_to_raise = table_profile(state)
    active_call, active_raise, active_fold = active_profile(state)
    heat, _, _ = recent_pressure(state)
    mom = sum(MOMENTUM) / START
    agg = 0.42 + pos * 0.13 + phase * 0.12 + clamp(ratio - 1, -1, 2) * 0.14
    agg += foldy * 0.13 + clamp(mom, -1, 1) * 0.09
    fe = 0.36 + foldy * 0.18 + active_fold * 0.16 + fold_to_raise * 0.10
    fe += pos * 0.11 + agg * 0.10 - cally * 0.05 - active_call * 0.12
    if state["amount_owed"] > 0:
        fe -= 0.10 + raisey * 0.06 + active_raise * 0.10 + heat * 0.18
    opps = len([p for p in state["players"] if p["seat"] != state["seat_to_act"] and not p.get("is_folded")])
    fe -= max(0, opps - 1) * 0.07
    if active_call > 0.40:
        agg -= 0.04
    if active_raise > 0.30:
        agg -= 0.05
    if state["your_stack"] < 10 * BB:
        agg -= 0.08
    if heat > 0.55:
        agg -= 0.08
    return clamp(agg, 0.16, 0.88), clamp(fe, 0.05, 0.80)


def equity(hole, board, opps, sims):
    known = set(hole + board)
    deck = [c for c in DECK if c not in known]
    need_board = 5 - len(board)
    need = need_board + opps * 2
    wins = ties = done = 0
    end = time.perf_counter() + MAX_EQUITY_SECONDS
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


def bet_size(state, kind, agg, wet):
    pot = max(BB, state["pot"])
    if kind == "value":
        mult = random.choice([0.60, 0.75, 0.95])
    elif kind == "thin":
        mult = random.choice([0.36, 0.46, 0.56])
    elif kind == "semi":
        mult = random.choice([0.52, 0.68, 0.86])
    else:
        mult = random.choice([0.42, 0.56, 0.72])
    return legal_raise(state, state["current_bet"] + pot * (mult + agg * 0.12 + wet * 0.06))


def preflop(state, pos, agg, fe):
    s = preflop_strength(state["your_cards"])
    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    heat, raise_count, all_in = recent_pressure(state)
    pressure = s + pos * 0.13 + fe * 0.15 + agg * 0.11
    stack_bb = state["your_stack"] / BB

    if all_in and s < (0.70 if stack_bb <= 8 else 0.78):
        return {"action": "fold"}
    if stack_bb <= 8 and pressure > 0.78:
        return {"action": "all_in"}
    if stack_bb <= 14 and pressure > 0.90 and heat < 0.45:
        return {"action": "all_in"}
    if owed == 0:
        if pressure > 0.60:
            mult = random.choice([2.15, 2.55, 3.05])
            if pos > 0.70 and fe > 0.48:
                mult = random.choice([2.35, 2.85, 3.45])
            return {"action": "raise", "amount": legal_raise(state, state["min_raise_to"] * mult)}
        return {"action": "check"}

    if s > 0.82:
        mult = random.choice([2.45, 3.10, 3.85])
        if raise_count >= 2:
            mult = random.choice([3.10, 4.20, 5.20])
        target = legal_raise(state, max(state["min_raise_to"], state["current_bet"] * mult))
        return {"action": "all_in"} if target > (state["your_stack"] + state["your_bet_this_street"]) * 0.84 else {"action": "raise", "amount": target}

    if heat > 0.62 and s < 0.67:
        return {"action": "check"} if state["can_check"] else {"action": "fold"}

    if pressure > 0.77 and owed < state["your_stack"] * 0.20 and heat < 0.50:
        return {"action": "raise", "amount": legal_raise(state, state["current_bet"] + pot * random.choice([0.75, 1.05]))}
    price = owed / max(1, pot + owed)
    call_edge = s * 0.34 + 0.055 - heat * 0.05
    if price < call_edge:
        return {"action": "call"}
    return {"action": "check"} if state["can_check"] else {"action": "fold"}


def postflop(state, pos, agg, fe):
    hole, board = state["your_cards"], state["community_cards"]
    made = eval7(hole + board)
    cat = made[0]
    wet, scary = texture(board)
    opps = max(1, len([p for p in state["players"] if p["seat"] != state["seat_to_act"] and not p.get("is_folded")]))
    sims = 760 if opps == 1 else 460
    if state["street"] == "river":
        sims += 160
    eq = equity(hole, board, opps, sims)
    draws = draw_info(hole, board)
    draw = draws["bonus"]
    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    price = owed / max(1, pot + owed)
    heat, raise_count, all_in = recent_pressure(state)
    spr = state["your_stack"] / max(1, pot)
    big_bet = owed > pot * 0.55 or owed > state["your_stack"] * 0.25
    recent = state.get("action_log", [])[-10:]
    hero = state["seat_to_act"]
    hero_aggressed = any(a.get("seat") == hero and a.get("action") in ("raise", "all_in") for a in recent)
    passive_line = not any(
        a.get("seat") != hero and a.get("action") in ("raise", "all_in") for a in recent[-5:]
    )
    bluff = fe * 0.45 + agg * 0.26 + pos * 0.13 + draw - wet * 0.12 - max(0, opps - 1) * 0.08
    if scary and pos > 0.45:
        bluff += 0.06
    if hero_aggressed and state["street"] == "flop":
        bluff += 0.07
    if passive_line and pos > 0.40:
        bluff += 0.04
    bluff -= heat * 0.22
    value_edge = eq - price

    if state["can_check"]:
        if cat >= 5 or eq > 0.78:
            return {"action": "raise", "amount": bet_size(state, "value", agg, wet)}
        if cat >= 2 or eq > 0.61:
            freq = 0.58 + agg * 0.18 - wet * 0.10
            return {"action": "raise", "amount": bet_size(state, "thin", agg, wet)} if random.random() < freq else {"action": "check"}
        if (draws["flush"] or draws["open"]) and bluff > 0.48:
            return {"action": "raise", "amount": bet_size(state, "semi", agg, wet)}
        if draws["gutshot"] and bluff > 0.62 and opps <= 2:
            return {"action": "raise", "amount": bet_size(state, "semi", agg, wet)}
        if opps <= 2 and bluff > 0.60 and random.random() < bluff * 0.50:
            return {"action": "raise", "amount": bet_size(state, "bluff", agg, wet)}
        return {"action": "check"}

    if cat >= 5 or eq > 0.82:
        if all_in and eq > 0.72:
            return {"action": "call"}
        if spr < 1.7 and eq > 0.68:
            return {"action": "all_in"}
        if agg + fe > 0.98 and heat < 0.75:
            return {"action": "raise", "amount": bet_size(state, "value", agg, wet)}
        return {"action": "call"}

    if all_in and eq < 0.66:
        return {"action": "fold"}

    if state["street"] == "river" and big_bet and cat < 2 and eq < 0.58:
        return {"action": "fold"}

    if cat >= 2 and value_edge > 0.10 + heat * 0.06:
        if eq > 0.70 and fe > 0.35 and heat < 0.52 and random.random() < agg * 0.45:
            return {"action": "raise", "amount": bet_size(state, "thin", agg, wet)}
        return {"action": "call"}

    strong_draw = draws["flush"] or draws["open"]
    marginal_draw = draws["straight"] or draws["overcards"] == 2
    if strong_draw and eq + draw > price + 0.04 + heat * 0.04 and owed < state["your_stack"] * 0.32:
        if bluff > 0.60 and raise_count == 0 and random.random() < 0.42 + agg * 0.10:
            return {"action": "raise", "amount": bet_size(state, "semi", agg, wet)}
        return {"action": "call"}

    if marginal_draw and eq + draw > price + 0.08 + heat * 0.05 and owed < state["your_stack"] * 0.18:
        return {"action": "call"}

    if bluff > 0.73 and opps == 1 and owed < state["your_stack"] * 0.18 and raise_count == 0 and random.random() < 0.32:
        return {"action": "raise", "amount": bet_size(state, "bluff", agg, wet)}

    if eq > price + 0.09 + heat * 0.05 and not (owed > pot * 0.65 and cat == 0):
        return {"action": "call"}
    return {"action": "fold"}


def decide(state):
    if state.get("type") == "warmup":
        return {"action": "check"}
    update(state)
    pos = position(state)
    agg, fe = risk_numbers(state, pos)
    if state["street"] == "preflop":
        return preflop(state, pos, agg, fe)
    return postflop(state, pos, agg, fe)
