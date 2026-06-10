# Anchoring AI — Blog Series

A nine-part series on building a production RAG system from scratch. Each post adds a layer; the reader builds along.

**Series title:** Anchoring AI — *because RAG grounds LLM answers in retrieved evidence, and this series is about AI that's stable and trustworthy, not demo-ware.*

## The Arc

| # | Slug | Title | Status |
|---|---|---|---|
| 01 | `01-the-blueprint` | The Blueprint | Draft |
| 02 | `02-from-text-to-vectors` | From Text to Vectors | Placeholder |
| 03 | `03-the-interface-layer` | The Interface Layer | Placeholder |
| 04 | `04-run-anywhere` | Run Anywhere | Placeholder |
| 05 | `05-built-to-last` | Built to Last | Placeholder |
| 06 | `06-what-should-we-measure` | What Should We Measure? | Placeholder |
| 07 | `07-wiring-it-up` | Wiring It Up | Placeholder |
| 08 | `08-trust-but-verify` | Trust, but Verify | Structured draft |
| 09 | `09-whats-next` | What's Next | Structured draft |

## Audience

Engineers with a software background, mixed AI expertise. Not assuming RAG knowledge; assuming solid programming fundamentals.

## Format

- Source: Markdown (`index.md` per post)
- Output: Standalone HTML (`index.html`) via `build.sh`
- Diagrams: Mermaid (renders in-browser via CDN — no pre-build step)

## Building HTML

```bash
brew install pandoc            # one-time setup
./blogs/build.sh               # build all posts → index.html per post
./blogs/build.sh 01            # build one post
./blogs/build.sh index         # build the series landing page only
```

## Structure

```
blogs/
├── build.sh           # HTML generator
├── template.html      # Pandoc HTML5 template (CSS + Mermaid.js)
├── index.md           # Series landing page source
├── README.md          # This file
├── assets/diagrams/   # Static exported diagrams (SVG/PNG)
└── NN-slug/
    ├── index.md       # Post source (Markdown)
    └── index.html     # Generated HTML (git-ignored or committed)
```
