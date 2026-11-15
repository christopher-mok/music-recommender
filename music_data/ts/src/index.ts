/**
 * Read-only music data API over a published bundle.
 *
 *   import { MusicData } from "music-data";
 *   const md = new MusicData("api_bundle");
 *   md.searchArtists("radiohead");
 *
 * Native port of `music_data.api` (Python). Same functions in camelCase, same
 * snake_case field names in returned objects. Uses Node's built-in
 * `node:sqlite`; the only network call is `getPreviewUrl`. Keep scoring,
 * rounding and tie-break rules identical to the Python version.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { readNpy, type Matrix } from "./npy.js";

export { readNpy, type Matrix } from "./npy.js";

export type Signal = "listeners" | "audio" | "text" | "genre";
export type VectorSignal = "audio" | "text";
const SIGNALS: readonly string[] = ["listeners", "audio", "text", "genre"];
const VECTOR_SIGNALS: readonly string[] = ["audio", "text"];
const PREVIEW_TTL_MS = 600_000;

// ---------------------------------------------------------------- types

export interface Genre { genre: string; weight: number }

export interface Artist {
  mbid: string;
  name: string;
  in_catalog: boolean;
  sort_name: string | null;
  artist_type: string | null;
  country: string | null;
  begin_year: number | null;
  end_year: number | null;
  gender: string | null;
  popularity: number | null;
  popularity_percentile: number | null;
  has_audio: boolean;
  has_text: boolean;
  has_listeners: boolean;
  has_genre: boolean;
  aliases: string[];
  genres: Genre[];
}

export interface SearchResult { mbid: string; name: string; in_catalog: boolean; score: number; matched: string }

export interface Track {
  id: number;
  recording_mbid: string | null;
  title: string;
  artist_mbid: string;
  duration_ms: number | null;
  release_date: string | null;
  has_preview: boolean;
}

export interface SimilarArtist { mbid: string; name: string; score: number; signal: Signal }
export interface GenreCount { genre: string; artist_count: number }
export interface GenreArtist { mbid: string; name: string; weight: number }

export interface Similarity {
  mbid_a: string;
  mbid_b: string;
  scores: { listeners: number | null; audio: number | null; text: number | null; genre: number | null };
  shared_genres: string[];
}

export interface Resolution { mbid: string; name: string; in_catalog: boolean; confidence: number }

export interface DatasetInfo {
  bundle_version: string | null;
  build_time: string | null;
  catalog_artists: number;
  uncatalogued_artists: number;
  tracks: number | null;
  genres: number;
  audio_vectors: number;
  text_vectors: number;
}

// ---------------------------------------------------------------- helpers (mirror Python)

/** Round to 6 decimals from the exact binary value, like Python's round(x, 6). */
export function round6(x: number): number {
  return Number(x.toFixed(6));
}

export function normalizeName(s: string): string {
  const out: string[] = [];
  for (const ch of s.normalize("NFKD")) {
    if (/\p{Mn}/u.test(ch)) continue;
    out.push(/[\p{L}\p{N}]/u.test(ch) ? ch.toLowerCase() : " ");
  }
  return out.join("").split(/\s+/).filter(Boolean).join(" ");
}

export function levenshtein(a: string, b: string): number {
  const A = Array.from(a);
  const B = Array.from(b);
  if (a === b) return 0;
  if (!A.length) return B.length;
  if (!B.length) return A.length;
  let prev = Array.from({ length: B.length + 1 }, (_, i) => i);
  for (let i = 1; i <= A.length; i++) {
    const cur = [i];
    for (let j = 1; j <= B.length; j++) {
      cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (A[i - 1] !== B[j - 1] ? 1 : 0)));
    }
    prev = cur;
  }
  return prev[B.length];
}

export function matchScore(q: string, name: string): number {
  if (!q || !name) return 0;
  if (name === q) return 1;
  if (name.startsWith(q)) return 0.9;
  if ((" " + name).includes(" " + q)) return 0.8;
  if (name.includes(q)) return 0.7;
  const lq = Array.from(q).length;
  const ln = Array.from(name).length;
  if (lq >= 3 && Math.abs(lq - ln) <= 2) {
    const d = levenshtein(q, name);
    if (d <= Math.max(1, Math.floor(lq / 4))) return round6(0.6 * (1 - d / Math.max(lq, ln)));
  }
  return 0;
}

