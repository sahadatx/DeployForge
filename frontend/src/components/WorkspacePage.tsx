import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Activity, AlertTriangle, ChevronLeft, ChevronRight, RefreshCw, RotateCcw, Search } from "lucide-react";
import { apiGet, apiGetAll, apiGetAllServices, type Workspace } from "../lib/api";
import { usePlatformContext } from "./PlatformContext";
import { PageFrame } from "./PageFrame";

export function useWorkspaceResource<T>(path: string | null, interval = 20_000, pagination: "none" | "array" | "catalog" = "none") {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const resourcePath = useRef(path);

  useEffect(() => {
    if (resourcePath.current !== path) {
      resourcePath.current = path;
      setData(null);
      setError("");
    }
    if (!path) {
      setData(null);
      setLoading(false);
      setError("");
      return;
    }
    let active = true;
    let inFlight = false;
    const controller = new AbortController();
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = pagination === "array"
          ? await apiGetAll(path, controller.signal) as T
          : pagination === "catalog"
            ? await apiGetAllServices(path, controller.signal) as T
            : await apiGet<T>(path, controller.signal);
        if (active) {
          setData(result);
          setError("");
        }
      } catch (caught) {
        if (active && !(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof Error ? caught.message : "The platform data could not be loaded.");
        }
      } finally {
        inFlight = false;
        if (active) setLoading(false);
      }
    };
    setLoading(true);
    void load();
    const timer = interval > 0 ? window.setInterval(() => {
      if (!document.hidden && !document.querySelector("dialog[open]")) void load();
    }, interval) : undefined;
    return () => {
      active = false;
      controller.abort();
      if (timer) window.clearInterval(timer);
    };
  }, [interval, pagination, path, revision]);

  const scopeChanged = resourcePath.current !== path;
  return { data: scopeChanged ? null : data, loading: scopeChanged || loading, error: scopeChanged ? "" : error, refresh: () => setRevision((value) => value + 1), setData };
}

export function useWorkspaceCatalog() {
  const { workspaceId } = usePlatformContext();
  const result = useWorkspaceResource<Workspace[]>("/workspaces", 60_000);
  const workspace = result.data?.find((item) => item.id === workspaceId) || null;
  return { ...result, workspace };
}

export type TableFilterOption = { value: string; label: string };

export type TableFilter<T> = {
  id: string;
  label: string;
  getValue: (item: T) => string | number | boolean | null | undefined;
  options?: readonly TableFilterOption[];
};

type TableToolbarFilter = {
  id: string;
  label: string;
  value: string;
  options: readonly TableFilterOption[];
};

type TableViewOptions<T> = {
  resetKey?: string | number;
  debounceMs?: number;
  filters?: readonly TableFilter<T>[];
};

const TABLE_PAGE_SIZES = [10, 25, 50, 100];

