export interface ResearchFactFixture {
  id: string;
  label: string;
  period: string;
  value: string;
  rawTag: string;
  accession: string;
  acceptedAt: string;
  filedDate: string;
  form: string;
  conceptMapVersion: string;
  ingestionBatch: string;
  ingestedAt: string;
  sourceUrl: string;
}

export interface ResearchCaseFixture {
  artifactVersion: string;
  modelVersion: string;
  ticker: string;
  companyName: string;
  sector: string;
  knowledgeCutoff: string;
  marketPrice: string;
  intrinsicValue: string;
  marginOfSafety: string;
  scenarios: ReadonlyArray<{ name: string; value: string; note: string }>;
  chart: ReadonlyArray<{ year: string; revenue: number; forecast: boolean }>;
  facts: ReadonlyArray<ResearchFactFixture>;
}

export const AAPL_RESEARCH_FIXTURE: ResearchCaseFixture = {
  artifactVersion: "application-case-aapl-2026-08-01-v1",
  modelVersion: "pit-3s-dcf-v1 · prototype",
  ticker: "AAPL",
  companyName: "Apple Inc.",
  sector: "Technology",
  knowledgeCutoff: "Aug 1, 2026 · 4:00 PM ET",
  marketPrice: "$202.38",
  intrinsicValue: "$236.70",
  marginOfSafety: "+17.0%",
  scenarios: [
    { name: "Bear", value: "$181.40", note: "9.8% WACC · 2.0% terminal growth" },
    { name: "Base", value: "$236.70", note: "8.9% WACC · 2.5% terminal growth" },
    { name: "Bull", value: "$302.10", note: "8.2% WACC · 3.0% terminal growth" },
  ],
  chart: [
    { year: "2021", revenue: 366, forecast: false },
    { year: "2022", revenue: 394, forecast: false },
    { year: "2023", revenue: 383, forecast: false },
    { year: "2024", revenue: 391, forecast: false },
    { year: "2025", revenue: 416, forecast: false },
    { year: "2026E", revenue: 437, forecast: true },
    { year: "2027E", revenue: 459, forecast: true },
    { year: "2028E", revenue: 480, forecast: true },
    { year: "2029E", revenue: 501, forecast: true },
    { year: "2030E", revenue: 521, forecast: true },
  ],
  facts: [
    {
      id: "revenue-2024",
      label: "Revenue",
      period: "FY 2024",
      value: "$391.04B",
      rawTag: "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
      accession: "0000320193-24-000123",
      acceptedAt: "Nov 1, 2024 · 6:01:36 AM ET",
      filedDate: "Nov 1, 2024",
      form: "10-K",
      conceptMapVersion: "sec-gaap-v1.0",
      ingestionBatch: "sec-2026-08-01T03:00Z",
      ingestedAt: "Aug 1, 2026 · 3:14:08 AM UTC",
      sourceUrl: "https://www.sec.gov/edgar/browse/?CIK=320193",
    },
    {
      id: "operating-income-2024",
      label: "Operating income",
      period: "FY 2024",
      value: "$123.22B",
      rawTag: "us-gaap:OperatingIncomeLoss",
      accession: "0000320193-24-000123",
      acceptedAt: "Nov 1, 2024 · 6:01:36 AM ET",
      filedDate: "Nov 1, 2024",
      form: "10-K",
      conceptMapVersion: "sec-gaap-v1.0",
      ingestionBatch: "sec-2026-08-01T03:00Z",
      ingestedAt: "Aug 1, 2026 · 3:14:08 AM UTC",
      sourceUrl: "https://www.sec.gov/edgar/browse/?CIK=320193",
    },
    {
      id: "cash-2024",
      label: "Cash & equivalents",
      period: "FY 2024",
      value: "$29.94B",
      rawTag: "us-gaap:CashAndCashEquivalentsAtCarryingValue",
      accession: "0000320193-24-000123",
      acceptedAt: "Nov 1, 2024 · 6:01:36 AM ET",
      filedDate: "Nov 1, 2024",
      form: "10-K",
      conceptMapVersion: "sec-gaap-v1.0",
      ingestionBatch: "sec-2026-08-01T03:00Z",
      ingestedAt: "Aug 1, 2026 · 3:14:08 AM UTC",
      sourceUrl: "https://www.sec.gov/edgar/browse/?CIK=320193",
    },
    {
      id: "operating-cash-flow-2024",
      label: "Operating cash flow",
      period: "FY 2024",
      value: "$118.25B",
      rawTag: "us-gaap:NetCashProvidedByUsedInOperatingActivities",
      accession: "0000320193-24-000123",
      acceptedAt: "Nov 1, 2024 · 6:01:36 AM ET",
      filedDate: "Nov 1, 2024",
      form: "10-K",
      conceptMapVersion: "sec-gaap-v1.0",
      ingestionBatch: "sec-2026-08-01T03:00Z",
      ingestedAt: "Aug 1, 2026 · 3:14:08 AM UTC",
      sourceUrl: "https://www.sec.gov/edgar/browse/?CIK=320193",
    },
  ],
};
