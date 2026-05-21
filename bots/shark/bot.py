"""PressureShark: aggressive exploitative tournament bot.

Pure Python, no poker libraries. The bot combines:
- 7-card hand evaluation
- bounded Monte Carlo equity
- opponent profiling from the rolling match log
- dynamic aggression and bluff selection
"""

import itertools
import random
import time
from collections import defaultdict, deque


BOT_NAME = "PressureShark"
BIG_BLIND = 100
STARTING_STACK = 10000
RANK_VALUE = {r: i for i, r in enumerate("23456789TJQKA", start=2)}
SUITS = "shdc"
FULL_DECK = [r + s for r in "23456789TJQKA" for s in SUITS]


OPPONENTS = defaultdict(lambda: {
    "actions": 0,
    "voluntary": 0,
    "raises": 0,
    "calls": 0,
    "folds": 0,
    "checks": 0,
    "allins": 0,
    "fold_after_raise": 0,
    "hands": set(),
})
SEEN_LOG_EVENTS = set()
RECENT_STACK_DELTAS = deque(maxlen=24)
LAST_STACK = None


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def ranks(cards):
    return [RANK_VALUE[c[0]] for c in cards]


def straight_high(unique_ranks):
    values = set(unique_ranks)
    if 14 in values:
        values.add(1)
    best = None
    for low in range(1, 11):
        run = {low, low + 1, low + 2, low + 3, low + 4}
        if run <= values:
            best = low + 4
    return 5 if best == 5 else best


def evaluate_five(cards):
    rs = ranks(cards)
    counts = defaultdict(int)
    for r in rs:
        counts[r] += 1

    groups = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    straight = straight_high(counts.keys())

    if flush and straight:
        return (8, straight)

    if groups[0][1] == 4:
        quad = groups[0][0]
        kicker = max(r for r in rs if r != quad)
        return (7, quad, kicker)

    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, groups[0][0], groups[1][0])

    if flush:
        return (5, *sorted(rs, reverse=True))

    if straight:
        return (4, straight)

    if groups[0][1] == 3:
        trip = groups[0][0]
        kickers = sorted([r for r in rs if r != trip], reverse=True)
        return (3, trip, *kickers)

    if groups[0][1] == 2 and groups[1][1] == 2:
        pair1, pair2 = groups[0][0], groups[1][0]
        kicker = max(r for r in rs if r != pair1 and r != pair2)
        return (2, pair1, pair2, kicker)

    if groups[0][1] == 2:
        pair = groups[0][0]
        kickers = sorted([r for r in rs if r != pair], reverse=True)
        return (1, pair, *kickers)

    return (0, *sorted(rs, reverse=True))


def evaluate_best(cards):
    best = None
    for combo in itertools.combinations(cards, 5):
        score = evaluate_five(combo)
        if best is None or score > best:
            best = score
    return best


def board_texture(board):
    if not board:
        return {"wet": 0.0, "paired": False, "flushy": False, "straighty": False, "scary": False}

    br = ranks(board)
    unique = set(br)
    suit_counts = defaultdict(int)
    for c in board:
        suit_counts[c[1]] += 1

    paired = len(unique) < len(br)
    flushy = max(suit_counts.values()) >= 3
    scary = any(r >= 12 for r in br)

    straighty = False
    values = set(unique)
    if 14 in values:
        values.add(1)
    for low in range(1, 11):
        if len({low, low + 1, low + 2, low + 3, low + 4} & values) >= 3:
            straighty = True
            break

    wet = 0.0
    wet += 0.30 if flushy else 0.0
    wet += 0.30 if straighty else 0.0
    wet += 0.15 if paired else 0.0
    wet += 0.10 if len(board) >= 4 and max(suit_counts.values()) >= 4 else 0.0
    return {
        "wet": clamp(wet, 0.0, 1.0),
        "paired": paired,
        "flushy": flushy,
        "straighty": straighty,
        "scary": scary,
    }


def draw_info(hole, board):
    cards = hole + board
    suit_counts = defaultdict(int)
    for c in cards:
        suit_counts[c[1]] += 1
    flush_draw = len(board) < 5 and max(suit_counts.values()) >= 4

    values = set(ranks(cards))
    if 14 in values:
        values.add(1)

    straight_draw = False
    open_ended = False
    for low in range(1, 11):
        run = {low, low + 1, low + 2, low + 3, low + 4}
        have = len(run & values)
        if have >= 4:
            straight_draw = True
            missing = sorted(run - values)
            if missing and low < missing[0] < low + 4:
                open_ended = True

    return {
        "flush_draw": flush_draw,
        "straight_draw": straight_draw,
        "open_ended": open_ended,
        "combo_draw": flush_draw and straight_draw,
    }


