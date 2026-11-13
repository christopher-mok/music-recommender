import type { Artist, Elo, Playlist, Track } from '../types';

/*
 * Placeholder catalogue. Every artist and song here is invented; swap this
 * module for a real API / database when there is a backend.
 */

type ArtistSeed = Omit<Artist, 'id'>;
type TrackSeed = Omit<Track, 'id' | 'n'>;

const artistSeeds: Record<string, ArtistSeed> = {
  a1: { name: 'Paper Lanterns', genre: 'Dream pop', color: '#3F5BD9', tint: '#AFC0FF', bpm: 84, wave: 'triangle', minor: false, pat: [0, 1, 2, 3, 2, 1, 2, 1] },
  a2: { name: 'Odette Marlow', genre: 'Soul', color: '#B4532A', tint: '#F4C7A8', bpm: 76, wave: 'sine', minor: false, pat: [0, 2, 1, 2, 0, 2, 3, 2] },
  a3: { name: 'Halcyon Drive', genre: 'Synthwave', color: '#7B3FC4', tint: '#D9B8FF', bpm: 108, wave: 'sawtooth', minor: true, pat: [0, 0, 2, 0, 1, 0, 2, 3] },
  a4: { name: 'Low Tide Social', genre: 'Indie rock', color: '#2E7D5B', tint: '#A9E0C4', bpm: 124, wave: 'square', minor: false, pat: [0, 2, 0, 2, 1, 2, 0, 3] },
  a5: { name: 'Kiko Arai', genre: 'House', color: '#C2410C', tint: '#FFC59E', bpm: 122, wave: 'square', minor: true, pat: [0, 3, 2, 3, 0, 3, 1, 3] },
  a6: { name: 'The Velvet Static', genre: 'Shoegaze', color: '#35607E', tint: '#A8CBE3', bpm: 92, wave: 'sawtooth', minor: false, pat: [0, 1, 2, 1, 0, 1, 2, 3] },
  a7: { name: 'Marrow & Pine', genre: 'Folk', color: '#6B6A2A', tint: '#DAD98A', bpm: 96, wave: 'triangle', minor: false, pat: [0, 2, 1, 2, 3, 2, 1, 2] },
  a8: { name: 'Juno Okafor', genre: 'R&B', color: '#A3336B', tint: '#F2A9CC', bpm: 70, wave: 'sine', minor: true, pat: [0, 2, 3, 2, 1, 2, 3, 1] },
  a9: { name: 'Brass Monsoon', genre: 'Jazz', color: '#8A5A12', tint: '#EBC878', bpm: 132, wave: 'triangle', minor: true, pat: [0, 1, 2, 3, 3, 2, 1, 0] },
  a10: { name: 'Sable Kid', genre: 'Hip-hop', color: '#2B2F36', tint: '#9AA3B2', bpm: 88, wave: 'square', minor: true, pat: [0, 0, 3, 0, 2, 0, 3, 1] },
};

