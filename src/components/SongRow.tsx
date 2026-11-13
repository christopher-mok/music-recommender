import { ARTISTS, TRACKS } from '../data/catalog';
import { score } from '../lib/elo';
import { fmt } from '../lib/util';
import { openSongMenu, playTrack, useCrate } from '../state/crate';
import { Cover } from './Cover';
import { Icon } from './Icon';

interface Props {
  id: string;
  /** The list this row belongs to; it becomes the queue when the row is played. */
  list: string[];
  /** Show the song's position in the user's ranking. */
  rank?: number;
  /** Show the user's score instead of the duration when the song is ranked. */
  showScore?: boolean;
}

export function SongRow({ id, list, rank, showScore }: Props) {
  const { cur, songElo, hl } = useCrate();
  const track = TRACKS[id];
  const artist = ARTISTS[track.artistId];
  const ranked = id in songElo;
  const moved = rank !== undefined && hl?.kind === 'songs' && hl.id === id;

  return (
    <div className={`row${moved ? ' is-moved' : ''}`}>
      <button className="row-main" onClick={() => playTrack(id, list)} aria-label={`Play ${track.title} by ${artist.name}`}>
        {rank !== undefined && <span className={`row-rank${rank <= 3 ? ' is-top' : ''}`}>{rank}</span>}
        <Cover trackId={id} />
        <span className="row-text">
          <span className={`row-title${cur === id ? ' is-current' : ''}`}>{track.title}</span>
          <span className="row-sub">{artist.name}</span>
        </span>
        {showScore && ranked ? (
          <span className="row-score">{score(songElo[id])}</span>
        ) : (
          <span className="row-meta">{fmt(track.dur)}</span>
        )}
      </button>
      <button className="icon-btn is-quiet" onClick={() => openSongMenu(id)} aria-label={`More actions for ${track.title}`}>
        <Icon name="more" />
      </button>
    </div>
  );
}