def preflop_score(hole):
    a, b = sorted(ranks(hole), reverse=True)
    suited = hole[0][1] == hole[1][1]
    gap = abs(a - b)

    if a == b:
        return clamp(0.48 + (a - 2) / 14.0, 0.0, 1.0)

    score = 0.18 + (a - 2) / 18.0 + (b - 2) / 30.0
    if suited:
        score += 0.08
    if gap == 1:
        score += 0.08
    elif gap == 2:
        score += 0.04
    elif gap >= 5:
        score -= 0.07
    if a == 14:
        score += 0.08
    if a >= 12 and b >= 10:
        score += 0.08
    return clamp(score, 0.0, 1.0)


def infer_hand_number(state):
    for entry in reversed(state.get("match_action_log", [])):
        if "hand_num" in entry:
            return int(entry["hand_num"])
    hand_id = str(state.get("hand_id", ""))
    if "_h" in hand_id:
        try:
            return int(hand_id.rsplit("_h", 1)[1])
        except ValueError:
            pass
    return 0


def blind_seats(state):
    sb = bb = None
    for action in state.get("action_log", [])[:4]:
        if action.get("action") == "small_blind":
            sb = action.get("seat")
        elif action.get("action") == "big_blind":
            bb = action.get("seat")
    return sb, bb


def active_seats(state):
    seats = []
    for p in state["players"]:
        if not p.get("is_folded") and not p.get("is_all_in") and p.get("stack", 0) > 0:
            seats.append(p["seat"])
    return seats


def position_score(state):
    n = len(state["players"])
    hero = state["seat_to_act"]
    active = active_seats(state)
    if hero not in active:
        active.append(hero)

    sb, bb = blind_seats(state)
    if sb is None or bb is None:
        return hero / max(n - 1, 1)

    dealer = sb if n == 2 else (sb - 1) % n
    if state["street"] == "preflop":
        start = (bb + 1) % n
    else:
        start = (dealer + 1) % n

    order = []
    for i in range(n):
        s = (start + i) % n
        if s in active:
            order.append(s)
    if hero not in order or len(order) <= 1:
        return 0.5
    return order.index(hero) / (len(order) - 1)


def update_opponent_model(state):
    global LAST_STACK

    my_seat = state.get("seat_to_act")
    players = state.get("players", [])
    my_id = players[my_seat].get("bot_id") if my_seat is not None and my_seat < len(players) else None

    current_stack = state.get("your_stack")
    if current_stack is not None:
        if LAST_STACK is not None:
            delta = current_stack - LAST_STACK
            if abs(delta) >= BIG_BLIND:
                RECENT_STACK_DELTAS.append(delta)
        LAST_STACK = current_stack

    previous_raise = {}
    for entry in state.get("match_action_log", []):
        key = (
            entry.get("hand_num"),
            entry.get("seat"),
            entry.get("bot_id"),
            entry.get("action"),
            entry.get("amount"),
        )
        if key in SEEN_LOG_EVENTS:
            continue
        SEEN_LOG_EVENTS.add(key)
        if len(SEEN_LOG_EVENTS) > 600:
            SEEN_LOG_EVENTS.clear()

        bot_id = entry.get("bot_id")
        if not bot_id or bot_id == my_id:
            continue

        action = str(entry.get("action", ""))
        hand_num = entry.get("hand_num")
        stat = OPPONENTS[bot_id]
        stat["hands"].add(hand_num)
        stat["actions"] += 1

        if action in ("call", "raise", "all_in"):
            stat["voluntary"] += 1
        if action == "raise":
            stat["raises"] += 1
            previous_raise[hand_num] = True
        elif action == "all_in":
            stat["allins"] += 1
            stat["raises"] += 1
            previous_raise[hand_num] = True
        elif action == "call":
            stat["calls"] += 1
        elif action == "fold":
            stat["folds"] += 1
            if previous_raise.get(hand_num):
                stat["fold_after_raise"] += 1
        elif action == "check":
            stat["checks"] += 1


