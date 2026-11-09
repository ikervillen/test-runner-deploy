import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { Header } from "@/components/Header";
import { ProductionQualityScorePanel } from "@/components/quality-score";
import { api } from "@/services/api";
import type {
  Catalog,
  CustomerQualityLabel,
  CustomerQualityResponse,
  FilterSelection,
  ProductionCatalog,
  ProductionQualityScoreResponse,
  ScoreConfigOverride,
  TrendQuery,
} from "@/types/api";

const EMPTY_FILTERS: FilterSelection = {
  defect_codes: [],
  cavities: [],
  colors: [],
  sections: [],
  machine_codes: [],
};

const SCORE_SEVERITIES = [
  "Critico",
  "Grave",
  "Mayor",
  "Menor",
  "Leve",
] as const;
const PRODUCTION_QUALITY_CACHE_LIMIT = 3;

type ScoreParameterState = {
  aql: Record<(typeof SCORE_SEVERITIES)[number], number>;
  weights: {
    mia_defect: number;
    categories: Record<(typeof SCORE_SEVERITIES)[number], number>;
    dar: number;
    zc: number;
  };
  dar: { score_5: number; score_10: number };
  zcTarget: number;
  status: { good_max: number; observed_max: number; bad_max: number };
};

type ProductionQualityCacheEntry = {
  signature: string;
  score: ProductionQualityScoreResponse;
  customerQuality: CustomerQualityResponse | null;
};

const DEFAULT_SCORE_PARAMETERS: ScoreParameterState = {
  aql: { Critico: 0.05, Grave: 0.1, Mayor: 0.4, Menor: 1, Leve: 4 },
  weights: {
    mia_defect: 1,
    categories: { Critico: 10, Grave: 5, Mayor: 3, Menor: 2, Leve: 2 },
    dar: 8,
    zc: 2,
  },
  dar: { score_5: 0.1, score_10: 0.4 },
  zcTarget: 95,
  status: { good_max: 40, observed_max: 80, bad_max: 120 },
};

