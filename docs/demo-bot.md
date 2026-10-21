# Demo bot (public)

`graea/demo/buggy_bot.py` is a public Telegram bot that exists to be pointed at
by Graea. It plants seven bugs that can be toggled per chat, so anyone can run
Graea's scenarios (`graea/demo/scenarios/`) against it.

Source-available: <https://github.com/estejosh/graea>.

## Commands (private chats only)

- `/start` — welcome message and the Order / Status / Help inline buttons.
- `/help` — the command list.
- `/menu` — a reply keyboard with A / B / C.
- `/bugs` — list the 7 bug names, which are on in this chat, and what each does.
- `/bugs on <name|all>` / `/bugs off <name|all>` — toggle bugs for this chat only.
- `/about` — what this bot is.

The seven bugs: `raw_markdown`, `truncated_button`, `edit_as_new`,
`missing_caption`, `silent_command`, `dead_button`, `wrong_keyboard_shape`.

Per-chat bug state is held in memory and resets when the bot restarts. The
default bug set comes from the `BUGS` env var (comma-separated names; empty by
default, which means every flow behaves correctly).

## Running it

1. Clone the repo and check out `demo-bot-public`:

   ```bash
   git clone git@github-estejosh:estejosh/graea.git ~/.local/share/graea-demo/repo
   cd ~/.local/share/graea-demo/repo && git checkout demo-bot-public
   ```

2. Create a venv and install the demo extra:

   ```bash
   uv venv --python 3.11 ~/.local/share/graea-demo/venv
   uv pip install --python ~/.local/share/graea-demo/venv/bin/python \
     -e "~/.local/share/graea-demo/repo[demo]"
   ```

3. Put the bot token in an env file (mode 600) outside the checkout:

   ```bash
   mkdir -p ~/.config/graea-demo
   printf 'DEMO_BOT_TOKEN=<token>\nBUGS=\n' > ~/.config/graea-demo/env
   chmod 600 ~/.config/graea-demo/env
   ```

   `BUGS` is optional; set it to a comma-separated list to enable bugs for all
   chats by default, e.g. `BUGS=raw_markdown,dead_button`.

4. A systemd `--user` service at
   `~/.config/systemd/user/graea-demo.service`:

   ```ini
   [Unit]
   Description=Graea demo bot (public, per-chat bug toggles)
   After=network.target

   [Service]
   Type=simple
   EnvironmentFile=/home/ubuntclaw/.config/graea-demo/env
   WorkingDirectory=/home/ubuntclaw/.local/share/graea-demo/repo
   ExecStart=/home/ubuntclaw/.local/share/graea-demo/venv/bin/python -m graea.demo.buggy_bot
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=default.target
   ```

5. Enable lingering so it survives reboot, then start it:

   ```bash
   sudo loginctl enable-linger ubuntclaw
   systemctl --user daemon-reload
   systemctl --user enable --now graea-demo
   systemctl --user status graea-demo
   ```

## Rotating the token

Edit the env file and restart — a one-line change:

```bash
printf 'DEMO_BOT_TOKEN=<new-token>\nBUGS=\n' > ~/.config/graea-demo/env
chmod 600 ~/.config/graea-demo/env
systemctl --user restart graea-demo
```

## Stopping it

```bash
systemctl --user stop graea-demo        # stop now
systemctl --user disable graea-demo     # also stop auto-starting at login/reboot
```

## Public hygiene

- Private chats only; groups and channels are ignored.
- Per-user rate limit: 30 messages per minute; over the limit the update is
  silently dropped.
- Logs carry counts and command names only, never message text or user ids.
