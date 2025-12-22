"""
Migration: Fix signals_params to pass is_configured() check

Problem: signals_params in DB is missing required fields:
- confidence.high_conf_rule
- m15_confirm.rsi_period
- m15_confirm.rsi_long_min  
- m15_confirm.rsi_short_max
- m15_confirm.sma_period

Run on VPS: docker exec ibkr-trading-bot python /app/migrations/fix_signals_params.py
"""
import sys
sys.path.insert(0, '/app')
import os
os.chdir('/app')

from dotenv import load_dotenv
load_dotenv()

from app.storage.db import SupabaseDB


def migrate():
    db = SupabaseDB()
    
    # Get current settings
    result = db.client.table('bot_settings').select('id,signals_params').limit(1).execute()
    if not result.data:
        print("ERROR: No bot_settings found")
        return False
    
    row = result.data[0]
    params = row['signals_params']
    
    print("Current signals_params:")
    print(f"  confidence: {params.get('confidence')}")
    print(f"  m15_confirm: {params.get('m15_confirm')}")
    print(f"  timeframes: {params.get('timeframes')}")
    
    # Add missing fields
    if 'timeframes' not in params or params['timeframes'] is None:
        params['timeframes'] = {
            'h4': 'H4',
            'h1': 'H1', 
            'm15': 'M15'
        }
    
    if 'confidence' not in params or params['confidence'] is None:
        params['confidence'] = {}
    params['confidence']['high_conf_rule'] = 'H4_H1_ALIGN_AND_M15_MOMENTUM_CONFIRM'
    
    if 'm15_confirm' not in params or params['m15_confirm'] is None:
        params['m15_confirm'] = {}
    params['m15_confirm']['rsi_period'] = 14
    params['m15_confirm']['rsi_long_min'] = 30.0
    params['m15_confirm']['rsi_short_max'] = 70.0
    params['m15_confirm']['sma_period'] = 50
    
    # Save
    db.client.table('bot_settings').update({'signals_params': params}).eq('id', row['id']).execute()
    
    print("\nUpdated signals_params:")
    print(f"  confidence: {params.get('confidence')}")
    print(f"  m15_confirm: {params.get('m15_confirm')}")
    print(f"  timeframes: {params.get('timeframes')}")
    
    # Verify
    from app.storage.bot_settings_repo import BotSettingsRepo
    repo = BotSettingsRepo(db)
    owner_uuid = os.getenv('BOT_OWNER_USER_ID')
    settings = repo.get(str(owner_uuid))
    sp = getattr(settings, 'signals_params', None)
    
    print(f"\nis_configured: {sp.is_configured()}")
    return sp.is_configured()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