export default function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selectedLines, setSelectedLines] = useState<string[]>([]);
  const [selectedProductions, setSelectedProductions] = useState<string[]>([]);
  const [selectedBranches, setSelectedBranches] = useState<string[]>([]);
  const [parameters, setParameters] = useState<ScoreParameterState>(
    DEFAULT_SCORE_PARAMETERS,
  );
  const [scoreResult, setScoreResult] =
    useState<ProductionQualityScoreResponse | null>(null);
  const [customerQuality, setCustomerQuality] =
    useState<CustomerQualityResponse | null>(null);
  const [lastSignature, setLastSignature] = useState<string | null>(null);
  const [cache, setCache] = useState<ProductionQualityCacheEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);

  useEffect(() => {
    void api
      .getCatalog()
      .then((response) => {
        setCatalog(response);
        const firstLine = response.lines[0];
        const firstProduction = response.productions.find(
          (row) => row.line === firstLine,
        );
        if (firstLine) setSelectedLines([firstLine]);
        if (firstProduction) {
          setSelectedProductions([firstProduction.id]);
          setSelectedBranches(
            firstProduction.branches.map((branch) => branch.id),
          );
        }
      })
      .catch((caught: unknown) => setError(formatError(caught)));
  }, []);

  const visibleProductions = useMemo(
    () =>
      sortProductions(
        catalog?.productions.filter((row) =>
          selectedLines.includes(row.line),
        ) ?? [],
      ),
    [catalog, selectedLines],
  );
  const selection = useMemo<TrendQuery>(
    () => ({
      production_ids: orderProductionIds(
        selectedProductions,
        catalog?.productions ?? [],
      ),
      branches: orderBranches(
        selectedBranches,
        selectedProductions,
        catalog?.productions ?? [],
      ),
      metric: "rejected_count",
      window: "10m",
      filters: EMPTY_FILTERS,
    }),
    [catalog, selectedBranches, selectedProductions],
  );
  const scoreOverride = useMemo(
    () =>
      JSON.stringify(parameters) === JSON.stringify(DEFAULT_SCORE_PARAMETERS)
        ? undefined
        : scoreParametersToOverride(parameters),
    [parameters],
  );
  useEffect(() => {
    if (catalog?.source !== "gold") return;
    let active = true;
    const timer = setInterval(() => {
      void api
        .getCatalog()
        .then((next) => {
          if (active) {
            setCatalog(next);
            setRefreshWarning(null);
          }
        })
        .catch(() => {
          if (active)
            setRefreshWarning(
              "No se ha podido comprobar una nueva publicación. Se conserva la última vista disponible.",
            );
        });
    }, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [catalog?.source]);
  const signature = JSON.stringify({
    selection,
    scoreOverride,
    publication: catalog?.publication_id,
  });
  const isStale = scoreResult !== null && lastSignature !== signature;

  const toggleLine = (line: string) => {
    const nextLines = toggleValue(selectedLines, line);
    const validProductions = new Set(
      (catalog?.productions ?? [])
        .filter((row) => nextLines.includes(row.line))
        .map((row) => row.id),
    );
    const nextProductions = selectedProductions.filter((id) =>
      validProductions.has(id),
    );
    const validBranches = new Set(
      (catalog?.productions ?? [])
        .filter((row) => nextProductions.includes(row.id))
        .flatMap((row) => row.branches.map((branch) => branch.id)),
    );
    setSelectedLines(nextLines);
    setSelectedProductions(nextProductions);
    setSelectedBranches((current) =>
      current.filter((branch) => validBranches.has(branch)),
    );
  };

  const toggleProduction = (productionId: string) => {
    const production = catalog?.productions.find(
      (row) => row.id === productionId,
    );
    if (!production) return;
    const isSelected = selectedProductions.includes(productionId);
    const nextProductions = toggleValue(selectedProductions, productionId);
    setSelectedProductions(nextProductions);
    setSelectedBranches((current) => {
      if (!isSelected) {
        return [
          ...new Set([
            ...current,
            ...production.branches.map((branch) => branch.id),
          ]),
        ];
      }
      const allowed = new Set(
        (catalog?.productions ?? [])
          .filter((row) => nextProductions.includes(row.id))
          .flatMap((row) => row.branches.map((branch) => branch.id)),
      );
      return current.filter((branch) => allowed.has(branch));
    });
  };

  const selectAllVisible = () => {
    setSelectedProductions(visibleProductions.map((row) => row.id));
    setSelectedBranches(
      visibleProductions.flatMap((row) =>
        row.branches.map((branch) => branch.id),
      ),
    );
  };

  const calculate = async () => {
    if (
      selection.production_ids.length === 0 ||
      selection.branches.length === 0
    )
      return;
    const cached = cache.find((entry) => entry.signature === signature);
    if (cached && catalog?.source !== "gold") {
      setScoreResult(cached.score);
      setCustomerQuality(cached.customerQuality);
      setLastSignature(signature);
      setCache((current) => promoteCacheEntry(current, cached));
      setError(null);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const [score, quality] = await Promise.allSettled([
        api.queryProductionQualityScore({
          selection,
          score_config_override: scoreOverride,
        }),
        api.queryCustomerQuality({ selection }),
      ]);
      if (score.status === "rejected") throw score.reason;
      const qualityResult =
        quality.status === "fulfilled" ? quality.value : null;
      setScoreResult(score.value);
      setCustomerQuality(qualityResult);
      setLastSignature(signature);
      setCache((current) =>
        promoteCacheEntry(current, {
          signature,
          score: score.value,
          customerQuality: qualityResult,
        }),
      );
    } catch (caught: unknown) {
      setError(formatError(caught));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="space-y-4 px-6 py-4">
        {refreshWarning && <StatusPanel text={refreshWarning} />}
        {!catalog && !error ? (
          <StatusPanel text="Cargando catalogo de producciones..." />
        ) : catalog ? (
          <>
            <QualityFilters
              catalog={catalog}
              isLoading={isLoading}
              parameters={parameters}
              selectedBranches={selectedBranches}
              selectedLines={selectedLines}
              selectedProductions={selectedProductions}
              visibleProductions={visibleProductions}
              onCalculate={() => void calculate()}
              onClear={() => {
                setSelectedProductions([]);
                setSelectedBranches([]);
              }}
              onParametersChange={setParameters}
              onResetParameters={() => setParameters(DEFAULT_SCORE_PARAMETERS)}
              onSelectAll={selectAllVisible}
              onToggleBranch={(branch) =>
                setSelectedBranches((current) => toggleValue(current, branch))
              }
              onToggleLine={toggleLine}
              onToggleProduction={toggleProduction}
            />
            <ProductionQualityScorePanel
              error={error}
              isLoading={isLoading}
              isStale={isStale}
              result={scoreResult}
              selection={selection}
              productions={visibleProductions}
              customerQualityLabels={
                Object.fromEntries(
                  (customerQuality?.rows ?? []).map((row) => [
                    row.production_id,
                    row.label,
                  ]),
                ) as Record<string, CustomerQualityLabel | "unknown">
              }
              onCalculate={() => void calculate()}
            />
          </>
        ) : (
          <ErrorPanel message={error ?? "No se pudo cargar el catalogo."} />
        )}
      </main>
    </div>
  );
}

function QualityFilters({
  catalog,
  isLoading,
  parameters,
  selectedBranches,
  selectedLines,
  selectedProductions,
  visibleProductions,
  onCalculate,
  onClear,
  onParametersChange,
  onResetParameters,
  onSelectAll,
  onToggleBranch,
  onToggleLine,
  onToggleProduction,
}: {
  catalog: Catalog;
  isLoading: boolean;
  parameters: ScoreParameterState;
  selectedBranches: string[];
  selectedLines: string[];
  selectedProductions: string[];
  visibleProductions: ProductionCatalog[];
  onCalculate: () => void;
  onClear: () => void;
  onParametersChange: (value: ScoreParameterState) => void;
  onResetParameters: () => void;
  onSelectAll: () => void;
  onToggleBranch: (id: string) => void;
  onToggleLine: (line: string) => void;
  onToggleProduction: (id: string) => void;
}) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <p className="text-sm font-semibold text-gray-700">Lineas</p>
          <div className="mt-2 flex gap-2">
            {catalog.lines.map((line) => (
              <FilterChip
                key={line}
                label={line}
                selected={selectedLines.includes(line)}
                onClick={() => onToggleLine(line)}
              />
            ))}
          </div>
        </div>
        <div className="text-sm text-gray-600">
          <p className="font-semibold text-gray-700">Modelo de calidad</p>
          <p className="mt-2">MIA, avisos/rechazos, DAR y rendimiento ZC</p>
        </div>
        <button
          type="button"
          disabled={isLoading || selectedBranches.length === 0}
          onClick={onCalculate}
          className="h-10 rounded-md bg-primary px-5 text-sm font-semibold text-white hover:bg-primary-light disabled:bg-gray-300"
        >
          {isLoading ? "Calculando..." : "Calcular calidad"}
        </button>
      </div>
      <ParametersPanel
        parameters={parameters}
        onChange={onParametersChange}
        onReset={onResetParameters}
      />
      <div className="mt-4 border-t border-gray-100 pt-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-gray-800">
            Producciones{" "}
            <span className="font-normal text-gray-400">
              {selectedProductions.length} seleccionadas
            </span>
          </h2>
          <div className="flex gap-2">
            <SmallButton label="Todas" onClick={onSelectAll} />
            <SmallButton label="Limpiar" onClick={onClear} />
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {visibleProductions.map((production) => (
            <FilterChip
              key={production.id}
              label={`${production.id} - ${formatDay(production.start_day)}-${formatDay(production.end_day)}`}
              selected={selectedProductions.includes(production.id)}
              onClick={() => onToggleProduction(production.id)}
            />
          ))}
        </div>
      </div>
      {selectedProductions.length > 0 && (
        <div className="mt-4 border-t border-gray-100 pt-4">
          <h2 className="text-sm font-semibold text-gray-800">Ramales</h2>
          <div className="mt-2 grid gap-2 xl:grid-cols-2">
            {catalog.productions
              .filter((row) => selectedProductions.includes(row.id))
              .map((production) => (
                <div
                  key={production.id}
                  className="rounded-md bg-gray-50 px-3 py-2"
                >
                  <p className="text-xs font-semibold text-gray-500">
                    {production.id}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {production.branches.map((branch) => (
                      <FilterChip
                        key={branch.id}
                        label={branch.id}
                        selected={selectedBranches.includes(branch.id)}
                        onClick={() => onToggleBranch(branch.id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}
    </section>
  );
}

function ParametersPanel({
  parameters,
  onChange,
  onReset,
}: {
  parameters: ScoreParameterState;
  onChange: (value: ScoreParameterState) => void;
  onReset: () => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const updateAql = (
    severity: (typeof SCORE_SEVERITIES)[number],
    value: number,
  ) =>
    onChange({ ...parameters, aql: { ...parameters.aql, [severity]: value } });
  const updateWeight = (
    severity: (typeof SCORE_SEVERITIES)[number],
    value: number,
  ) =>
    onChange({
      ...parameters,
      weights: {
        ...parameters.weights,
        categories: { ...parameters.weights.categories, [severity]: value },
      },
    });
  return (
    <details
      className="mt-4 overflow-hidden rounded-lg border border-emerald-100"
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary className="flex cursor-pointer items-center justify-between bg-white px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-primary">
            Parametros del score
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Configuracion temporal de la consulta.
          </p>
        </div>
        <button
          type="button"
          onClick={(event) => {
            event.preventDefault();
            onReset();
          }}
          className="rounded-md border border-emerald-200 px-3 py-1.5 text-xs font-semibold text-emerald-700"
        >
          Restaurar base
        </button>
      </summary>
      <div className="grid gap-3 border-t border-emerald-100 bg-slate-50 p-3 xl:grid-cols-3">
        <ParameterCard title="AQL por categoria">
          <div className="grid grid-cols-2 gap-2">
            {SCORE_SEVERITIES.map((severity) => (
              <NumberField
                key={severity}
                label={severity}
                suffix="%"
                step={0.01}
                value={parameters.aql[severity]}
                onChange={(value) => updateAql(severity, value)}
              />
            ))}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <NumberField
              label="Limite inferior DAR"
              suffix="%"
              step={0.01}
              value={parameters.dar.score_5}
              onChange={(value) =>
                onChange({
                  ...parameters,
                  dar: { ...parameters.dar, score_5: value },
                })
              }
            />
            <NumberField
              label="Limite superior DAR"
              suffix="%"
              step={0.01}
              value={parameters.dar.score_10}
              onChange={(value) =>
                onChange({
                  ...parameters,
                  dar: { ...parameters.dar, score_10: value },
                })
              }
            />
          </div>
        </ParameterCard>
        <ParameterCard title="Pesos del score">
          <p className="mb-2 text-xs font-semibold text-primary">
            MIA defectos
          </p>
          <NumberField
            label="Peso por defecto MIA"
            step={1}
            value={parameters.weights.mia_defect}
            onChange={(value) =>
              onChange({
                ...parameters,
                weights: { ...parameters.weights, mia_defect: value },
              })
            }
          />
          <p className="mb-2 mt-3 text-xs font-semibold text-primary">
            A/R IFA
          </p>
          <div className="grid grid-cols-2 gap-2">
            {SCORE_SEVERITIES.map((severity) => (
              <NumberField
                key={severity}
                label={severity}
                step={1}
                value={parameters.weights.categories[severity]}
                onChange={(value) => updateWeight(severity, value)}
              />
            ))}
            <NumberField
              label="DAR"
              step={1}
              value={parameters.weights.dar}
              onChange={(value) =>
                onChange({
                  ...parameters,
                  weights: { ...parameters.weights, dar: value },
                })
              }
            />
          </div>
          <p className="mb-2 mt-3 text-xs font-semibold text-primary">
            Rendimiento ZC
          </p>
          <NumberField
            label="Peso ZC"
            step={1}
            value={parameters.weights.zc}
            onChange={(value) =>
              onChange({
                ...parameters,
                weights: { ...parameters.weights, zc: value },
              })
            }
          />
        </ParameterCard>
        <ParameterCard title="Rendimiento y estado">
          <NumberField
            label="Objetivo ZC"
            suffix="%"
            step={0.1}
            value={parameters.zcTarget}
            onChange={(value) => onChange({ ...parameters, zcTarget: value })}
          />
          <div className="mt-3 grid grid-cols-3 gap-2">
            <NumberField
              label="Bueno hasta"
              step={1}
              value={parameters.status.good_max}
              onChange={(value) =>
                onChange({
                  ...parameters,
                  status: { ...parameters.status, good_max: value },
                })
              }
            />
            <NumberField
              label="Observado hasta"
              step={1}
              value={parameters.status.observed_max}
              onChange={(value) =>
                onChange({
                  ...parameters,
                  status: { ...parameters.status, observed_max: value },
                })
              }
            />
            <NumberField
              label="Malo hasta"
              step={1}
              value={parameters.status.bad_max}
              onChange={(value) =>
                onChange({
                  ...parameters,
                  status: { ...parameters.status, bad_max: value },
                })
              }
            />
          </div>
        </ParameterCard>
      </div>
    </details>
  );
}

function ParameterCard({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <article className="rounded-md border border-gray-200 bg-white p-3">
      <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-primary">
        {title}
      </h3>
      {children}
    </article>
  );
}
function NumberField({
  label,
  value,
  step,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  step: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="min-w-0 text-[11px] font-semibold text-gray-700">
      {label}
      <div className="mt-1 flex rounded-md border border-gray-200 bg-white">
        <input
          type="number"
          value={Number.isFinite(value) ? value : 0}
          step={step}
          onChange={(event) =>
            onChange(Number.parseFloat(event.target.value) || 0)
          }
          className="min-w-0 flex-1 rounded-l-md px-2 py-1 text-right text-xs outline-none"
        />
        {suffix && (
          <span className="flex items-center border-l border-gray-200 bg-gray-50 px-2 text-xs text-gray-500">
            {suffix}
          </span>
        )}
      </div>
    </label>
  );
}
function FilterChip({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs font-semibold ${selected ? "border-primary bg-primary/10 text-primary" : "border-gray-300 text-gray-600 hover:border-gray-400"}`}
    >
      {label}
    </button>
  );
}
function SmallButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-gray-200 px-3 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-50"
    >
      {label}
    </button>
  );
}
function StatusPanel({ text }: { text: string }) {
  return (
    <section className="flex min-h-64 items-center justify-center rounded-lg border border-gray-200 bg-white">
      <p className="text-sm text-gray-500">{text}</p>
    </section>
  );
}
function ErrorPanel({ message }: { message: string }) {
  return (
    <section className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </section>
  );
}
function toggleValue(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}
function sortProductions(
  productions: ProductionCatalog[],
): ProductionCatalog[] {
  return [...productions].sort(
    (left, right) =>
      left.start_day.localeCompare(right.start_day) ||
      left.end_day.localeCompare(right.end_day) ||
      left.id.localeCompare(right.id),
  );
}
function orderProductionIds(
  ids: string[],
  productions: ProductionCatalog[],
): string[] {
  const positions = new Map(
    sortProductions(productions).map((row, index) => [row.id, index]),
  );
  return [...ids].sort(
    (left, right) =>
      (positions.get(left) ?? Number.MAX_SAFE_INTEGER) -
        (positions.get(right) ?? Number.MAX_SAFE_INTEGER) ||
      left.localeCompare(right),
  );
}
function orderBranches(
  ids: string[],
  productionIds: string[],
  productions: ProductionCatalog[],
): string[] {
  const selected = new Set(productionIds);
  const ordered = sortProductions(productions)
    .filter((row) => selected.has(row.id))
    .flatMap((row) => row.branches.map((branch) => branch.id));
  const requested = new Set(ids);
  return ordered.filter((id) => requested.has(id));
}
function formatDay(value: string): string {
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}
function formatError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Se ha producido un error inesperado.";
}
function scoreParametersToOverride(
  parameters: ScoreParameterState,
): ScoreConfigOverride {
  return {
    aql: { category_limits_percent: { ...parameters.aql } },
    weights: {
      mia_defect: parameters.weights.mia_defect,
      categories: { ...parameters.weights.categories },
      dar: parameters.weights.dar,
      zc: parameters.weights.zc,
    },
    dar: {
      progressive_thresholds_percent: {
        score_5: parameters.dar.score_5,
        score_10: parameters.dar.score_10,
      },
    },
    zc: { target_percent: { default: parameters.zcTarget } },
    score: { interpretation: { ...parameters.status } },
  };
}
function promoteCacheEntry(
  entries: ProductionQualityCacheEntry[],
  entry: ProductionQualityCacheEntry,
): ProductionQualityCacheEntry[] {
  return [
    entry,
    ...entries.filter((item) => item.signature !== entry.signature),
  ].slice(0, PRODUCTION_QUALITY_CACHE_LIMIT);
}
