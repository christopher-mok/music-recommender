import { ARTISTS, ARTIST_IDS, TRACKS, TRACK_IDS } from '../data/catalog';
import type { Rec } from '../types';
import { rand } from './util';

/*
 * Placeholder recommender: keyword matching against each song's tags, genre,
 * artist and title. Replace with a real model / service; the UI only depends
 * on the `Rec` shapes returned here.
 */

const STOP = new Set([
  'the', 'and', 'for', 'with', 'songs', 'song', 'music', 'playlist', 'some', 'that', 'like', 'feel', 'feels',
  'vibe', 'vibes', 'something', 'from', 'after', 'while', 'when', 'into', 'long', 'home', 'want', 'make', 'give',
]);

export function tokens(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/r&b/g, 'rnb')
    .replace(/hip-hop|hip hop/g, 'rap')
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length > 2 && !STOP.has(w));
}

/** The prompt tokens that a song matches. */
export function match(id: string, toks: string[]): string[] {
  const t = TRACKS[id];
  const a = ARTISTS[t.artistId];
  const words = [
    ...t.tags,
    ...a.genre.toLowerCase().split(/[^a-z]+/),
    ...a.name.toLowerCase().split(/[^a-z]+/),
    ...t.title.toLowerCase().split(/[^a-z0-9]+/),
  ].filter((w) => w.length > 2);
  return toks.filter((k) => words.some((w) => w.startsWith(k) || k.startsWith(w)));
}

/** One random song, narrowed by genre and/or a free-text vibe. */
export function spinSong(genre: string, vibe: string, avoidId?: string): Rec {
  let pool = TRACK_IDS.filter((id) => genre === 'Any' || ARTISTS[TRACKS[id].artistId].genre === genre);
  if (avoidId && pool.length > 1) pool = pool.filter((id) => id !== avoidId);
  const toks = tokens(vibe);
  const where = genre === 'Any' ? 'the whole library' : genre;

  let best = 0;
  let top: string[] = [];
  for (const id of pool) {
    const n = match(id, toks).length;
    if (n > best) {
      best = n;
      top = [id];
    } else if (n === best && n > 0) {
      top.push(id);
    }
  }

  if (best > 0) {
    const id = rand(top);
    return { type: 'song', id, reason: `Random pick among ${top.length} in ${where} tagged for “${match(id, toks).join('”, “')}”.` };
  }
  return {
    type: 'song',
    id: rand(pool),
    reason: toks.length
      ? `Nothing in ${where} is tagged for that vibe, so this is a straight random pull.`
      : `Straight random pull from ${where}.`,
  };
}

const PLAYLIST_LENGTH = 8;

/** Eight songs for a prompt: best tag matches first, random filler after. */
export function generatePlaylist(prompt: string): Rec {
  const text = prompt.trim();
  const toks = tokens(text);
  const picks = TRACK_IDS.map((id) => ({ id, n: match(id, toks).length, r: Math.random() }))
    .sort((x, y) => y.n - x.n || x.r - y.r)
    .slice(0, PLAYLIST_LENGTH);
  const hits = picks.filter((p) => p.n > 0).length;
  const note = !text
    ? 'No prompt given, so these are eight random songs.'
    : hits === PLAYLIST_LENGTH
      ? 'All eight match words in your prompt.'
      : hits > 0
        ? `${hits} of ${PLAYLIST_LENGTH} match words in your prompt. The rest fill it out.`
        : 'No tags matched that prompt, so these are eight random songs.';
  return {
    type: 'pl',
    name: text ? (text.charAt(0).toUpperCase() + text.slice(1)).slice(0, 40) : 'Random mix',
    note,
    ids: picks.map((p) => p.id),
    prompt: text,
    saved: false,
  };
}

/* Search: plain substring match. */

export function searchSongs(query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return TRACK_IDS.filter((id) => {
    const t = TRACKS[id];
    const a = ARTISTS[t.artistId];
    return `${t.title} ${a.name} ${a.genre} ${t.tags.join(' ')}`.toLowerCase().includes(q);
  });
}

export function searchArtists(query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return ARTIST_IDS.filter((id) => `${ARTISTS[id].name} ${ARTISTS[id].genre}`.toLowerCase().includes(q));
}
