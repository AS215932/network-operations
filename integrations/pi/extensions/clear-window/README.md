# Clear Window Pi Extension

Adds a `/clear` command for Pi.

## Behavior

```text
/clear          # clear terminal viewport/scrollback and start a fresh Pi session
/clear both     # same as /clear
/clear window   # clear only the terminal viewport/scrollback
/clear session  # start a fresh Pi session without clearing scrollback
```

`/clear` does **not** delete saved session files. It switches to a new session so
old conversation context is no longer sent to the model; use `/resume` if you
need to recover the previous session.

The window clear path emits only fixed ANSI clear-screen/scrollback sequences and
runs no shell commands.

## Install / Sync

Until Pi has a packaged extension installer for this repo, sync the source into
the local Pi extension directory:

```bash
mkdir -p ~/.pi/agent/extensions/clear-window
cp integrations/pi/extensions/clear-window/index.ts \
  ~/.pi/agent/extensions/clear-window/index.ts
```

Then restart Pi or run `/reload`.
