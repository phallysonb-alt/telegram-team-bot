"""Bounded-field transport diagnostics: never log payloads, identities or URLs."""
import json
import logging
import math

logger = logging.getLogger('team_bot.bridge')
logger.setLevel(logging.INFO)
FIELDS = {'mode', 'previous_mode', 'reason', 'status', 'local_ms', 'telegram_ms',
          'outage_ms', 'retry_seconds', 'pending', 'endpoint'}
WORDS = {'WebSocket', 'HTTP兼容', 'starting', 'network', 'timeout', 'telegram_network',
         'telegram_timeout', 'telegram_rate_limited', 'telegram_unavailable',
         'service_error', 'storage_error', 'permission_denied', 'invalid_request',
         'normal_close', 'socket_error', 'http_error', 'http_403', 'http_503', 'ok'}
PATHS = {'/api/session', '/api/history', '/api/feedback', '/api/summary', '/api/tasks',
         '/api/publish', '/api/query', '/api/submit', '/api/metrics', '/api/people',
         '/api/people/invite', '/api/people/approve', '/api/people/reject'}


def emit(event, **fields):
    # Callers supply constant event names; arbitrary data is rejected by allowlist.
    if event not in {'request_completed', 'timing_write_failed', 'connected',
                     'disconnected', 'mode_changed', 'result_retained',
                     'delivery_retry', 'membership_unavailable'}:
        return
    safe = {'event': event}
    for key, value in fields.items():
        if key not in FIELDS:
            continue
        if isinstance(value, (int, float)) and math.isfinite(value):
            safe[key] = round(value, 2)
        elif key == 'endpoint' and isinstance(value, str) and value in PATHS:
            safe[key] = value
        elif isinstance(value, str) and value in WORDS:
            safe[key] = value
    logger.info('bridge %s', json.dumps(safe, ensure_ascii=False, sort_keys=True))


class MemberCheckUnavailable(PermissionError):
    """Fail closed; subtype preserves existing registry retry callers."""
    def __init__(self, reason='telegram_unavailable', elapsed_ms=0):
        super().__init__('群成员核验暂时不可用，请稍后重试；未执行本次操作')
        self.reason = reason if reason in WORDS else 'telegram_unavailable'
        self.elapsed_ms = elapsed_ms
