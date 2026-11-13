import {
  ARTISTS,
  INITIAL_ARTIST_ELO,
  INITIAL_PLAYLISTS,
  INITIAL_SONG_ELO,
  PLAYLIST_COLORS,
  TRACKS,
  TRACK_IDS,
} from '../data/catalog';
import { Synth } from '../lib/audio';
import { PLACEMENT_ROUNDS, applyResult, pickPair } from '../lib/elo';
import { generatePlaylist, spinSong } from '../lib/recommend';
import { rand } from '../lib/util';
import type { Duel, Elo, Kind, Modal, Playlist, Rec, Screen } from '../types';
import { createStore, useStore } from './store';

/*
 * App state and every action that changes it. Nothing here is persisted:
 * a reload returns to the placeholder data in data/catalog.ts.
 */

interface Focus {
  id: string;
  left: number;
}

type EloKey = 'songElo' | 'artistElo';

interface UndoSnapshot {
  key: EloKey;
  elo: Elo;
  duel: Duel | null;
  focus: Focus | null;
  modal: Modal | null;
  note: string;
  comps: number;
  hl: State['hl'];
}

export interface State {
  /* navigation */
  screen: Screen;
  openPl: string;
  artistId: string;
  /* search */
  searching: boolean;
  q: string;
  /* player (playback position lives in posStore) */
  cur: string;
  queue: string[];
  playing: boolean;
  vol: number;
  shuffle: boolean;
  repeat: boolean;
  /* ratings */
  songElo: Elo;
  artistElo: Elo;
  comps: number;
  /** The row that moved most recently, outlined on the Ratings page. */
  hl: { kind: Kind; id: string } | null;
  rateTab: Kind;
  /* popups + the ranking flow inside them */
  modal: Modal | null;
  duel: Duel | null;
  focus: Focus | null;
  note: string;
  undo: UndoSnapshot | null;
  /* recommendations */
  recMode: 'song' | 'pl';
  genre: string;
  prompt: string;
  recs: Rec[];
  /* playlists */
  playlists: Playlist[];
  plCount: number;
}

const initial: State = {
  screen: 'home',
  openPl: 'p1',
  artistId: 'a1',
  searching: false,
  q: '',
  cur: 't1',
  queue: [...TRACK_IDS],
  playing: false,
  vol: 0.7,
  shuffle: false,
  repeat: true,
  songElo: INITIAL_SONG_ELO,
  artistElo: INITIAL_ARTIST_ELO,
  comps: 0,
  hl: null,
  rateTab: 'songs',
  modal: null,
  duel: null,
  focus: null,
  note: '',
  undo: null,
  recMode: 'song',
  genre: 'Any',
  prompt: '',
  recs: [],
  playlists: INITIAL_PLAYLISTS,
  plCount: 0,
};

export const store = createStore<State>(initial);
/** Playback position, kept separate so the 5x-a-second tick only re-renders the player. */
export const posStore = createStore({ pos: 0 });

export const useCrate = () => useStore(store, (s) => s);
export const usePos = () => useStore(posStore, (s) => s.pos);

const { get, set } = store;

export const eloKey = (kind: Kind): EloKey => (kind === 'songs' ? 'songElo' : 'artistElo');
export const labelOf = (kind: Kind, id: string) => (kind === 'songs' ? TRACKS[id].title : ARTISTS[id].name);

/* ---------------------------------------------------------------- navigation */

const SEARCH_OFF = { searching: false, q: '' };

export function go(screen: Screen) {
  set({ screen, ...SEARCH_OFF });
}
export function openPlaylist(id: string) {
  set({ screen: 'playlist', openPl: id, modal: null, ...SEARCH_OFF });
}
export function openArtist(id: string) {
  set({ screen: 'artist', artistId: id, modal: null, ...SEARCH_OFF });
}
export function showRatings(tab: Kind) {
  set({ screen: 'ratings', rateTab: tab, ...SEARCH_OFF });
}
export const setRateTab = (rateTab: Kind) => set({ rateTab });

/* -------------------------------------------------------------------- search */

export function openSearch() {
  if (!get().searching) set({ searching: true });
}
export function closeSearch() {
  set(SEARCH_OFF);
}
export const setQuery = (q: string) => set({ q, searching: true });

/* -------------------------------------------------------------------- player */

const synth = new Synth();
let ticker: number | undefined;
let lastTick = 0;

