import { useEffect, useRef } from 'react';
import { Dialogs } from './components/Dialogs';
import { PlayerBar } from './components/PlayerBar';
import { SearchOverlay, TopBar } from './components/Search';
import { Sidebar, TabBar } from './components/Sidebar';
import { ArtistPage } from './pages/ArtistPage';
import { Home } from './pages/Home';
import { PlaylistDetail, Playlists } from './pages/Playlists';
import { Profile } from './pages/Profile';
import { Ratings } from './pages/Ratings';
import { Recommendations } from './pages/Recommendations';
import { useCrate } from './state/crate';
import type { Screen } from './types';

const PAGES: Record<Screen, () => JSX.Element> = {
  home: Home,
  recs: Recommendations,
  ratings: Ratings,
  playlists: Playlists,
  playlist: PlaylistDetail,
  artist: ArtistPage,
  profile: Profile,
};

/** Pages you click into (rather than reach from the sidebar) slide in from the right. */
const DRILL_IN: Screen[] = ['playlist', 'artist', 'profile'];

export default function App() {
  const { screen, openPl, artistId, searching } = useCrate();
  const scroller = useRef<HTMLDivElement>(null);
  const Page = PAGES[screen];
  // A new key replays the slide when moving between two playlists or two artists.
  const pageKey = `${screen}:${screen === 'playlist' ? openPl : screen === 'artist' ? artistId : ''}`;

  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [pageKey]);

  return (
    <div className="app">
      <div className="shell">
        <Sidebar />
        <main className="main">
          <TopBar />
          <div className="region">
            {searching && <SearchOverlay />}
            <div ref={scroller} className={`scroll scroll-pad${searching ? ' is-blurred' : ''}`}>
              <div key={pageKey} className={`container page${DRILL_IN.includes(screen) ? ' slide' : ''}`}>
                <Page />
              </div>
            </div>
          </div>
        </main>
      </div>
      <TabBar />
      <PlayerBar />
      <Dialogs />
    </div>
  );
}
