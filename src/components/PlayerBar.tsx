import { ARTISTS, TRACKS } from '../data/catalog';
import { score } from '../lib/elo';
import { fmt } from '../lib/util';
import {
  next,
  openQueue,
  openRank,
  prev,
  seek,
  setVolume,
  togglePlay,
  toggleRepeat,
  toggleShuffle,
  useCrate,
  usePos,
} from '../state/crate';
import { Cover } from './Cover';
import { Icon } from './Icon';

function SeekBar({ duration }: { duration: number }) {
  const pos = usePos();
  return (
    <div className="seek">
      <span className="seek-time is-left">{fmt(pos)}</span>
      <input
        type="range"
        aria-label="Seek"
        min={0}
        max={duration}
        step={1}
        value={Math.floor(pos)}
        onChange={(e) => seek(Number(e.target.value))}
      />
      <span className="seek-time">{fmt(duration)}</span>
    </div>
  );
}

export function PlayerBar() {
  const { cur, playing, shuffle, repeat, vol, songElo } = useCrate();
  const track = TRACKS[cur];
  const artist = ARTISTS[track.artistId];
  const ranked = cur in songElo;

  return (
    <footer className="player" aria-label="Player">
      <div className="player-now">
        <Cover trackId={cur} size={52} />
        <div className="row-text">
          <span className="row-title">{track.title}</span>
          <span className="row-sub">
            {artist.name} · {artist.genre}
          </span>
        </div>
      </div>

      <div className="player-center">
        <div className="player-controls">
          <button className={`icon-btn${shuffle ? ' is-on' : ' is-quiet'}`} onClick={toggleShuffle} aria-label="Shuffle" aria-pressed={shuffle}>
            <Icon name="shuffle" />
          </button>
          <button className="icon-btn" onClick={prev} aria-label="Previous song">
            <Icon name="prev" size={22} />
          </button>
          <button className="play-btn" onClick={togglePlay} aria-label={playing ? 'Pause' : 'Play'}>
            <Icon name={playing ? 'pause' : 'play'} size={22} />
          </button>
          <button className="icon-btn" onClick={() => next()} aria-label="Next song">
            <Icon name="next" size={22} />
          </button>
          <button className={`icon-btn${repeat ? ' is-on' : ' is-quiet'}`} onClick={toggleRepeat} aria-label="Repeat" aria-pressed={repeat}>
            <Icon name="repeat" />
          </button>
        </div>
        <SeekBar duration={track.dur} />
      </div>

      <div className="player-side">
        <button className="btn btn-outline btn-small" onClick={() => openRank('songs', cur)}>
          {ranked ? `Your score ${score(songElo[cur])}` : 'Rank this song'}
        </button>
        <button className="icon-btn" onClick={openQueue} aria-label="Queue">
          <Icon name="queue" />
        </button>
        <div className="volume">
          <Icon name="volume" />
          <input
            type="range"
            aria-label="Volume"
            min={0}
            max={100}
            step={1}
            value={Math.round(vol * 100)}
            onChange={(e) => setVolume(Number(e.target.value) / 100)}
          />
        </div>
      </div>
    </footer>
  );
}
