"""Read existing host logs locally, preserving observation and correlation boundaries.

No requests are replayed. No reasoning payloads, system prompts, credentials or
entire conversations are exported. Adapters are deliberately version-tolerant.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

EVENT_TYPES = {'user-input', 'model-output', 'skill-loaded', 'skill-read', 'tool-call',
               'tool-result', 'hook-triggered', 'artifact', 'error'}
SECRET_KEYS = re.compile(r'api.?key|authorization|cookie|password|secret|token|encrypted|reasoning|base_instructions', re.I)
SECRET_TEXT = re.compile(r'(?:sk-(?:proj-|ant-)?[\w-]{16,}|gh[pousr]_[\w]{20,}|github_pat_[\w]{20,}|Bearer\s+\S+|(?:api[_-]?key|password|secret|cookie|authorization)\s*[:=]\s*[^\s,;]+|-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----)', re.I)


def sanitize(value, limit=1800):
    if isinstance(value, dict):
        return {k: '[redacted]' if SECRET_KEYS.search(k) else sanitize(v, limit)
                for k, v in value.items() if k not in ('reasoning', 'encrypted_content', 'base_instructions')}
    if isinstance(value, list): return [sanitize(v, limit) for v in value]
    if isinstance(value, str):
        return SECRET_TEXT.sub('[redacted]', value)[:limit]
    if isinstance(value, (int, float, bool)) or value is None: return value
    return str(value)[:limit]


def _text(content):
    if isinstance(content, str): return content
    if isinstance(content, list):
        return '\n'.join(c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') in ('text', 'input_text', 'output_text'))
    return ''


def _events(record, host):
    """Normalize only explicit events; reading SKILL.md never implies loading."""
    p = record.get('payload', record)
    stamp = record.get('timestamp') or p.get('timestamp')
    result = []
    if record.get('type') in ('compacted', 'turn_context', 'world_state', 'token_usage_record'):
        return result
    if record.get('type') == 'session_meta': return result
    if p.get('type') in ('reasoning', 'thinking'): return result
    if host == 'codex' and record.get('type') != 'response_item':
        return result  # event_msg mirrors response_item; avoid duplicate claims
    role = p.get('role') or record.get('type')
    message = p.get('message', p)
    if not isinstance(message, dict): message = p
    content = message.get('content', p.get('content', []))
    if p.get('type') in ('function_call', 'custom_tool_call'):
        raw = p.get('arguments', p.get('input', ''))
        result.append({'type': 'tool-call', 'callId': p.get('call_id'), 'name': p.get('name'), 'text': raw})
    elif p.get('type') in ('function_call_output', 'custom_tool_call_output'):
        result.append({'type': 'tool-result', 'callId': p.get('call_id'), 'text': p.get('output', '')})
    elif p.get('type') in EVENT_TYPES:
        result.append({k: p[k] for k in ('type', 'text', 'name', 'skill', 'artifact', 'callId', 'parentId', 'status') if k in p})
    elif p.get('event') in ('SessionStart', 'PreToolUse', 'PostToolUse', 'Stop'):
        result.append({'type': 'hook-triggered', 'name': p['event'], 'text': p.get('message', '')})
    elif record.get('jsonrpc') == '2.0':
        if record.get('method') == 'tools/call':
            result.append({'type': 'tool-call', 'callId': str(record.get('id')), 'name': record.get('params', {}).get('name'), 'text': json.dumps(record.get('params', {}).get('arguments', {}))})
        elif 'result' in record or 'error' in record:
            result.append({'type': 'error' if 'error' in record else 'tool-result', 'callId': str(record.get('id')), 'text': json.dumps(record.get('result', record.get('error')))})
    else:
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict): continue
                if block.get('type') == 'tool_use':
                    result.append({'type': 'tool-call', 'callId': block.get('id'), 'name': block.get('name'), 'text': json.dumps(block.get('input', {}), ensure_ascii=False)})
                elif block.get('type') == 'tool_result':
                    result.append({'type': 'tool-result', 'callId': block.get('tool_use_id'), 'text': _text(block.get('content', ''))})
        text = _text(content)
        if text and role in ('user', 'assistant'):
            result.append({'type': 'user-input' if role == 'user' else 'model-output', 'text': text})
    for event in result: event['timestamp'] = stamp
    return result


def _records_from_json(path, max_bytes):
    """Read JSONL or a JSON object/array without treating arbitrary strings as events."""
    raw = Path(path).read_bytes()
    if len(raw) > max_bytes:
        return [(1, raw[:max_bytes], 'byte-budget')]
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        records = []
        for line_no, line in enumerate(raw.splitlines(keepends=True), 1):
            records.append((line_no, line, None))
        return records
    if isinstance(value, list):
        return [(index + 1, json.dumps(item, ensure_ascii=False).encode(), None)
                for index, item in enumerate(value)]
    if isinstance(value, dict):
        return [(1, raw, None)]
    return [(1, raw, 'invalid-record:1')]


def read_trace(path, aliases, max_bytes=12_000_000, max_events=12000):
    """Return redacted events with immutable line locators and original hash."""
    digest = hashlib.sha256()
    events, gaps, meta = [], [], {}
    host, consumed = 'generic', 0
    anchors = []
    pattern = re.compile(r'(?<![\w-])(?:' + '|'.join(re.escape(a) for a in aliases if a) + r')(?![\w-])', re.I) if aliases else None
    for line_no, raw, record_gap in _records_from_json(path, max_bytes):
            if record_gap:
                gaps.append(record_gap)
            if consumed + len(raw) > max_bytes:
                gaps.append('byte-budget'); break
            consumed += len(raw)
            digest.update(raw)
            if len(raw) > 1_000_000:
                gaps.append(f'oversized-line:{line_no}'); continue
            try: record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                gaps.append(f'malformed-line:{line_no}'); continue
            if not isinstance(record, dict):
                gaps.append(f'invalid-record:{line_no}'); continue
            p = record.get('payload', record)
            if not isinstance(p, dict): continue
            if record.get('type') == 'session_meta':
                host = 'codex'
                meta.update({k: p.get(k) for k in ('id', 'session_id', 'cwd', 'timestamp')})
                git = p.get('git') or {}
                if isinstance(git, dict): meta['revision'] = git.get('commit_hash')
            elif record.get('sessionId'):
                host = 'claude'; meta['id'] = record['sessionId']; meta['cwd'] = record.get('cwd', meta.get('cwd'))
            for key in ('revision', 'skillRevision', 'skill', 'completeness', 'source'):
                if key in p and p[key] is not None: meta[key] = p[key]
            normalized = _events(record, host)
            for offset, raw_event in enumerate(normalized):
                # Match before excerpt truncation, but after excluding private
                # reasoning/system fields. This is correlation, not causation.
                haystack = json.dumps(raw_event, ensure_ascii=False)
                matched = bool(pattern and pattern.search(haystack))
                event = sanitize(raw_event)
                event['id'] = f'event:{line_no}:{offset}'
                event['locator'] = {'line': line_no, 'recordSha256': hashlib.sha256(raw).hexdigest()}
                event['skillMatch'] = matched
                if matched: anchors.append(len(events))
                events.append(event)
                if len(events) >= max_events: break
            if len(events) >= max_events:
                gaps.append('event-budget'); break
    trace_id = 'trace:' + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:20]
    return {'traceId': trace_id, 'sourcePath': str(Path(path).resolve()), 'adapter': host,
            'contentSha256': digest.hexdigest(), 'hashScope': 'read-prefix' if 'byte-budget' in gaps else 'read-records',
            'metadata': sanitize(meta), 'events': events, 'anchors': anchors, 'gaps': gaps}


def default_roots(source_root=None, config_path=None):
    roots = []
    if source_root:
        root = Path(source_root).resolve()
        roots += [root / '.skill-lens' / 'traces', root / 'traces', root / 'logs']
    cfg = Path(config_path) if config_path else Path.home() / '.config/skill-lens/config.json'
    if cfg.is_file():
        config = json.loads(cfg.read_text())
        roots += [Path(p).expanduser() for p in config.get('traceRoots', [])]
    roots += [Path.home() / '.codex/sessions', Path.home() / '.codex/archived_sessions', Path.home() / '.claude/projects']
    return list(dict.fromkeys(str(p.resolve()) for p in roots if p.is_dir()))


def _timestamp(text):
    try:
        value = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError): return None


def search(aliases, revision, source_root=None, roots=None, session_id=None, max_traces=5,
           max_files=200, max_bytes=12_000_000, time_window_days=None,
           max_total_bytes=80_000_000):
    """Called only after static insufficiency. No absence-of-event inference."""
    roots = list(roots) if roots is not None else default_roots(source_root)
    paths, diagnostics = [], []
    for root in roots:
        directory = Path(root).expanduser()
        if directory.is_file(): paths.append(directory); continue
        if not directory.is_dir(): diagnostics.append('trace-root-unavailable'); continue
        for file in directory.rglob('*.jsonl'):
            if not file.is_symlink(): paths.append(file)
        for file in directory.glob('*.json'):
            if not file.is_symlink(): paths.append(file)
    paths = sorted(set(paths), key=lambda p: p.stat().st_mtime, reverse=True)
    if session_id: paths = [p for p in paths if session_id in p.name]
    if len(paths) > max_files: diagnostics.append('file-budget')
    cutoff = datetime.now(timezone.utc) - timedelta(days=time_window_days) if time_window_days else None
    selected, index, event_total = [], [], 0
    considered_files = 0
    matched_files = 0
    rejected = {'no-skill-match': 0, 'outside-time-window': 0,
                'current-session': 0, 'unreadable': 0}
    current_id = os.environ.get('CODEX_THREAD_ID')
    total_bytes = 0
    scanned_files = 0
    for path in paths[:max_files]:
        considered_files += 1
        if current_id and current_id in path.name and not session_id:
            rejected['current-session'] += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            diagnostics.append('trace-unreadable'); rejected['unreadable'] += 1; continue
        if total_bytes + min(size, max_bytes) > max_total_bytes:
            diagnostics.append('total-byte-budget'); break
        total_bytes += min(size, max_bytes)
        scanned_files += 1
        try: trace = read_trace(path, aliases, max_bytes=max_bytes)
        except OSError:
            diagnostics.append('trace-unreadable'); rejected['unreadable'] += 1; continue
        meta = trace['metadata']
        if cutoff and (stamp := _timestamp(meta.get('timestamp'))) and stamp < cutoff:
            rejected['outside-time-window'] += 1
            continue
        matched = trace['anchors'] or meta.get('skill') in aliases
        if not matched:
            rejected['no-skill-match'] += 1
            continue
        matched_files += 1
        # A host checkout commit isn't necessarily the installed Skill commit.
        # Only explicit skillRevision is a binding. Other revision metadata is
        # retained as a hint and never upgraded to exact Skill identity.
        binding = meta.get('skillRevision')
        binding_status = 'matched' if binding == revision else 'conflicting' if binding else 'unknown'
        positions = set()
        for anchor in trace['anchors'][:8]:
            positions.update(range(max(0, anchor - 3), min(len(trace['events']), anchor + 9)))
        if not positions and matched: positions.update(range(min(32, len(trace['events']))))
        events = [trace['events'][i] for i in sorted(positions)]
        # Recover tool pairs by explicit call IDs, not timestamp proximity.
        call_ids = {e.get('callId') for e in events if e.get('callId')}
        ids = {e['id'] for e in events}
        for e in trace['events']:
            if e.get('callId') in call_ids and e['id'] not in ids and len(events) < 100:
                events.append(e); ids.add(e['id'])
        if len(events) < len(trace['events']): trace['gaps'].append('selected-event-slice')
        event_total += len(events)
        edges = []
        calls = {e['callId']: e['id'] for e in events if e['type'] == 'tool-call' and e.get('callId')}
        for e in events:
            if e['type'] in ('tool-result', 'error') and e.get('callId') in calls:
                edges.append({'from': calls[e['callId']], 'to': e['id'], 'type': 'call-result', 'basis': 'explicit-call-id'})
        slice_ = {k: trace[k] for k in ('traceId', 'sourcePath', 'adapter', 'contentSha256', 'hashScope', 'gaps')}
        slice_.update(schema='skill-lens.trace-slice.v0.1', metadata=meta, events=events, edges=edges,
                      revisionBinding=binding_status, selectionReason='explicit skill mention or metadata; correlation requires review',
                      completeness='partial', privacy='local-redacted-excerpts')
        selected.append(slice_)
        index.append({k: slice_[k] for k in ('traceId', 'sourcePath', 'adapter', 'contentSha256', 'hashScope', 'revisionBinding', 'completeness')})
        if len(selected) >= max_traces: break
    if selected:
        decision = 'historical-slices-selected'
    elif not paths:
        decision = 'no-trace-files-in-configured-roots'
    elif matched_files == 0:
        decision = 'no-skill-correlated-records'
    else:
        decision = 'correlated-records-excluded-by-budget-or-selection'
    return {'schema': 'skill-lens.trace-search.v0.1', 'status': 'found' if selected else 'not-found',
            'slices': selected, 'index': {'schema': 'skill-lens.trace-index.v0.1', 'records': index},
            'diagnostics': sorted(set(diagnostics)), 'searchedFileCount': scanned_files, 'eventCount': event_total,
            'selection': {'aliases': sorted(set(aliases)), 'revision': revision,
                          'roots': [str(Path(root).expanduser().resolve()) for root in roots],
                          'consideredFileCount': considered_files, 'matchedFileCount': matched_files,
                          'rejectedFileCounts': {k: v for k, v in rejected.items() if v},
                          'decision': decision, 'mode': 'automatic-fallback'},
            'budget': {'maxFiles': max_files, 'maxBytesPerFile': max_bytes,
                       'maxTotalBytes': max_total_bytes, 'readBytes': total_bytes}}
