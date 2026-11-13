import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ARTISTS, TRACKS } from '../data/catalog';
import { PLACEMENT_ROUNDS, SEED, score } from '../lib/elo';
import { mark, order } from '../lib/util';
import {
  closeModal,
  createPlaylist,
  deletePlaylist,
  eloKey,
  labelOf,
  openArtist,
  openRank,
  playNext,
  playTrack,
  renamePlaylist,
  seedRank,
  skipDuel,
  toggleInPlaylist,
  undoVote,
  upcoming,
  useCrate,
  vote,
} from '../state/crate';
import type { Kind, Modal } from '../types';
import { Icon } from './Icon';
import { SongRow } from './SongRow';

/** Blurred backdrop + centred panel. Closes on backdrop click, the X, or Escape. */
function Shell({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
  }, []);

  return (
    <div className="modal" onKeyDown={(e) => e.key === 'Escape' && closeModal()}>
      <button className="modal-backdrop fade" onClick={() => closeModal()} aria-label="Close" />
      <div className="modal-panel pop" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <div className="modal-titles">
            <span className="eyebrow">{eyebrow}</span>
            <h2>{title}</h2>
          </div>
          <button ref={close} className="icon-btn" onClick={() => closeModal()} aria-label="Close">
            <Icon name="close" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- ranking */

function DuelCard({ kind, id, other }: { kind: Kind; id: string; other: string }) {
  const isSong = kind === 'songs';
  const artist = isSong ? ARTISTS[TRACKS[id].artistId] : ARTISTS[id];
  const title = labelOf(kind, id);
  return (
    <button className="duel-card card" onClick={() => vote(id, other)}>
      <span className="duel-mark" aria-hidden="true" style={{ background: artist.color, borderRadius: isSong ? 10 : '50%' }}>
        {mark(title)}
      </span>
      <span className="row-text">
        <span className="duel-title">{title}</span>
        <span className="row-sub">{isSong ? `${artist.name} · ${artist.genre}` : artist.genre}</span>
      </span>
    </button>
  );
}

function RankDialog({ modal }: { modal: Extract<Modal, { type: 'rank' }> }) {
  const s = useCrate();
  const { kind, id, stage, votes } = modal;
  const elo = s[eloKey(kind)];
  const title = id ? `Rank “${labelOf(kind, id)}”` : `Keep ranking ${kind}`;
  const eyebrow =
    stage === 'seed'
      ? 'First impression'
      : stage === 'done'
        ? 'Ranked'
        : id
          ? `Matchup ${Math.min(PLACEMENT_ROUNDS, votes + 1)} of ${PLACEMENT_ROUNDS}`
          : `Matchup ${votes + 1}`;
  const undo = s.undo && (
    <button className="btn btn-outline" onClick={undoVote}>
      Undo last pick
    </button>
  );

  return (
    <Shell eyebrow={eyebrow} title={title}>
      {stage === 'seed' && (
        <>
          <p className="muted">How was it? This sets where the matchups start.</p>
          <div className="seed-options">
            <button className="card" onClick={() => seedRank(SEED.loved)}>Loved it</button>
            <button className="card" onClick={() => seedRank(SEED.fine)}>It was fine</button>
            <button className="card" onClick={() => seedRank(SEED.no)}>Not for me</button>
          </div>
        </>
      )}

      {stage === 'duel' && s.duel && (
        <>
          <p className="muted">Which do you like more?</p>
          <div className="duel">
            <DuelCard kind={kind} id={s.duel.a} other={s.duel.b} />
            <DuelCard kind={kind} id={s.duel.b} other={s.duel.a} />
          </div>
          <p className="muted small">{s.note || 'Close any time. Finished matchups are kept.'}</p>
          <div className="actions">
            <button className="btn btn-outline" onClick={skipDuel}>
              Too close, skip
            </button>
            {undo}
            {!id && (
              <button className="btn btn-primary" onClick={() => closeModal()}>
                Done
              </button>
            )}
          </div>
        </>
      )}

      {stage === 'done' && id && (
        <>
          <p className="result">
            {labelOf(kind, id)} is now #{order(elo).indexOf(id) + 1} of {Object.keys(elo).length} {kind}, with a score of{' '}
            {score(elo[id])}.
          </p>
          <div className="actions">
            <button className="btn btn-primary" onClick={() => closeModal()}>
              Done
            </button>
            <button className="btn btn-outline" onClick={() => closeModal(true)}>
              See full ranking
            </button>
            {undo}
          </div>
        </>
      )}
    </Shell>
  );
}

/* ----------------------------------------------------------------- song menu */

function SongMenu({ id }: { id: string }) {
  const { songElo, playlists } = useCrate();
  const track = TRACKS[id];
  const artist = ARTISTS[track.artistId];

  return (
    <Shell eyebrow={artist.name} title={track.title}>
      <div className="menu">
        <button
          onClick={() => {
            playTrack(id);
            closeModal();
          }}
        >
          Play now
        </button>
        <button onClick={() => playNext(id)}>Play next</button>
        <button onClick={() => openRank('songs', id)}>
          {id in songElo ? `Re-rank (score ${score(songElo[id])})` : 'Rank this song'}
        </button>
        <button onClick={() => openArtist(artist.id)}>Go to {artist.name}</button>
      </div>
      <div className="menu menu-section">
        <span className="eyebrow">Playlists</span>
        {playlists.map((p) => {
          const added = p.ids.includes(id);
          return (
            <button key={p.id} aria-pressed={added} onClick={() => toggleInPlaylist(p.id, id)}>
              <span>{p.name}</span>
              <span className={added ? 'menu-state' : 'menu-state is-add'}>{added ? 'Added · remove' : 'Add'}</span>
            </button>
          );
        })}
      </div>
    </Shell>
  );
}

/* --------------------------------------------------------------------- queue */

function QueueDialog() {
  const s = useCrate();
  const list = upcoming(s);
  const note = s.shuffle
    ? 'Shuffle is on, so the order below will vary.'
    : list.length
      ? `Playing after “${TRACKS[s.cur].title}”.`
      : 'Nothing queued after this song.';
  return (
    <Shell eyebrow="Up next" title="Queue">
      <p className="muted small">{note}</p>
      <div className="list">
        {list.map((id) => (
          <SongRow key={id} id={id} list={s.queue} />
        ))}
      </div>
    </Shell>
  );
}

/* ------------------------------------------------------------- playlist name */

function PlaylistNameDialog({ modal }: { modal: Extract<Modal, { type: 'plname' }> }) {
  const { playlists } = useCrate();
  const existing = modal.mode === 'rename' ? playlists.find((p) => p.id === modal.plId) : undefined;
  const [name, setName] = useState(existing?.name ?? '');

  const save = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (existing) renamePlaylist(existing.id, trimmed);
    else {
      closeModal();
      createPlaylist(trimmed);
    }
  };

  return (
    <Shell eyebrow="Playlist" title={existing ? 'Rename playlist' : 'New playlist'}>
      <div className="field">
        <label htmlFor="playlist-name">Name</label>
        <input
          id="playlist-name"
          type="text"
          value={name}
          placeholder="Morning commute"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && save()}
        />
      </div>
      <div className="actions">
        <button className="btn btn-primary" onClick={save}>
          {existing ? 'Save name' : 'Create playlist'}
        </button>
        {existing && (
          <button className="btn btn-outline is-danger" onClick={() => deletePlaylist(existing.id)}>
            Delete playlist
          </button>
        )}
      </div>
    </Shell>
  );
}

/** Renders whichever popup is open, if any. */
export function Dialogs() {
  const { modal } = useCrate();
  if (!modal) return null;
  switch (modal.type) {
    case 'rank':
      return <RankDialog modal={modal} />;
    case 'song':
      return <SongMenu key={modal.id} id={modal.id} />;
    case 'queue':
      return <QueueDialog />;
    case 'plname':
      return <PlaylistNameDialog modal={modal} />;
  }
}
