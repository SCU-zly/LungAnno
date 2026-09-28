/**
 * SharedArrayBuffer 兼容 polyfill。
 *
 * 浏览器只在安全上下文（HTTPS 或 localhost）提供 SharedArrayBuffer；
 * 本系统当前经 HTTP 公网域名访问（非安全上下文），该 API 不存在，
 * 而 cornerstone/nifti-volume-loader 中的 `instanceof SharedArrayBuffer`
 * 与 `new SharedArrayBuffer(...)` 会直接抛 ReferenceError。
 *
 * 这里将其退化为普通 ArrayBuffer：功能可用，只是不跨 worker 共享内存
 * （配合 useViewer 中的 setUseSharedArrayBuffer(FALSE)，不会走到依赖
 * 共享内存的路径）。将来上 HTTPS 后可删除本文件并恢复 AUTO 模式。
 */
if (typeof window.SharedArrayBuffer === "undefined") {
  (window as unknown as { SharedArrayBuffer: typeof ArrayBuffer }).SharedArrayBuffer = ArrayBuffer;
}

export {};
