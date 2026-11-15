import { readFileSync } from "node:fs";
function halfToFloat(h) {
    const sign = h & 0x8000 ? -1 : 1;
    const exp = (h >> 10) & 0x1f;
    const frac = h & 0x3ff;
    if (exp === 0)
        return sign * 2 ** -14 * (frac / 1024);
    if (exp === 0x1f)
        return frac ? NaN : sign * Infinity;
    return sign * 2 ** (exp - 15) * (1 + frac / 1024);
}
/** Read a 2-D little-endian float16 or float32 `.npy` file (C order). */
export function readNpy(path) {
    const buf = readFileSync(path);
    if (buf.readUInt32BE(0) !== 0x934e554d || buf.toString("latin1", 4, 6) !== "PY") {
        throw new Error(`Not a .npy file: ${path}`);
    }
    const major = buf[6];
    const headerLen = major === 1 ? buf.readUInt16LE(8) : buf.readUInt32LE(8);
    const headerStart = major === 1 ? 10 : 12;
    const header = buf.toString("latin1", headerStart, headerStart + headerLen);
    const descr = /'descr':\s*'([^']+)'/.exec(header)?.[1];
    const fortran = /'fortran_order':\s*True/.test(header);
    const shape = (/'shape':\s*\(([^)]*)\)/.exec(header)?.[1] ?? "")
        .split(",").map((s) => s.trim()).filter(Boolean).map(Number);
    if (fortran)
        throw new Error("fortran_order .npy not supported");
    if (shape.length !== 2)
        throw new Error(`Expected 2-D array, got shape ${shape}`);
    const [rows, cols] = shape;
    const n = rows * cols;
    const off = headerStart + headerLen;
    const data = new Float32Array(n);
    if (descr === "<f2") {
        for (let i = 0; i < n; i++)
            data[i] = halfToFloat(buf.readUInt16LE(off + 2 * i));
    }
    else if (descr === "<f4") {
        for (let i = 0; i < n; i++)
            data[i] = buf.readFloatLE(off + 4 * i);
    }
    else {
        throw new Error(`Unsupported dtype ${descr}`);
    }
    return { rows, cols, data };
}
