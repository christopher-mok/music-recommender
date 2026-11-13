import { ArtistRow } from '../components/ArtistRow';
import { SongRow } from '../components/SongRow';
import { ARTISTS, TRACKS, USER } from '../data/catalog';
import { order } from '../lib/util';
import { showRatings, useCrate } from '../state/crate';

/**
 * Placeholder for the user's standing among all Crate users, which is meant to
 * depend on how many songs they have ranked. There are no other users yet, so
 * this is a stand-in formula; replace it with a value from the backend.
 */
const globalRank = (songsRanked: number) => Math.max(1, 1200 - songsRanked * 40);

/** The two genres that show up most in the user's top six songs. */
function topGenres(songOrder: string[]): string {
  const counts: Record<string, number> = {};
  for (const id of songOrder.slice(0, 6)) {
    const genre = ARTISTS[TRACKS[id].artistId].genre;
    counts[genre] = (counts[genre] ?? 0) + 1;
  }
  return Object.keys(counts)
    .sort((x, y) => counts[y] - counts[x])
    .slice(0, 2)
    .join(' and ')
    .toLowerCase();
}

export function Profile() {
  const { songElo, artistElo, playlists, comps } = useCrate();
  const songs = order(songElo);
  const artists = order(artistElo);
  const stats = [
    { n: songs.length, label: 'Songs ranked' },
    { n: artists.length, label: 'Artists ranked' },
    { n: playlists.length, label: 'Playlists' },
    { n: comps, label: 'Matchups this session' },
  ];

  return (
    <>
      <header className="hero is-center">
        <span className="profile-avatar" aria-hidden="true">
          {USER.initial}
        </span>
        <div className="hero-text">
          <div className="profile-name">
            <h1>{USER.name}</h1>
            <span className="rank-badge">Rank on Crate #{globalRank(songs.length)}</span>
          </div>
          <p className="muted">Leaning {topGenres(songs)}, going by the top of your song ranking.</p>
        </div>
      </header>

      <div className="grid stat-grid">
        {stats.map((s) => (
          <div key={s.label} className="stat">
            <span className="stat-n">{s.n}</span>
            <span className="row-sub">{s.label}</span>
          </div>
        ))}
      </div>

      <div className="grid two-col">
        <section className="list">
          <div className="list-heading section-head">
            <h2>Top songs</h2>
            <button className="btn-link" onClick={() => showRatings('songs')}>
              Full ranking
            </button>
          </div>
          {songs.slice(0, 3).map((id, i) => (
            <SongRow key={id} id={id} list={songs} rank={i + 1} showScore />
          ))}
        </section>
        <section className="list">
          <div className="list-heading section-head">
            <h2>Top artists</h2>
            <button className="btn-link" onClick={() => showRatings('artists')}>
              Full ranking
            </button>
          </div>
          {artists.slice(0, 3).map((id, i) => (
            <ArtistRow key={id} id={id} rank={i + 1} action={false} />
          ))}
        </section>
      </div>
    </>
  );
}
