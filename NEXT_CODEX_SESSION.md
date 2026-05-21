# Next Codex Session Handoff

The main bot being edited for submission is:

```text
bots/template/bot.py
```

This bot should be treated as the user's real poker bot submission. It is currently named:

```python
BOT_NAME = "t5115"
```

Before making changes, read the project README:

```text
README.md
```

The README explains the tournament format, sandbox restrictions, valid actions, timing limits, and submission requirements. The bot must continue to satisfy those requirements.

## Goal

Build and improve a Texas Hold'em poker bot for the Fullhouse engine.

The bot should:

- Try to win the tournament and maximize chip count.
- Play strong Texas Hold'em strategy.
- Bluff intelligently.
- Semi-bluff draws such as flush draws and straight draws.
- Learn from available game state and match history.
- Adapt its strategy every hand based on position, stack size, table behavior, board texture, pot odds, opponent aggression, and recent momentum.
- Avoid playing one fixed/static style.

## Desired Style

The user wants an adaptive aggressive bot, not a passive survival bot.

The bot should:

- Steal blinds in good spots.
- Pressure passive or tight opponents.
- Bluff more when fold equity is high.
- Bluff less when opponents show strength or call too much.
- Value bet strong hands.
- Avoid calling huge bets with weak hands.
- Change risk tolerance depending on stack size and match phase.

## Important Constraints

The bot runs in the tournament sandbox:

- 2 seconds maximum per decision.
- About 768 MB RAM.
- About 0.5 CPU core per bot.
- No external APIs or network calls.
- No subprocess or shell commands.
- No file writes during gameplay.
- No poker libraries or external decision engines should be used inside the bot.
- Keep the bot self-contained in `bots/template/bot.py` unless the submission format is intentionally changed.

## Current Implementation Notes

The current `t5115` bot already includes:

- A pure Python 5-card and 7-card hand evaluator.
- Monte Carlo equity estimation with a strict time budget.
- Preflop hand scoring.
- Board texture analysis.
- Draw detection.
- Opponent/stat tracking from `match_action_log`.
- Adaptive aggression and fold-equity estimates.
- Bluff, semi-bluff, value, and thin-value betting logic.

Future work should focus on careful tuning, not random complexity. Any changes should be validated with:

```bash
python sandbox/validator.py bots/template/bot.py --json
python -m py_compile bots/template/bot.py demo.py
```

If local dependencies `treys` or `eval7` are installed, also run match simulations against the other bots.

