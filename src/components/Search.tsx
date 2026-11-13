import { useRef } from 'react';
import { USER } from '../data/catalog';
import { searchArtists, searchSongs } from '../lib/recommend';
import { closeSearch, go, openSearch, setQuery, useCrate } from '../state/crate';
import { ArtistRow } from './ArtistRow';
import { SongRow } from './SongRow';

/** Pinned bar above every page: the search field and the profile button. */
export function TopBar() {
  const { q, searching } = useCrate();
  const input = useRef<HTMLInputElement>(null);

  const cancel = () => {
    input.current?.blur();
    closeSearch();
  };

  return (
    <div className="topbar">
      <div className="container topbar-inner">
        <input
          ref={input}
          type="search"
          className={`search-input${searching ? ' is-open' : ''}`}
          aria-label="Search songs, artists and genres"
          placeholder="Search songs, artists, genres"
          value={q}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={openSearch}
          onKeyDown={(e) => e.key === 'Escape' && cancel()}
        />
        {searching && (
          <button className="btn-link" onClick={cancel}>
            Cancel
          </button>
        )}
        <button className="profile-btn" onClick={() => go('profile')} aria-label="Your profile">
          {USER.initial}
        </button>
      </div>
    </div>
  );
}

/** Results layer shown over the blurred page while the search field is active. */
export function SearchOverlay() {
  const { q } = useCrate();
  const songs = searchSongs(q);
  const artists = searchArtists(q);
  const term = q.trim();
  const hint = !term
    ? 'Type a song, an artist, a genre, or a mood like “rain”.'
    : songs.length || artists.length
      ? `Results for “${term}”`
      : `Nothing matches “${term}”.`;

  return (
    <div className="search-layer fade">
      <button className="search-backdrop" onClick={closeSearch} aria-label="Close search" />
      <div className="scroll-pad search-results">
        <div className="container stack-24">
          <p className="search-hint">{hint}</p>
          {songs.length > 0 && (
            <section className="list">
              <h2 className="list-heading">Songs</h2>
              {songs.map((id) => (
                <SongRow key={id} id={id} list={songs} showScore />
              ))}
            </section>
          )}
          {artists.length > 0 && (
            <section className="list">
              <h2 className="list-heading">Artists</h2>
              {artists.map((id) => (
                <ArtistRow key={id} id={id} />
              ))}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
