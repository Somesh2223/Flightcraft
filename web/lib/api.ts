export type CarrierClass = "low_cost" | "full_service" | "hybrid" | "unknown";
export type ScanDepth = "quick" | "standard" | "deep";

export type BodyType =
  | "widebody"
  | "narrowbody"
  | "regional"
  | "turboprop"
  | "unknown";

export interface Segment {
  carrier: string;
  carrier_name: string | null;
  flight_number: string;
  origin: string;
  destination: string;
  departure_local: string | null;
  arrival_local: string | null;
  duration_minutes: number | null;
  aircraft: string | null;
  aircraft_family: string | null;
  aircraft_body: BodyType;
}

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
  duration_minutes: number | null;
  observed_at: string;
  segments: Segment[];
}

export interface BookingLink {
  provider_name: string;
  provider_type: string | null;
  price: string | null;
  currency: string | null;
  url: string;
}

export type Verdict = "exceptional" | "good" | "typical" | "high" | "unknown";
export type Confidence = "none" | "low" | "medium" | "high";

export interface PriceContext {
  verdict: Verdict;
  confidence: Confidence;
  samples: number;
  distinct_days: number;
  percentile: number | null;
  median: string | null;
  cheapest_seen: string | null;
  note: string;
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
  price_context: PriceContext | null;
  // A live quote names its flights and can be priced to a checkout page.
  // Anything else is a cached estimate: a lead, not an offer.
  is_live_quote: boolean;
  provider_ref: string | null;
  duration_minutes: number | null;
  points_options: Eligibility[];
  applied_offer: OfferApplication | null;
}

export interface Offer {
  id: string;
  label: string;
  sellers: string[];
  percent: number | null;
  max_discount: number | null;
  flat_discount: number | null;
  min_spend: number | null;
  valid_until: string | null;
  promo_code: string | null;
  note?: string | null;
}

export interface OfferApplication {
  offer_id: string;
  label: string;
  discount: string;
  effective_price: string;
  promo_code: string | null;
  days_left: number | null;
  expiring_soon: boolean;
  note: string;
}

export interface LoyaltyProgram {
  code: string;
  name: string;
  label: string;
  airline: string;
  books_alliance: string | null;
  currency_pool: string | null;
  books_award_seats: boolean;
}

export interface Holding {
  program: string;
  balance: number;
}

export interface Wallet {
  holdings: Holding[];
  baseline_per_point?: number | null;
}

export interface Eligibility {
  program: string;
  label: string;
  balance: number;
  note: string;
}

export type PointsVerdict =
  | "strong"
  | "fair"
  | "poor"
  | "worse_than_cash"
  | "not_enough"
  | "unknown";

export interface PointsAssessment {
  program: string;
  label: string;
  points: number;
  cash_component: string;
  balance: number;
  shortfall: number;
  cash_price: string;
  best_cash_alternative: string | null;
  best_cash_date: string | null;
  value_per_point: string;
  value_per_point_flexible: string | null;
  verdict: PointsVerdict;
  note: string;
}

export const POINTS_VERDICT_STYLE: Record<
  PointsVerdict,
  { label: string; className: string }
> = {
  strong: {
    label: "Strong redemption",
    className: "bg-emerald-400/20 text-emerald-200 ring-1 ring-emerald-400/40",
  },
  fair: { label: "Fair redemption", className: "bg-emerald-500/15 text-emerald-300" },
  poor: { label: "Cash is better", className: "bg-amber-500/15 text-amber-300" },
  worse_than_cash: {
    label: "Costs more than cash",
    className: "bg-rose-500/20 text-rose-200 ring-1 ring-rose-400/40",
  },
  not_enough: { label: "Not enough points", className: "bg-white/5 text-muted" },
  unknown: { label: "Value", className: "bg-white/5 text-muted" },
};

export interface SplitTicketSaving {
  saving: string;
  two_one_ways: string;
  round_trip: string;
  depart_date: string;
  return_date: string;
}

