import os
from supabase import create_client, Client


class SupabaseDB:
    """
    Thin wrapper around Supabase client.
    Uses SERVICE ROLE key (server-side only).
    """

    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set")
        self.client: Client = create_client(url, key)

    def ping(self) -> bool:
        """
        Lightweight DB connectivity check.
        """
        try:
            self.client.table("risk_events").select("id").limit(1).execute()
            return True
        except Exception:
            return False
