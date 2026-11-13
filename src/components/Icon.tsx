import type { ReactNode } from 'react';

type IconName =
  | 'home' | 'spark' | 'bars' | 'playlist' | 'play' | 'pause' | 'prev' | 'next'
  | 'shuffle' | 'repeat' | 'queue' | 'volume' | 'more' | 'close';

/* `fill` icons are solid shapes; the rest are 2px strokes. */
const ICONS: Record<IconName, { fill?: boolean; body: ReactNode }> = {
  home: { body: <path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" /> },
  spark: { body: <path d="M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z" /> },
  bars: { body: <path d="M5 20V10M12 20V4M19 20v-7" /> },
  playlist: { body: <><path d="M3 6h14M3 12h14M3 18h8" /><circle cx="18" cy="18" r="3" /></> },
  play: { fill: true, body: <path d="M8 5v14l11-7z" /> },
  pause: { fill: true, body: <><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></> },
  prev: { fill: true, body: <><rect x="5" y="5" width="2.5" height="14" rx="1" /><path d="M20 5L9.5 12 20 19z" /></> },
  next: { fill: true, body: <><rect x="16.5" y="5" width="2.5" height="14" rx="1" /><path d="M4 5l10.5 7L4 19z" /></> },
  shuffle: { body: <path d="M3 7h3c5 0 6 10 11 10h4M3 17h3c2 0 3-2 4-4M14 9c1-1 2-2 3-2h4M18 4l3 3-3 3M18 14l3 3-3 3" /> },
  repeat: { body: <path d="M17 3l3 3-3 3M4 11V9a3 3 0 0 1 3-3h13M7 21l-3-3 3-3M20 13v2a3 3 0 0 1-3 3H4" /> },
  queue: { body: <path d="M4 6h16M4 12h16M4 18h10" /> },
  volume: { body: <><path d="M4 9v6h4l5 4V5L8 9z" /><path d="M17 9a4 4 0 0 1 0 6" /></> },
  more: { fill: true, body: <><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></> },
  close: { body: <path d="M6 6l12 12M18 6L6 18" /> },
};

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const icon = ICONS[name];
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={icon.fill ? 'currentColor' : 'none'}
      stroke={icon.fill ? 'none' : 'currentColor'}
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {icon.body}
    </svg>
  );
}
