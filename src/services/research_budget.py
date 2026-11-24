"""USD reservations before paid research; unknown prices block requests."""
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from datetime import date
from src.services.recovery import current_job

def micros(value):
    value=Decimal(str(value))
    if not value.is_finite() or value<0: raise ValueError('Invalid price')
    return int((value*1000000).to_integral_value(rounding=ROUND_CEILING))

def reserve(storage, run_id, amount):
    context=current_job.get()
    if context: context.authorize()
    if not context or context.job.kind!='research_refresh': return None
    return storage.reserve_research_cost(run_id,micros(amount),micros(context.job.payload.get('budget_usd',1)))

def settle(storage, reservation, amount):
    if reservation and amount is not None: storage.settle_research_cost(reservation,micros(amount))

def text_upper_price(settings, route, input_tokens, output_tokens):
    rates=json.loads(settings.research_price_rates)
    if not rates:rates=json.loads((Path(__file__).resolve().parents[1]/'config/research-pricing.json').read_text(encoding='utf-8'))
    price=rates.get(route.litellm_model)
    if not price or not price.get('verified_at'): raise ValueError('Research model price unverified; request blocked')
    age=(date.today()-date.fromisoformat(price['verified_at'])).days
    if not 0<=age<=30:raise ValueError('Research price verification expired; request blocked')
    if Decimal(str(price['input']))<=0 or Decimal(str(price['output']))<=0:raise ValueError('Invalid model rate')
    long_context=input_tokens>price.get('long_context_threshold',float('inf'))
    input_rate=price.get('long_input',price['input']) if long_context else price['input']
    output_rate=price.get('long_output',price['output']) if long_context else price['output']
    return (Decimal(str(input_rate))*input_tokens+Decimal(str(output_rate))*output_tokens)/1000000*Decimal('1.25')
