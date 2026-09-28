/**
 * HTJ2K wasm 解码封装（@cornerstonejs/codec-openjph，OpenJPH 的 JS/WebAssembly 构建）。
 * wasm 模块单例懒加载；.wasm 文件经 vite ?url 作为静态资源分发。
 */
import openjphFactory from "@cornerstonejs/codec-openjph";
import wasmUrl from "@cornerstonejs/codec-openjph/wasm?url";

/* eslint-disable @typescript-eslint/no-explicit-any */
let modulePromise: Promise<any> | null = null;

function getModule(): Promise<any> {
  if (!modulePromise) {
    modulePromise = openjphFactory({ locateFile: () => wasmUrl });
  }
  return modulePromise;
}

/**
 * 解码一段 HTJ2K 码流为 Int16Array（LE）。
 * getDecodedBuffer 返回的是 wasm 堆上的视图，下轮解码会失效——先整体 memcpy 再建视图。
 */
export async function decodeHtj2kSlice(bytes: Uint8Array): Promise<Int16Array> {
  const mod = await getModule();
  const decoder = new mod.HTJ2KDecoder();
  decoder.getEncodedBuffer(bytes.length).set(bytes);
  decoder.readHeader();
  decoder.decode();
  const buf: Uint8Array = decoder.getDecodedBuffer();
  const copy = new Uint8Array(buf.byteLength);
  copy.set(buf);
  decoder.delete?.();
  return new Int16Array(copy.buffer);
}
