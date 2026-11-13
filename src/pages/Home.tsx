import { Avatar, Cover } from '../components/Cover';
import { SongRow } from '../components/SongRow';
import { ARTISTS, ARTIST_IDS, TRACKS, TRACK_IDS, USER, tracksByArtist } from '../data/catalog';
import { score } from '../lib/elo';
import { order, plural } from '../lib/util';
import { go, openArtist, openPlaylist, openRank, playTrack, useCrate } from '../state/crate';

/** The song to feature: something unranked by the user's highest-ranked artist that has one. */
function pickFeature(unranked: string[], artistOrder: string[]): { id: string; why: string } | null {
  for (let i = 0; i < artistOrder.length; i++) {
    const id = unranked.find((t) => TRACKS[t].artistId === artistOrder[i]);
    if (id) return { id, why: `By ${ARTISTS[artistOrder[i]].name}, your #${i + 1} artist, and you have not ranked it yet.` };
  }
  if (!unranked.length) return null;
  return { id: unranked[0], why: `You have not ranked anything by ${ARTISTS[TRACKS[unranked[0]].artistId].name} yet.` };
}

export function Home() {
  const { songElo, artistElo, playlists } = useCrate();
  const unranked = TRACK_IDS.filter((id) => !(id in songElo));
  const artistOrder = order(artistElo);
  const feature = pickFeature(unranked, artistOrder);
  const discover = [...ARTIST_IDS.filter((id) => !(id in artistElo)), ...artistOrder];

  return (
    <>
      <h1>Welcome back, {USER.name}</h1>

      <div className="grid lead-grid">
        <section className="panel is-filled lead">
          <span className="eyebrow">Your ranking</span>
          <h2 className="lead-title">{unranked.length ? `${plural(unranked.length, 'song')} left to rank` : 'Everything is ranked'}</h2>
          <p className="muted">
            {unranked.length
              ? 'Each one takes a first impression and three quick matchups.'
              : 'More head-to-head matchups will sharpen the order.'}
          </p>
          <button className="btn btn-primary btn-large" onClick={() => openRank('songs', unranked[0] ?? null)}>
            {unranked.length ? 'Start ranking' : 'Keep ranking'}
          </button>
        </section>

        {feature && (
          <section className="panel lead">
            <span className="eyebrow">Picked for you</span>
            <div className="feature">
              <Cover trackId={feature.id} size={76} radius={12} />
              <div className="row-text">
                <span className="feature-title">{TRACKS[feature.id].title}</span>
                <span className="muted">
                  {ARTISTS[TRACKS[feature.id].artistId].name} · {ARTISTS[TRACKS[feature.id].artistId].genre}
                </span>
                <span className="muted small">{feature.why}</span>
              </div>
            </div>
            <div className="actions">
              <button className="btn btn-dark" onClick={() => playTrack(feature.id)}>
                Play
              </button>
              <button className="btn btn-outline" onClick={() => openRank('songs', feature.id)}>
                Rank it
              </button>
            </div>
          </section>
        )}
      </div>

      <section className="stack-14">
        <div className="section-head">
          <h2>Your playlists</h2>
          <button className="btn-link" onClick={() => go('playlists')}>
            See all
          </button>
        </div>
        <div className="shelf">
          {playlists.map((p) => (
            <button key={p.id} className="playlist-card card" onClick={() => openPlaylist(p.id)}>
              <span className="playlist-cover" style={{ background: p.color }}>
                {p.name}
              </span>
              <span className="playlist-count">{plural(p.ids.length, 'song')}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="stack-14">
        <h2>Discover artists</h2>
        <div className="grid artist-grid">
          {discover.map((id) => {
            const ranked = id in artistElo;
            const tracks = tracksByArtist(id);
            return (
              <div key={id} className="artist-card">
                <button className="artist-card-main" onClick={() => openArtist(id)} aria-label={`Open ${ARTISTS[id].name}`}>
                  <Avatar artistId={id} size={64} />
                  <span className="row-text">
                    <span className="row-title">{ARTISTS[id].name}</span>
                    <span className="row-sub">{ARTISTS[id].genre}</span>
                  </span>
                </button>
                <div className="actions is-center">
                  <button className="btn btn-dark btn-small" onClick={() => playTrack(tracks[0], tracks)} aria-label={`Play ${ARTISTS[id].name}`}>
                    Play
                  </button>
                  {ranked ? (
                    <span className="muted small strong">
                      #{artistOrder.indexOf(id) + 1} · {score(artistElo[id])}
                    </span>
                  ) : (
                    <button className="btn btn-outline btn-small" onClick={() => openRank('artists', id)} aria-label={`Rank ${ARTISTS[id].name}`}>
                      Rank
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {unranked.length > 0 && (
        <section className="list">
          <h2 className="list-heading">Not ranked yet</h2>
          {unranked.slice(0, 5).map((id) => (
            <SongRow key={id} id={id} list={unranked} />
          ))}
        </section>
      )}
    </>
  );
}
