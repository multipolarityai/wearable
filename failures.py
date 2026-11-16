"""Evidence categories, not an automatic root-cause or answer-quality judge."""
import asyncio


def classify(error, boundary):
    if isinstance(error, asyncio.CancelledError):
        category, origin = 'cancelled', 'external'
    elif isinstance(error, TimeoutError):
        category, origin = 'timeout', 'undetermined'
    elif type(error).__name__ == 'MaxTurnsExceeded':
        category, origin = 'turn_budget_exhausted', 'undetermined'
    elif type(error).__name__ == 'ModelBehaviorError':
        category, origin = 'invalid_model_output', 'model_output'
    elif type(error).__name__ == 'UnsupportedSDKInterruption':
        category, origin = 'unsupported_interruption', 'adapter'
    else:
        category, origin = 'execution_error', 'undetermined'
    return {'category': category, 'origin': origin, 'boundary': boundary,
            'exception': type(error).__name__, 'root_cause_proven': False}
