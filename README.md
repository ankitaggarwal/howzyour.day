# HowzYourDay — slide deck

A self-contained, animated HTML deck (12 slides) telling the story of HowzYourDay:
the problem, the idea, the instant-vs-intimate tension, and the engineering that
resolves it. Same warm nocturnal aesthetic as [howzyour.day](https://howzyour.day).

**Live:** https://ankitaggarwal.github.io/howzyour.day/

## Controls
`→` / `Space` next · `←` back · `Home`/`End` jump · `N` presenter notes · `F` fullscreen.
Click the right/left half of the slide to advance/go back.

## Run locally
```bash
python3 -m http.server 4540   # then open http://localhost:4540
```

## Build artifacts (dev)
```bash
node render-slides.js   # → slides-png/slide-NN.png (review thumbnails)
node generate-pdf.js    # → howzyourday-deck.pdf (1280×720, one page per slide)
```

Slides live in `slides/slide-NN.html` (each is one `.slide-inner`); `index.html` is
the shell — themes, navigation, scaling, presenter notes. MIT.
