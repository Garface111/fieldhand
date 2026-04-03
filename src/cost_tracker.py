"""
Cost tracking for every agent interaction.
Tracks tokens per model, computes per-message cost and monthly projection.
"""
from dataclasses import dataclass, field
from typing import Optional

# Pricing per million tokens (USD)
PRICING = {
    'claude-haiku-4-5':         {'input': 0.80,  'output': 4.00,  'cache_read': 0.08},
    'claude-haiku-4-5-20251001':{'input': 0.80,  'output': 4.00,  'cache_read': 0.08},
    'claude-sonnet-4-5':        {'input': 3.00,  'output': 15.00, 'cache_read': 0.30},
    'claude-sonnet-4-5-20250929':{'input': 3.00, 'output': 15.00, 'cache_read': 0.30},
    'claude-sonnet-4-6':        {'input': 3.00,  'output': 15.00, 'cache_read': 0.30},
    'claude-sonnet-4-6-20251101':{'input': 3.00, 'output': 15.00, 'cache_read': 0.30},
}

DEFAULT_MESSAGES_PER_DAY = 15


@dataclass
class MessageCost:
    tier: int
    model: str
    classifier_model: str = 'claude-haiku-4-5'
    
    # Token counts
    classifier_input: int = 0
    classifier_output: int = 0
    agent_input: int = 0
    agent_output: int = 0
    agent_cache_read: int = 0
    agent_cache_write: int = 0
    thinking_tokens: int = 0
    
    # Derived
    @property
    def classifier_cost(self) -> float:
        p = PRICING.get(self.classifier_model, PRICING['claude-haiku-4-5'])
        return (self.classifier_input * p['input'] + self.classifier_output * p['output']) / 1_000_000
    
    @property 
    def agent_cost(self) -> float:
        p = PRICING.get(self.model, PRICING['claude-sonnet-4-5'])
        input_cost = (self.agent_input * p['input']) / 1_000_000
        output_cost = (self.agent_output * p['output']) / 1_000_000
        cache_cost = (self.agent_cache_read * p['cache_read']) / 1_000_000
        return input_cost + output_cost + cache_cost
    
    @property
    def total_cost(self) -> float:
        return self.classifier_cost + self.agent_cost
    
    @property
    def total_input_tokens(self) -> int:
        return self.classifier_input + self.agent_input
    
    @property
    def total_output_tokens(self) -> int:
        return self.classifier_output + self.agent_output
    
    @property
    def monthly_projection(self) -> float:
        """Project monthly cost at 15 messages/day."""
        return self.total_cost * DEFAULT_MESSAGES_PER_DAY * 30
    
    def summary(self) -> dict:
        return {
            'tier': self.tier,
            'model': self.model,
            'classifier_model': self.classifier_model,
            'tokens': {
                'classifier_in': self.classifier_input,
                'classifier_out': self.classifier_output,
                'agent_in': self.agent_input,
                'agent_out': self.agent_output,
                'cache_read': self.agent_cache_read,
                'thinking': self.thinking_tokens,
                'total': self.total_input_tokens + self.total_output_tokens,
            },
            'cost': {
                'classifier': round(self.classifier_cost, 6),
                'agent': round(self.agent_cost, 6),
                'total': round(self.total_cost, 6),
                'monthly_projection_usd': round(self.monthly_projection, 2),
            },
        }
