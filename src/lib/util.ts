import type { Elo } from '../types';

/** Seconds -> "m:ss". */
export function fmt(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const r = s % 60;
  return `${Math.floor(s / 60)}:${r < 10 ? '0' : ''}${r}`;
}

/** Up to two initials, for avatars and matchup tiles. */
export function mark(text: string): string {
  return text
    .split(/\s+/)
    .filter((w) => /^[A-Za-z0-9]/.test(w))
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join('');
}

export function rand<T>(list: T[]): T {
  return list[Math.floor(Math.random() * list.length)];
}

/** Ids sorted from highest to lowest rating. */
export function order(elo: Elo): string[] {
  return Object.keys(elo).sort((x, y) => elo[y] - elo[x]);
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
