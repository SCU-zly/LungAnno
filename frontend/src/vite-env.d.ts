/// <reference types="vite/client" />

declare module "@cornerstonejs/codec-openjph" {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const factory: (options?: { locateFile?: (file: string) => string }) => Promise<any>;
  export default factory;
}
