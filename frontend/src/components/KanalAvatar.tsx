import { createContext, useContext } from "react";
import type { KanalKurz } from "../lib/api";
import { thumbUrl } from "../lib/api";
import { Bild } from "./Bild";

export const KanalKontext = createContext<KanalKurz[]>([]);

/** Lokale Kanalbilder verwenden; noch nicht erfasste Bilder zeigen Initialen. */
export function KanalAvatar({ kanalId, name, avatar, className = "avatar" }: {
  kanalId: string | null;
  name: string | null;
  avatar?: string | null;
  className?: string;
}) {
  const kanaele = useContext(KanalKontext);
  const kanal = kanaele.find((k) => k.id === kanalId);
  const bild = thumbUrl(avatar ?? kanal?.avatar ?? null);
  const initialen = (Array.from((name || kanal?.name || "").trim())[0] || "?").toLocaleUpperCase("de");
  return <Bild className={className} src={bild} alt="" loading="lazy">
    <span className={`${className} avatar-initialen`} aria-hidden="true">{initialen}</span>
  </Bild>;
}
