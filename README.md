# music-recommender

**Crate** is a music rating and recommendation web app. You rank songs and artists through head-to-head matchups (Elo, in the style of Beli) instead of star ratings, and get recommendations as a single song or a generated playlist.

This is the front end, ported from the design mockup. It runs entirely in the browser on placeholder data; see [What is still a placeholder](#what-is-still-a-placeholder).

## Run it

Requires Node 22.13 or newer (the `music_data` TS package uses `node:sqlite`).

```bash
npm install
npm run dev        # http://localhost:5173
```

The `music-data` package (`music_data/ts`) is committed prebuilt, and the data it reads is committed in `music_data/api_bundle/`, so you don't need Python to work on the app.

### Working on the data pipeline (optional)

Only needed if you change the data itself. See `music_data/README.md`.

```bash
cd music_data
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env          # then add your API keys
```

The full working database is committed as two SQLite files so each stays under GitHub's 100 MB limit:

- `music_data/data/music_metadata_100.db`: all metadata (~42 MB)
- `music_data/data/music_metadata_100.embeddings.db`: `embedding_specs`, `embeddings`, `artist_vectors` (~84 MB)

There's nothing to unzip or merge. `music_data.database.connect()` attaches the embeddings file automatically, so queries work as if it were one database. To open it outside the pipeline (e.g. DB Browser or plain `sqlite3`), open the main file and run `ATTACH 'music_metadata_100.embeddings.db' AS emb;`.

If a pipeline run creates embeddings in a database that has no `.embeddings.db` sibling yet, move them out before committing (from `music_data/`):

```bash
python scripts/split_embeddings_db.py data/music_metadata_100.db
```

After regenerating data, republish the bundle into `music_data/api_bundle/`. If you change `music_data/ts/src`, run `npm run build` in `music_data/ts`. Commit both outputs. Commit the DB files at milestones rather than after every run: each version stays in the repo history permanently, and the embeddings file has ~15 MB of headroom before the 100 MB limit.

Other scripts:

| Command | What it does |
| --- | --- |
| `npm run build` | Type-checks, then builds to `dist/` |
| `npm run preview` | Serves the production build |
| `npm run typecheck` | Type-checks only |

## What is in the app

- **Home**: how many songs are left to rank, one picked song, a playlist shelf, artists to discover.
- **Recommendations**: one prompt box with two modes. *One song* picks a random song from a genre and/or a typed vibe. *A playlist* drafts eight songs from a prompt, which can be saved.
- **Ratings**: ordered rankings of your songs and artists, each with a 0-10 score.
- **Ranking popup**: opened from the player, any row, or "Keep ranking". A new item gets a first-impression question, then three placement matchups. The last pick can be undone.
- **Playlists**: create, rename, delete; add or remove songs from any song's More menu.
- **Search**: in place, over the blurred page. Matches titles, artists, genres and mood tags.
- **Player**: play/pause, previous/next, seek, volume, shuffle, repeat, queue.

## Project layout

```
src/
  main.tsx            entry point
  App.tsx             app shell and page switching
  styles.css          all styling (design tokens at the top)
  types.ts            shared types
  data/catalog.ts     placeholder artists, songs, playlists and starting ratings
  lib/
    elo.ts            rating maths and matchup selection
    recommend.ts      recommendation and search logic
    audio.ts          placeholder synth that stands in for real audio
    util.ts           small formatting helpers
  state/
    store.ts          minimal external store
    crate.ts          app state and every action that changes it
  components/         sidebar, player, rows, search, popups
  pages/              one file per page
```

There is no router and no state library. The current page is a field in the store (`screen`), and `state/crate.ts` holds the state plus plain functions that update it.

## How ranking works

- Every ranked item has an Elo rating. A new item enters at 1650, 1500 or 1350 depending on the first-impression answer.
- Each matchup applies a standard Elo update with K = 32, so an upset moves ratings further than an expected result.
- When placing one item, its opponents are drawn from the three closest-rated items, without repeating the previous opponent.
- The 0-10 score shown in the UI is the item's chance of beating an average (1500) pick, times ten.

## What is still a placeholder

| Area | Now | To make it real |
| --- | --- | --- |
| Audio | `lib/audio.ts` generates a short synth loop per song | Swap `Synth` for an `<audio>` element or a streaming SDK with the same `play` / `pause` / `setVolume` methods |
| Catalogue | 20 invented songs by 10 invented artists in `data/catalog.ts` | Load from an API or database |
| Persistence and accounts | State lives in memory and resets on reload; the user is hard-coded | Add sign-in and store ratings and playlists server-side |
| Recommendations | Keyword matching on tags in `lib/recommend.ts` | Call a real recommender; the UI only depends on the `Rec` types |
| Rank on Crate | A stand-in formula in `pages/Profile.tsx` | Compute from real users' ranked-song counts |
| Cover art | Generated from each artist's colour | Use real artwork |

## DB has been added
v1 has +1k artist
