export type CarrierClass = "low_cost" | "full_service" | "hybrid" | "unknown";
export type ScanDepth = "quick" | "standard" | "deep";

export interface Leg {
  origin: string;
  destination: string;
  depart_date: string;
  price: string;
  stops: number;
  airline: string | null;
  airline_name: string | null;
  carrier_class: CarrierClass;
  alliance: string | null;
  flight_number: string | null;
  departure_at: string | null;
  observed_at: string;
}

export interface TripOption {
  kind: "one_way" | "round_trip" | "combined_one_ways";
  depart_date: string;
  return_date: string | null;
  nights: number | null;
  total_price: string;
  currency: string;
  max_stops: number;
  outbound: Leg;
  inbound: Leg | null;
  booking_link: string;
  observed_at: string;
}

export interface CalendarCell {
  depart_date: string;
  price: string;
  stops: number;
  airline: string | null;
  airline_name: string | null;
  return_date: string | null;
}

export interface SearchResponse {
  origin: string;
  destination: string;
  currency: string;
  depth: ScanDepth;
  provider_calls: number;
  cheapest: TripOption | null;
  results: TripOption[];
  calendar: CalendarCell[];
  total_before_filters: number;
  filtered_out: Record<string, number>;
  needs_deep_scan: boolean;
  demo_mode: boolean;
  warnings: string[];
}

export interface DateRangeInput {
  start: string;
  end: string;
}

export interface SearchRequest {
  origin: string;
  destination: string;
  outbound: DateRangeInput;
  inbound?: DateRangeInput | null;
  min_nights?: number | null;
  max_nights?: number | null;
  max_stops?: number | null;
  include_airlines?: string[] | null;
  exclude_airlines?: string[];
  carrier_classes?: CarrierClass[] | null;
  alliances?: string[] | null;
  max_price?: number | null;
  depth?: ScanDepth;
  currency?: string;
  passengers?: number;
  limit?: number;
}

export interface Carrier {
  code: string;
  name: string;
  class: CarrierClass;
  alliance: string | null;
  country: string | null;
}

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });

  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    throw new Error(detail ?? `Request failed (${response.status})`);
  }

  return response.json() as Promise<T>;
}

export function search(body: SearchRequest): Promise<SearchResponse> {
  return request<SearchResponse>("/api/search", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listCarriers(): Promise<{ carriers: Carrier[] }> {
  return request("/api/carriers");
}

export const REJECTION_LABELS: Record<string, string> = {
  stops: "too many stops",
  price: "over budget",
  stale: "price too old",
  airline_excluded: "excluded airline",
  airline_not_in_list: "different airline",
  carrier_class: "wrong carrier type",
  alliance: "outside alliance",
  unknown_airline: "airline not identified",
};

export function formatMoney(value: string | number, currency: string): string {
  const amount = typeof value === "string" ? Number(value) : value;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: currency.toUpperCase(),
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${currency.toUpperCase()} ${Math.round(amount).toLocaleString()}`;
  }
}

export function relativeAge(iso: string): string {
  const hours = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