def opponent_type(bot_id):
    stat = OPPONENTS[bot_id]
    actions = max(1, stat["actions"])
    vpip = stat["voluntary"] / max(1, len(stat["hands"]))
    raise_rate = stat["raises"] / actions
    call_rate = stat["calls"] / actions
    fold_rate = stat["folds"] / actions

    if raise_rate > 0.35:
        return "maniac"
    if call_rate > 0.42 and fold_rate < 0.25:
        return "calling_station"
    if fold_rate > 0.42 and vpip < 1.2:
        return "nit"
    if raise_rate > 0.22:
        return "lag"
    if call_rate > 0.32:
        return "passive_fish"
    return "tag"


def table_tightness(state):
    seen = []
    for p in state.get("players", []):
        bot_id = p.get("bot_id")
        if bot_id in OPPONENTS and OPPONENTS[bot_id]["actions"] >= 4:
            actions = max(1, OPPONENTS[bot_id]["actions"])
            seen.append(OPPONENTS[bot_id]["folds"] / actions)
    return sum(seen) / len(seen) if seen else 0.33


def fold_equity(state, pos, aggression):
    active_opp = max(1, len([p for p in state["players"]
                             if p["seat"] != state["seat_to_act"] and not p.get("is_folded")]))
    base = 0.43 - 0.08 * (active_opp - 1)
    base += 0.18 * table_tightness(state)
    base += 0.12 * pos
    base += 0.10 * aggression

    if state["amount_owed"] > 0:
        base -= 0.12
    recent_actions = state.get("action_log", [])[-4:]
    if recent_actions and all(a.get("action") in ("check", "call") for a in recent_actions):
        base += 0.10
    if any(a.get("action") in ("raise", "all_in") for a in recent_actions):
        base -= 0.10

    for p in state["players"]:
        if p["seat"] == state["seat_to_act"] or p.get("is_folded"):
            continue
        typ = opponent_type(p.get("bot_id"))
        if typ in ("nit", "passive_fish"):
            base += 0.04
        elif typ == "calling_station":
            base -= 0.10
        elif typ == "maniac":
            base -= 0.06

    return clamp(base, 0.05, 0.82)


def dynamic_aggression(state, pos):
    stacks = [max(0, p.get("stack", 0)) for p in state["players"] if not p.get("is_folded")]
    avg_stack = sum(stacks) / max(1, len(stacks))
    stack_ratio = state["your_stack"] / max(1, avg_stack)
    phase = clamp(infer_hand_number(state) / 400.0, 0.0, 1.0)
    tight = table_tightness(state)
    momentum = sum(RECENT_STACK_DELTAS) / max(1, STARTING_STACK)

    aggression = 0.50
    aggression += 0.16 * clamp(stack_ratio - 1.0, -1.0, 1.8)
    aggression += 0.16 * phase
    aggression += 0.15 * tight
    aggression += 0.12 * pos
    aggression += 0.18 * clamp(momentum, -0.8, 0.8)

    if state["your_stack"] < 12 * BIG_BLIND:
        aggression -= 0.12
    if state["your_stack"] > 2.0 * avg_stack:
        aggression += 0.10

    return clamp(aggression, 0.18, 0.95)


def monte_carlo_equity(hole, board, opponents, target_sims, max_ms=680):
    known = set(hole + board)
    deck = [c for c in FULL_DECK if c not in known]
    missing_board = 5 - len(board)
    needed = opponents * 2 + missing_board
    if opponents <= 0:
        return 1.0, 0.0, 0.0, 1
    if needed > len(deck):
        return 0.0, 0.0, 1.0, 1

    wins = ties = losses = sims = 0
    deadline = time.perf_counter() + max_ms / 1000.0

    while sims < target_sims and time.perf_counter() < deadline:
        sample = random.sample(deck, needed)
        runout = board + sample[:missing_board]
        idx = missing_board
        hero_score = evaluate_best(hole + runout)
        best_opp = None
        tied = False

        for _ in range(opponents):
            opp_hole = sample[idx:idx + 2]
            idx += 2
            score = evaluate_best(opp_hole + runout)
            if best_opp is None or score > best_opp:
                best_opp = score
                tied = False
            elif score == best_opp:
                tied = True

        if hero_score > best_opp:
            wins += 1
        elif hero_score == best_opp:
            ties += 1
        else:
            losses += 1
        sims += 1

    sims = max(1, sims)
    return wins / sims, ties / sims, losses / sims, sims


