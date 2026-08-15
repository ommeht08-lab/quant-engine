import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth";
import { assertSafeAlpacaBaseUrl } from "@/lib/alpaca-url";

// Always hit Alpaca live — this is a live account snapshot, never a
// cacheable resource.
export const dynamic = "force-dynamic";

interface AlpacaConfig {
  apiKey: string;
  secretKey: string;
  baseUrl: string;
}

interface AlpacaAccount {
  equity: string;
}

interface AlpacaPosition {
  symbol: string;
  qty: string;
  market_value: string;
  current_price: string;
  avg_entry_price: string;
  unrealized_pl: string;
  unrealized_plpc: string;
}

export interface PositionSummary {
  symbol: string;
  qty: number;
  marketValue: number;
  currentPrice: number;
  avgEntryPrice: number;
  unrealizedPl: number;
  unrealizedPlPercent: number;
}

export interface PositionsPayload {
  equity: number;
  positions: PositionSummary[];
}

function getAlpacaConfig(): AlpacaConfig {
  const apiKey = process.env.APCA_API_KEY_ID;
  const secretKey = process.env.APCA_API_SECRET_KEY;
  const rawBaseUrl = process.env.APCA_API_BASE_URL;
  if (!apiKey || !secretKey || !rawBaseUrl) {
    throw new Error("Alpaca API credentials are not configured for this deployment.");
  }
  const baseUrl = assertSafeAlpacaBaseUrl(rawBaseUrl);
  return { apiKey, secretKey, baseUrl };
}

async function alpacaGet<T>(path: string, config: AlpacaConfig): Promise<T> {
  const response = await fetch(`${config.baseUrl}${path}`, {
    headers: {
      "APCA-API-KEY-ID": config.apiKey,
      "APCA-API-SECRET-KEY": config.secretKey,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`Alpaca ${path} request failed (HTTP ${response.status}): ${body}`);
  }

  return response.json() as Promise<T>;
}

/**
 * GET /api/positions
 *
 * Returns the account's total equity and every currently open position
 * (symbol, quantity, market value, price) as reported live by Alpaca's
 * `/v2/account` and `/v2/positions` endpoints — used to visualize the
 * autonomous execution engine's actual, realized inverse-beta position
 * weights.
 */
export async function GET() {
  if (!(await requireSession())) {
    return NextResponse.json({ error: "Not authenticated." }, { status: 401 });
  }

  let config: AlpacaConfig;
  try {
    config = getAlpacaConfig();
  } catch (error) {
    // Logged server-side only; the error message itself never contains
    // the actual credential/URL values, only a description of which
    // configuration requirement wasn't met.
    console.error(error);
    return NextResponse.json(
      { error: "Alpaca API is not configured correctly for this deployment." },
      { status: 500 }
    );
  }

  try {
    const [account, positions] = await Promise.all([
      alpacaGet<AlpacaAccount>("/v2/account", config),
      alpacaGet<AlpacaPosition[]>("/v2/positions", config),
    ]);

    const payload: PositionsPayload = {
      equity: parseFloat(account.equity),
      positions: positions.map((position) => ({
        symbol: position.symbol,
        qty: parseFloat(position.qty),
        marketValue: parseFloat(position.market_value),
        currentPrice: parseFloat(position.current_price),
        avgEntryPrice: parseFloat(position.avg_entry_price),
        unrealizedPl: parseFloat(position.unrealized_pl),
        unrealizedPlPercent: parseFloat(position.unrealized_plpc),
      })),
    };

    return NextResponse.json(payload);
  } catch (error) {
    console.error("Failed to fetch Alpaca account/positions:", error);
    return NextResponse.json(
      { error: "Failed to fetch live portfolio positions from Alpaca." },
      { status: 500 }
    );
  }
}
