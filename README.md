# Zero-Dependency Self-Built Party Game Engine

> Build a room-based multiplayer party game for phones by writing only a ~120-line "kernel".
> No third-party dependencies — pure Python standard library.

## What it is

A tiny engine for making party games (Werewolf, Avalon, "Who is the Undercover", …) that friends play
through a link on their phones. You write only the game **rules** (the "kernel"); the engine already
provides the **shell** — HTTP server, room management, polling, voting gates, static files, and audio.

It bundles three things an AI can't reason out, only accumulate through rework:

1. **A battle-tested shell** — `engine.py`, shared by all games. Don't rewrite it.
2. **A pitfall map** — real bugs hit in production, especially environment-specific ones that are AI blind spots.
3. **A kernel dictionary** — `kernels.md`, to classify any game and pick the right template (currently written in Chinese).

## Features

- **Zero dependencies** — `http.server` + `threading` only. Nothing to `pip install`.
- **Room lifecycle** — create / join / start / restart / force / disband / leave.
- **Polling sync** — works behind Tailscale Funnel or any tunnel; no WebSocket needed for turn-based games.
- **Two-step confirm + all-players gate** — irreversible actions require "select → change → confirm"; results settle only when everyone has submitted. No first-come-first-served races.
- **Faction secret chat** — per-faction private channels that span every phase.
- **Custom actions** — a `set()` of actions auto-mounts as `/api/<action>` routes, so frontends don't change API paths.
- **Audio with MIME sniffing** — serves custom voice clips, detects format from file headers (not extensions), cache-busted.

## Quick start

1. **Read the rules → classify.** Look up the routing table in `kernels.md`.
2. **Hit kernel A/B/D** → copy the matching template. **Hit C/E/F** → honestly tell the user it's unsupported (don't fake it).
3. **Write the kernel.** A `Game` subclass (~120 lines) + `static/index.html` + a `start.bat`. **Don't modify `engine.py`.**
4. **Self-check.** Audio silence ✅ two-step confirm ✅ all-players gate ✅ cache-busting ✅ one-click start ✅.
5. **Test locally → expose the port** (`tailscale funnel --bg <port>`, or any tunnel) and share the link.

Minimal copyable example: [`examples/undercover/`](examples/undercover/) — a full "Who is the Undercover" game in ~120 lines of kernel + a frontend.

## Install

Requirements: Python 3.8+ (standard library only). On Windows the `py` launcher is used; elsewhere `python` works the same.

```bash
git clone https://github.com/Kellen-Chen0926/zero-dependency-self-built-party-game-engine.git
cd zero-dependency-self-built-party-game-engine/examples/undercover
py server.py          # or: python server.py
```

Open `http://localhost:8092` (or your tunnel URL) and share the link. To change the port, edit `PORT` in `server.py`.

## Usage

A game = one `Game` subclass + one static frontend. The engine handles everything else.

### Engine hooks (`Game` base class)

| Hook | Purpose | Required |
|---|---|---|
| `name / min_players / max_players / port` | Game metadata | ✅ |
| `config_for(player_count)` | Player count → role/faction config | ✅ |
| `setup(room)` | Deal roles, init kernel fields, enter first phase | ✅ |
| `on_action(room, pid, action, body)` | Handle the game's custom actions (via `/api/action`) | as needed |
| `phase_view(room, p, view)` | Add phase-specific fields to the poll result | as needed |
| `tick(room)` / `force(room)` / `reset(room)` | Time advance / host force-advance / restart cleanup | as needed |
| `can_chat(room, p)` | Whether public chat is allowed | as needed |
| `on_chat(room, pid, text)` | Route chat (faction secret chat, night chat) | as needed |
| `options_from_body(body)` | Extract custom create options (defaults to the `options` field) | as needed |
| `on_leave(room, p)` | Handle mid-game leave (default: mark `left`, refreshable) | as needed |
| `deal(room, role_list)` / `winner(room, w)` | Helpers: shuffle-deal roles / declare winner | use directly |

The `actions` class attribute is a `set()` of supported actions; the engine mounts each as `/api/<action>` automatically.

### Player state

Three fields cover the common cases: `alive` (eliminated), `left` (left mid-game), `out` (voted out). Each game uses what it needs.

## Pitfall map (read before you start)

**Environment (an AI can't reason these out — highest value):**

- Some phone browsers cache aggressively — after changing the frontend, do a hard refresh (`Ctrl+Shift+R`) before blaming the code.
- On Windows, writing Chinese into a `.bat` garbles it (GBK). Keep `.bat` files ASCII-only; put comments in English.
- Call Python with `py`, not `python`/`python3` — the latter may not be on PATH.
- Don't trust audio file extensions — sniff the file header (`RIFF`+`WAVE` → wav, `ID3`/`0xFF` → mp3).

**Engineering (easy to miss):**

- Editing `server.py` requires restarting the process (no hot reload).
- Add `Cache-Control: no-store` to every response; version audio URLs with `?v=N`.
- Every irreversible action = two-step "select → change → confirm to submit"; results settle only after everyone confirms (collect first, submit after — never first-come-first-served).
- In faction games, faction secret chat must span **every** phase, not just the last one.
- In self-tests, when several players perform the "same action", collect all first and submit together — don't poll-and-submit interleaved (it races and looks like a bug).
- Pure Python standard library. Don't add third-party deps.

## Project structure

```
.
├── engine.py              # the shell (one shared file — don't modify)
├── kernels.md             # kernel dictionary: classify a game → pick a template
├── README.md              # this file
├── LICENSE                # MIT
├── .gitignore
└── examples/
    └── undercover/        # minimal copyable example ("Who is the Undercover")
        ├── server.py      #   the kernel (~120 lines)
        ├── static/
        │   └── index.html #   the frontend
        └── start.bat      #   one-click start
```

## License

MIT — see [LICENSE](LICENSE).

## Contact

Found a bug or want to add a game? Open an issue in this repository (Issues tab) or submit a pull request.
