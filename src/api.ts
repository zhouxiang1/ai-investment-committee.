import type { CompanyUniverseResponse, CompanyUniverseSummary, Expert, Health, Market, ReportState, ReportSummary, V2RatingsResponse } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(url), {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...init
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // keep status text
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function apiUrl(url: string) {
  if (url.startsWith("/") && typeof window !== "undefined") {
    return `${window.location.origin}${url}`;
  }
  return url;
}

export const api = {
  health: () => request<Health>("/api/health"),
  searchCompanies: (q: string, market: Market) =>
    request<{ results: any[] }>(`/api/companies/search?q=${encodeURIComponent(q)}&market=${market}`),
  companies: (q = "", market: Market = "AUTO", limit = 80, offset = 0) =>
    request<CompanyUniverseResponse>(`/api/companies?q=${encodeURIComponent(q)}&market=${market}&limit=${limit}&offset=${offset}`),
  companySummary: () => request<CompanyUniverseSummary>("/api/companies/summary"),
  syncCompanies: (markets: Array<Exclude<Market, "AUTO">> = ["A", "HK", "US"]) =>
    request<{ sync: CompanyUniverseSummary["sync"]; summary: CompanyUniverseSummary }>("/api/companies/sync", {
      method: "POST",
      body: JSON.stringify({ markets })
    }),
  v2Ratings: (market: Market = "AUTO", q = "") =>
    request<V2RatingsResponse>(`/api/v2/ratings?market=${market}&q=${encodeURIComponent(q)}`),
  rebuildV2Ratings: (market: Market = "AUTO", q = "") =>
    request<V2RatingsResponse>("/api/v2/ratings/rebuild", {
      method: "POST",
      body: JSON.stringify({ market, q })
    }),
  createCommittee: (ticker: string, market: Market, analysisMode = "deep") =>
    request<any>("/api/committee/create", {
      method: "POST",
      body: JSON.stringify({ ticker, market, analysis_mode: analysisMode })
    }),
  selectExperts: (reportId: string, expertIds: string[]) =>
    request<any>(`/api/committee/${reportId}/select-experts`, {
      method: "POST",
      body: JSON.stringify({ expert_ids: expertIds })
    }),
  selectChairman: (reportId: string, chairmanId?: string, autoSelected = true) =>
    request<any>(`/api/committee/${reportId}/select-chairman`, {
      method: "POST",
      body: JSON.stringify({ chairman_id: chairmanId, auto_selected: autoSelected })
    }),
  collectData: (reportId: string) =>
    request<any>(`/api/committee/${reportId}/collect-data`, {
      method: "POST"
    }),
  autorunCommittee: (reportId: string) =>
    request<any>(`/api/committee/${reportId}/autorun`, {
      method: "POST"
    }),
  runRound: (reportId: string, roundNumber: number) =>
    request<any>(`/api/committee/${reportId}/round/${roundNumber}/run`, {
      method: "POST"
    }),
  status: (reportId: string) => request<ReportState>(`/api/committee/${reportId}/status`),
  reports: () => request<{ reports: ReportSummary[] }>("/api/reports"),
  report: (reportId: string) => request<ReportState>(`/api/reports/${reportId}`),
  exportPdf: (reportId: string) =>
    request<{ pdf_url: string; pdf_path: string }>(`/api/committee/${reportId}/export-pdf`, {
      method: "POST"
    }),
  experts: () => request<{ experts: Expert[] }>("/api/experts"),
  createExpert: (expert: Partial<Expert>) =>
    request<{ expert: Expert }>("/api/experts", {
      method: "POST",
      body: JSON.stringify(expert)
    }),
  updateExpert: (expert: Expert) =>
    request<{ expert: Expert }>(`/api/experts/${expert.id}`, {
      method: "PUT",
      body: JSON.stringify(expert)
    }),
  uploadMaterial: (expertId: string, form: FormData) =>
    request<any>(`/api/experts/${expertId}/materials`, {
      method: "POST",
      body: form
    }),
  distillExpert: (expertId: string) =>
    request<any>(`/api/experts/${expertId}/distill`, {
      method: "POST"
    }),
  researchExpert: (expertId: string, maxSources = 4) =>
    request<any>(`/api/experts/${expertId}/web-research`, {
      method: "POST",
      body: JSON.stringify({ max_sources: maxSources })
    }),
  bulkDistillExperts: () =>
    request<{ distilled_count: number; results: Array<{ expert_id: string; name: string; material_id: string }> }>("/api/experts/bulk-distill", {
      method: "POST"
    }),
  bulkResearchExperts: (maxSources = 3) =>
    request<{ researched_count: number; results: Array<{ expert_id: string; name: string; source_count: number; status: string }> }>("/api/experts/bulk-web-research", {
      method: "POST",
      body: JSON.stringify({ max_sources: maxSources })
    })
};
