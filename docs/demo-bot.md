# Demo bot (run your own)

`graea/demo/buggy_bot.py` is a small Telegram bot that exists to be pointed at
by Graea. It plants seven bugs that you can toggle per chat, so you can run
Graea's scenarios (`graea/demo/scenarios/`) against something that is broken
on purpose.

You run it yourself. There is no shared or hosted instance: you create your
own bot with BotFather and run it on your own machine, so the token stays with
you and nothing goes through anyone else's servers. This matches the
Operational Scope of the Usufruct License (no third-party hosting).

Graea is source-available: <https://github.com/estejosh/graea>.

## Install and run

1. Create a bot. In Telegram, message `@BotFather`, send `/newbot`, and copy
   the token it gives you. Treat the token like a password.

2. Install Graea with the demo extra:

   ```bash
   git clone https://github.com/estejosh/graea.git
   cd graea
   python -m venv .venv && . .venv/bin/activate
   pip install -e ".[demo]"
   ```

3. Put the token in an env file that git ignores (`.env` and `*.env` are in
   `.gitignore`), or export it in your shell:

   ```bash
   printf 'DEMO_BOT_TOKEN=<your token>\nBUGS=\n' > demo.env
   chmod 600 demo.env
   set -a && . ./demo.env && set +a
   ```

   `BUGS` is optional. Set it to a comma-separated list to enable bugs for
   every chat by default, for example `BUGS=raw_markdown,dead_button`. Empty
   means every flow behaves correctly.

4. Start it:

   ```bash
   python -m graea.demo.buggy_bot
   ```

   Leave it running, open your bot in Telegram, and send `/start`. Stop it with
   Ctrl+C.

5. Point Graea at it. Use your own Telegram test account (not your main one)
   for `graea login`, then run a scenario against your bot's username.

To keep it running in the background, use whatever your system already has
(a systemd user service, a launchd agent, `tmux`, or a container). It needs
only outbound HTTPS to Telegram.

## Commands (private chats only)

- `/start` — welcome message and the Order / Status / Help inline buttons.
- `/help` — the command list.
- `/menu` — a reply keyboard with A / B / C.
- `/bugs` — list the 7 bug names, which are on in this chat, and what each does.
- `/bugs on <name|all>` / `/bugs off <name|all>` — toggle bugs for this chat only.
- `/about` — what this bot is.

The seven bugs: `raw_markdown`, `truncated_button`, `edit_as_new`,
`missing_caption`, `silent_command`, `dead_button`, `wrong_keyboard_shape`.

Per-chat bug state is held in memory and resets when the bot restarts.

## If your token leaks

Revoke it in BotFather (`/revoke`), then put the new token in your env file and
restart the bot.

## Hygiene

- Private chats only; groups and channels are ignored.
- Per-user rate limit: 30 messages per minute; over the limit the update is
  silently dropped. The limiter's memory is pruned, so it stays bounded.
- Logs carry counts and command names only, never message text or user ids.
- Do not put personal information in a test bot. Messages are not stored.
