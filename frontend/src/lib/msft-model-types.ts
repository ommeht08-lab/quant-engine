// Explicit types for src/data/msft-model-data.json, generated offline
// from archived, reviewed evidence (see extract script referenced in
// SHARED_HANDOFF.md). Written by hand rather than relying on TypeScript's
// structural inference across this file's mixed scenario shapes, which
// otherwise widens into an unusable union when indexed by a union of
// scenario names.

export type YearStatus = "completed" | "refused" | "not_attempted" | "verification_failed";

export interface Period {
  income: Record<string, string>;
  balance: Record<string, string>;
  cashFlow: Record<string, string>;
  debt: Record<string, string>;
  unleveredFcf: string;
}

export interface DcfCrossCheck {
  applicable: boolean;
  difference?: string;
  tolerance?: string;
}

export interface RefusalClassification {
  expected: boolean;
  classification: string;
  reason: string;
}

export type ScenarioYear =
  | { year: number; status: "completed"; period: Period; dcfCrossCheck: DcfCrossCheck; identityResidualIsExactZero: boolean }
  | { year: number; status: "refused"; refusalMessage: string; classification?: RefusalClassification | null }
  | { year: number; status: "not_attempted"; reason: string }
  | { year: number; status: "verification_failed"; message: string };

export interface AssumptionYear {
  year: number;
  baseRevenue: string;
  revenueGrowth: string;
  resultingRevenue: string;
  operatingMargin: string;
  taxRate: string;
  capex: string;
  depreciation: string;
  dividends: string;
  equityIssuance: string;
  shareRepurchases: string;
  debtInterestRate: string;
  revolverInterestRate: string;
  revolverLimit: string;
  minimumCash: string;
  commitmentFeeRate: string;
  drawFeeRate: string;
  longTermDebtNoncurrentMovement: string;
}

export interface ScenarioData {
  name: string;
  type: "development" | "validation";
  rationale: string;
  expectedStatus: string | null;
  expectedStatusDetail: string;
  predeclaredInvariants: string[];
  assumptionYears: AssumptionYear[];
  runSource: "development" | "original_execution" | "continuation";
  stopped: boolean;
  allChecksPassed: boolean;
  expectedRefusalConfirmed?: boolean;
  years: ScenarioYear[];
}

export interface MaterialityRow {
  year: number;
  scenarioFcf: string;
  referenceFcf: string;
  signedDifference: string;
  absoluteDifference: string;
  percentageDifference: string;
  material: boolean;
}

export interface InvariantResult {
  scenario: string;
  reference: string;
  allExact: boolean;
  perYear: Array<{
    year: number;
    scenarioFcf: string;
    referenceFcf: string;
    signedDifference: string;
    exactMatch: boolean;
  }>;
}

export interface TenScenarioStatusRow {
  name: string;
  status: string;
  note: string;
  runSource: string;
  completedYears: number;
}

export interface X1FundingDetail {
  year: number;
  maximumAvailableCashUsdMillions: string;
  minimumRequiredCashUsdMillions: string;
  shortfallUsdMillions: string;
  shortfallUsdBillionsApprox: number;
  refusalMessage: string;
  note: string;
}

export interface MsftModelData {
  generatedAtUtc: string;
  generatedFrom: string;
  foundation: Record<string, string>;
  openingBalanceUsdMillions: Record<string, string>;
  materialityRule: Record<string, string>;
  freeTags: string[];
  fixedZeroUnsupportedTags: string[];
  developmentCases: string[];
  validationCases: string[];
  scenarios: Record<string, ScenarioData>;
  crossScenarioInvariants: Record<string, InvariantResult>;
  sensitivityVsR: Record<string, MaterialityRow[] | null>;
  tenScenarioStatus: TenScenarioStatusRow[];
  runs: {
    originalExecution: {
      archivePath: string;
      reportPath: string;
      reportSha256: string;
      processExitCode: number;
      stopReason: unknown;
      openingBalanceStoredFactCrossCheck: { concepts_checked: number; all_matched: boolean };
    };
    continuation: {
      archivePath: string;
      reportPath: string;
      reportSha256: string;
      processExitCode: number;
      authorizedScenarios: string[];
    };
  };
  x1FundingDetail: X1FundingDetail;
  sourceHashes: Record<string, string>;
}
