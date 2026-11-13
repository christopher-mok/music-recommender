import { SongRow } from '../components/SongRow';
import { TRACKS } from '../data/catalog';
import { plural } from '../lib/util';
import { go, openNewPlaylist, openPlaylist, openRenamePlaylist, playTrack, useCrate } from '../state/crate';

export function Playlists() {
  const { playlists } = useCrate();
  return (
    <>
      <div className="section-head is-wrap">
        <h1>Playlists</h1>
        <div className="actions">
          <button className="btn btn-primary" onClick={openNewPlaylist}>
            New playlist
          </button>
          <button className="btn btn-outline" onClick={() => go('recs')}>
            Generate one
          </button>
        </div>
      </div>
      <div className="grid playlist-grid">
        {playlists.map((p) => (
          <button key={p.id} className="playlist-card card" onClick={() => openPlaylist(p.id)}>
            <span className="playlist-cover" style={{ background: p.color }}>
              {p.name}
            </span>
            <span className="playlist-meta">
              <span className="row-sub">{p.desc}</span>
              <span className="playlist-count">{plural(p.ids.length, 'song')}</span>
            </span>
          </button>
        ))}
      </div>
    </>
  );
}

export function PlaylistDetail() {
  const { playlists, openPl } = useCrate();
  const playlist = playlists.find((p) => p.id === openPl);
  if (!playlist) return <p className="muted">That playlist no longer exists.</p>;
  const minutes = Math.round(playlist.ids.reduce((n, id) => n + TRACKS[id].dur, 0) / 60);

  return (
    <>
      <header className="hero">
        <span className="hero-cover" aria-hidden="true" style={{ background: playlist.color }} />
        <div className="hero-text">
          <span className="eyebrow">Playlist</span>
          <h1>{playlist.name}</h1>
          <p className="muted">{playlist.desc}</p>
          <span className="strong small">
            {plural(playlist.ids.length, 'song')} · {minutes} min
          </span>
          <div className="actions">
            <button className="btn btn-primary" disabled={!playlist.ids.length} onClick={() => playTrack(playlist.ids[0], playlist.ids)}>
              Play all
            </button>
            <button className="btn btn-outline" onClick={() => openRenamePlaylist(playlist.id)}>
              Rename
            </button>
          </div>
        </div>
      </header>
      {playlist.ids.length === 0 ? (
        <p className="muted">No songs yet. Open the More button on any song and choose this playlist to add it.</p>
      ) : (
        <div className="list">
          {playlist.ids.map((id) => (
            <SongRow key={id} id={id} list={playlist.ids} />
          ))}
        </div>
      )}
    </>
  );
}