/** Seeded PRNG, bit-identical with the Python `Mulberry32`. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

// ---------------------------------------------------------------- main class

interface ArtistRow {
  mbid: string; name: string; sort_name: string | null; artist_type: string | null;
  country: string | null; begin_year: number | null; end_year: number | null; gender: string | null;
  popularity: number | null; popularity_percentile: number | null;
  has_audio: number; has_text: number; has_listeners: number; has_genre: number; in_catalog: number;
}

interface VectorSet { mat: Matrix; ids: string[]; index: Map<string, number> }

export class MusicData {
  readonly bundleDir: string;
  private db: DatabaseSync;
  private manifest: any;
  private artistRows = new Map<string, ArtistRow>();
  private names: [string, string, string, boolean][] = [];
  private vectors = new Map<string, VectorSet>();
  private genreVecs: Map<string, Map<string, number>> | null = null;
  private previewCache = new Map<number, [number, string | null]>();

  constructor(bundleDir: string) {
    this.bundleDir = bundleDir;
    this.db = new DatabaseSync(join(bundleDir, "music.db"), { readOnly: true });
    this.manifest = JSON.parse(readFileSync(join(bundleDir, "manifest.json"), "utf-8"));
    for (const r of this.all<ArtistRow>("SELECT * FROM artists ORDER BY rowid")) {
      this.artistRows.set(r.mbid, r);
      this.names.push([normalizeName(r.name), r.mbid, r.name, false]);
    }
    for (const r of this.all<{ artist_mbid: string; name: string }>(
      "SELECT artist_mbid, name FROM aliases ORDER BY rowid",
    )) {
      this.names.push([normalizeName(r.name), r.artist_mbid, r.name, true]);
    }
  }

  close(): void {
    this.db.close();
  }

  // ---------------- internal

  private all<T>(sql: string, ...params: (string | number | null)[]): T[] {
    return this.db.prepare(sql).all(...params) as unknown as T[];
  }

  private catalog(mbid: unknown): ArtistRow | null {
    if (typeof mbid !== "string") return null;
    const r = this.artistRows.get(mbid);
    return r && r.in_catalog ? r : null;
  }

  private loadVectors(signal: string): VectorSet {
    let v = this.vectors.get(signal);
    if (!v) {
      const mat = readNpy(join(this.bundleDir, `${signal}.f16.npy`));
      const ids: string[] = JSON.parse(readFileSync(join(this.bundleDir, `${signal}_ids.json`), "utf-8"));
      v = { mat, ids, index: new Map(ids.map((m, i) => [m, i])) };
      this.vectors.set(signal, v);
    }
    return v;
  }

  private row(mat: Matrix, i: number): Float32Array {
    return mat.data.subarray(i * mat.cols, (i + 1) * mat.cols);
  }

  private dots(mat: Matrix, v: ArrayLike<number>): Float64Array {
    const out = new Float64Array(mat.rows);
    const d = mat.data;
    for (let i = 0; i < mat.rows; i++) {
      let s = 0;
      const o = i * mat.cols;
      for (let j = 0; j < mat.cols; j++) s += d[o + j] * v[j];
      out[i] = s;
    }
    return out;
  }

  private genreMap(): Map<string, Map<string, number>> {
    if (!this.genreVecs) {
      const g = new Map<string, Map<string, number>>();
      for (const r of this.all<{ artist_mbid: string; genre: string; weight: number }>(
        "SELECT artist_mbid, genre, weight FROM genres",
      )) {
        if (!g.has(r.artist_mbid)) g.set(r.artist_mbid, new Map());
        g.get(r.artist_mbid)!.set(r.genre, r.weight);
      }
      this.genreVecs = g;
    }
    return this.genreVecs;
  }

  private static genreCos(a: Map<string, number>, b: Map<string, number>): number {
    let dot = 0;
    for (const [k, w] of a) if (b.has(k)) dot += w * b.get(k)!;
    if (dot === 0) return 0;
    let na = 0;
    let nb = 0;
    for (const w of a.values()) na += w * w;
    for (const w of b.values()) nb += w * w;
    return dot / (Math.sqrt(na) * Math.sqrt(nb));
  }

  private listenerEdges(mbid: string): Map<string, number> {
    const out = new Map<string, number>();
    for (const r of this.all<{ other: string; score: number | null }>(
      "SELECT target_mbid AS other, score FROM similarity_edges WHERE source_mbid = ? " +
        "UNION ALL SELECT source_mbid AS other, score FROM similarity_edges WHERE target_mbid = ?",
      mbid, mbid,
    )) {
      if (r.other !== mbid && r.score !== null) out.set(r.other, Math.max(out.get(r.other) ?? 0, r.score));
    }
    if (!out.size) return out;
    const top = Math.max(...out.values());
    for (const [k, v] of out) out.set(k, v / top);
    return out;
  }

  private ranked(scores: Iterable<[string, number]>, signal: Signal, limit: number): SimilarArtist[] {
    const rows: SimilarArtist[] = [];
    for (const [m, s] of scores) {
      const r = this.catalog(m);
      if (r) rows.push({ mbid: m, name: r.name, score: round6(s), signal });
    }
    rows.sort((a, b) => b.score - a.score || cmp(a.mbid, b.mbid) || cmp(a.name, b.name));
    return rows.slice(0, Math.max(0, Math.trunc(limit)));
  }

  private toArtist(r: ArtistRow): Artist {
    const base: Artist = {
      mbid: r.mbid, name: r.name, in_catalog: false,
      sort_name: null, artist_type: null, country: null, begin_year: null, end_year: null, gender: null,
      popularity: null, popularity_percentile: null,
      has_audio: false, has_text: false, has_listeners: false, has_genre: false,
      aliases: [], genres: [],
    };
    if (!r.in_catalog) return base;
    return {
      ...base,
      in_catalog: true,
      sort_name: r.sort_name, artist_type: r.artist_type, country: r.country,
      begin_year: r.begin_year, end_year: r.end_year, gender: r.gender,
      popularity: r.popularity, popularity_percentile: r.popularity_percentile,
      has_audio: !!r.has_audio, has_text: !!r.has_text, has_listeners: !!r.has_listeners, has_genre: !!r.has_genre,
      aliases: this.all<{ name: string }>(
        "SELECT DISTINCT name FROM aliases WHERE artist_mbid = ? ORDER BY name", r.mbid,
      ).map((x) => x.name),
      genres: this.all<Genre>(
        "SELECT genre, weight FROM genres WHERE artist_mbid = ? ORDER BY weight DESC, genre", r.mbid,
      ).map((x) => ({ genre: x.genre, weight: x.weight })),
    };
  }

  // ---------------- search

  searchArtists(query: string, limit = 20, includeUncatalogued = true): SearchResult[] {
    const q = typeof query === "string" ? normalizeName(query) : "";
    if (!q || limit <= 0) return [];
    const best = new Map<string, [number, string]>();
    for (const [norm, mbid, display, isAlias] of this.names) {
      let s = matchScore(q, norm);
      if (s <= 0) continue;
      if (isAlias) s = round6(s * 0.95);
      const cur = best.get(mbid);
      if (!cur || s > cur[0]) best.set(mbid, [s, display]);
    }
    const rows: { r: ArtistRow; s: number; display: string }[] = [];
    for (const [mbid, [s, display]] of best) {
      const r = this.artistRows.get(mbid);
      if (!r || (!includeUncatalogued && !r.in_catalog)) continue;
      rows.push({ r, s, display });
    }
    rows.sort(
      (a, b) =>
        b.r.in_catalog - a.r.in_catalog ||
        b.s - a.s ||
        (b.r.popularity ?? -1) - (a.r.popularity ?? -1) ||
        cmp(a.r.mbid, b.r.mbid),
    );
    return rows.slice(0, limit).map(({ r, s, display }) => ({
      mbid: r.mbid, name: r.name, in_catalog: !!r.in_catalog, score: s, matched: display,
    }));
  }

  resolveArtist(name: string): Resolution | null {
    const hits = this.searchArtists(name, 1);
    if (!hits.length || hits[0].score < 0.5) return null;
    const h = hits[0];
    return { mbid: h.mbid, name: h.name, in_catalog: h.in_catalog, confidence: h.score };
  }

  // ---------------- artists

  getArtist(mbid: string): Artist | null {
    const r = typeof mbid === "string" ? this.artistRows.get(mbid) : undefined;
    return r ? this.toArtist(r) : null;
  }

  getArtists(mbids: string[]): (Artist | null)[] {
    return mbids.map((m) => this.getArtist(m));
  }

  getArtistTracks(mbid: string): Track[] {
    if (!this.catalog(mbid)) return [];
    return this.all<any>(
      `SELECT DISTINCT t.id, t.recording_mbid, t.title, t.artist_mbid, t.duration_ms,
              t.release_date, t.has_preview
       FROM tracks t
       WHERE t.artist_mbid = ?
          OR t.id IN (SELECT recording_id FROM track_artists WHERE artist_mbid = ?)
       ORDER BY t.release_date IS NULL, t.release_date, t.title, t.id`,
      mbid, mbid,
    ).map((r) => ({
      id: r.id, recording_mbid: r.recording_mbid, title: r.title, artist_mbid: r.artist_mbid,
      duration_ms: r.duration_ms, release_date: r.release_date, has_preview: !!r.has_preview,
    }));
  }

  // ---------------- similarity

  similarArtists(mbid: string, signal: Signal, limit = 20): SimilarArtist[] {
    if (!SIGNALS.includes(signal) || !this.catalog(mbid)) return [];
    if (signal === "listeners") return this.ranked(this.listenerEdges(mbid), signal, limit);
    if (signal === "genre") {
      const gm = this.genreMap();
      const mine = gm.get(mbid);
      if (!mine || !mine.size) return [];
      const scores: [string, number][] = [];
      for (const [m, g] of gm) {
        if (m === mbid) continue;
        const s = MusicData.genreCos(mine, g);
        if (s > 0) scores.push([m, s]);
      }
      return this.ranked(scores, signal, limit);
    }
    const { mat, ids, index } = this.loadVectors(signal);
    const i = index.get(mbid);
    if (i === undefined) return [];
    const sims = this.dots(mat, this.row(mat, i));
    const scores: [string, number][] = [];
    sims.forEach((s, k) => { if (ids[k] !== mbid) scores.push([ids[k], s]); });
    return this.ranked(scores, signal, limit);
  }

  similarityBetween(mbidA: string, mbidB: string): Similarity | null {
    if (!this.catalog(mbidA) || !this.catalog(mbidB)) return null;
    const edges = this.listenerEdges(mbidA);
    const scores: Similarity["scores"] = {
      listeners: edges.has(mbidB) ? round6(edges.get(mbidB)!) : null,
      audio: null,
      text: null,
      genre: null,
    };
    for (const sig of ["audio", "text"] as const) {
      const { mat, index } = this.loadVectors(sig);
      const a = index.get(mbidA);
      const b = index.get(mbidB);
      if (a !== undefined && b !== undefined) {
        const ra = this.row(mat, a);
        const rb = this.row(mat, b);
        let s = 0;
        for (let j = 0; j < mat.cols; j++) s += ra[j] * rb[j];
        scores[sig] = round6(s);
      }
    }
    const gm = this.genreMap();
    const ga = gm.get(mbidA);
    const gb = gm.get(mbidB);
    let shared: string[] = [];
    if (ga?.size && gb?.size) {
      scores.genre = round6(MusicData.genreCos(ga, gb));
      shared = [...ga.keys()]
        .filter((k) => gb.has(k))
        .map((k) => [Math.min(ga.get(k)!, gb.get(k)!), k] as const)
        .sort((x, y) => y[0] - x[0] || cmp(x[1], y[1]))
        .map((x) => x[1]);
    }
    return { mbid_a: mbidA, mbid_b: mbidB, scores, shared_genres: shared };
  }

  nearestToVector(
    vector: ArrayLike<number>, signal: VectorSignal, limit = 20, exclude: Iterable<string> = [],
  ): SimilarArtist[] {
    if (!VECTOR_SIGNALS.includes(signal) || !vector) return [];
    const { mat, ids } = this.loadVectors(signal);
    if (vector.length !== mat.cols) return [];
    let n = 0;
    for (let j = 0; j < vector.length; j++) {
      const x = Number(vector[j]);
      if (!Number.isFinite(x)) return [];
      n += x * x;
    }
    n = Math.sqrt(n);
    if (n === 0) return [];
    const v = Float64Array.from({ length: vector.length }, (_, j) => Number(vector[j]) / n);
    const skip = new Set(exclude);
    const sims = this.dots(mat, v);
    const scores: [string, number][] = [];
    sims.forEach((s, k) => { if (!skip.has(ids[k])) scores.push([ids[k], s]); });
    return this.ranked(scores, signal, limit);
  }

  getVector(mbid: string, signal: VectorSignal): Float32Array | null {
    if (!VECTOR_SIGNALS.includes(signal) || typeof mbid !== "string") return null;
    const { mat, index } = this.loadVectors(signal);
    const i = index.get(mbid);
    return i === undefined ? null : Float32Array.from(this.row(mat, i));
  }

  // ---------------- genres / sampling

  listGenres(): GenreCount[] {
    return this.all<{ genre: string; n: number }>(
      "SELECT g.genre, COUNT(DISTINCT g.artist_mbid) AS n FROM genres g " +
        "JOIN artists a ON a.mbid = g.artist_mbid AND a.in_catalog = 1 " +
        "GROUP BY g.genre ORDER BY n DESC, g.genre",
    ).map((r) => ({ genre: r.genre, artist_count: r.n }));
  }

  artistsByGenre(genre: string, limit = 50): GenreArtist[] {
    if (typeof genre !== "string" || limit <= 0) return [];
    return this.all<GenreArtist>(
      "SELECT a.mbid, a.name, g.weight FROM genres g " +
        "JOIN artists a ON a.mbid = g.artist_mbid AND a.in_catalog = 1 " +
        "WHERE g.genre = ? " +
        "ORDER BY g.weight DESC, COALESCE(a.popularity, -1) DESC, a.mbid LIMIT ?",
      genre.trim().toLowerCase(), Math.trunc(limit),
    ).map((r) => ({ mbid: r.mbid, name: r.name, weight: r.weight }));
  }

  randomArtists(n: number, genre: string | null = null, seed: number | null = null): Artist[] {
    if (n <= 0) return [];
    const pool =
      genre === null || genre === undefined
        ? this.all<{ mbid: string }>("SELECT mbid FROM artists WHERE in_catalog = 1 ORDER BY mbid").map((r) => r.mbid)
        : this.all<{ m: string }>(
            "SELECT DISTINCT g.artist_mbid AS m FROM genres g JOIN artists a ON a.mbid = g.artist_mbid " +
              "AND a.in_catalog = 1 WHERE g.genre = ? ORDER BY g.artist_mbid",
            genre.trim().toLowerCase(),
          ).map((r) => r.m);
    const rng = mulberry32(seed ?? (Date.now() >>> 0));
    const k = Math.min(Math.trunc(n), pool.length);
    for (let i = 0; i < k; i++) {
      const j = i + Math.floor(rng() * (pool.length - i));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    return pool.slice(0, k).map((m) => this.toArtist(this.artistRows.get(m)!));
  }

  // ---------------- network

  /** Fresh 30s Deezer preview URL for a bundle track id, or null. */
  async getPreviewUrl(trackId: number): Promise<string | null> {
    const tid = Math.trunc(Number(trackId));
    if (!Number.isFinite(tid)) return null;
    const now = Date.now();
    const hit = this.previewCache.get(tid);
    if (hit && now - hit[0] < PREVIEW_TTL_MS) return hit[1];
    const r = this.db.prepare("SELECT deezer_id FROM tracks WHERE id = ?").get(tid) as
      | { deezer_id: string | null } | undefined;
    if (!r?.deezer_id) return null;
    try {
      const res = await fetch(`https://api.deezer.com/track/${r.deezer_id}`, {
        signal: AbortSignal.timeout(10_000),
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { preview?: string };
      const url = body.preview || null;
      this.previewCache.set(tid, [now, url]);
      return url;
    } catch {
      return null;
    }
  }

  // ---------------- info

  datasetInfo(): DatasetInfo {
    const m = this.manifest;
    return {
      bundle_version: m.bundle_version ?? null,
      build_time: m.build_time ?? null,
      catalog_artists: m.artists.complete,
      uncatalogued_artists: m.artists.stub,
      tracks: m.tracks ?? null,
      genres: this.listGenres().length,
      audio_vectors: m.embeddings.audio.count,
      text_vectors: m.embeddings.text.count,
    };
  }
}
