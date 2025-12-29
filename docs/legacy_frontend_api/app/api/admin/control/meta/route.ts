// Historical: moved from backend app/api; kept for reference only.
import { NextResponse } from "next/server";

import { isOwner, requireUser } from "@/lib/authz";
import { getServiceRoleSupabaseClient } from "@/lib/supabaseServer";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const NO_STORE_HEADERS = { "Cache-Control": "no-store" };
const BOT_OWNER_USER_ID = process.env.BOT_OWNER_USER_ID;
const STATIC_FLAGS = [
  "EXECUTION_DISABLED",
  "RECONCILIATION_DISABLED",
  "SIGNALS_RULES_NOT_SPECIFIED",
];
const RECON_STATUSES = ["ANY", "OK", "WARN", "ERROR", "SKIPPED"];
const RECENT_PREVIEWS_LOOKBACK_HOURS = 48;
const RECENT_PREVIEWS_LIMIT = 1000;

function normalizeStrings(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  const res = input
    .map((v) => (typeof v === "string" ? v.trim().toUpperCase() : ""))
    .filter(Boolean);
  return Array.from(new Set(res));
}

export async function GET() {
  const userResult = await requireUser();
  if (userResult.kind === "unauthenticated") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401, headers: NO_STORE_HEADERS });
  }
  if (!isOwner(userResult.user.id)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403, headers: NO_STORE_HEADERS });
  }

  const service = getServiceRoleSupabaseClient();

  let symbolsFromSettings: string[] = [];
  const settingsRes = await service
    .from("bot_settings")
    .select("symbols")
    .eq("owner_user_id", BOT_OWNER_USER_ID)
    .limit(1)
    .maybeSingle();
  if (settingsRes.error) {
    return NextResponse.json({ error: settingsRes.error.message }, { status: 500, headers: NO_STORE_HEADERS });
  }
  if (settingsRes.data?.symbols) {
    symbolsFromSettings = normalizeStrings(settingsRes.data.symbols);
  }

  let symbolsFromPreviews: string[] = [];
  let flagsFromPreviews: string[] = [];
  const since = new Date(Date.now() - RECENT_PREVIEWS_LOOKBACK_HOURS * 60 * 60 * 1000).toISOString();
  const previewsRes = await service
    .from("signal_previews")
    .select("symbol, flags")
    .gte("ts_utc", since)
    .order("ts_utc", { ascending: false })
    .limit(RECENT_PREVIEWS_LIMIT);
  if (!previewsRes.error && Array.isArray(previewsRes.data)) {
    symbolsFromPreviews = normalizeStrings(previewsRes.data.map((p) => p.symbol));
    flagsFromPreviews = normalizeStrings(
      previewsRes.data.flatMap((p) => (Array.isArray(p.flags) ? p.flags : []))
    );
  }

  const symbols = Array.from(new Set([...symbolsFromSettings, ...symbolsFromPreviews])).sort();
  const flags = Array.from(new Set([...STATIC_FLAGS, ...flagsFromPreviews])).sort();

  return NextResponse.json(
    {
      symbols,
      flags,
      recon_statuses: RECON_STATUSES,
    },
    { status: 200, headers: NO_STORE_HEADERS }
  );
}
