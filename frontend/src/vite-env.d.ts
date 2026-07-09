/// <reference types="vite/client" />

declare module "@fontsource-variable/geist";
declare module "@fontsource-variable/inter";
declare module "@tabler/icons-webfont/dist/tabler-icons.css";
declare module "*.css";

interface ImportMetaEnv {
  readonly VITE_OPENOPS_API_MODE?: "mock" | "real";
  readonly VITE_OPENOPS_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
