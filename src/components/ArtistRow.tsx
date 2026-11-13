import { ARTISTS } from '../data/catalog';
import { score } from '../lib/elo';
import { openArtist, openRank, useCrate } from '../state/crate';
import { Avatar } from './Cover';

interface Props {
  id: string;
  /** Show the artist's position in the user's ranking. */
  rank?: number;
  /** Show the Rank / Re-rank button. */
  action?: boolean;
}

export function ArtistRow({ id, rank, action = true }: Props) {
  const { artistElo, hl } = useCrate();
  const artist = ARTISTS[id];
  const ranked = id in artistElo;
  const moved = rank !== undefined && hl?.kind === 'artists' && hl.id === id;
  const label = ranked ? 'Re-rank' : 'Rank';

  return (
    <div className={`row${moved ? ' is-moved' : ''}`}>
      <button className="row-main" onClick={() => openArtist(id)} aria-label={`Open ${artist.name}`}>
        {rank !== undefined && <span className={`row-rank${rank <= 3 ? ' is-top' : ''}`}>{rank}</span>}
        <Avatar artistId={id} />
        <span className="row-text">
          <span className="row-title">{artist.name}</span>
          <span className="row-sub">{artist.genre}</span>
        </span>
        {ranked && <span className="row-score">{score(artistElo[id])}</span>}
      </button>
      {action ? (
        <button className="btn btn-outline btn-small" onClick={() => openRank('artists', id)} aria-label={`${label} ${artist.name}`}>
          {label}
        </button>
      ) : (
        <span className="row-spacer" aria-hidden="true" />
      )}
    </div>
  );
}