def legal_raise_to(state, desired_total):
    cap = state["your_stack"] + state["your_bet_this_street"]
    target = max(int(desired_total), state["min_raise_to"])
    return min(target, cap)


def pressure_bet_size(state, kind, aggression, texture):
    pot = max(BIG_BLIND, state["pot"])
    stack_total = state["your_stack"] + state["your_bet_this_street"]

    if kind == "thin":
        mult = random.choice([0.34, 0.42, 0.50])
    elif kind == "value":
        mult = random.choice([0.62, 0.75, 0.90])
    elif kind == "overbet":
        mult = random.choice([1.05, 1.25, 1.55])
    elif kind == "semi":
        mult = random.choice([0.55, 0.70, 0.85])
    else:
        mult = random.choice([0.38, 0.50, 0.66, 0.78])

    mult += 0.18 * aggression
    mult += 0.10 * texture["wet"]
    desired = state["current_bet"] + int(pot * mult)
    target = legal_raise_to(state, desired)

    if target >= stack_total * 0.82 and aggression > 0.70:
        return stack_total
    return target


def should_shove_preflop(state, pf_score, aggression, fe):
    stack_bb = state["your_stack"] / BIG_BLIND
    pressure = aggression + fe + pf_score
    if stack_bb <= 9 and pf_score > 0.46:
        return True
    if stack_bb <= 16 and pressure > 1.85 and pf_score > 0.55:
        return True
    if stack_bb <= 24 and pressure > 2.10 and pf_score > 0.66:
        return True
    return False


def preflop_decision(state, pos, aggression, fe):
    hole = state["your_cards"]
    score = preflop_score(hole)
    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    stack_total = state["your_stack"] + state["your_bet_this_street"]
    call_price = owed / max(1, pot + owed)
    steal_bonus = 0.16 * pos + 0.18 * fe + 0.14 * aggression
    pressure_score = score + steal_bonus

    if should_shove_preflop(state, score, aggression, fe):
        return {"action": "all_in"}

    if owed == 0:
        if pressure_score > 0.64 or (pos > 0.65 and pressure_score > 0.54):
            size = random.choice([2.2, 2.6, 3.1, 3.8])
            raise_to = legal_raise_to(state, state["min_raise_to"] * size)
            return {"action": "raise", "amount": raise_to}
        return {"action": "check"}

    big_pressure = owed > pot * 0.55 or owed > state["your_stack"] * 0.22

    if score > 0.78:
        size = random.choice([2.4, 3.2, 4.2])
        raise_to = legal_raise_to(state, max(state["min_raise_to"], state["current_bet"] * size))
        if raise_to >= stack_total * 0.85:
            return {"action": "all_in"}
        return {"action": "raise", "amount": raise_to}

    if score > 0.64 and not big_pressure:
        if aggression + fe + pos > 1.65 and random.random() < 0.55:
            raise_to = legal_raise_to(state, state["current_bet"] + int(pot * random.choice([0.8, 1.1, 1.4])))
            return {"action": "raise", "amount": raise_to}
        return {"action": "call"}

    if pressure_score > 0.72 and pos > 0.55 and not big_pressure:
        raise_to = legal_raise_to(state, state["current_bet"] + int(pot * random.choice([0.75, 1.0])))
        return {"action": "raise", "amount": raise_to}

    if call_price < score * 0.28 + 0.07 and owed < state["your_stack"] * 0.18:
        return {"action": "call"}

    if state["can_check"]:
        return {"action": "check"}
    return {"action": "fold"}