function tick() {
  const s = get();
  if (!s.playing) return;
  const now = Date.now();
  const dt = Math.min(1, (now - lastTick) / 1000);
  lastTick = now;
  const pos = posStore.get().pos + dt;
  if (pos >= TRACKS[s.cur].dur) {
    next(true);
    return;
  }
  posStore.set({ pos });
}

function startAudio(id: string) {
  const track = TRACKS[id];
  synth.play(track, ARTISTS[track.artistId], get().vol);
  lastTick = Date.now();
  if (ticker === undefined) ticker = window.setInterval(tick, 200);
}

/** Play a song. `list` becomes the queue (defaults to just this song). */
export function playTrack(id: string, list?: string[]) {
  set({ cur: id, queue: list && list.length ? [...list] : [id], playing: true });
  posStore.set({ pos: 0 });
  startAudio(id);
}

export function togglePlay() {
  const playing = !get().playing;
  set({ playing });
  if (playing) startAudio(get().cur);
  else synth.pause();
}

function switchTo(id: string) {
  set({ cur: id });
  posStore.set({ pos: 0 });
  if (get().playing) startAudio(id);
}

/** `auto` is true when a song ended by itself, so repeat-off can stop at the end of the queue. */
export function next(auto = false) {
  const s = get();
  const i = s.queue.indexOf(s.cur);
  if (s.shuffle && s.queue.length > 1) return switchTo(rand(s.queue.filter((x) => x !== s.cur)));
  if (i + 1 < s.queue.length) return switchTo(s.queue[i + 1]);
  if (s.repeat || !auto) return switchTo(s.queue[0]);
  synth.pause();
  set({ playing: false });
  posStore.set({ pos: 0 });
}

export function prev() {
  const s = get();
  if (posStore.get().pos > 3) {
    posStore.set({ pos: 0 });
    return;
  }
  const i = Math.max(0, s.queue.indexOf(s.cur));
  switchTo(s.queue[(i - 1 + s.queue.length) % s.queue.length]);
}

export function playNext(id: string) {
  const s = get();
  if (id === s.cur) return set({ modal: null });
  const queue = s.queue.filter((x) => x !== id);
  queue.splice(queue.indexOf(s.cur) + 1, 0, id);
  set({ queue, modal: null });
}

export const seek = (pos: number) => posStore.set({ pos });
export function setVolume(vol: number) {
  set({ vol });
  synth.setVolume(vol);
}
export const toggleShuffle = () => set({ shuffle: !get().shuffle });
export const toggleRepeat = () => set({ repeat: !get().repeat });

/** Songs that will play after the current one, in order (up to 12). */
export function upcoming(s: State): string[] {
  const i = s.queue.indexOf(s.cur);
  return [...s.queue.slice(i + 1), ...(s.repeat ? s.queue.slice(0, Math.max(0, i)) : [])].slice(0, 12);
}

/* ------------------------------------------------------------------- ranking */

const RANK_RESET = { focus: null, duel: null, note: '', undo: null };

/**
 * Open the ranking popup. With an id: a new item first gets a first-impression
 * question, then three placement matchups. Without an id: endless matchups.
 */
export function openRank(kind: Kind, id: string | null) {
  const elo = get()[eloKey(kind)];
  if (id && !(id in elo)) {
    set({ ...RANK_RESET, modal: { type: 'rank', kind, id, stage: 'seed', votes: 0 } });
    return;
  }
  set({
    ...RANK_RESET,
    modal: { type: 'rank', kind, id, stage: 'duel', votes: 0 },
    focus: id ? { id, left: PLACEMENT_ROUNDS } : null,
    duel: pickPair(elo, id, null),
  });
}

/** Answer the first-impression question: enter the item at `start` and begin matchups. */
export function seedRank(start: number) {
  const s = get();
  const m = s.modal;
  if (!m || m.type !== 'rank' || !m.id) return;
  const key = eloKey(m.kind);
  const elo = { ...s[key], [m.id]: start };
  set({
    [key]: elo,
    modal: { ...m, stage: 'duel' },
    focus: { id: m.id, left: PLACEMENT_ROUNDS },
    duel: pickPair(elo, m.id, null),
    hl: { kind: m.kind, id: m.id },
  } as Partial<State>);
}

