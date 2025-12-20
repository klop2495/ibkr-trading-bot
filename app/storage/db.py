import os
import sys
from supabase import create_client, Client


class _NullSupabaseClient:
    """
    Minimal stub to surface meaningful errors when Supabase env is missing.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def table(self, *args, **kwargs):
        raise RuntimeError(f"Supabase client not configured: {self.reason}")


class SupabaseDB:
    """
    Thin wrapper around Supabase client.
    Uses SERVICE ROLE key (server-side only).
    """

    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            reason = "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set"
            print(f"[SupabaseDB] {reason}", file=sys.stderr)
            self.client: Client | _NullSupabaseClient = _NullSupabaseClient(reason)
            self.disabled = True
            return
        self.client: Client | _NullSupabaseClient = create_client(url, key)
        self.disabled = False

    def ping(self) -> bool:
        """
        Lightweight DB connectivity check.
        """
        try:
            self.client.table("risk_events").select("id").limit(1).execute()
            return True
        except Exception:
            return False
