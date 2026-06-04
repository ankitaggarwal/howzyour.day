# Screenshots & promo assets

Visuals for the README and the LinkedIn launch kit ([`../linkedin/`](../linkedin)).
Every card uses the brand mark — the **Echo orb** and the HowzYour**Day** wordmark —
for a consistent set, in the site's warm nocturnal palette.

| File | What it is |
|------|-----------|
| `howzyourday-app.png` | The live landing page — the Echo orb & "how was your day?". **README hero.** |
| `howzyourday-cover.png` | Title card — wordmark, orb, the one-line pitch. |
| `howzyourday-architecture.png` | One-page architecture ("instant, and it remembers"). Also embedded in the README. |
| `howzyourday-site.png` | "Live · try it" — the two doors (call / web). Site-link thumbnail. |
| `howzyourday-github.png` | The repo card — the GitHub-link thumbnail. |
| `howzyourday-deck.png` | "The story, in 12 slides" — the LinkedIn deck-link thumbnail. |
| `howzyourday-deck-cover.png` | The deck cover / title card (slide 1 of the [live deck](https://ankitaggarwal.github.io/howzyour.day/)). |

The designed cards (`cover`, `github`, `site`, `architecture`, `deck`) render from matching
HTML in [`../linkedin/`](../linkedin); `app` is captured from the live landing page,
and `deck-cover` is slide 1 of the deck.
Regenerate with the helper renderer (reuses an existing puppeteer install):

```bash
node /Users/ankitaggarwal/Codes/PodcastSearch/podsearch-deck/render-hyd.js
```
