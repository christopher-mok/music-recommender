/** A row-major float matrix loaded from a `.npy` file. */
export interface Matrix {
    rows: number;
    cols: number;
    data: Float32Array;
}
/** Read a 2-D little-endian float16 or float32 `.npy` file (C order). */
export declare function readNpy(path: string): Matrix;