export function vote(win: string, lose: string) {
  const s = get();
  const m = s.modal;
  if (!m || m.type !== 'rank') return;
  const key = eloKey(m.kind);
  const { elo, delta } = applyResult(s[key], win, lose);
  const focus = s.focus && s.focus.left > 1 ? { id: s.focus.id, left: s.focus.left - 1 } : null;
  const finished = !!m.id && !focus;
  const avoid = focus ? (win === focus.id ? lose : win) : null;
  set({
    [key]: elo,
    undo: { key, elo: s[key], duel: s.duel, focus: s.focus, modal: m, note: s.note, comps: s.comps, hl: s.hl },
    comps: s.comps + 1,
    focus,
    hl: { kind: m.kind, id: m.id ?? win },
    note: `${labelOf(m.kind, win)} +${delta}  ·  ${labelOf(m.kind, lose)} −${delta}`,
    duel: finished ? null : pickPair(elo, focus ? focus.id : null, avoid),
    modal: { ...m, votes: m.votes + 1, stage: finished ? 'done' : 'duel' },
  } as Partial<State>);
}

export function undoVote() {
  const u = get().undo;
  if (!u) return;
  set({
    [u.key]: u.elo,
    undo: null,
    duel: u.duel,
    focus: u.focus,
    modal: u.modal,
    note: 'Last pick undone.',
    comps: u.comps,
    hl: u.hl,
  } as Partial<State>);
}

export function skipDuel() {
  const s = get();
  const m = s.modal;
  if (!m || m.type !== 'rank') return;
  set({ duel: pickPair(s[eloKey(m.kind)], s.focus ? s.focus.id : null, null) });
}

/* -------------------------------------------------------------------- popups */

export const openSongMenu = (id: string) => set({ modal: { type: 'song', id } });
export const openQueue = () => set({ modal: { type: 'queue' } });
export const openNewPlaylist = () => set({ modal: { type: 'plname', mode: 'create' } });
export const openRenamePlaylist = (plId: string) => set({ modal: { type: 'plname', mode: 'rename', plId } });

export function closeModal(toRatings = false) {
  const m = get().modal;
  set({ modal: null, focus: null, duel: null, undo: null });
  if (toRatings && m && m.type === 'rank') showRatings(m.kind);
}

/* ----------------------------------------------------------------- playlists */

export function toggleInPlaylist(plId: string, trackId: string) {
  set({
    playlists: get().playlists.map((p) =>
      p.id !== plId
        ? p
        : { ...p, ids: p.ids.includes(trackId) ? p.ids.filter((x) => x !== trackId) : [...p.ids, trackId] },
    ),
  });
}

function addPlaylist(fields: Omit<Playlist, 'id'>): Playlist {
  const s = get();
  const n = s.plCount + 1;
  const playlist = { id: `u${n}`, ...fields };
  set({ plCount: n, playlists: [...s.playlists, playlist] });
  return playlist;
}

export function createPlaylist(name: string) {
  const color = PLAYLIST_COLORS[get().plCount % PLAYLIST_COLORS.length];
  const playlist = addPlaylist({ name, desc: 'Made by you', ids: [], color });
  openPlaylist(playlist.id);
}

export function renamePlaylist(plId: string, name: string) {
  set({ modal: null, playlists: get().playlists.map((p) => (p.id === plId ? { ...p, name } : p)) });
}

export function deletePlaylist(plId: string) {
  set({ modal: null, screen: 'playlists', playlists: get().playlists.filter((p) => p.id !== plId) });
}

/* ----------------------------------------------------------- recommendations */

export const setRecMode = (recMode: State['recMode']) => set({ recMode });
export const setGenre = (genre: string) => set({ genre });
export const setPrompt = (prompt: string) => set({ prompt });

export function runRec() {
  const s = get();
  const last = s.recs[0];
  const entry =
    s.recMode === 'song'
      ? spinSong(s.genre, s.prompt, last && last.type === 'song' ? last.id : undefined)
      : generatePlaylist(s.prompt);
  set({ recs: [entry, ...s.recs].slice(0, 6) });
}

/** Save the newest generated playlist to the user's playlists. */
export function saveRec() {
  const s = get();
  const r = s.recs[0];
  if (!r || r.type !== 'pl' || r.saved) return;
  addPlaylist({
    name: r.name,
    desc: r.prompt ? `Generated from “${r.prompt}”` : 'Generated random mix',
    ids: [...r.ids],
    color: '#111418',
  });
  set({ recs: [{ ...r, saved: true }, ...s.recs.slice(1)] });
}

/** Bring an earlier result back to the top. */
export function reopenRec(index: number) {
  const list = [...get().recs];
  const [entry] = list.splice(index, 1);
  if (entry) set({ recs: [entry, ...list] });
}
