// Historical: moved from backend app/api; kept for reference only.
import { NextResponse } from "next/server";

import { requireUser, isOwner } from "../../../../../lib/authz";
import { getServiceRoleSupabaseClient } from "../../../../../lib/supabaseServer";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const NO_STORE_HEADERS = { "Cache-Control": "no-store" };

export async function GET(request: Request) {
  const warnings: string[] = [];
  const userResult = await requireUser();
  if (userResult.kind === "unauthenticated") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401, headers: NO_STORE_HEADERS });
  }
  if (!isOwner(userResult.user.id)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403, headers: NO_STORE_HEADERS });
  }

  const url = new URL(request.url);
  const previewId = (url.searchParams.get("preview_id") || "").trim();

  if (!previewId) {
    return NextResponse.json(
      { chain: { preview_id: null, preview: null, decision: null, verdict: null }, warnings: ["PREVIEW_ID_REQUIRED"] },
      { headers: NO_STORE_HEADERS }
    );
  }

  const service = getServiceRoleSupabaseClient();

  let preview: any = null;
  try {
    const res = await service.from("signal_previews").select("*").eq("id", previewId).limit(1).maybeSingle();
    if ((res as any).error) {
      warnings.push("PREVIEW_FETCH_FAILED");
    } else {
      preview = res.data ?? null;
    }
  } catch (err) {
    warnings.push("PREVIEW_FETCH_FAILED");
  }

  let decision: any = null;
  try {
    const res = await service
      .from("control_decisions")
      .select("*")
      .eq("signal_preview_id", previewId)
      .order("decision_version", { ascending: false })
      .limit(1)
      .maybeSingle();
    if ((res as any).error) {
      warnings.push("DECISION_FETCH_FAILED");
    } else {
      decision = res.data ?? null;
    }
  } catch (err) {
    warnings.push("DECISION_FETCH_FAILED");
  }

  let verdict: any = null;
  if (decision?.id) {
    try {
      const res = await service
        .from("risk_verdicts")
        .select("*")
        .eq("decision_id", decision.id)
        .order("verdict_version", { ascending: false })
        .limit(1)
        .maybeSingle();
      if ((res as any).error) {
        warnings.push("VERDICT_FETCH_FAILED");
      } else {
        verdict = res.data ?? null;
      }
    } catch (err) {
      warnings.push("VERDICT_FETCH_FAILED");
    }
  }

  return NextResponse.json(
    {
      chain: { preview_id: previewId, preview, decision, verdict },
      ...(warnings.length ? { warnings } : {}),
    },
    { headers: NO_STORE_HEADERS }
  );
}