export function useTableView<T>(
  items: T[],
  searchableText: (item: T) => string = (item) => JSON.stringify(item),
  { resetKey, debounceMs = 250, filters: filterDefinitions = [] }: TableViewOptions<T> = {},
) {
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [requestedPage, setRequestedPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const normalizedQuery = appliedQuery.trim().toLocaleLowerCase();

  useEffect(() => {
    if (query === appliedQuery) return;
    const timer = window.setTimeout(() => {
      setAppliedQuery(query);
      setRequestedPage(0);
    }, Math.max(0, debounceMs));
    return () => window.clearTimeout(timer);
  }, [appliedQuery, debounceMs, query]);

  useEffect(() => {
    setQuery("");
    setAppliedQuery("");
    setFilterValues({});
    setRequestedPage(0);
  }, [resetKey]);

  const filteredItems = useMemo(
    () => items.filter((item) => (
      (!normalizedQuery || searchableText(item).toLocaleLowerCase().includes(normalizedQuery))
      && filterDefinitions.every((filter) => !filterValues[filter.id] || String(filter.getValue(item) ?? "") === filterValues[filter.id])
    )),
    [filterDefinitions, filterValues, items, normalizedQuery, searchableText],
  );
  const pageCount = Math.max(1, Math.ceil(filteredItems.length / pageSize));
  const page = Math.min(Math.max(0, requestedPage), pageCount - 1);

  // Refreshes may replace the array without changing its records. Only clamp the
  // page when its rows disappear, so periodic updates preserve the reader's place.
  useEffect(() => setRequestedPage((current) => Math.min(Math.max(0, current), pageCount - 1)), [pageCount]);

  const firstItem = filteredItems.length ? page * pageSize + 1 : 0;
  const lastItem = Math.min((page + 1) * pageSize, filteredItems.length);
  const filters: TableToolbarFilter[] = filterDefinitions.map((filter) => ({
    id: filter.id,
    label: filter.label,
    value: filterValues[filter.id] || "",
    options: filter.options || Array.from(new Set(items.map((item) => String(filter.getValue(item) ?? "")).filter(Boolean)))
      .sort((left, right) => left.localeCompare(right))
      .map((value) => ({ value, label: value.replaceAll("_", " ") })),
  }));

  const resetFilters = () => {
    setQuery("");
    setAppliedQuery("");
    setFilterValues({});
    setRequestedPage(0);
  };

  return {
    query,
    setQuery,
    isFiltering: query !== appliedQuery,
    filters,
    setFilterValue: (id: string, value: string) => {
      if (!filterDefinitions.some((filter) => filter.id === id)) return;
      setFilterValues((current) => ({ ...current, [id]: value }));
      setRequestedPage(0);
    },
    resetFilters,
    hasActiveFilters: Boolean(query || filters.some((filter) => filter.value)),
    page,
    setPage: (value: number) => setRequestedPage(Math.min(Math.max(0, Math.trunc(value) || 0), pageCount - 1)),
    pageSize,
    setPageSize: (value: number) => {
      if (!Number.isFinite(value) || value < 1) return;
      setPageSize(Math.trunc(value));
      setRequestedPage(0);
    },
    filteredItems,
    pageItems: filteredItems.slice(page * pageSize, (page + 1) * pageSize),
    totalItems: items.length,
    firstItem,
    lastItem,
    pageCount,
  };
}

export function TableToolbar({
  query,
  onQueryChange,
  filteredCount,
  totalCount,
  pageSize,
  onPageSizeChange,
  label = "records",
  filters = [],
  onFilterChange,
  onResetFilters,
  isFiltering = false,
  disabled = false,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  filteredCount: number;
  totalCount: number;
  pageSize: number;
  onPageSizeChange: (value: number) => void;
  label?: string;
  filters?: readonly TableToolbarFilter[];
  onFilterChange?: (id: string, value: string) => void;
  onResetFilters?: () => void;
  isFiltering?: boolean;
  disabled?: boolean;
}) {
  const controlId = useId();
  const hasActiveFilters = Boolean(query || filters.some((filter) => filter.value));
  const pageSizes = TABLE_PAGE_SIZES.includes(pageSize) ? TABLE_PAGE_SIZES : [...TABLE_PAGE_SIZES, pageSize].sort((left, right) => left - right);
  const resetFilters = () => {
    if (onResetFilters) {
      onResetFilters();
      return;
    }
    onQueryChange("");
    filters.forEach((filter) => onFilterChange?.(filter.id, ""));
  };

  return <div className="table-toolbar">
    <div className="table-toolbar-controls">
      <label className="table-search" htmlFor={`${controlId}-search`}><Search size={15} aria-hidden="true" /><span className="sr-only">Search {label}</span><input id={`${controlId}-search`} type="search" value={query} disabled={disabled} onChange={(event) => onQueryChange(event.target.value)} placeholder={`Search ${label}…`} autoComplete="off" /></label>
      {filters.map((filter) => <label className="table-filter-field" key={filter.id} htmlFor={`${controlId}-${filter.id}`}><span>{filter.label}</span><select id={`${controlId}-${filter.id}`} aria-label={`Filter ${label} by ${filter.label.toLowerCase()}`} value={filter.value} disabled={disabled || !onFilterChange} onChange={(event) => onFilterChange?.(filter.id, event.target.value)}><option value="">All {filter.label.toLowerCase()}</option>{filter.options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>)}
      <div className="table-toolbar-meta"><span role="status" aria-live="polite">{isFiltering ? "Updating results…" : filteredCount === totalCount ? `${totalCount} ${label}` : `${filteredCount} of ${totalCount} ${label}`}</span><label htmlFor={`${controlId}-size`}><span>Rows</span><select id={`${controlId}-size`} aria-label={`Rows per page for ${label}`} value={pageSize} disabled={disabled} onChange={(event) => onPageSizeChange(Number(event.target.value))}>{pageSizes.map((size) => <option key={size} value={size}>{size}</option>)}</select></label></div>
    </div>
    <div className="table-filter-footer"><button type="button" className="table-reset-filters" disabled={disabled || !hasActiveFilters} onClick={resetFilters} aria-label={`Reset filters for ${label}`}><RotateCcw size={13} aria-hidden="true" />Reset filters</button></div>
  </div>;
}

export function TablePagination({
  firstItem,
  lastItem,
  totalItems,
  page,
  pageCount,
  onPageChange,
  label = "records",
  disabled = false,
}: {
  firstItem: number;
  lastItem: number;
  totalItems: number;
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  label?: string;
  disabled?: boolean;
}) {
  const controlId = useId();
  const safePageCount = Math.max(1, Math.trunc(pageCount) || 1);
  const safePage = Math.min(Math.max(0, Math.trunc(page) || 0), safePageCount - 1);
  const safeTotal = Math.max(0, totalItems);
  const rangeStart = safeTotal ? Math.min(Math.max(1, firstItem), safeTotal) : 0;
  const rangeEnd = safeTotal ? Math.min(Math.max(rangeStart, lastItem), safeTotal) : 0;
  return <nav className="table-pagination" aria-label={`Pagination for ${label}`} aria-busy={disabled || undefined}>
    <span role="status" aria-live="polite">Showing <strong>{rangeStart}–{rangeEnd}</strong> of <strong>{safeTotal}</strong><span className="sr-only"> {label}</span></span>
    <div className="table-pagination-controls">
      <button type="button" aria-label={`Previous page of ${label}`} disabled={disabled || safePage === 0} onClick={() => onPageChange(safePage - 1)}><ChevronLeft size={14} aria-hidden="true" /> Previous</button>
      <label className="table-page-select" htmlFor={`${controlId}-page`}><span>Page</span><select id={`${controlId}-page`} aria-label={`Current page of ${label}`} value={safePage} disabled={disabled || safePageCount <= 1} onChange={(event) => onPageChange(Number(event.target.value))}>{Array.from({ length: safePageCount }, (_, index) => <option key={index} value={index}>{index + 1}</option>)}</select><span>of {safePageCount}</span></label>
      <button type="button" aria-label={`Next page of ${label}`} disabled={disabled || safePage + 1 >= safePageCount} onClick={() => onPageChange(safePage + 1)}>Next <ChevronRight size={14} aria-hidden="true" /></button>
    </div>
  </nav>;
}

export function WorkspacePage({
  eyebrow,
  title,
  description,
  actions,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const { workspaceId } = usePlatformContext();
  const { workspace, loading, error } = useWorkspaceCatalog();
  return (
    <PageFrame eyebrow={eyebrow} title={title} description={description}>
      <div className="operational-toolbar">
        <span className="context-chip"><span className="context-chip-dot" />{workspace?.name || (workspaceId ? "Selected workspace" : "No workspace selected")}</span>
        <div className="operational-toolbar-actions">{actions}<span className="live-indicator"><span className="status-dot" /> Live data</span></div>
      </div>
      {!workspaceId && <div className="workspace-empty"><Activity size={17} /><span><strong>Select a workspace</strong><small>Choose a workspace from the sidebar to view its platform data.</small></span></div>}
      {workspaceId && error && <div className="settings-notice is-error resource-error" role="alert"><AlertTriangle size={14} />{error}{loading && <span> · Refreshing</span>}</div>}
      {children}
    </PageFrame>
  );
}

export function RefreshButton({ onClick, busy = false }: { onClick: () => void; busy?: boolean }) {
  return <button className="catalog-secondary-button" onClick={onClick} disabled={busy}><RefreshCw size={13} className={busy ? "spin" : ""} /> Refresh</button>;
}

export function ResourceErrors({ resources }: { resources: Array<{ label: string; error: string }> }) {
  const failures = resources.filter((resource) => resource.error);
  if (!failures.length) return null;
  return <div className="resource-error-list">{failures.map((resource) => <div className="settings-notice is-error resource-error" role="alert" key={resource.label}><AlertTriangle size={14} /><span><strong>{resource.label}:</strong> {resource.error}</span></div>)}</div>;
}

export function ResourceStatus({ value }: { value: string | null | undefined }) {
  const status = (value || "unknown").toLowerCase();
  const tone = ["succeeded", "healthy", "approved", "resolved", "closed", "synced", "available"].includes(status)
    ? "success"
    : ["failed", "error", "breaching", "blocked", "rejected", "degraded", "firing"].includes(status)
      ? "danger"
      : ["open", "investigating", "mitigated", "running", "queued", "deploying", "building", "validating", "pending", "acknowledged", "planned", "drift_detected"].includes(status) || status.startsWith("awaiting_")
        ? "warning"
        : "neutral";
  return <span className={`resource-status ${tone}`}><span className="status-dot" />{status.replaceAll("_", " ")}</span>;
}

export function LoadingOrEmpty({ loading, empty, label = "No records yet" }: { loading: boolean; empty: boolean; label?: string }) {
  if (loading) return <div className="resource-loading"><span className="spinner" />Loading live data…</div>;
  if (empty) return <div className="resource-empty"><div className="empty-state-icon"><Activity size={17} /></div><strong>{label}</strong><p>New records will appear here when this workspace starts using the corresponding workflow.</p></div>;
  return null;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const preference = localStorage.getItem("deployforge.timezone") || "system";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", ...(preference === "system" ? {} : { timeZone: preference }) }).format(date);
}
