import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { matchPath, Route, Routes, useLocation, useNavigate, type Location } from "react-router-dom";
import { SeitenFehlergrenze } from "./SeitenFehlergrenze";
import { Skelettgitter } from "./ui";

const Wiedergabeseite = lazy(() => import("../pages/Wiedergabe").then((m) => ({ default: m.Wiedergabeseite })));

/** Die Videoroute bleibt beim Stöbern montiert: ein Videoelement, eine Lease. */
export function Weitersehen({ children }: { children: ReactNode }) {
  const ort = useLocation();
  const navigate = useNavigate();
  const aufVideo = !!matchPath("/video/:videoId", ort.pathname);
  const [letztesVideo, setLetztesVideo] = useState<Location | null>(null);
  const zuletztGestoebert = useRef("/");
  const videoOrt = aufVideo ? ort : letztesVideo;

  useEffect(() => {
    if (aufVideo) setLetztesVideo(ort);
    else zuletztGestoebert.current = ort.pathname + ort.search + ort.hash;
  }, [ort, aufVideo]);

  return <>
    {children}
    {videoOrt ? <SeitenFehlergrenze key={`weitersehen:${videoOrt.pathname}`}>
      <Suspense fallback={aufVideo ? <Skelettgitter anzahl={3} /> : null}>
        <Routes location={videoOrt}>
          <Route path="/video/:videoId" element={<Wiedergabeseite
            minimiert={!aufVideo}
            aufMinimieren={() => navigate(zuletztGestoebert.current, { state: { weitersehen: true } })}
            aufVergroessern={() => navigate(videoOrt.pathname + videoOrt.search + videoOrt.hash, { state: { weitersehen: true } })}
            aufSchliessen={() => setLetztesVideo(null)}
          />} />
        </Routes>
      </Suspense>
    </SeitenFehlergrenze> : null}
  </>;
}
