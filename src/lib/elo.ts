import type { Duel, Elo } from '../types';
import { rand } from './util';

/** How far one result can move a rating. */
export const K = 32;

/** Rating a new item starts at, by first impression. */
export const SEED = { loved: 1650, fine: 1500, no: 1350 } as const;

/** Number of placement matchups when ranking one specific item. */
export const PLACEMENT_ROUNDS = 3;

/** 0-10 display score: the chance of beating an average (1500) pick, times ten. */
export function score(elo: number): string {
  return (10 / (1 + Math.pow(10, (1500 - elo) / 400))).toFixed(1);
}

/** Standard Elo update. Returns a new table and how many points changed hands. */
export function applyResult(elo: Elo, win: string, lose: string): { elo: Elo; delta: number } {
  const expected = 1 / (1 + Math.pow(10, (elo[lose] - elo[win]) / 400));
  const delta = Math.max(1, Math.round(K * (1 - expected)));
  return { elo: { ...elo, [win]: elo[win] + delta, [lose]: elo[lose] - delta }, delta };
}

/**
 * Choose the next matchup. With `focus`, that item is always one side and its
 * opponent is one of the three closest-rated others; otherwise both are drawn
 * from near each other. `avoid` keeps the same opponent from repeating.
 */
export function pickPair(elo: Elo, focus: string | null, avoid: string | null): Duel {
  const ids = Object.keys(elo);
  const a = focus ?? rand(ids);
  let pool = ids.filter((x) => x !== a && x !== avoid);
  if (!pool.length) pool = ids.filter((x) => x !== a);
  pool.sort((x, y) => Math.abs(elo[x] - elo[a]) - Math.abs(elo[y] - elo[a]));
  const b = rand(pool.slice(0, 3));
  return Math.random() < 0.5 ? { a, b } : { a: b, b: a };
}