def postflop_decision(state, pos, aggression, fe):
    hole = state["your_cards"]
    board = state["community_cards"]
    score = evaluate_best(hole + board)
    category = score[0]
    texture = board_texture(board)
    draws = draw_info(hole, board)

    remaining = [p for p in state["players"]
                 if p["seat"] != state["seat_to_act"] and not p.get("is_folded")]
    opponents = max(1, len(remaining))
    target_sims = 1350 if opponents == 1 else 900 if opponents == 2 else 560
    if state["street"] == "river":
        target_sims += 350
    win, tie, loss, sims = monte_carlo_equity(hole, board, opponents, target_sims)
    equity = win + tie * 0.5

    owed = state["amount_owed"]
    pot = max(1, state["pot"])
    call_price = owed / max(1, pot + owed)
    multiway_penalty = 0.08 * max(0, opponents - 1)
    draw_bonus = 0.0
    if draws["combo_draw"]:
        draw_bonus = 0.28
    elif draws["flush_draw"] or draws["open_ended"]:
        draw_bonus = 0.17
    elif draws["straight_draw"]:
        draw_bonus = 0.10

    recent = state.get("action_log", [])[-8:]
    hero_seat = state["seat_to_act"]
    hero_aggressed = any(a.get("seat") == hero_seat and a.get("action") in ("raise", "all_in")
                         for a in recent)
    checked_to_us = recent and all(a.get("action") in ("check", "call", "small_blind", "big_blind")
                                   for a in recent[-min(3, len(recent)):])

    scare_bluff = 0.08 if texture["scary"] and pos > 0.45 else 0.0
    cbet_bluff = 0.10 if hero_aggressed and state["street"] == "flop" else 0.0
    bluff_score = (
        fe * 0.55
        + aggression * 0.35
        + pos * 0.18
        + draw_bonus
        + scare_bluff
        + cbet_bluff
        - texture["wet"] * 0.16
        - multiway_penalty
    )

    value_score = equity + category * 0.075
    danger = call_price + texture["wet"] * 0.10 + multiway_penalty
    big_bet = owed > pot * 0.60 or owed > state["your_stack"] * 0.28

    if state["can_check"]:
        if category >= 5 or equity > 0.74:
            kind = "overbet" if aggression > 0.72 and fe > 0.35 else "value"
            return {"action": "raise", "amount": pressure_bet_size(state, kind, aggression, texture)}

        if category >= 2 or equity > 0.58:
            if random.random() < 0.72 + aggression * 0.18:
                return {"action": "raise", "amount": pressure_bet_size(state, "value", aggression, texture)}
            return {"action": "check"}

        if draws["combo_draw"] or ((draws["flush_draw"] or draws["open_ended"]) and bluff_score > 0.55):
            return {"action": "raise", "amount": pressure_bet_size(state, "semi", aggression, texture)}

        if checked_to_us and opponents <= 2 and bluff_score > 0.56 and random.random() < bluff_score:
            return {"action": "raise", "amount": pressure_bet_size(state, "bluff", aggression, texture)}

        return {"action": "check"}

    if category >= 5 or equity > 0.80:
        if aggression + fe > 1.05 and not (big_bet and equity < 0.88):
            kind = "overbet" if equity > 0.87 and aggression > 0.65 else "value"
            target = pressure_bet_size(state, kind, aggression, texture)
            if target >= (state["your_stack"] + state["your_bet_this_street"]) * 0.82:
                return {"action": "all_in"}
            return {"action": "raise", "amount": target}
        return {"action": "call"}

    if category >= 2 and equity > call_price + 0.10:
        if value_score > danger + 0.45 and fe > 0.30 and random.random() < aggression:
            return {"action": "raise", "amount": pressure_bet_size(state, "thin", aggression, texture)}
        return {"action": "call"}

    if draws["combo_draw"] and bluff_score > 0.48 and owed < state["your_stack"] * 0.32:
        if random.random() < 0.58 + aggression * 0.25:
            return {"action": "raise", "amount": pressure_bet_size(state, "semi", aggression, texture)}
        return {"action": "call"} if equity > call_price - 0.02 else {"action": "fold"}

    if (draws["flush_draw"] or draws["open_ended"]) and equity + draw_bonus > call_price + 0.04:
        if bluff_score > 0.62 and opponents <= 2 and random.random() < 0.45:
            return {"action": "raise", "amount": pressure_bet_size(state, "semi", aggression, texture)}
        return {"action": "call"}

    pressure_bluff = bluff_score > 0.68 and opponents == 1 and not big_bet
    if pressure_bluff and random.random() < clamp(bluff_score - 0.18, 0.18, 0.58):
        return {"action": "raise", "amount": pressure_bet_size(state, "bluff", aggression, texture)}

    if equity > call_price + 0.08 and not (big_bet and category == 0):
        return {"action": "call"}

    return {"action": "fold"}


def decide(state):
    if state.get("type") == "warmup":
        return {"action": "check"}

    update_opponent_model(state)

    pos = position_score(state)
    aggression = dynamic_aggression(state, pos)
    fe = fold_equity(state, pos, aggression)

    if state["street"] == "preflop":
        return preflop_decision(state, pos, aggression, fe)

    return postflop_decision(state, pos, aggression, fe)
