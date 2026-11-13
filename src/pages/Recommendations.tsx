import { Cover } from '../components/Cover';
import { SongRow } from '../components/SongRow';
import { ARTISTS, GENRES, TRACKS } from '../data/catalog';
import {
  openRank,
  openSongMenu,
  playTrack,
  reopenRec,
  runRec,
  saveRec,
  setGenre,
  setPrompt,
  setRecMode,
  useCrate,
} from '../state/crate';

export function Recommendations() {
  const { recMode, genre, prompt, recs, songElo } = useCrate();
  const songMode = recMode === 'song';
  const latest = recs[0];
  const button = songMode
    ? latest?.type === 'song' ? 'Spin again' : 'Spin a song'
    : latest?.type === 'pl' ? 'Regenerate' : 'Generate playlist';

  return (
    <>
      <h1>Recommendations</h1>

      <section className="panel is-filled stack-16">
        <div className="segmented">
          <button aria-pressed={songMode} onClick={() => setRecMode('song')}>
            One song
          </button>
          <button aria-pressed={!songMode} onClick={() => setRecMode('pl')}>
            A playlist
          </button>
        </div>

        {songMode && (
          <div className="chips">
            {['Any', ...GENRES].map((g) => (
              <button key={g} className="chip" aria-pressed={genre === g} onClick={() => setGenre(g)}>
                {g}
              </button>
            ))}
          </div>
        )}

        <div className="composer">
          <div className="field">
            <label htmlFor="rec-prompt">{songMode ? 'Vibe (optional)' : 'What is the playlist for?'}</label>
            <input
              id="rec-prompt"
              type="text"
              value={prompt}
              placeholder={songMode ? 'rainy night, cozy morning, workout' : 'late drive home after a long shift'}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runRec()}
            />
          </div>
          <button className="btn btn-primary btn-large" onClick={runRec}>
            {button}
          </button>
        </div>
      </section>

      {latest?.type === 'song' && (
        <section className="panel stack-14">
          <div className="feature">
            <Cover trackId={latest.id} size={88} radius={14} />
            <div className="row-text">
              <span className="feature-title is-large">{TRACKS[latest.id].title}</span>
              <span className="muted">
                {ARTISTS[TRACKS[latest.id].artistId].name} · {ARTISTS[TRACKS[latest.id].artistId].genre}
              </span>
              <span className="muted small">{latest.reason}</span>
            </div>
          </div>
          <div className="actions">
            <button className="btn btn-dark" onClick={() => playTrack(latest.id)}>
              Play
            </button>
            <button className="btn btn-outline" onClick={() => openRank('songs', latest.id)}>
              {latest.id in songElo ? 'Re-rank' : 'Rank it'}
            </button>
            <button className="btn btn-outline" onClick={() => openSongMenu(latest.id)}>
              Add to playlist
            </button>
          </div>
        </section>
      )}

      {latest?.type === 'pl' && (
        <section className="panel stack-14">
          <div className="row-text">
            <span className="feature-title is-large">{latest.name}</span>
            <span className="muted small">{latest.note}</span>
          </div>
          <div className="actions">
            <button className="btn btn-dark" onClick={() => playTrack(latest.ids[0], latest.ids)}>
              Play all
            </button>
            <button className="btn btn-outline" onClick={saveRec}>
              {latest.saved ? 'Saved to playlists' : 'Save to playlists'}
            </button>
          </div>
          <div className="list">
            {latest.ids.map((id) => (
              <SongRow key={id} id={id} list={latest.ids} />
            ))}
          </div>
        </section>
      )}

      {recs.length > 1 && (
        <section className="list">
          <h2 className="list-heading">Earlier results</h2>
          {recs.slice(1).map((r, i) => (
            <button key={i} className="row history-row" onClick={() => reopenRec(i + 1)}>
              <span className="row-text">
                <span className="row-title">{r.type === 'song' ? TRACKS[r.id].title : r.name}</span>
                <span className="row-sub">
                  {r.type === 'song' ? `Song · ${ARTISTS[TRACKS[r.id].artistId].name}` : `Playlist · ${r.ids.length} songs`}
                </span>
              </span>
              <span className="history-open">Open</span>
            </button>
          ))}
        </section>
      )}
    </>
  );
}
