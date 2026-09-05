import { createContext, useContext } from "react";
import type { KanalKurz } from "../lib/api";
import { thumbUrl } from "../lib/api";

export const KanalKontext = createContext<KanalKurz[]>([]);

/** Lokale Kanalbilder verwenden; noch nicht erfasste Bilder zeigen Initialen. */
export function KanalAvatar({ kanalId, name, className = "avatar" }: {
  kanalId: string | null;
  name: string | null;
  className?: string;
}) {
  const kanaele = useContext(KanalKontext);
  const kanal = kanaele.find((k) => k.id === kanalId);
  const bild = thumbUrl(kanal?.avatar ?? null);
  const initialen = (name ?? kanal?.name ?? "?").trim().slice(0, 1).toLocaleUpperCase("de");
  return bild ? <img className={className} src={bild} alt="" loading="lazy" /> : (
    <span className={`${className} avatar-initialen`} aria-hidden="true">{initialen}</span>
  );
}
