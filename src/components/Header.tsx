import { useEffect, useState } from "react";

export function Header() {
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const update = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);

  const toggleFullscreen = async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  };

  return (
    <header className="bg-primary shadow-sm">
      <div className="px-6 py-4 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-white">
            Calidad de producción
          </h1>
          <p className="mt-1 text-sm text-white/75">
            Consulta histórica de inspección de botellas
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white">
            LOCAL
          </span>
          <button
            type="button"
            onClick={() => void toggleFullscreen()}
            className="p-2 rounded-lg bg-white/20 hover:bg-white/30 text-white"
            aria-label={
              isFullscreen ? "Salir de pantalla completa" : "Pantalla completa"
            }
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" />
            </svg>
          </button>
          <div className="h-14 min-w-28 bg-white p-1.5 flex items-center justify-center">
            <img src="/logo.png" alt="Logo Vidrala" className="h-12 block" />
          </div>
        </div>
      </div>
    </header>
  );
}
