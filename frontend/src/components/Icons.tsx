import type { CSSProperties } from "react";

const paths = {
  menu: "M3 6h18M3 12h18M3 18h18",
  home: "m3 10 9-7 9 7v11h-6v-7H9v7H3Z",
  channels: "M7 3h10M4 6h16M3 9h18v12H3ZM10 12l5 3-5 3Z",
  queue: "M4 4h11M4 9h11M4 14h6M17 9v12m-4-4 4 4 4-4",
  storage: "M3 4h18v6H3ZM3 14h18v6H3ZM17 7h1M17 17h1",
  settings: "m9 3-1 3-3 1-2 4 2 2v3l3 2 1 3h6l1-3 3-2v-3l2-2-2-4-3-1-1-3Zm6 9a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  search: "M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0Zm-1.5 4.5L21 21",
  plus: "M12 4v16M4 12h16",
  close: "m6 6 12 12M6 18 18 6",
  arrowLeft: "m11 4-8 8 8 8M3 12h18",
  chevronRight: "m9 5 7 7-7 7",
  chevronDown: "m5 9 7 7 7-7",
  play: "m8 4 12 8-12 8Z",
  playlist: "M3 5h14M3 10h14M3 15h7m5-2 7 4-7 4Z",
  download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
  filter: "M3 6h18M3 12h18M3 18h18M8 3v6M16 9v6M10 15v6",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  refresh: "M20 7a9 9 0 1 0 1 7M20 3v5h-5",
  check: "m4 12 5 5L20 6",
  sun: "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1",
  moon: "M20.5 14A9 9 0 0 1 10 3a9 9 0 1 0 10.5 11Z",
  more: "M12 4h.01M12 12h.01M12 20h.01",
  info: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 11v6M12 7h.01",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 24, className, style }: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg className={className} style={style} width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={paths[name]} />
    </svg>
  );
}
