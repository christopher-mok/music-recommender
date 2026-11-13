import { ARTISTS, COVER_SPOTS, TRACKS } from '../data/catalog';
import { mark } from '../lib/util';

/** Generated cover art for a song: the artist's colour with an offset circle. */
export function Cover({ trackId, size = 44, radius = 8 }: { trackId: string; size?: number; radius?: number }) {
  const track = TRACKS[trackId];
  const artist = ARTISTS[track.artistId];
  const [left, top] = COVER_SPOTS[track.n % COVER_SPOTS.length];
  return (
    <span className="cover" aria-hidden="true" style={{ width: size, height: size, borderRadius: radius, background: artist.color }}>
      <span style={{ background: artist.tint, left, top }} />
    </span>
  );
}

/** Round avatar with the artist's initials. */
export function Avatar({ artistId, size = 44 }: { artistId: string; size?: number }) {
  const artist = ARTISTS[artistId];
  return (
    <span className="avatar" aria-hidden="true" style={{ width: size, height: size, background: artist.color, fontSize: Math.round(size * 0.34) }}>
      {mark(artist.name)}
    </span>
  );
}
