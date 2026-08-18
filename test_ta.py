import sys, os
sys.path.insert(0, 'C:/MITRAseries/TradingAgents')
os.environ['INVESTMITRA_NEON_URL'] = os.environ.get('CC_POSTGRES_URL', '')
from dotenv import load_dotenv
load_dotenv('.env.prod')
load_dotenv('C:/MITRAseries/TradingAgents/.env')
try:
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    config = DEFAULT_CONFIG.copy()
    config['deep_think_llm'] = 'gpt-4o-mini'
    config['quick_think_llm'] = 'gpt-4o-mini'
    config['max_debate_rounds'] = 1
    config['max_risk_discuss_rounds'] = 1
    ta = TradingAgentsGraph(debug=False, config=config)
    state, decision = ta.propagate('ANTHEM.NS', '2026-08-15')
    print('Decision:', decision)
except Exception as e:
    import traceback
    traceback.print_exc()
