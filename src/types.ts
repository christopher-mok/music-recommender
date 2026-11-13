export type Kind = 'songs' | 'artists';

export interface Artist {
  id: string;
  name: string;
  genre: string;
  /** Cover / avatar colour. */
  color: string;
  /** Lighter companion colour used for the cover shape. */
  tint: string;
  /* Parameters for the placeholder synth loop (see lib/audio.ts). */
  bpm: number;
  wave: OscillatorType;
  minor: boolean;
  pat: number[];
}

export interface Track {
  id: string;
  title: string;
  artistId: string;
  /** Duration in seconds. */
  dur: number;
  /** Mood / context tags used by search and recommendations. */
  tags: string[];
  /* Placeholder synth loop parameters. */
  root: number;
  prog: number[];
  /** Index in the catalogue, used to vary cover art. */
  n: number;
}

export interface Playlist {
  id: string;
  name: string;
  desc: string;
  ids: string[];
  color: string;
}

export type Elo = Record<string, number>;

export type Screen = 'home' | 'recs' | 'ratings' | 'playlists' | 'playlist' | 'artist' | 'profile';

export type Modal =
  | { type: 'rank'; kind: Kind; id: string | null; stage: 'seed' | 'duel' | 'done'; votes: number }
  | { type: 'song'; id: string }
  | { type: 'queue' }
  | { type: 'plname'; mode: 'create' | 'rename'; plId?: string };

export type Rec =
  | { type: 'song'; id: string; reason: string }
  | { type: 'pl'; name: string; note: string; ids: string[]; prompt: string; saved: boolean };

export interface Duel {
  a: string;
  b: string;
}
