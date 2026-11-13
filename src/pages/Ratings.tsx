import { ArtistRow } from '../components/ArtistRow';
import { SongRow } from '../components/SongRow';
import { order } from '../lib/util';
import { openRank, setRateTab, useCrate } from '../state/crate';

export function Ratings() {
  const { rateTab, songElo, artistElo, note } = useCrate();
  const songs = order(songElo);
  const artists = order(artistElo);
  const showNote = note && note !== 'Last pick undone.';

  return (
    <>
      <h1>Your ratings</h1>
      <div className="section-head is-wrap">
        <div className="segmented">
          <button aria-pressed={rateTab === 'songs'} onClick={() => setRateTab('songs')}>
            Songs
          </button>
          <button aria-pressed={rateTab === 'artists'} onClick={() => setRateTab('artists')}>
            Artists
          </button>
        </div>
        <button className="btn btn-primary btn-large" onClick={() => openRank(rateTab, null)}>
          Keep ranking
        </button>
      </div>
      <p className="muted small">
        {showNote
          ? `Last matchup: ${note}`
          : 'Ordered by head-to-head matchups. The outlined row is the one that moved last.'}
      </p>
      <div className="list">
        {rateTab === 'songs'
          ? songs.map((id, i) => <SongRow key={id} id={id} list={songs} rank={i + 1} showScore />)
          : artists.map((id, i) => <ArtistRow key={id} id={id} rank={i + 1} />)}
      </div>
    </>
  );
}