const trackSeeds: Record<string, TrackSeed> = {
  t1: { title: 'Glass Weather', artistId: 'a1', dur: 204, tags: ['rain', 'dreamy', 'night', 'calm', 'soft'], root: 60, prog: [0, 5, 3, 4] },
  t2: { title: 'Slow Polaroid', artistId: 'a1', dur: 231, tags: ['nostalgic', 'summer', 'dreamy', 'sunset', 'slow'], root: 62, prog: [0, 3, 5, 4] },
  t3: { title: 'Honey in the Wire', artistId: 'a2', dur: 198, tags: ['warm', 'love', 'sunday', 'morning', 'cozy'], root: 65, prog: [0, 5, 1, 4] },
  t4: { title: 'Sunday Collar', artistId: 'a2', dur: 245, tags: ['sunday', 'slow', 'cozy', 'coffee', 'calm'], root: 63, prog: [0, 3, 0, 4] },
  t5: { title: 'Neon Causeway', artistId: 'a3', dur: 222, tags: ['drive', 'night', 'neon', 'energy', 'city'], root: 57, prog: [0, 5, 2, 6] },
  t6: { title: "Afterimage '86", artistId: 'a3', dur: 263, tags: ['nostalgic', 'retro', 'night', 'drive', 'late'], root: 59, prog: [0, 2, 5, 4] },
  t7: { title: 'Parking Lot Saints', artistId: 'a4', dur: 187, tags: ['loud', 'summer', 'friends', 'energy', 'upbeat'], root: 64, prog: [0, 4, 5, 3] },
  t8: { title: 'Borrowed Jacket', artistId: 'a4', dur: 209, tags: ['autumn', 'walk', 'bittersweet', 'sad', 'guitar'], root: 62, prog: [0, 5, 3, 3] },
  t9: { title: 'Tangerine Loop', artistId: 'a5', dur: 312, tags: ['dance', 'party', 'upbeat', 'summer', 'workout'], root: 57, prog: [0, 0, 5, 3] },
  t10: { title: '4AM Laundromat', artistId: 'a5', dur: 348, tags: ['late', 'night', 'dance', 'focus', 'hypnotic'], root: 55, prog: [0, 2, 0, 6] },
  t11: { title: 'Feedback Garden', artistId: 'a6', dur: 276, tags: ['loud', 'dreamy', 'hazy', 'rain', 'wall'], root: 61, prog: [0, 3, 5, 3] },
  t12: { title: 'Blurred to Blue', artistId: 'a6', dur: 254, tags: ['sad', 'rain', 'hazy', 'night', 'slow'], root: 59, prog: [0, 5, 3, 4] },
  t13: { title: 'Creek Bed Hymn', artistId: 'a7', dur: 193, tags: ['morning', 'calm', 'acoustic', 'nature', 'cozy'], root: 67, prog: [0, 3, 4, 0] },
  t14: { title: 'Kettle Song', artistId: 'a7', dur: 176, tags: ['cozy', 'morning', 'coffee', 'acoustic', 'happy'], root: 65, prog: [0, 4, 3, 4] },
  t15: { title: 'Velour', artistId: 'a8', dur: 215, tags: ['love', 'late', 'night', 'smooth', 'slow', 'rnb'], root: 58, prog: [0, 5, 3, 6] },
  t16: { title: 'Text Me the Moon', artistId: 'a8', dur: 201, tags: ['love', 'night', 'sad', 'smooth', 'late', 'rnb'], root: 56, prog: [0, 3, 6, 4] },
  t17: { title: 'Monsoon Shuffle', artistId: 'a9', dur: 289, tags: ['rain', 'upbeat', 'brass', 'dinner', 'swing'], root: 60, prog: [0, 3, 6, 2] },
  t18: { title: 'Blue Hour Standard', artistId: 'a9', dur: 334, tags: ['study', 'focus', 'calm', 'late', 'dinner'], root: 58, prog: [0, 5, 1, 4] },
  t19: { title: 'Corner Store Crown', artistId: 'a10', dur: 172, tags: ['confident', 'workout', 'energy', 'city', 'walk', 'rap'], root: 55, prog: [0, 0, 5, 6] },
  t20: { title: 'Ledger', artistId: 'a10', dur: 196, tags: ['focus', 'study', 'night', 'city', 'moody', 'rap'], root: 53, prog: [0, 6, 5, 6] },
};

export const ARTIST_IDS = Object.keys(artistSeeds);
export const TRACK_IDS = Object.keys(trackSeeds);

export const ARTISTS: Record<string, Artist> = Object.fromEntries(
  ARTIST_IDS.map((id) => [id, { id, ...artistSeeds[id] }]),
);

export const TRACKS: Record<string, Track> = Object.fromEntries(
  TRACK_IDS.map((id, n) => [id, { id, n, ...trackSeeds[id] }]),
);

export const GENRES = ARTIST_IDS.map((id) => ARTISTS[id].genre);

export const tracksByArtist = (artistId: string) => TRACK_IDS.filter((id) => TRACKS[id].artistId === artistId);

/** Offsets for the circle on each generated cover, cycled by track index. */
export const COVER_SPOTS: Array<[string, string]> = [
  ['-20%', '-20%'],
  ['50%', '-25%'],
  ['45%', '50%'],
  ['-25%', '45%'],
  ['15%', '15%'],
  ['55%', '15%'],
];

export const PLAYLIST_COLORS = ['#35607E', '#A3336B', '#2E7D5B', '#8A5A12'];

/* Placeholder starting state for the signed-in user. */

export const INITIAL_SONG_ELO: Elo = {
  t1: 1712, t11: 1688, t15: 1655, t5: 1630, t3: 1604, t9: 1571,
  t17: 1548, t7: 1520, t13: 1493, t19: 1466, t6: 1431, t12: 1402,
};

export const INITIAL_ARTIST_ELO: Elo = {
  a1: 1698, a6: 1664, a8: 1621, a3: 1590, a2: 1556, a5: 1512, a9: 1470, a4: 1433,
};

export const INITIAL_PLAYLISTS: Playlist[] = [
  { id: 'p1', name: 'Night Bus Home', desc: 'Windows fogged, headphones on.', ids: ['t5', 't15', 't12', 't10', 't20', 't6'], color: '#2438E0' },
  { id: 'p2', name: 'Sunday Slow', desc: 'Coffee first, everything else later.', ids: ['t3', 't4', 't13', 't14', 't1'], color: '#B93C0A' },
  { id: 'p3', name: 'Desk Hours', desc: 'Steady enough to work to.', ids: ['t18', 't20', 't10', 't2', 't13'], color: '#0F6B5C' },
  { id: 'p4', name: 'Loud Kitchen', desc: 'Cooking for people you like.', ids: ['t7', 't9', 't17', 't19', 't11'], color: '#7A2E8E' },
];

export const USER = { name: 'Chris', initial: 'C' };
