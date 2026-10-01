# Graea preview video: script (as recorded)

Voiceover is Josh's recording, edited only to drop flubs (see the audit at the end of `CLAIMS.md`). Timecodes are from his actual word times in the edited audio.
Every product claim is backed in `CLAIMS.md`. Run counts, the pizza bot and "order summary" are illustrative and tagged on screen.

## Cut A: preview for developers (1:16, 145 words)

| # | on screen | voiceover |
|---|---|---|
| 1 (0:00) | synthetic chat with `@demo_pizza_bot` next to an AI assistant pane: "Done. The bot works." `bot.py saved` | your ai built a telegram bot. it says it works. |
| 2 (0:05) | a hand taps **Order**. The spinner never stops. "no response" | you open telegram, you tap the button, nothing happens. |
| 3 (0:10) | a screenshot flies into the AI pane. Tag: "you: the eyes, relaying screenshots". The human types "the button does nothing" | so you screenshot it, paste it back, and explain. |
| 4 (0:14) | title card: eye mark, **graea**, "your ai's own eyes" | graea gives your ai its own eyes. |
| 5 (0:17) | rail: **act**. Test-user badge, `/start`, the bot replies, graea's cursor taps **Order** | it logs in as a test user and messages your bot. it taps the inline button like a person would. |
| 6 (0:24) | rail: **capture, read**. Flash, screenshot card, reader card with four provider chips (OpenAI-compatible endpoint, Anthropic, OCR fallback, your own AI) | it screenshots the chat as telegram renders it. a vision model reads it, you choose which one. |
| 7 (0:31) | reader card: *expected: order summary, saw: nothing, button callback unanswered*, **FAIL** stamp | expected an order summary, saw nothing, the callback never answered. |
| 8 (0:38) | three bug examples with findings: `raw_markdown`, `truncated_button`, `missing_caption`, each flagged | markdown, truncated buttons, missing captions. it catches those. |
| 9 (0:44) | rail: **log**. DuckDB panel fills row by row, then *run 1: 3 failures* and the first bar | every step goes into duckdb log. run one, three failures. |
| 10 (0:51) | rail: **fix**. MCP tool list counts up to 16 tools. Your AI reads the result, the diff strikes out the `dead_button` early return | your ai plugs in over mcp, the ai reads what broke and patches the bot. |
| 11 (0:58) | rail: **re-check**. *run 2: 1 failure*, second bar | graea runs it again. run two, one failure. |
| 12 (1:02) | *run 3: all pass*, third bar, `fixed 3 · regressed 0` | run three, all passed. the diff shows the progress. |
| 13 (1:05) | your ai, graea, your bot in a loop. The human icon is crossed out | no human relaying screenshots. |
| 14 (1:08) | end card: eye, **graea**, **source-available**, `github.com/estejosh/graea`, "Usufruct License (UFL)" | graea, source available. usufruct licensing. ufl all the way. |

## Cut B: teaser for X and LinkedIn (0:30, 61 words)

| # | on screen | voiceover |
|---|---|---|
| 1 (0:00) | the dead-button chat, AI says "Done. The bot works." | your ai says the bot works. the button does nothing. |
| 2 (0:05) | graea taps, flash, reader card: expected / saw nothing / callback unanswered | graea taps it, screenshots it, and reads it. |
| 3 (0:08) | DuckDB panel, *run 1: 3 failures* | it logs every step in duckdb. |
| 4 (0:11) | structured result card (`step`, `timed_out`, `assertions`, `vision.issues`) | it tells your ai exactly what broke and where. |
| 5 (0:14) | the patch strikes out the early return, then re-run | the ai fixes it. graea checks again. |
| 6 (0:17) | bars: 3 failures, 1, 0 | run one, three failures. run three, all pass. |
| 7 (0:20) | the human icon crossed out | no human relaying screenshots. |
| 8 (0:23) | end card as in Cut A | graea, source available. usufruct licensing. ufl all the way. |

## Notes

- Differences from the draft script, kept as spoken: "duckdb log", "all passed" (Cut A), "and where" (Cut B), and the added "usufruct licensing. ufl all the way."
- **"15 tools" was wrong (there are 16)** and is cut from the audio. The on-screen list counts to 16. Optional pickup to restore the beat: record "sixteen tools." on its own, to be spliced after "mcp,".
- Cut A runs 1:16 against a 60 to 75 s target. Cut B runs 0:30, the bottom of its 30 to 45 s range.
- Captions are lowercase to match the voiceover style. Captions are burned into the 1080x1350 renders only. The 1920x1080 renders ship with `.srt` files.

## Recording sheet

pause about one second between lines.

Cut A

1. your ai built a telegram bot.
2. it says it works.
3. you open telegram, you tap the button, nothing happens.
4. so you screenshot it, paste it back, and explain.
5. graea gives your ai its own eyes.
6. it logs in as a test user and messages your bot.
7. it taps the inline button like a person would.
8. it screenshots the chat as telegram renders it.
9. a vision model reads it, you choose which one.
10. expected an order summary, saw nothing, the callback never answered.
11. markdown, truncated buttons, missing captions. it catches those.
12. every step goes into duckdb log.
13. run one, three failures.
14. your ai plugs in over mcp. (optional pickup: sixteen tools.)
15. the ai reads what broke and patches the bot.
16. graea runs it again. run two, one failure.
17. run three, all passed. the diff shows the progress.
18. no human relaying screenshots.
19. graea, source available. usufruct licensing. ufl all the way.

Cut B

1. your ai says the bot works. the button does nothing.
2. graea taps it, screenshots it, and reads it.
3. it logs every step in duckdb.
4. it tells your ai exactly what broke and where.
5. the ai fixes it. graea checks again.
6. run one, three failures. run three, all pass.
7. no human relaying screenshots.
8. graea, source available. usufruct licensing. ufl all the way.
