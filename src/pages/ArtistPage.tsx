import { Avatar } from '../components/Cover';
import { SongRow } from '../components/SongRow';
import { ARTISTS, tracksByArtist } from '../data/catalog';
import { score } from '../lib/elo';
import { order } from '../lib/util';
import { openRank, playTrack, useCrate } from '../state/crate';

export function ArtistPage() {
  const { artistId, artistElo } = useCrate();
  const artist = ARTISTS[artistId];
  const tracks = tracksByArtist(artistId);
  const ranked = artistId in artistElo;

  return (
    <>
      <header className="hero is-center">
        <Avatar artistId={artistId} size={128} />
        <div className="hero-text">
          <span className="eyebrow">{artist.genre}</span>
          <h1>{artist.name}</h1>
          <span className="strong small">
            {ranked
              ? `Your #${order(artistElo).indexOf(artistId) + 1} artist · score ${score(artistElo[artistId])}`
              : 'Not ranked yet'}
          </span>
          <div className="actions">
            <button className="btn btn-primary" onClick={() => playTrack(tracks[0], tracks)}>
              Play
            </button>
            <button className="btn btn-outline" onClick={() => openRank('artists', artistId)}>
              {ranked ? 'Re-rank artist' : 'Rank artist'}
            </button>
          </div>
        </div>
      </header>
      <section className="list">
        <h2 className="list-heading">Songs</h2>
        {tracks.map((id) => (
          <SongRow key={id} id={id} list={tracks} showScore />
        ))}
      </section>
    </>
  );
}
