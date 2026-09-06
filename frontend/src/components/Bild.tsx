import { useState, type ImgHTMLAttributes, type ReactNode } from "react";

/** Ein defektes Archivbild darf weder ein Browser-Fehlersymbol noch Layoutsprünge erzeugen. */
export function Bild({ src, children, onError, ...attribute }: Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  src: string | null | undefined;
  children: ReactNode;
}) {
  const [defekteQuelle, setDefekteQuelle] = useState<string | null>(null);
  if (!src || src === defekteQuelle) return <>{children}</>;
  return <img {...attribute} src={src} decoding="async" onError={(event) => {
    setDefekteQuelle(src);
    onError?.(event);
  }} />;
}
