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
export { readNpy, type Matrix } from "./npy.js";
export type Signal = "listeners" | "audio" | "text" | "genre";
export type VectorSignal = "audio" | "text";
export interface Genre {
    genre: string;
    weight: number;
}
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
export interface SearchResult {
    mbid: string;
    name: string;
    in_catalog: boolean;
    score: number;
    matched: string;
}
export interface Track {
    id: number;
    recording_mbid: string | null;
    title: string;
    artist_mbid: string;
    duration_ms: number | null;
    release_date: string | null;
    has_preview: boolean;
}
export interface SimilarArtist {
    mbid: string;
    name: string;
    score: number;
    signal: Signal;
}
export interface GenreCount {
    genre: string;
    artist_count: number;
}
export interface GenreArtist {
    mbid: string;
    name: string;
    weight: number;
}
export interface Similarity {
    mbid_a: string;
    mbid_b: string;
    scores: {
        listeners: number | null;
        audio: number | null;
        text: number | null;
        genre: number | null;
    };
    shared_genres: string[];
}
export interface Resolution {
    mbid: string;
    name: string;
    in_catalog: boolean;
    confidence: number;
}
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
/** Round to 6 decimals from the exact binary value, like Python's round(x, 6). */
export declare function round6(x: number): number;
export declare function normalizeName(s: string): string;
export declare function levenshtein(a: string, b: string): number;
export declare function matchScore(q: string, name: string): number;
/** Seeded PRNG, bit-identical with the Python `Mulberry32`. */
export declare function mulberry32(seed: number): () => number;
export declare class MusicData {
    readonly bundleDir: string;
    private db;
    private manifest;
    private artistRows;
    private names;
    private vectors;
    private genreVecs;
    private previewCache;
    constructor(bundleDir: string);
    close(): void;
    private all;
    private catalog;
    private loadVectors;
    private row;
    private dots;
    private genreMap;
    private static genreCos;
    private listenerEdges;
    private ranked;
    private toArtist;
    searchArtists(query: string, limit?: number, includeUncatalogued?: boolean): SearchResult[];
    resolveArtist(name: string): Resolution | null;
    getArtist(mbid: string): Artist | null;
    getArtists(mbids: string[]): (Artist | null)[];
    getArtistTracks(mbid: string): Track[];
    similarArtists(mbid: string, signal: Signal, limit?: number): SimilarArtist[];
    similarityBetween(mbidA: string, mbidB: string): Similarity | null;
    nearestToVector(vector: ArrayLike<number>, signal: VectorSignal, limit?: number, exclude?: Iterable<string>): SimilarArtist[];
    getVector(mbid: string, signal: VectorSignal): Float32Array | null;
    listGenres(): GenreCount[];
    artistsByGenre(genre: string, limit?: number): GenreArtist[];
    randomArtists(n: number, genre?: string | null, seed?: number | null): Artist[];
    /** Fresh 30s Deezer preview URL for a bundle track id, or null. */
    getPreviewUrl(trackId: number): Promise<string | null>;
    datasetInfo(): DatasetInfo;
}