export interface CalendarCell {
  depart_date: string;
  price: string;
  stops: number;
  airline: string | null;
  airline_name: string | null;
  return_date: string | null;
  effective_price: string | null;
  offer_label: string | null;
}

export interface MatrixCell {
  depart_date: string;
  return_date: string;
  nights: number;
  price: string;
  effective_price: string;
  stops: number;
  airline: string | null;
  is_live_quote: boolean;
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
  matrix: MatrixCell[];
  total_before_filters: number;
  filtered_out: Record<string, number>;
  needs_deep_scan: boolean;
  demo_mode: boolean;
  live_requests: number;
  live_cache_hits: number;
  split_ticket_saving: SplitTicketSaving | null;
  observations_recorded: number;
  warnings: string[];
}

export const VERDICT_STYLE: Record<Verdict, { label: string; className: string }> = {
  exceptional: {
    label: "Unusually cheap",
    className: "bg-emerald-400/20 text-emerald-200 ring-1 ring-emerald-400/40",
  },
  good: { label: "Good price", className: "bg-emerald-500/15 text-emerald-300" },
  typical: { label: "Typical price", className: "bg-white/5 text-muted" },
  high: { label: "Above usual", className: "bg-amber-500/15 text-amber-300" },
  unknown: { label: "No history yet", className: "bg-white/5 text-muted" },
};

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
  aircraft_families?: string[] | null;
  body_types?: BodyType[] | null;
  retiring_only?: boolean;
  depth?: ScanDepth;
  currency?: string;
  passengers?: number;
  limit?: number;
  wallet?: Wallet | null;
  offers?: Offer[];
}

export interface ResolveDateRequest {
  origin: string;
  destination: string;
  depart_date: string;
  return_date?: string | null;
  max_stops?: number | null;
  include_airlines?: string[] | null;
  currency?: string;
  passengers?: number;
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

/** Where to buy one itinerary, and what each seller actually charges. */
export function bookingLinks(providerRef: string): Promise<{ links: BookingLink[] }> {
  return request("/api/booking-links", {
    method: "POST",
    body: JSON.stringify({ provider_ref: providerRef }),
  });
}

export interface SearchEstimate {
  dates_in_window: number;
  depths: Record<ScanDepth, { cached_calls: number; live_requests: number }>;
}

/** What a search would cost before running it. Free — no provider call. */
export function estimateSearch(body: SearchRequest): Promise<SearchEstimate> {
  return request<SearchEstimate>("/api/search/estimate", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listPrograms(): Promise<{ programs: LoyaltyProgram[] }> {
  return request("/api/loyalty/programs");
}

/** Value an award the traveller is looking at, against the whole cash scan. */
export function assessPoints(body: {
  wallet: Wallet;
  quote: { program: string; points: number; cash_component: number };
  cash_price: number;
  best_cash_alternative?: number | null;
  best_cash_date?: string | null;
}): Promise<PointsAssessment> {
  return request<PointsAssessment>("/api/points/assess", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Turn one estimated date into real, bookable itineraries. Costs one request. */
export function resolveDate(body: ResolveDateRequest): Promise<TripOption[]> {
  return request<TripOption[]>("/api/resolve-date", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
  aircraft_body: "wrong aircraft type",
  aircraft_family: "different aircraft",
  aircraft_not_retiring: "not a retiring type",
  unknown_aircraft: "aircraft not identified",
};

export const BODY_LABEL: Record<BodyType, string> = {
  widebody: "Widebody",
  narrowbody: "Narrowbody",
  regional: "Regional jet",
  turboprop: "Turboprop",
  unknown: "Unknown",
};

export function formatDuration(minutes: number | null): string | null {
  if (minutes === null || minutes <= 0) return null;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

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

/** Per-point values live in fractions of a rupee, so whole-rupee rounding —
 *  which is right for fares — turns every one of them into ₹0. */
export function formatRate(value: string | number, currency: string): string {
  const amount = typeof value === "string" ? Number(value) : value;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: currency.toUpperCase(),
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${currency.toUpperCase()} ${amount.toFixed(2)}`;
  }
}

export function relativeAge(iso: string): string {
  const hours = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
