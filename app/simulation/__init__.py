# SimulationEngine v1 - Shadow Trading Simulator
# Runs parallel to trading bot, does NOT execute real orders
# Reads: signal_previews, control_decisions, risk_verdicts
# Writes: sim_trades, sim_fills, sim_equity_curve, sim_events

from app.simulation.engine import SimulationEngine
from app.simulation.models import SimTrade, SimFill, SimEquityPoint, SimEvent
from app.simulation.price_feed import PriceFeed
from app.simulation.position_sizer import PositionSizer

__all__ = [
    "SimulationEngine",
    "SimTrade",
    "SimFill", 
    "SimEquityPoint",
    "SimEvent",
    "PriceFeed",
    "PositionSizer",
]
