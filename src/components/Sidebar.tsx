import { go, openNewPlaylist, openPlaylist, useCrate } from '../state/crate';
import type { Screen } from '../types';
import { Icon } from './Icon';

export const NAV: Array<{ screen: Screen; label: string; short: string; icon: 'home' | 'spark' | 'bars' | 'playlist' }> = [
  { screen: 'home', label: 'Home', short: 'Home', icon: 'home' },
  { screen: 'recs', label: 'Recommendations', short: 'For you', icon: 'spark' },
  { screen: 'ratings', label: 'Ratings', short: 'Ratings', icon: 'bars' },
  { screen: 'playlists', label: 'Playlists', short: 'Playlists', icon: 'playlist' },
];

/** The Playlists item stays highlighted while a single playlist is open. */
export const isActive = (item: Screen, current: Screen) =>
  item === current || (item === 'playlists' && current === 'playlist');

export function Sidebar() {
  const { screen, playlists } = useCrate();
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true"><span /></span>
        <span className="brand-name">Crate</span>
      </div>

      {NAV.map((item) => (
        <button
          key={item.screen}
          className={`nav-item${isActive(item.screen, screen) ? ' is-active' : ''}`}
          aria-current={isActive(item.screen, screen) ? 'page' : undefined}
          onClick={() => go(item.screen)}
        >
          <Icon name={item.icon} />
          {item.label}
        </button>
      ))}

      <div className="sidebar-group">
        <div className="eyebrow sidebar-heading">Your playlists</div>
        {playlists.map((p) => (
          <button key={p.id} className="nav-sub" onClick={() => openPlaylist(p.id)}>
            {p.name}
          </button>
        ))}
        <button className="nav-sub is-accent" onClick={openNewPlaylist}>
          New playlist
        </button>
      </div>
    </nav>
  );
}

/** Replaces the sidebar at phone width. */
export function TabBar() {
  const { screen } = useCrate();
  return (
    <nav className="tabbar" aria-label="Main, compact">
      {NAV.map((item) => (
        <button
          key={item.screen}
          className={isActive(item.screen, screen) ? 'is-active' : ''}
          aria-current={isActive(item.screen, screen) ? 'page' : undefined}
          onClick={() => go(item.screen)}
        >
          <Icon name={item.icon} />
          {item.short}
        </button>
      ))}
    </nav>
  );
}
